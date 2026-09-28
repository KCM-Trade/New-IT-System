"""Tool 1 — ``get_client_overview`` (docs/ai-agent/02-contracts.md §3.1).

Who the client is, which accounts they hold, and how the money stands. Every
figure comes from an existing service-layer definition; this module only
assembles them:

* accounts            — live ``fxbackoffice.mt4_users`` (balance / equity /
                        credit, CEN ÷100), demo/test dropped, employees refused
* net deposit legs    — ``account_enrichment.query_net_deposit_split``
                        (trading leg vs 'ib withdrawal' leg, DECOMPOSED — no
                        include_ib_withdrawal switch, §2.2)
* profit / rebate /
  floating / net_gain — ``net_gain_sql.net_gain_by_ids`` on the risk_cases PG
                        (STRICT: any leg NULL → net_gain NULL)
* activity_status     — the risk_cases ``activity_status_case()`` waterfall,
                        evaluated as-of ``date_range.to``
* crm_tags            — PG mirror ``kcm.crm_user_tags``

The model-facing tool takes 1-``MAX_SUBJECTS`` subjects per call
(``get_client_overviews``, 2026-09-28): both money services underneath were
always batch functions, and the single-subject signature threw that away —
"which of these 10 clients is net-negative" was refused by the call budget,
not by the data. ``get_client_overview`` (one subject, one envelope) is the
same code path with a list of one.

The data-access functions are module-level so tests can monkeypatch them and
exercise the assembly logic without a database. Each takes a LIST of client
ids and returns a dict keyed by client id.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from app.core.config import Settings
from app.core.risk_cases_pg import risk_cases_conn
from app.services.account_enrichment import query_net_deposit_split
from app.services.net_gain_sql import net_gain_by_ids
from app.services.risk_cases_service import activity_status_case

from .common import (
    CallerCtx,
    DateRange,
    ResolvedSubject,
    Subject,
    connect_mysql,
    error_envelope,
    is_error,
    mt_local_to_utc_iso,
    ok_envelope,
    parse_date_range,
    parse_subject,
    resolve_subject,
    run_sync_with_timeout,
    today_mt,
    utc_now_iso,
)

TOOL_NAME = "get_client_overview"

# The same waterfall risk_cases uses for the watchlist, evaluated for ONE user
# and as-of a caller-chosen day instead of current_date. ``holding`` reads the
# live positions snapshot, which only makes sense when the as-of day is today;
# for a historical as-of the branch is switched off (see _fetch_activity).
_ACTIVITY_SQL = f"""
    WITH pos AS (
        SELECT DISTINCT user_id FROM kcm.active_positions_snapshot
        WHERE %(holding_live)s
    )
    SELECT p.user_id,
           {activity_status_case("%(asof)s::date")} AS activity_status,
           t.last_trade_date
    FROM kcm.user_profile p
    LEFT JOIN kcm.user_activity_summary t ON t.user_id = p.user_id
    LEFT JOIN pos ON pos.user_id = p.user_id
    WHERE p.user_id = ANY(%(uids)s)
"""

_CRM_TAGS_SQL = """
    SELECT ut.user_id, t.tag
    FROM kcm.crm_user_tags ut
    JOIN kcm.crm_tags t ON t.id = ut.tag_id
    WHERE ut.user_id = ANY(%(uids)s)
    ORDER BY ut.user_id, t.tag
"""

# One call's subject cap: 10 -> 50 on 2026-09-28 (user: remove usage caps).
# Kept finite only as input validation — the ids go into one IN (...) list.
MAX_SUBJECTS = 50

_LAST_TRADE_SQL = """
    SELECT t.loginSid AS login_sid, MAX(t.OPEN_TIME) AS last_open
    FROM fxbackoffice.mt4_trades t
    WHERE t.loginSid IN ({placeholders})
      AND t.openDate BETWEEN %s AND %s
      AND t.CMD IN (0, 1)
      AND COALESCE(t.isDeleted, 0) = 0
    GROUP BY t.loginSid
