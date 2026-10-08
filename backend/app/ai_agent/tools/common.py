"""Shared plumbing for the certified tools (docs/ai-agent/02-contracts.md §2).

Everything the three tools have in common lives here so the contract is
enforced in ONE place: the envelope shape, the error codes, the subject and
date-range validation, the scope check, and the "never raise" wrapper around
synchronous DB work.

Design rules (each one is a contract line, not a preference):

* Tools return STRUCTURES, never raise. An exception escaping a tool would be
  handed back to the model by the framework as a tool error, and the wording
  the model then produces is not ours to control (§2.6).
* ``scope`` is ``None`` (unrestricted) or a frozenset (restricted, possibly
  empty). The two are opposite and both falsy — test ``is None``, never
  truthiness (§2.1; same trap as ``caller_cids()``).
* The caller identity is a per-request value captured in ``CallerCtx``. No
  tool reads a module-level identity and no tool accepts "who am I" from the
  model's arguments (§2.1).
* Sync DB calls go through ``run_sync_with_timeout`` — the framework's tool
  callback is async and a bare psycopg2/PyMySQL call would block the agent
  process's event loop for every concurrent user (§4.2).
"""

from __future__ import annotations

import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional, TypeVar

import anyio
import pymysql

from app.core.config import Settings, get_settings
from app.core.data_scope import (
    KNOWN_CIDS,
    _refusal_log_decision,
)
from app.core.logging_config import get_logger
from app.core.mysql_readonly import connect_readonly
from app.services.rule_intraday_return_service import MT_SERVER_TZ, _local_to_utc

logger = get_logger(__name__)

T = TypeVar("T")

# ── contract constants (§2.7) ────────────────────────────────────────────────

# No generic cap (None) since 2026-09-28 (366 -> 1096 -> removed at the user's
# request): "since the account opened" is a normal risk question. What protects
# the replica is TOOL_TIMEOUT_SECONDS plus the DB-side statement timeouts, and
# the two tools whose limit is a fact about the data keep their own cap
# (rank_accounts 92 days = whole-universe scan, get_risk_alerts 31 days because
# alert_events is only kept 30).
MAX_RANGE_DAYS: Optional[int] = None
MAX_ROWS = 200
MAX_ALERTS = 500
# Raised from 25.0 on 2026-09-28. One tool call is one DB round trip, and the
# turn budget (harness.TURN_WALL_CLOCK_SECONDS) is what bounds a turn; 25s made
# every multi-year or group query fail as upstream_timeout.
TOOL_TIMEOUT_SECONDS = 60.0
DAY_BASIS = "MT server day (DST-aware, UTC+3 summer / UTC+2 winter, US DST calendar)"

# ── MySQL connection: the db-timeout-guard three lines ──────────────────────
#
# Same helper data_scope uses (core/mysql_readonly.py), with a larger statement
# budget: the trade-activity aggregation legitimately needs more than the 5s a
# point lookup gets. Same three defences, different number — which is exactly
# why the helper takes the number as a parameter instead of being copied.
# 30s since 2026-10-06 (was 15s), same number as run_sql.STATEMENT_TIMEOUT_MS —
# see the note there for the measurement and why it should not go higher.
_MAX_EXECUTION_TIME_MS = 30_000
_READ_TIMEOUT_S = _MAX_EXECUTION_TIME_MS // 1000 + 10


def connect_mysql(settings: Optional[Settings] = None) -> pymysql.connections.Connection:
    """Read-only replica connection with connect/read/statement timeouts."""
    return connect_readonly(
        settings or get_settings(), max_execution_ms=_MAX_EXECUTION_TIME_MS, read_timeout=_READ_TIMEOUT_S
    )


# ── caller context ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CallerCtx:
    """The identity a tool closure is bound to. Built once per request by the
    server from the main API's ``caller`` + ``scope`` fields and never
    modified afterwards."""

    user_id: int
    email: str
    role: str
    allowed_modules: tuple[str, ...]
    scope: Optional[frozenset[int]]  # None = unrestricted
    trace_id: str
    settings: Settings = field(default_factory=get_settings, repr=False, compare=False)
    # Per-TURN memo of resolved subjects, keyed (kind, value). One question
    # usually drives all three tools at the same client, and each used to pay
    # the resolver's three MySQL round trips again. The dataclass is frozen
    # but the dict is mutable, which is the point: identity is immutable for
    # the turn, the cache is not. Error envelopes are never cached — a
    # transient timeout must not become "not found" for the rest of the turn.
    subject_cache: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def cids_applied(self) -> Any:
        """The ``scope.cids_applied`` value for the envelope (§2.5)."""
        return "all" if self.scope is None else sorted(self.scope)


def ctx_from_request(caller: dict, scope: Any, trace_id: str) -> CallerCtx:
    """Turn the internal request body into a CallerCtx.

    ``scope`` arrives as JSON ``null`` or a list. A list — even an empty one —
    means RESTRICTED; only JSON null is "no restriction". Normalising an empty
    list to None here would be the exact ``if not scope`` bug the contract
    forbids, just moved one layer up.
    """
    return CallerCtx(
        user_id=int(caller["user_id"]),
        email=str(caller.get("email") or ""),
        role=str(caller.get("role") or ""),
        allowed_modules=tuple(caller.get("allowed_modules") or ()),
        scope=None if scope is None else frozenset(int(c) for c in scope),
        trace_id=str(trace_id or ""),
    )


# ── module gates (docs/ai-agent/11 §0 T1) ───────────────────────────────────


def has_module(ctx: CallerCtx, name: str) -> bool:
    """Same meaning as the main API's ``caller_has_module()``: ``"*"`` = every
    module (including future ones), ``[]`` = none, otherwise membership. The
    main API already turned SQL NULL into ``["*"]`` before forwarding
    (``auth_service.parse_allowed_modules``), so a missing list here is ``[]``
    — fail closed. Never a truthiness test on the tuple."""
    mods = ctx.allowed_modules
    return "*" in mods or name in mods


def risk_tools_enabled(ctx: CallerCtx) -> bool:
    """Register the slice-3 Risk control tools (get_risk_alerts /
    get_alert_orders / get_window_scan)? Only for callers holding ``risk`` AND
    unrestricted (``scope is None``) — 11 §0 T1: the module gate is an API
    gate, so the agent must not become a side door into the risk pages; and a
    restricted person is, by the data-scope design, never meant to hold
    ``risk`` (the pages would 403 them fail-closed). The WARNING mirrors that
    page-side refusal so a mis-ticked grant is visible in the log."""
    if not has_module(ctx, "risk"):
        return False
    if ctx.scope is not None:
        logger.warning(
            "AI risk tools withheld: caller holds 'risk' but is data-scope restricted "
            "(email=%s scope=%s trace=%s) — 'risk' is not in SCOPED_MODULES; untick it in /cfg/managers",
            ctx.email, sorted(ctx.scope), ctx.trace_id,
        )
        return False
    return True


# pymysql error numbers that mean "the SERVER stopped the statement":
# 3024 = ER_QUERY_TIMEOUT (MAX_EXECUTION_TIME fired), 1317 = ER_QUERY_INTERRUPTED,
# 2013 = CR_SERVER_LOST (read_timeout abandoned the socket). Same set as run_sql.
MYSQL_TIMEOUT_CODES = frozenset({3024, 1317, 2013})


def mysql_timeout_envelope(exc: BaseException, ctx: CallerCtx) -> Optional[dict]:
    """``upstream_timeout`` envelope when ``exc`` is a MySQL statement/read
    timeout, else None (caller decides)."""
    if isinstance(exc, pymysql.MySQLError):
        code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
        if code in MYSQL_TIMEOUT_CODES:
            return error_envelope(
                "upstream_timeout",
                f"The database stopped the query at its {_MAX_EXECUTION_TIME_MS // 1000}s limit. Narrow the window/filters and retry once.",
                {"trace_id": ctx.trace_id, "mysql_errno": code},
            )
    return None