"""


# ── data access (monkeypatch targets) ────────────────────────────────────────


def _fetch_money_pg(settings: Settings, client_ids: list[int]) -> dict[int, dict[str, Optional[float]]]:
    with risk_cases_conn(settings) as conn:
        with conn.cursor() as cur:
            by_id = net_gain_by_ids(cur, list(client_ids))
    out: dict[int, dict[str, Optional[float]]] = {}
    for cid in client_ids:
        legs = by_id.get(cid) or {}
        out[cid] = {
            "profit_all": legs.get("profit_all"),
            "rebate_all": legs.get("rebate_all"),
            "floating_pl": legs.get("floating_pl"),
            "net_gain": legs.get("net_gain"),
        }
    return out


def _fetch_net_deposit_split(settings: Settings, client_ids: list[int]) -> dict[int, dict[str, float]]:
    conn = connect_mysql(settings)
    try:
        by_id = query_net_deposit_split(conn, list(client_ids))
    finally:
        conn.close()
    out: dict[int, dict[str, float]] = {}
    for cid in client_ids:
        split = by_id.get(cid) or {}
        out[cid] = {
            "net_deposit_trading": round(float(split.get("trading_net_deposit") or 0.0), 2),
            "ib_withdrawal": round(float(split.get("ib_withdrawal") or 0.0), 2),
        }
    return out


def _fetch_last_trades(settings: Settings, login_sids: list[str], rng: DateRange) -> dict[str, Optional[str]]:
    if not login_sids:
        return {}
    conn = connect_mysql(settings)
    try:
        with conn.cursor() as cur:
            cur.execute(
                _LAST_TRADE_SQL.format(placeholders=", ".join(["%s"] * len(login_sids))),
                (*login_sids, rng.day_from.isoformat(), rng.day_to.isoformat()),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return {r["login_sid"]: mt_local_to_utc_iso(r.get("last_open")) for r in rows}


def _fetch_activity(settings: Settings, client_ids: list[int], asof: date) -> dict[int, dict[str, Any]]:
    holding_live = asof >= today_mt()
    uids = list(client_ids)
    with risk_cases_conn(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(_ACTIVITY_SQL, {"uids": uids, "asof": asof.isoformat(), "holding_live": holding_live})
            status = {int(r["user_id"]): r["activity_status"] for r in cur.fetchall()}
            cur.execute(_CRM_TAGS_SQL, {"uids": uids})
            tags: dict[int, list[str]] = {}
            for r in cur.fetchall():
                tags.setdefault(int(r["user_id"]), []).append(r["tag"])
    return {
        cid: {"activity_status": status.get(cid), "crm_tags": tags.get(cid, []), "holding_live": holding_live}
        for cid in client_ids
    }


# ── the tool ─────────────────────────────────────────────────────────────────


def _client_block(resolved: ResolvedSubject, *, money_pg: dict, split: dict, last: dict, activity: dict, rng: DateRange) -> dict:
    cid = resolved.client_id
    return {
        "client": {
            "client_id": cid,
            "cid": resolved.cid,
            "country": resolved.country,
            "registered_at": resolved.registered_at,
            "crm_tags": activity[cid]["crm_tags"],
        },
        "accounts": [{**a, "last_trade_at": last.get(a["login_sid"])} for a in resolved.accounts],
        "money": {
            "net_deposit_trading": split[cid]["net_deposit_trading"],
            "ib_withdrawal": split[cid]["ib_withdrawal"],
            "profit_all": money_pg[cid]["profit_all"],
            "rebate_all": money_pg[cid]["rebate_all"],
            "floating_pl": money_pg[cid]["floating_pl"],
            "net_gain": money_pg[cid]["net_gain"],
            "net_gain_definition": "profit_all + floating_pl + rebate_all (STRICT: null when any leg is null)",
        },
        "activity_status": activity[cid]["activity_status"],
        "date_range": rng.as_dict(),
        "accounts_excluded_as_demo": resolved.excluded_accounts,
    }


async def _overview_many(ctx: CallerCtx, subjects: list[Subject], rng: DateRange) -> tuple[list[tuple[Subject, dict]], dict | None]:
    """Resolve every subject (each through the same gate as a single call),
    then fetch money / legs / last trades / activity ONCE for all resolved
    clients. Returns ``[(subject, client_block | error_envelope)]`` in input
    order, or a whole-call error envelope when a batch fetch failed."""
    per_subject: list[tuple[Subject, ResolvedSubject | dict]] = []
    for subj in subjects:
        per_subject.append((subj, await resolve_subject(ctx, subj, tool=TOOL_NAME)))
    good = [r for _, r in per_subject if isinstance(r, ResolvedSubject)]
    if not good:
        return [(s, r) for s, r in per_subject], None  # type: ignore[misc]

    cids = list(dict.fromkeys(r.client_id for r in good))
    login_sids = list(dict.fromkeys(sid for r in good for sid in r.login_sids))
    money_pg = await run_sync_with_timeout(_fetch_money_pg, ctx.settings, cids, ctx=ctx)
    if is_error(money_pg):
        return [], money_pg
    split = await run_sync_with_timeout(_fetch_net_deposit_split, ctx.settings, cids, ctx=ctx)
    if is_error(split):
        return [], split
    last = await run_sync_with_timeout(_fetch_last_trades, ctx.settings, login_sids, rng, ctx=ctx)
    if is_error(last):
        return [], last
    activity = await run_sync_with_timeout(_fetch_activity, ctx.settings, cids, rng.day_to, ctx=ctx)
    if is_error(activity):
        return [], activity

    out: list[tuple[Subject, dict]] = []
    for subj, r in per_subject:
        if isinstance(r, ResolvedSubject):
            out.append((subj, _client_block(r, money_pg=money_pg, split=split, last=last, activity=activity, rng=rng)))
        else:
            out.append((subj, r))
    return out, None


async def get_client_overview(ctx: CallerCtx, subject: Any, date_range: Any) -> dict:
    """ONE subject, one envelope: an error envelope when that subject fails,
    else ``data`` is the client block."""
    subj = parse_subject(subject)
    if isinstance(subj, dict):
        return subj
    rng = parse_date_range(date_range)
    if isinstance(rng, dict):
        return rng
    results, failed = await _overview_many(ctx, [subj], rng)
    if failed is not None:
        return failed
    block = results[0][1]
    if is_error(block):
        return block
    return ok_envelope(block, definition=_definition(), source=_source(), ctx=ctx)


async def get_client_overviews(ctx: CallerCtx, subjects: Any, date_range: Any) -> dict:
    """1..MAX_SUBJECTS subjects in one call. A subject that fails (not found,
    excluded, scope_denied) is reported in ``data.failed`` with its error and
    does NOT fail the others; only a data-source failure fails the call."""
    if not isinstance(subjects, list) or not subjects:
        return error_envelope("invalid_argument", "subjects must be a non-empty list of {kind, value}")
    if len(subjects) > MAX_SUBJECTS:
        return error_envelope(
            "invalid_argument",
            f"At most {MAX_SUBJECTS} subjects per call; split the list across calls.",
            {"given": len(subjects), "limit": MAX_SUBJECTS},
        )
    parsed: list[Subject] = []
    for raw in subjects:
        subj = parse_subject(raw)
        if isinstance(subj, dict):
            return error_envelope(
                "invalid_argument", f"subjects[{len(parsed)}]: {subj['error']['message']}", {"index": len(parsed)}
            )
        if subj not in parsed:
            parsed.append(subj)
    rng = parse_date_range(date_range)
    if isinstance(rng, dict):
        return rng
    results, failed = await _overview_many(ctx, parsed, rng)
    if failed is not None:
        return failed
    clients = [{"subject": s.label, **block} for s, block in results if not is_error(block)]
    errors = [
        {"subject": s.label, "code": block["error"]["code"], "message": block["error"]["message"]}
        for s, block in results
        if is_error(block)
    ]
    data = {"clients": clients, "failed": errors, "date_range": rng.as_dict()}
    return ok_envelope(data, definition=_definition(batch=True), source=_source(), ctx=ctx)


def _definition(*, batch: bool = False) -> dict:
    caveats = [
        "CEN (cent) accounts are already divided by 100; all money is USD.",
        "Demo/test accounts are excluded from `accounts`; employee clients are refused (subject_excluded).",
        "`money` is CUMULATIVE up to `source.as_of` — it is NOT restricted to `date_range`. "
        "`date_range` only affects `activity_status` (as-of its last day) and `accounts[].last_trade_at`.",
        "`net_gain` is the STRICT definition: profit_all + floating_pl + rebate_all, null if any leg is unknown; "
        "`rebate_all` is the FULL-CHAIN rebate (every IB level), from kcm.daily_user_rebate.",
        "`net_deposit_trading` (deposits + withdrawals) and `ib_withdrawal` (IB commission cash-outs) are two legs on "
        "purpose; the legacy single net-deposit number was their sum and misleads for IB-cum-traders.",
        "`activity_status` follows the risk-watchlist waterfall (holding > active_1d/7d/30d/90d > dormant > "
        "funded_no_trade > new_no_fund > no_fund) with calendar-day windows; `holding` is only evaluated when "
        "date_range.to is today.",
        "`accounts[].balance/equity/credit` are the live broker values at call time, not a date_range snapshot.",
    ]
    if batch:
        caveats = caveats + [
            "One entry per subject in `clients`, in the order given; a subject that could not be reported "
            "(not found / excluded / outside your data scope) is in `failed` with its error code instead — "
            "say which ones, and do not retry them.",
        ]
    return {
        "summary": "Client identity, compliant trading accounts with live balances, cumulative money legs "
        "(trading net deposit, IB withdrawal, closed profit, full-chain rebate, floating P&L, STRICT net gain) "
        "and the risk-watchlist activity status.",
        "caveats": caveats,
        "doc": "CLAUDE.md#净入金公司盈亏口径; .cursor/skills/rebate-arbitrage/SKILL.md §2.2; docs/features/risk-watchlist.md",
    }


def _source() -> dict:
    return {
        "service": "app.services.net_gain_sql + app.services.account_enrichment + app.services.risk_cases_service",
        "function": "net_gain_by_ids / query_net_deposit_split / activity_status_case",
        "as_of": utc_now_iso(),
    }