# ── envelopes (§2.5 / §2.6) ──────────────────────────────────────────────────

ERROR_CODES = frozenset(
    {
        "subject_not_found",
        "subject_excluded",
        "scope_denied",
        "range_too_wide",
        "upstream_timeout",
        "internal",
        # Argument-shape problems the model can fix by re-reading the manual.
        # Not in the contract table because the contract assumes valid input;
        # a structured answer is still better than a framework tool error.
        "invalid_argument",
        # search_web (OPT-0078). Own codes rather than upstream_timeout: that
        # one tells the model to retry, and a retried search is billed twice.
        "query_rejected",
        "search_limit_reached",
        "web_search_timeout",
        "web_search_unavailable",
    }
)


def ok_envelope(
    data: dict,
    *,
    definition: dict,
    source: dict,
    ctx: CallerCtx,
    truncated: bool = False,
) -> dict:
    """The success envelope. ``definition`` and ``source`` are mandatory on
    every call — the UI's provenance badge is rendered from them."""
    assert "summary" in definition and "caveats" in definition, "definition incomplete"
    src = {"certified": True, **source}
    src.setdefault("as_of", utc_now_iso())
    return {
        "ok": True,
        "data": data,
        "definition": {"day_basis": DAY_BASIS, **definition},
        "source": src,
        "scope": {"cids_applied": ctx.cids_applied},
        "truncated": bool(truncated),
    }


def error_envelope(code: str, message: str, detail: Optional[dict] = None) -> dict:
    assert code in ERROR_CODES, f"unknown error code {code!r}"
    return {"ok": False, "error": {"code": code, "message": message, "detail": detail or {}}}


def is_error(value: Any) -> bool:
    """True only for an ERROR ENVELOPE. Data-access helpers return plain
    dicts (no ``ok`` key), so ``not value.get("ok")`` would misread every
    successful lookup as a failure."""
    return isinstance(value, dict) and value.get("ok") is False and "error" in value


# ── time helpers ─────────────────────────────────────────────────────────────


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def to_utc_iso(value: Any) -> Optional[str]:
    """Any datetime-ish → UTC ISO ``...Z``. Naive datetimes are treated as
    UTC (the only naive values reaching here are already-UTC SQLite/PG
    columns); MT wall-clock values must go through ``mt_local_to_utc_iso``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return f"{value.isoformat()}T00:00:00Z"
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s
    return to_utc_iso(dt)


def mt_local_to_utc_iso(value: Any) -> Optional[str]:
    """MT server wall clock (naive, as mt4_trades stores it) → UTC ISO,
    DST-aware via the intraday-return SSOT."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_utc_iso(_local_to_utc(value.replace(tzinfo=None)))
    try:
        return mt_local_to_utc_iso(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def mt_day_bounds_utc(day_from: date, day_to: date) -> tuple[str, str]:
    """Closed MT-day interval → half-open UTC ISO bounds ``[since, until)``."""
    start = _local_to_utc(datetime(day_from.year, day_from.month, day_from.day))
    end = _local_to_utc(datetime(day_to.year, day_to.month, day_to.day) + timedelta(days=1))
    return to_utc_iso(start), to_utc_iso(end)  # type: ignore[return-value]


def today_mt() -> date:
    return datetime.now(MT_SERVER_TZ).date()


# ── argument validation (§2.3 / §2.4) ────────────────────────────────────────

_LOGIN_SID_RE = re.compile(r"^(\d{1,3})-(\d{1,12})$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Subject:
    kind: str  # "client_id" | "login_sid"
    value: str

    @property
    def client_id(self) -> Optional[int]:
        return int(self.value) if self.kind == "client_id" else None

    @property
    def login_parts(self) -> Optional[tuple[str, str]]:
        if self.kind != "login_sid":
            return None
        m = _LOGIN_SID_RE.match(self.value)
        return (m.group(1), m.group(2)) if m else None

    @property
    def label(self) -> str:
        return f"{'client' if self.kind == 'client_id' else 'login'}:{self.value}"


def parse_subject(raw: Any) -> Subject | dict:
    """Validate the model's ``subject`` argument. Returns a Subject or an
    ``invalid_argument`` envelope — never raises."""
    if not isinstance(raw, dict):
        return error_envelope("invalid_argument", "subject must be an object {kind, value}")
    kind = str(raw.get("kind") or "").strip()
    value = str(raw.get("value") or "").strip()
    if kind == "client_id":
        if not value.isdigit():
            return error_envelope("invalid_argument", "client_id must be a positive integer string")
        return Subject(kind, str(int(value)))
    if kind == "login_sid":
        if not _LOGIN_SID_RE.match(value):
            return error_envelope(
                "invalid_argument", "login_sid must look like '{SID}-{LOGIN}', e.g. '1-8522845'"
            )
        return Subject(kind, value)
    return error_envelope("invalid_argument", "subject.kind must be 'client_id' or 'login_sid'")


@dataclass(frozen=True)
class DateRange:
    day_from: date
    day_to: date

    @property
    def days(self) -> int:
        return (self.day_to - self.day_from).days + 1

    def as_dict(self) -> dict:
        return {"from": self.day_from.isoformat(), "to": self.day_to.isoformat()}


def parse_date_range(raw: Any) -> DateRange | dict:
    """Closed interval of MT server days, at most MAX_RANGE_DAYS wide (None = no cap).

    Over-wide ranges are REFUSED with ``range_too_wide``, not clipped: a
    silently narrowed window is a number that looks right and is not (§2.4).
    """
    if not isinstance(raw, dict):
        return error_envelope("invalid_argument", "date_range must be an object {from, to}")
    f, t = str(raw.get("from") or "").strip(), str(raw.get("to") or "").strip()
    if not (_DATE_RE.match(f) and _DATE_RE.match(t)):
        return error_envelope("invalid_argument", "date_range.from/to must be 'YYYY-MM-DD'")
    try:
        day_from, day_to = date.fromisoformat(f), date.fromisoformat(t)
    except ValueError:
        return error_envelope("invalid_argument", "date_range holds an impossible calendar date")
    if day_from > day_to:
        return error_envelope("invalid_argument", "date_range.from is after date_range.to")
    rng = DateRange(day_from, day_to)
    if MAX_RANGE_DAYS is not None and rng.days > MAX_RANGE_DAYS:
        return error_envelope(
            "range_too_wide",
            f"date_range spans {rng.days} days; the limit is {MAX_RANGE_DAYS}. Narrow the range.",
            {"days": rng.days, "max_days": MAX_RANGE_DAYS},
        )
    return rng


# ── subject resolution (shared by all three tools) ───────────────────────────


@dataclass
class ResolvedSubject:
    client_id: int
    cid: Optional[int]
    is_employee: bool
    country: Optional[str]
    registered_at: Optional[str]
    accounts: list[dict]  # compliant (non-demo/test) trading accounts, sid in (1,5,6)
    excluded_accounts: int  # demo/test accounts dropped
    crm_row_found: bool
    # An MT account that exists but has no CRM owner (mt4_users.userId NULL).
    # It is "outside the client universe" in the contract's sense (§2.2), so it
    # answers subject_excluded — not subject_not_found, which would make a real
    # account read as a typo. Observed live on 2026-09-27 with 5-60000017, the
    # risk team's validation account.
    unlinked: bool = False

    @property
    def login_sids(self) -> list[str]:
        return [a["login_sid"] for a in self.accounts]

    @property
    def is_excluded(self) -> bool:
        """Demo / employee / outside the client universe (§2.2)."""
        if self.is_employee:
            return True
        # A client whose only accounts are demo/test is a demo client.
        return self.excluded_accounts > 0 and not self.accounts


_DEMO_TEST_MARKERS = ("demo", "test")


def _is_demo_or_test(group: Any, name: Any) -> bool:
    """Same predicate as login_ip_trade_profit_service._ACCOUNT_FILTER_SQL,
    evaluated in Python so the account list can COUNT what it dropped."""
    g = (group or "").lower()
    n = (name or "").lower()
    return any(m in g or m in n for m in _DEMO_TEST_MARKERS)


def _fetch_subject(settings: Settings, subject: Subject) -> Optional[ResolvedSubject]:
    """One MySQL round trip per table: users → mt4_users. Sync; call through
    ``run_sync_with_timeout``. Returns None when the id does not exist."""
    conn = connect_mysql(settings)
    try:
        with conn.cursor() as cur:
            client_id = subject.client_id
            if client_id is None:
                cur.execute(
                    "SELECT userId FROM fxbackoffice.mt4_users WHERE loginSid = %s LIMIT 1",
                    (subject.value,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                if row.get("userId") is None:
                    return ResolvedSubject(
                        client_id=0, cid=None, is_employee=False, country=None,
                        registered_at=None, accounts=[], excluded_accounts=0,
                        crm_row_found=False, unlinked=True,
                    )
                client_id = int(row["userId"])
            cur.execute(
                "SELECT u.id, u.cid, COALESCE(u.isEmployee, 0) AS is_employee, "
                "NULLIF(u.country, '') AS country, u.createdAt AS created_at "
                "FROM fxbackoffice.users u WHERE u.id = %s",
                (client_id,),
            )
            urow = cur.fetchone()
            cur.execute(
                "SELECT mu.loginSid, mu.sid, mu.LOGIN AS login, mu.`GROUP` AS grp, mu.NAME AS name, "
                "UPPER(mu.CURRENCY) AS currency, mu.BALANCE AS balance, mu.EQUITY AS equity, "
                "mu.CREDIT AS credit, mu.REGDATE AS regdate "
                "FROM fxbackoffice.mt4_users mu "
                "WHERE mu.userId = %s AND mu.sid IN (1, 5, 6) "
                "ORDER BY mu.sid, mu.LOGIN",
                (client_id,),
            )
            arows = cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if urow is None and not arows:
        return None

    accounts: list[dict] = []
    dropped = 0
    for r in arows:
        if _is_demo_or_test(r.get("grp"), r.get("name")):
            dropped += 1
            continue
        is_cent = (r.get("currency") or "") == "CEN"
        div = 100.0 if is_cent else 1.0
        accounts.append(
            {
                "login_sid": r["loginSid"],
                "sid": int(r["sid"]),
                "login": int(r["login"]),
                "group": r.get("grp"),
                "currency": "USD" if is_cent else (r.get("currency") or "USD"),
                "is_cent": is_cent,
                "balance": round(float(r.get("balance") or 0.0) / div, 2),
                "equity": round(float(r.get("equity") or 0.0) / div, 2),
                "credit": round(float(r.get("credit") or 0.0) / div, 2),
                "opened_at": mt_local_to_utc_iso(r.get("regdate")),
                "last_trade_at": None,  # filled by the tool that asked for it
            }
        )

    cid_raw = urow.get("cid") if urow else None
    try:
        cid = int(cid_raw) if cid_raw is not None else None
    except (TypeError, ValueError):
        cid = None
    if cid is not None and cid not in KNOWN_CIDS:
        cid = None

    return ResolvedSubject(
        client_id=int(client_id),
        cid=cid,
        is_employee=bool(urow and int(urow.get("is_employee") or 0)),
        country=(urow or {}).get("country"),
        registered_at=to_utc_iso((urow or {}).get("created_at")),
        accounts=accounts,
        excluded_accounts=dropped,
        crm_row_found=urow is not None,
    )


async def resolve_subject(ctx: CallerCtx, subject: Subject, *, tool: str) -> ResolvedSubject | dict:
    """Resolve + gate. Order is load-bearing:

    1. not found → ``subject_not_found``;
    2. SCOPE before exclusion — a restricted caller must not learn whether a
       CN id is a demo account, an employee, or a real client. All three
       answer ``scope_denied`` identically (the same "no oracle" rule as
       ``require_cids_allowed``);
    3. demo/employee → ``subject_excluded``.
    """
    cache_key = (subject.kind, subject.value)
    resolved = ctx.subject_cache.get(cache_key)
    if resolved is None:
        resolved = await run_sync_with_timeout(_fetch_subject, ctx.settings, subject, ctx=ctx)
        if isinstance(resolved, dict):  # error envelope from the wrapper
            return resolved
        if resolved is not None:
            ctx.subject_cache[cache_key] = resolved
    if resolved is None:
        # For a restricted caller "not found" and "not yours" must read the
        # same, otherwise refusals enumerate the id space (§2.1 / data_scope).
        if ctx.scope is not None:
            return scope_denied(ctx, subject, tool=tool)
        return error_envelope("subject_not_found", f"No client or account matches {subject.label}.")
    denied = check_scope(ctx, resolved.cid, subject, tool=tool)
    if denied is not None:
        return denied
    if resolved.unlinked:
        return error_envelope(
            "subject_excluded",
            f"{subject.label} exists on the MT server but is not linked to any CRM "
            "client, so it is outside the client universe every certified number "
            "is defined on. No figures are reported for it.",
            {"reason": "no_crm_user"},
        )
    if resolved.is_excluded:
        why = "an employee account" if resolved.is_employee else "a demo/test account"
        return error_envelope(
            "subject_excluded",
            f"{subject.label} is {why} and is outside the client universe every "
            "certified number is defined on. No figures are reported for it.",
            {"client_id": resolved.client_id, "reason": "employee" if resolved.is_employee else "demo"},
        )
    return resolved


# ── scope (§2.1) ─────────────────────────────────────────────────────────────


def check_scope(ctx: CallerCtx, cid: Optional[int], subject: Subject, *, tool: str) -> Optional[dict]:
    """``scope_denied`` envelope when the subject's cid is outside the caller's
    scope; ``None`` when allowed. ``cid is None`` (unresolvable) is REFUSED for
    a restricted caller — fail closed, never "show it because we could not
    tell whose it is"."""
    if ctx.scope is None:
        return None
    if cid is not None and cid in ctx.scope:
        return None
    return scope_denied(ctx, subject, tool=tool)


def scope_denied(ctx: CallerCtx, subject: Subject, *, tool: str) -> dict:
    """Refuse, and log through data_scope's throttled refusal path so a run of
    refusals from one person escalates exactly like the HTTP gate's."""
    level, suppressed, busy = _refusal_log_decision(ctx.email or f"user:{ctx.user_id}", time.monotonic())
    msg = "AI tool scope refused: tool=%s email=%s scope=%s asked_for=%s trace=%s%s%s"
    args = (
        tool,
        ctx.email,
        sorted(ctx.scope or ()),
        subject.label,
        ctx.trace_id,
        f" (+{suppressed} suppressed since the previous line)" if suppressed else "",
        (
            f" — SUSTAINED: {busy} consecutive minutes of refusals from this caller."
            if level == logging.ERROR
            else ""
        ),
    )
    if level is None:
        logger.debug(msg, *args)
    else:
        logger.log(level, msg, *args)
    # No auth_events write HERE: inside the agent container users.db sits on a
    # read-only mount, so the row could never be written and every refusal
    # would print a full traceback instead (OPT-0058: an expected refusal is
    # not an ERROR). The durable trace is written by the main API, which sees
    # this refusal as a ``tool_done`` with ``error_code == "scope_denied"`` and
    # records the ``permission_denied`` auth event there (routes/ai.py).
    return error_envelope(
        "scope_denied",
        "该查询对象不在你的数据范围内 / This subject is outside your data scope.",
        {"subject": subject.label},
    )


# ── sync → async bridge with the contract's timeouts ─────────────────────────


async def run_sync_with_timeout(
    fn: Callable[..., T], *args: Any, ctx: CallerCtx, seconds: float = TOOL_TIMEOUT_SECONDS, **kwargs: Any
) -> T | dict:
    """Run a blocking function in a worker thread under a deadline.

    Returns the function's value, or an error ENVELOPE (``upstream_timeout`` /
    ``internal``) — callers must check ``isinstance(result, dict) and not
    result.get("ok", True)`` or use ``is_error``. The thread is not killed on
    timeout (Python cannot); the DB-side MAX_EXECUTION_TIME / statement_timeout
    is what actually stops the query, this deadline stops the MODEL waiting.
    """
    try:
        with anyio.fail_after(seconds):
            # abandon_on_cancel: when the deadline fires the WAITER stops; the
            # worker thread runs to completion in the background (Python cannot
            # kill it) and its result is discarded. Without it anyio postpones the
            # cancellation until the thread returns and the deadline is moot.
            return await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs), abandon_on_cancel=True)
    except TimeoutError:
        logger.warning("AI tool upstream timeout after %.0fs fn=%s trace=%s", seconds, getattr(fn, "__name__", fn), ctx.trace_id)
        return error_envelope(
            "upstream_timeout",
            f"The data source did not answer within {int(seconds)}s. Narrow the date range and retry once.",
            {"trace_id": ctx.trace_id},
        )
    except Exception as exc:  # noqa: BLE001 — the contract forbids raising
        # A MySQL statement/read timeout is the documented `upstream_timeout`
        # (§2.6: "narrow the range, retry once"), not an internal error — for
        # EVERY tool, not only the ones that wrap their own calls
        # (rank_accounts over a month returned `internal`, 2026-09-28).
        timeout_env = mysql_timeout_envelope(exc, ctx)
        if timeout_env is not None:
            logger.warning("AI tool MySQL timeout fn=%s trace=%s: %s", getattr(fn, "__name__", fn), ctx.trace_id, exc)
            return timeout_env
        logger.error("AI tool internal error fn=%s trace=%s: %s", getattr(fn, "__name__", fn), ctx.trace_id, exc, exc_info=True)
        return error_envelope(
            "internal",
            "An internal error occurred while querying the data source.",
            {"trace_id": ctx.trace_id},
        )


async def run_async_with_timeout(
    coro_fn: Callable[..., Awaitable[T]], *args: Any, ctx: CallerCtx, seconds: float = TOOL_TIMEOUT_SECONDS
) -> T | dict:
    try:
        with anyio.fail_after(seconds):
            return await coro_fn(*args)
    except TimeoutError:
        return error_envelope("upstream_timeout", f"Timed out after {int(seconds)}s.", {"trace_id": ctx.trace_id})
    except Exception as exc:  # noqa: BLE001
        logger.error("AI tool internal error trace=%s: %s", ctx.trace_id, exc, exc_info=True)
        return error_envelope("internal", "An internal error occurred.", {"trace_id": ctx.trace_id})


# ── misc ─────────────────────────────────────────────────────────────────────


def mask_ip(ip: Any) -> Optional[str]:
    """IPv4 → ``a.b.c.0/24``; IPv6 → ``/48``; garbage → None. Analysis needs
    the neighbourhood, not the host (§3.3 — one less thing to leak)."""
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return None
    if addr.version == 4:
        return str(ipaddress.ip_network(f"{addr}/24", strict=False))
    return str(ipaddress.ip_network(f"{addr}/48", strict=False))


def money(value: Any, divisor: float = 1.0) -> float:
    """Decimal/None → rounded float USD."""
    if value is None:
        return 0.0
    return round(float(value) / divisor, 2)
