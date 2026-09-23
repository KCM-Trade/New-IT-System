"""
Trade-IP profit attribution (OPT-0063 Phase 2).

Two halves sharing one module:

1. **Nightly reconcile** — ``reconcile_trade_ip_pnl`` runs inside the 08:30
   report job (``login_ip_scheduler._report_job``). It joins one MT day's
   closed trades (``fxbackoffice.mt4_trades`` for the MT4 family,
   ``mt5_live.mt5_deals`` for MT5) back to the opening order's client IPv4
   captured by Phase 1 (``order_ip`` table) and lands ONE ROW PER CLOSE DEAL
   in ``trade_ip_pnl``. The query API reads only that SQLite table — it never
   touches the MySQL slave (a 90-day window of raw deals would be ~100 s of
   slave time per request; precomputed SQLite is milliseconds).

2. **Grouping + rankings** — over a close-date window, accounts that share
   private IPs are connected into union-find components ("account groups"),
   so a mule cluster rotating across 20 IPs shows up as ONE row instead of
   twenty. Group results are cached in Redis keyed by the FULL parameter set
   (no scope suffix — this is a risk-module route, there is no per-caller
   row filter to key on).

Attribution contract (SSOT: docs/optimization/items/OPT-0063-*.md):
- Profit belongs to the IP of the OPEN ("下单时的 IP"); windows slice by
  CLOSE day (profit realizes at close).
- MT5 open/close cannot be told apart in the journal, so Phase 1 stored ALL
  'order performed' tickets. The reconcile resolves them: a position's open
  order ticket IS its PositionID (verified 2026-09-18: every Entry=0 deal has
  Order == PositionID), so ``order_ref = PositionID`` hits the open row in
  ``order_ip``, and the close deal's own ``Order`` ticket yields ``close_ip``.
- MT4 partial closes mint a NEW ticket with ``COMMENT = 'from #<prev>'``; the
  remainder never had a placement line, so the reconcile walks the chain back
  to the original ticket's IP (cap ``_MAX_CHAIN_DEPTH``).
- Shared exits (carrier NAT / VPN): an IP with >= ``public_ip_clients``
  distinct CRM clients in the window never connects accounts. The page sends
  1000 so this cap does not drop the IPs the fixed rule below is meant to keep.
- Edge rule (decided 2026-09-22, legacy when ``ip_min_clients`` is 0): two
  accounts connect only when they share >= 2 IP-days (same IP, same close day)
  OR >= 2 distinct private IPs. A single co-occurrence is too weak over a
  90-day window — one home broadband reassigned between tenants would merge
  unrelated households.
- Page rule (``ip_min_clients`` >= 2, the UI always sends 5): an IP used by
  that many distinct CRM clients connects every account that used it, even on
  one day. The legacy edges are skipped in this mode, so a chain of two-person
  IPs cannot invent a group. One client with many accounts never reaches the
  count (clients, not accounts).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import Counter, defaultdict
from itertools import combinations
from typing import Any, Callable, Iterable, NamedTuple, Optional
from zoneinfo import ZoneInfo

import pymysql
import pymysql.cursors

from ..core import login_ip_orders_db
from ..core.config import Settings, get_settings
from ..core.sql_helpers import FILETIME_EPOCH_OFFSET, FILETIME_TICKS_PER_SEC
from .rule_intraday_return_service import MT_SERVER_TZ

logger = logging.getLogger(__name__)

HKT = ZoneInfo("Asia/Hong_Kong")

# First day with order_ip data (Phase 1 went live 2026-09-22, backfilled to
# 2026-09-15). Opens before this can never match — they get their own
# no_ip_cause so the bucket is explainable instead of a silent hole.
GO_LIVE_DATE = "2026-09-15"

# Bridge groups (KCM\5LS_*, KCMC\5LS_*): the journal's IP column is
# systematically empty for these (~14% of MT5 order lines, measured
# 2026-09-18) because orders arrive through the bridge, not a client
# terminal. This is a data property, not a bug — the coverage endpoint
# reports the bucket separately.
BRIDGE_GROUP_MARKER = "5LS"

NO_IP_CAUSES = (
    "pre_golive",
    "journal_incomplete",
    "bridge_group",
    "partial_remainder",
    "server_initiated",
)

# MT4 'from #N' chains are short in practice (a position partially closed
# twice is rare); the cap only guards against pathological comment loops.
_MAX_CHAIN_DEPTH = 5
_FROM_RE = re.compile(r"from #(\d+)")

# Server label used in order_ip / trade_ip_pnl <-> fxbackoffice sid.
_SID_TO_SERVER = {1: "MT4", 6: "MT4_Live2"}
_SERVER_TO_SID = {v: k for k, v in _SID_TO_SERVER.items()}

# Per-statement ceiling on the shared read replica (db-timeout-guard). The
# heaviest statement here is the MT5 one-day deals pull (~70k rows, measured
# 0.55 s); 120 s is two hundred times that, and the reconcile is a nightly
# batch job — a timeout skips the day, it does not queue.
_STATEMENT_TIMEOUT_MS = 120_000

# IN-chunk size for both MySQL and SQLite lookups.
_CHUNK = 500

# Redis cache (fail-open, same pattern as hold_bucket_service). Only the
# grouping / ranking results are cached; coverage is a cheap aggregate.
CACHE_PREFIX = "login_ip_trade_profit:v1"
CACHE_TTL_S = 1800

_REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
_REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
_redis_client = None
_redis_lock = threading.Lock()


# ---------------------------------------------------------------------------
# MySQL access (reconcile only)
# ---------------------------------------------------------------------------


def _connect(settings: Settings):
    """Slave connection with the three timeout lines (db-timeout-guard):
    connect_timeout + read_timeout on the socket, MAX_EXECUTION_TIME per
    statement. autocommit so a failed statement never holds a transaction."""
    conn = pymysql.connect(
        host=settings.DB_HOST,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        port=int(settings.DB_PORT),
        charset=settings.DB_CHARSET,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=300,
        autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute(f"SET SESSION MAX_EXECUTION_TIME={_STATEMENT_TIMEOUT_MS}")
    return conn


def _filetime(local_naive: dt.datetime) -> int:
    """MT wall-clock naive datetime -> Windows FILETIME (true UTC instant, as
    mt5_deals.Timestamp stores it). DST-aware via MT_SERVER_TZ — the SSOT for
    the US-DST GMT+2/+3 rule lives in rule_intraday_return_service."""
    utc = local_naive.replace(tzinfo=MT_SERVER_TZ).astimezone(dt.timezone.utc)
    return (int(utc.timestamp()) + FILETIME_EPOCH_OFFSET) * FILETIME_TICKS_PER_SEC


def _mt5_day_filetime_range(day: dt.date) -> tuple[int, int]:
    """[start, end) FILETIME bounds of one MT server day."""
    start_local = dt.datetime(day.year, day.month, day.day)
    end_local = start_local + dt.timedelta(days=1)
    return _filetime(start_local), _filetime(end_local)


# Demo/test/employee filter — the exact口径 of ip_profit_backtest.py DAY_SQL:
# GROUP/NAME substring match on the account row, plus an INNER JOIN to the CRM
# user that also drops accounts with no CRM link and employees.
_ACCOUNT_FILTER_SQL = """
  AND LOWER(mu.`GROUP`) NOT LIKE '%%demo%%' AND LOWER(mu.`GROUP`) NOT LIKE '%%test%%'
  AND LOWER(mu.NAME)    NOT LIKE '%%demo%%' AND LOWER(mu.NAME)    NOT LIKE '%%test%%'
"""


def _pull_mt4_closed(conn, day_iso: str) -> list[dict]:
    """One row per MT4-family trade closed on `day_iso` (MT day)."""
    sql = (
        """
        SELECT t.ticketSid AS deal_ref, t.sid AS sid, t.LOGIN AS account_id,
               t.TICKET AS ticket, t.SYMBOL AS symbol, t.lots AS lots,
               t.totalProfit / IF(mu.CURRENCY = 'CEN', 100, 1) AS profit_usd,
               t.openDate AS open_date,
               TIMESTAMPDIFF(SECOND, t.OPEN_TIME, t.CLOSE_TIME) AS hold_sec,
               t.COMMENT AS comment,
               mu.userId AS user_id, mu.`GROUP` AS grp
        FROM fxbackoffice.mt4_trades t
        JOIN fxbackoffice.mt4_users mu ON mu.loginSid = t.loginSid
        JOIN fxbackoffice.users u ON u.id = mu.userId AND COALESCE(u.isEmployee, 0) = 0
        WHERE t.closeDate = %s
          AND t.sid IN (1, 6)
          AND t.CMD IN (0, 1)
          AND (t.isDeleted = 0 OR t.isDeleted IS NULL)
        """
        + _ACCOUNT_FILTER_SQL
    )
    with conn.cursor() as cur:
        cur.execute(sql, (day_iso,))
        return list(cur.fetchall())


def _pull_mt5_closes(conn, day: dt.date) -> list[dict]:
    """One row per MT5 CLOSE deal of the MT day (Entry 1/2/3 = out / in-out /
    out-by). Sliced by Timestamp (FILETIME, indexed IDX_mt5_deals_Stamp) —
    never by Time (OPT-0062 measured 19.3 s vs 0.25 s)."""
    start_ft, end_ft = _mt5_day_filetime_range(day)
    sql = (
        """
        SELECT d.Deal AS deal_ref, d.Login AS account_id, d.`Order` AS close_order,
               d.PositionID AS position_id, d.Entry AS entry,
               d.Symbol AS symbol, d.Volume / 10000 AS lots,
               (d.Profit + d.Storage + d.Commission)
                   / IF(mu.CURRENCY = 'CEN', 100, 1) AS profit_usd,
               d.Time AS close_time,
               mu.userId AS user_id, mu.`GROUP` AS grp
        FROM mt5_live.mt5_deals d
        JOIN fxbackoffice.mt4_users mu ON mu.loginSid = CONCAT('5-', d.Login)
        JOIN fxbackoffice.users u ON u.id = mu.userId AND COALESCE(u.isEmployee, 0) = 0
        WHERE d.Timestamp >= %s AND d.Timestamp < %s
          AND d.Action IN (0, 1) AND d.Entry IN (1, 2, 3)
        """
        + _ACCOUNT_FILTER_SQL
    )
    with conn.cursor() as cur:
        cur.execute(sql, (start_ft, end_ft))
        return list(cur.fetchall())


def _pull_mt5_open_deals(conn, keys: set[tuple[int, int]]) -> dict[tuple[int, int], dict]:
    """Open-deal info {(login, position_id): row} — open time + open Reason.

    Uses IDX_POSITION (Login, PositionID). Hedging mode gives exactly one IN
    deal per position with Order == PositionID; if several IN deals ever show
    up (netting add-ons), the one whose ticket IS the position id wins.
    """
    out: dict[tuple[int, int], dict] = {}
    key_list = sorted(keys)
    with conn.cursor() as cur:
        for part in _chunks(key_list, _CHUNK):
            ph = ",".join("(%s,%s)" for _ in part)
            flat = [v for pair in part for v in pair]
            cur.execute(
                f"""
                SELECT d.Login AS account_id, d.PositionID AS position_id,
                       d.`Order` AS open_order, d.Reason AS reason, d.Time AS open_time
                FROM mt5_live.mt5_deals d
                WHERE (d.Login, d.PositionID) IN ({ph})
                  AND d.Entry = 0 AND d.Action IN (0, 1)
                """,
                flat,
            )
            for r in cur.fetchall():
                k = (r["account_id"], r["position_id"])
                prev = out.get(k)
                if prev is None or r["open_order"] == r["position_id"]:
                    out[k] = r
    return out


def _pull_mt4_comments(conn, ticksids: list[str]) -> dict[str, str]:
    """ticketSid -> COMMENT, for 'from #N' chain walking. PK lookups."""
    out: dict[str, str] = {}
    with conn.cursor() as cur:
        for part in _chunks(sorted(set(ticksids)), _CHUNK):
            ph = ",".join(["%s"] * len(part))
            cur.execute(
                f"SELECT ticketSid, COMMENT FROM fxbackoffice.mt4_trades "
                f"WHERE ticketSid IN ({ph})",
                part,
            )
            for r in cur.fetchall():
                out[r["ticketSid"]] = r["COMMENT"] or ""
    return out


def _fetch_ib_map(conn, user_ids: Iterable[int]) -> dict[int, int]:
    """user_id -> direct IB (ib_tree level=1; verified 1 row per referralId)."""
    out: dict[int, int] = {}
    ids = sorted({int(u) for u in user_ids if u is not None})
    with conn.cursor() as cur:
        for part in _chunks(ids, _CHUNK):
            ph = ",".join(["%s"] * len(part))
            cur.execute(
                f"SELECT referralId AS user_id, ibId AS ib_id "
                f"FROM fxbackoffice.ib_tree WHERE level = 1 AND referralId IN ({ph})",
                part,
            )
            for r in cur.fetchall():
                out[r["user_id"]] = r["ib_id"]
    return out


class Pullers(NamedTuple):
    """The five slave reads the reconcile needs, injectable for tests."""

    mt4_closed: Callable[[Any, str], list[dict]]
    mt5_closes: Callable[[Any, dt.date], list[dict]]
    mt5_opens: Callable[[Any, set], dict]
    mt4_comments: Callable[[Any, list], dict]
    ib_map: Callable[[Any, Iterable[int]], dict]


_REAL_PULLERS = Pullers(
    mt4_closed=_pull_mt4_closed,
    mt5_closes=_pull_mt5_closes,
    mt5_opens=_pull_mt5_open_deals,
    mt4_comments=_pull_mt4_comments,
    ib_map=_fetch_ib_map,
)


# ---------------------------------------------------------------------------
# Reconcile (nightly, per MT day)
# ---------------------------------------------------------------------------


def _iso_date(value: Any) -> Optional[str]:
    """date/datetime/str -> 'YYYY-MM-DD'. None-safe."""
    if value is None:
        return None
    return str(value)[:10]


def _f(value: Any) -> Optional[float]:
    """float-or-None. MySQL DECIMAL columns arrive as decimal.Decimal, which
    sqlite3 refuses to bind — coerce at the boundary, not per column."""
    return float(value) if value is not None else None


def _classify_no_ip(
    *,
    open_date: Optional[str],
    server: str,
    grp: Optional[str],
    parse_days: set[tuple[str, str]],
    was_remainder: bool,
) -> str:
    """Why this close deal has no open IP. Checked most-definitive first:
    a known-bad explanation always beats the residual 'server_initiated'."""
    if open_date and open_date < GO_LIVE_DATE:
        return "pre_golive"
    if open_date and (open_date.replace("-", ""), server) not in parse_days:
        return "journal_incomplete"
    if grp and BRIDGE_GROUP_MARKER in grp.upper():
        return "bridge_group"
    if was_remainder:
        return "partial_remainder"
    return "server_initiated"


def _resolve_mt4_open_ips(
    mt4_rows: list[dict],
    ip_map: dict[tuple[str, int], str],
    conn: Any,
    pullers: Pullers,
) -> tuple[dict[tuple[str, int], str], set[tuple[str, int]]]:
    """Walk 'from #N' chains for MT4 trades whose own ticket has no IP.

    Returns (resolved_ips, remainder_keys): resolved_ips maps (server,
    close_ticket) -> IP of the original order; remainder_keys is every close
    trade that CARRIED a 'from #' pointer (whether or not the walk found an
    IP — the classifier needs to know).
    """
    # walker: (server, close_ticket) -> ticket currently being looked up.
    walker: dict[tuple[str, int], int] = {}
    for r in mt4_rows:
        server = _SID_TO_SERVER[r["sid"]]
        ticket = int(r["ticket"])
        if (server, ticket) in ip_map:
            continue
        m = _FROM_RE.search(r.get("comment") or "")
        if m:
            walker[(server, ticket)] = int(m.group(1))

    remainder_keys = set(walker)
    resolved: dict[tuple[str, int], str] = {}
    for _depth in range(_MAX_CHAIN_DEPTH):
        if not walker:
            break
        refs_by_server: dict[str, set[int]] = defaultdict(set)
        for (server, _ticket), ref in walker.items():
            refs_by_server[server].add(ref)
        found = login_ip_orders_db.get_order_ips_by_refs(refs_by_server)

        still_missing: dict[tuple[str, int], int] = {}
        comments_needed: list[str] = []
        for key, ref in walker.items():
            ip = found.get((key[0], ref))
            if ip:
                resolved[key] = ip
            else:
                still_missing[key] = ref
                comments_needed.append(f"{_SERVER_TO_SID[key[0]]}-{ref}")
        if not still_missing:
            break
        comments = pullers.mt4_comments(conn, comments_needed)
        walker = {}
        for key, ref in still_missing.items():
            m = _FROM_RE.search(comments.get(f"{_SERVER_TO_SID[key[0]]}-{ref}") or "")
            if m:
                walker[key] = int(m.group(1))
    return resolved, remainder_keys


def reconcile_trade_ip_pnl(target_date: str, *, pullers: Optional[Pullers] = None) -> dict:
    """Reconcile one MT day's closed trades against order_ip. Idempotent.

    `target_date` is YYYYMMDD — the same MT-day convention as
    order_ip.trade_date (HKT yesterday, because MT 00:00 = HKT 05:00 and the
    05:10 job downloads the just-rotated log).

    Re-running the same day DELETEs and rewrites that day's rows, so a fixed
    parser or a re-pulled log can be re-reconciled without manual cleanup.
    """
    day = dt.date(int(target_date[:4]), int(target_date[4:6]), int(target_date[6:8]))
    day_iso = day.isoformat()
    t0 = time.perf_counter()

    own_conn = pullers is None
    conn = _connect(get_settings()) if own_conn else None
    pullers = pullers or _REAL_PULLERS
    try:
        mt4_rows = pullers.mt4_closed(conn, day_iso)
        mt5_rows = pullers.mt5_closes(conn, day)

        # One batched order_ip lookup: MT4 open tickets + MT5 PositionIDs
        # (open order ticket == PositionID) + MT5 close order tickets.
        refs: dict[str, set[int]] = {"MT4": set(), "MT4_Live2": set(), "MT5": set()}
        for r in mt4_rows:
            refs[_SID_TO_SERVER[r["sid"]]].add(int(r["ticket"]))
        for r in mt5_rows:
            refs["MT5"].add(int(r["position_id"]))
            refs["MT5"].add(int(r["close_order"]))
        ip_map = login_ip_orders_db.get_order_ips_by_refs(refs)

        mt4_chain_ips, mt4_remainders = _resolve_mt4_open_ips(
            mt4_rows, ip_map, conn, pullers
        )
        open_info = pullers.mt5_opens(
            conn, {(int(r["account_id"]), int(r["position_id"])) for r in mt5_rows}
        )
        ib_map = pullers.ib_map(
            conn, [r["user_id"] for r in (*mt4_rows, *mt5_rows) if r.get("user_id")]
        )
        parse_days = login_ip_orders_db.get_parse_run_server_days()

        records: list[tuple] = []
        cause_counts: Counter = Counter()

        for r in mt4_rows:
            server = _SID_TO_SERVER[r["sid"]]
            key = (server, int(r["ticket"]))
            open_ip = ip_map.get(key) or mt4_chain_ips.get(key)
            open_date = _iso_date(r.get("open_date"))
            no_ip_cause = None
            if open_ip is None:
                no_ip_cause = _classify_no_ip(
                    open_date=open_date,
                    server=server,
                    grp=r.get("grp"),
                    parse_days=parse_days,
                    was_remainder=key in mt4_remainders,
                )
                cause_counts[no_ip_cause] += 1
            records.append((
                server,
                str(r["deal_ref"]),
                day_iso,
                int(r["account_id"]),
                str(r["ticket"]),
                open_ip,
                None,  # close_ip: MT4 close lines are not order-placement events
                r.get("user_id"),
                ib_map.get(r.get("user_id")),
                r.get("symbol"),
                _f(r.get("lots")),
                _f(r.get("profit_usd")),
                r.get("hold_sec"),
                open_date,
                None,  # reason: MT4 has no deal Reason
                no_ip_cause,
            ))

        for r in mt5_rows:
            position_id = int(r["position_id"])
            open_ip = ip_map.get(("MT5", position_id))
            close_ip = ip_map.get(("MT5", int(r["close_order"])))
            oi = open_info.get((int(r["account_id"]), position_id))
            open_time = oi["open_time"] if oi else None
            open_date = _iso_date(open_time)
            close_time = r.get("close_time")
            hold_sec = (
                int((close_time - open_time).total_seconds())
                if open_time is not None and close_time is not None
                else None
            )
            no_ip_cause = None
            if open_ip is None:
                no_ip_cause = _classify_no_ip(
                    open_date=open_date,
                    server="MT5",
                    grp=r.get("grp"),
                    parse_days=parse_days,
                    was_remainder=False,  # MT4-only mechanism
                )
                cause_counts[no_ip_cause] += 1
            records.append((
                "MT5",
                str(r["deal_ref"]),
                day_iso,
                int(r["account_id"]),
                str(position_id),
                open_ip,
                close_ip,
                r.get("user_id"),
                ib_map.get(r.get("user_id")),
                r.get("symbol"),
                _f(r.get("lots")),
                _f(r.get("profit_usd")),
                hold_sec,
                open_date,
                oi["reason"] if oi else None,
                no_ip_cause,
            ))

        written = login_ip_orders_db.replace_trade_ip_pnl_for_date(day_iso, records)
        summary = {
            "close_date": day_iso,
            "mt4_rows": len(mt4_rows),
            "mt5_rows": len(mt5_rows),
            "rows_written": written,
            "with_ip": sum(1 for rec in records if rec[5] is not None),
            "no_ip_by_cause": dict(cause_counts),
            "elapsed_s": round(time.perf_counter() - t0, 2),
        }
        logger.info("[trade-ip-pnl] reconcile %s: %s", day_iso, summary)
        return summary
    finally:
        if own_conn and conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# Grouping (union-find over shared private IPs)
# ---------------------------------------------------------------------------


class GroupParams(NamedTuple):
    """Every knob that changes the grouping result. The Redis cache key and
    the group_id hash are both derived from the full set — a group_id is only
    meaningful together with the parameters that produced it."""

    date_from: str  # YYYY-MM-DD (close day)
    date_to: str
    min_clients: int = 2
    public_ip_clients: int = 10
    include_same_client: bool = False
    # 0 keeps the legacy "2 IP-days / 2 IPs" edges. >= 2 switches the page
    # rule on: only IPs with this many distinct CRM clients create edges,
    # and one shared use of such an IP is enough.
    ip_min_clients: int = 0


class _UnionFind:
    """Path-halving union-find over account keys ('MT5-67043240')."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        root = self._parent.setdefault(x, x)
        while root != self._parent[root]:
            self._parent[root] = self._parent[self._parent[root]]
            root = self._parent[root]
        # Compress the entry point too, so repeated finds stay O(1).
        self._parent[x] = root
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def _chunks(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _norm_symbol(symbol: Optional[str]) -> str:
    """'XAUUSD.cent' / 'XAUUSD.kcm' -> 'XAUUSD' — the broker suffix splits one
    instrument into fake separate buckets on the dominant-symbol metric."""
    return (symbol or "").split(".")[0].upper()


def _account_key(server: str, account_id: int) -> str:
    return f"{server}-{account_id}"


def compute_groups(p: GroupParams) -> list[dict]:
    """Connected components of accounts sharing private IPs in the window.

    Returns the FULL group dicts (including per-account detail and member IP
    list) sorted by profit desc — the list route projects this down, the
    detail route picks one group out of the cached list.
    """
    with login_ip_orders_db.get_connection() as conn:
        # 1. Candidate IPs: used by >= 2 distinct accounts anywhere in the
        # window. Per-IP, NOT per (ip, day): the distinct-IP edge condition
        # counts CROSS-day sharing, so an IP the group never used twice on
        # the same day is still evidence. Solo-account IPs (the overwhelming
        # majority — mobile IPs churn daily) drop out in the HAVING.
        cand_ips = [
            r["open_ip"]
            for r in conn.execute(
                """
                SELECT open_ip
                FROM trade_ip_pnl
                WHERE close_date BETWEEN ? AND ? AND open_ip IS NOT NULL
                GROUP BY open_ip
                HAVING COUNT(DISTINCT server || '-' || account_id) >= 2
                """,
                (p.date_from, p.date_to),
            )
        ]
        if not cand_ips:
            return []

        # 2. Window-wide distinct CRM clients per candidate IP -> shared exits
        # (carrier NAT / VPN) never connect accounts. Counted by CLIENT, not
        # account: one client with 17 accounts on one IP is a finding, not a
        # NAT.
        ip_clients: dict[str, set[int]] = defaultdict(set)
        for part in _chunks(cand_ips, _CHUNK):
            ph = ",".join("?" * len(part))
            for r in conn.execute(
                f"""
                SELECT open_ip, user_id FROM trade_ip_pnl
                WHERE close_date BETWEEN ? AND ? AND open_ip IN ({ph})
                GROUP BY open_ip, user_id
                """,
                (p.date_from, p.date_to, *part),
            ):
                ip_clients[r["open_ip"]].add(r["user_id"])
        public_ips = {
            ip for ip, clients in ip_clients.items() if len(clients) >= p.public_ip_clients
        }
        # Page rule. These IPs are still subject to public_ips: the UI raises
        # public_ip_clients to the cap so a 5-person IP is not dropped first.
        qualifying_ips = {
            ip
            for ip, clients in ip_clients.items()
            if p.ip_min_clients >= 2 and len(clients) >= p.ip_min_clients
        }
        private_ips = [ip for ip in cand_ips if ip not in public_ips]
        if not private_ips:
            return []

        # 3. Account usage of the surviving private IPs. All days of the
        # window are read, not only shared ones: the distinct-IP edge
        # condition counts cross-day usage (same-day is the stricter rule).
        ipday_accounts: dict[tuple[str, str], set[str]] = defaultdict(set)
        ip_accounts: dict[str, set[str]] = defaultdict(set)
        for part in _chunks(private_ips, _CHUNK):
            ph = ",".join("?" * len(part))
            for r in conn.execute(
                f"""
                SELECT open_ip, close_date, server, account_id FROM trade_ip_pnl
                WHERE close_date BETWEEN ? AND ? AND open_ip IN ({ph})
                GROUP BY open_ip, close_date, server, account_id
                """,
                (p.date_from, p.date_to, *part),
            ):
                acc = _account_key(r["server"], r["account_id"])
                ipday_accounts[(r["open_ip"], r["close_date"])].add(acc)
                ip_accounts[r["open_ip"]].add(acc)

        # 4. Pairwise evidence. pair_ipdays counts shared (ip, day) buckets;
        # pair_ips collects the distinct shared private IPs (doubles as the
        # bridge-IP evidence list on the group's detail view).
        pair_ipdays: Counter = Counter()
        pair_ips: dict[tuple[str, str], set[str]] = defaultdict(set)
        for (ip, _day), accs in ipday_accounts.items():
            if len(accs) < 2:
                continue
            for a, b in combinations(sorted(accs), 2):
                pair_ipdays[(a, b)] += 1
        for ip, accs in ip_accounts.items():
            if len(accs) < 2:
                continue
            for a, b in combinations(sorted(accs), 2):
                pair_ips[(a, b)].add(ip)

        uf = _UnionFind()
        edge_bridge_ips: dict[frozenset, set[str]] = {}
        # ip_min_clients >= 2 replaces the legacy edge: only a qualifying IP
        # connects, and it connects every account that used it (one day, or
        # even different days, is enough). Legacy edges stay for
        # callers that do not pass the knob, so the older tests still hold.
        strict_ip = p.ip_min_clients >= 2
        for a, b in set(pair_ipdays) | set(pair_ips):
            shared = pair_ips.get((a, b), ())
            if strict_ip:
                via = shared & qualifying_ips
                if not via:
                    continue
                bridge = set(via)
            elif pair_ipdays.get((a, b), 0) >= 2 or len(shared) >= 2:
                bridge = set(shared)
            else:
                continue
            uf.union(a, b)
            edge_bridge_ips[frozenset((a, b))] = bridge

        components: dict[str, list[str]] = defaultdict(list)
        for acc in list(uf._parent):
            components[uf.find(acc)].append(acc)
        if not components:
            return []

        # 5. Per-member stats, restricted to grouped accounts (indexed by
        # (server, account_id, close_date)). Three small pulls: totals,
        # per-day profit, per-symbol counts.
        members = sorted(
            {
                (acc.rsplit("-", 1)[0], int(acc.rsplit("-", 1)[1]))
                for accs in components.values()
                for acc in accs
            }
        )
        acct_stats: dict[str, dict] = {}
        acct_daily: dict[str, list[tuple[str, float]]] = defaultdict(list)
        acct_symbols: dict[str, Counter] = defaultdict(Counter)
        for part in _chunks(members, _CHUNK):
            ph = ",".join("(?,?)" for _ in part)
            flat = [v for server, aid in part for v in (server, aid)]
            for r in conn.execute(
                f"""
                SELECT server, account_id,
                       MAX(user_id) AS user_id, MAX(ib_id) AS ib_id,
                       COUNT(*) AS trades, SUM(profit_usd) AS profit_usd,
                       SUM(lots) AS lots, SUM(hold_sec) AS hold_sec_sum,
                       COUNT(DISTINCT close_date) AS active_days
                FROM trade_ip_pnl
                WHERE close_date BETWEEN ? AND ? AND open_ip IS NOT NULL
                  AND (server, account_id) IN ({ph})
                GROUP BY server, account_id
                """,
                (p.date_from, p.date_to, *flat),
            ):
                acct_stats[_account_key(r["server"], r["account_id"])] = dict(r)
            for r in conn.execute(
                f"""
                SELECT server, account_id, close_date, SUM(profit_usd) AS profit_usd
                FROM trade_ip_pnl
                WHERE close_date BETWEEN ? AND ? AND open_ip IS NOT NULL
                  AND (server, account_id) IN ({ph})
                GROUP BY server, account_id, close_date
                """,
                (p.date_from, p.date_to, *flat),
            ):
                acct_daily[_account_key(r["server"], r["account_id"])].append(
                    (r["close_date"], r["profit_usd"] or 0.0)
                )
            for r in conn.execute(
                f"""
                SELECT server, account_id, symbol, COUNT(*) AS trades
                FROM trade_ip_pnl
                WHERE close_date BETWEEN ? AND ? AND open_ip IS NOT NULL
                  AND (server, account_id) IN ({ph})
                GROUP BY server, account_id, symbol
                """,
                (p.date_from, p.date_to, *flat),
            ):
                acct_symbols[_account_key(r["server"], r["account_id"])][
                    _norm_symbol(r["symbol"])
                ] += r["trades"]

    # 6. Assemble groups, apply the client-count filter, hash the id.
    groups: list[dict] = []
    for accs in components.values():
        accs = sorted(accs)
        # Every member has stats rows by construction (edges come from the
        # same filtered table), but skip defensively rather than misalign a
        # zip — a missing account must not shift the next one's numbers.
        stats_by_acc = {a: acct_stats[a] for a in accs if a in acct_stats}
        if not stats_by_acc:
            continue
        stats = list(stats_by_acc.values())
        clients = {s["user_id"] for s in stats if s["user_id"] is not None}
        ibs = {s["ib_id"] for s in stats if s["ib_id"] is not None}
        # Cross-client groups pass on min_clients; one-client-multi-account
        # groups ("一人多户") are a separate class behind their own switch.
        is_same_client = len(clients) <= 1
        if is_same_client:
            if not (p.include_same_client and len(accs) >= 2):
                continue
        elif len(clients) < max(2, p.min_clients):
            continue

        trades = sum(s["trades"] for s in stats)
        profit = sum(s["profit_usd"] or 0.0 for s in stats)
        lots = sum(s["lots"] or 0.0 for s in stats)
        hold_sum = sum(s["hold_sec_sum"] or 0 for s in stats)

        daily_counter: Counter = Counter()
        profitable_days = 0
        for a in accs:
            for d, pnl in acct_daily.get(a, []):
                daily_counter[d] += pnl
        daily = [
            {"date": d, "profit_usd": round(v, 2)} for d, v in sorted(daily_counter.items())
        ]
        profitable_days = sum(1 for v in daily_counter.values() if v > 0)

        sym_counter: Counter = Counter()
        for a in accs:
            sym_counter.update(acct_symbols.get(a, {}))
        dominant_symbol, dominant_trades = ("", 0)
        if sym_counter:
            dominant_symbol, dominant_trades = sym_counter.most_common(1)[0]

        # Private IPs used by >= 2 member accounts = the group's shared-IP
        # evidence. Bridge IPs are the subset that actually created an edge.
        member_set = set(accs)
        bridge_ip_set: set[str] = set()
        for pair, ips in edge_bridge_ips.items():
            if pair <= member_set:
                bridge_ip_set |= ips
        shared_ips = sorted(
            ip for ip, users in ip_accounts.items() if len(users & member_set) >= 2
        )

        id_material = (
            f"{p.date_from}|{p.date_to}|{p.min_clients}|{p.public_ip_clients}|"
            f"{int(p.include_same_client)}|{p.ip_min_clients}|{','.join(accs)}"
        )
        group_id = hashlib.sha1(id_material.encode()).hexdigest()[:12]

        accounts_detail = []
        for a in accs:
            s = stats_by_acc.get(a)
            if s is None:
                continue
            server, _, aid = a.rpartition("-")
            a_sym = acct_symbols.get(a, Counter())
            a_dom = a_sym.most_common(1)[0][0] if a_sym else ""
            accounts_detail.append({
                "account_key": a,
                "server": server,
                "account_id": int(aid),
                "user_id": s["user_id"],
                "ib_id": s["ib_id"],
                "trades": s["trades"],
                "profit_usd": round(s["profit_usd"] or 0.0, 2),
                "lots": round(s["lots"] or 0.0, 3),
                "active_days": s["active_days"],
                "avg_hold_min": round((s["hold_sec_sum"] or 0) / s["trades"] / 60, 1)
                if s["trades"]
                else 0.0,
                "dominant_symbol": a_dom,
            })

        groups.append({
            "group_id": group_id,
            "profit_usd": round(profit, 2),
            "trades": trades,
            "lots": round(lots, 3),
            "accounts": len(accs),
            "clients": len(clients),
            "ibs": len(ibs),
            "same_client": is_same_client,
            "shared_ips": len(shared_ips),
            "active_days": len(daily_counter),
            "profitable_days": profitable_days,
            "dominant_symbol": dominant_symbol,
            "dominant_symbol_share": round(dominant_trades / trades, 3) if trades else 0.0,
            "avg_hold_min": round(hold_sum / trades / 60, 1) if trades else 0.0,
            "daily": daily,
            "account_keys": accs,
            "accounts_detail": accounts_detail,
            "member_ips": [
                {"ip": ip, "accounts": len(ip_accounts[ip] & member_set),
                 "bridge": ip in bridge_ip_set}
                for ip in sorted(shared_ips)
            ],
        })

    groups.sort(key=lambda g: g["profit_usd"], reverse=True)
    return groups


# ---------------------------------------------------------------------------
# Per-IP ranking (the IP-level view — a VPS like 45.32.124.94 is an IP-level
# finding that the group view would hide inside its group)
# ---------------------------------------------------------------------------


def compute_ip_ranking(date_from: str, date_to: str, public_ip_clients: int = 10) -> list[dict]:
    with login_ip_orders_db.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT open_ip AS ip,
                   COUNT(*) AS trades,
                   SUM(profit_usd) AS profit_usd,
                   SUM(lots) AS lots,
                   COUNT(DISTINCT server || '-' || account_id) AS accounts,
                   COUNT(DISTINCT user_id) AS clients,
                   COUNT(DISTINCT ib_id) AS ibs,
                   COUNT(DISTINCT close_date) AS active_days,
                   SUM(hold_sec) AS hold_sec_sum
            FROM trade_ip_pnl
            WHERE close_date BETWEEN ? AND ? AND open_ip IS NOT NULL
            GROUP BY open_ip
            """,
            (date_from, date_to),
        ).fetchall()
        sym_rows = conn.execute(
            """
            SELECT open_ip AS ip, symbol, COUNT(*) AS trades
            FROM trade_ip_pnl
            WHERE close_date BETWEEN ? AND ? AND open_ip IS NOT NULL
            GROUP BY open_ip, symbol
            """,
            (date_from, date_to),
        ).fetchall()

    ip_symbols: dict[str, Counter] = defaultdict(Counter)
    for r in sym_rows:
        ip_symbols[r["ip"]][_norm_symbol(r["symbol"])] += r["trades"]

    out = []
    for r in rows:
        r = dict(r)
        trades = r["trades"] or 0
        dom, dom_n = ("", 0)
        if ip_symbols.get(r["ip"]):
            dom, dom_n = ip_symbols[r["ip"]].most_common(1)[0]
        out.append({
            "ip": r["ip"],
            "profit_usd": round(r["profit_usd"] or 0.0, 2),
            "trades": trades,
            "lots": round(r["lots"] or 0.0, 3),
            "accounts": r["accounts"],
            "clients": r["clients"],
            "ibs": r["ibs"],
            "active_days": r["active_days"],
            "dominant_symbol": dom,
            "dominant_symbol_share": round(dom_n / trades, 3) if trades else 0.0,
            "avg_hold_min": round((r["hold_sec_sum"] or 0) / trades / 60, 1) if trades else 0.0,
            "shared_exit": (r["clients"] or 0) >= public_ip_clients,
        })
    out.sort(key=lambda r: r["profit_usd"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Coverage (how much of the window carries an IP, and why the rest doesn't)
# ---------------------------------------------------------------------------


def get_coverage(date_from: str, date_to: str) -> dict:
    """Window totals + the no-IP bucket split by cause + log/reconcile gaps.

    'Incomplete logs' = a (date, server) inside the window with NO parse run
    (that server's journal was never parsed that day — e.g. its download
    failed). A parse run with few rows is NOT flagged: weekends legitimately
    produce near-empty MT4 logs, and content truncation of a present file is
    not distinguishable from a quiet day by the audit row alone.
    """
    with login_ip_orders_db.get_connection() as conn:
        total_row = conn.execute(
            """
            SELECT COUNT(*) AS trades, SUM(profit_usd) AS profit_usd
            FROM trade_ip_pnl WHERE close_date BETWEEN ? AND ?
            """,
            (date_from, date_to),
        ).fetchone()
        with_ip_row = conn.execute(
            """
            SELECT COUNT(*) AS trades, SUM(profit_usd) AS profit_usd
            FROM trade_ip_pnl WHERE close_date BETWEEN ? AND ? AND open_ip IS NOT NULL
            """,
            (date_from, date_to),
        ).fetchone()
        cause_rows = conn.execute(
            """
            SELECT no_ip_cause, COUNT(*) AS trades, SUM(profit_usd) AS profit_usd
            FROM trade_ip_pnl
            WHERE close_date BETWEEN ? AND ? AND open_ip IS NULL
            GROUP BY no_ip_cause
            """,
            (date_from, date_to),
        ).fetchall()
        present_dates = {
            r["close_date"]
            for r in conn.execute(
                "SELECT DISTINCT close_date FROM trade_ip_pnl "
                "WHERE close_date BETWEEN ? AND ?",
                (date_from, date_to),
            )
        }
        parse_days = {
            (r["trade_date"], r["server_name"])
            for r in conn.execute(
                "SELECT trade_date, server_name FROM order_ip_parse_runs "
                "WHERE trade_date BETWEEN ? AND ?",
                (date_from.replace("-", ""), date_to.replace("-", "")),
            )
        }

    # Expected calendar: from max(from, go-live) to min(to, HKT yesterday) —
    # today's logs have not rotated yet, so today can never be expected.
    first = max(dt.date.fromisoformat(date_from), dt.date.fromisoformat(GO_LIVE_DATE))
    last = min(
        dt.date.fromisoformat(date_to),
        (dt.datetime.now(HKT) - dt.timedelta(days=1)).date(),
    )
    expected_dates: list[dt.date] = []
    d = first
    while d <= last:
        expected_dates.append(d)
        d += dt.timedelta(days=1)

    incomplete_logs = [
        {"date": day.isoformat(), "server": server}
        for day in expected_dates
        for server in ("MT4", "MT5", "MT4_Live2")
        if (day.strftime("%Y%m%d"), server) not in parse_days
    ]
    unreconciled_dates = [
        day.isoformat() for day in expected_dates if day.isoformat() not in present_dates
    ]

    no_ip_trades = sum(r["trades"] for r in cause_rows)
    no_ip_profit = sum(r["profit_usd"] or 0.0 for r in cause_rows)
    return {
        "date_from": date_from,
        "date_to": date_to,
        "data_from": GO_LIVE_DATE,
        "total_trades": total_row["trades"] or 0,
        "total_profit_usd": round(total_row["profit_usd"] or 0.0, 2),
        "with_ip_trades": with_ip_row["trades"] or 0,
        "with_ip_profit_usd": round(with_ip_row["profit_usd"] or 0.0, 2),
        "no_ip_trades": no_ip_trades,
        "no_ip_profit_usd": round(no_ip_profit, 2),
        "no_ip_by_cause": [
            {"cause": r["no_ip_cause"], "trades": r["trades"],
             "profit_usd": round(r["profit_usd"] or 0.0, 2)}
            for r in sorted(cause_rows, key=lambda x: -(x["trades"] or 0))
        ],
        "incomplete_logs": incomplete_logs,
        "unreconciled_dates": unreconciled_dates,
    }


# ---------------------------------------------------------------------------
# Redis cache wrappers (fail-open; key = the full parameter set)
# ---------------------------------------------------------------------------


def _get_redis():
    """Module-level Redis client; fail-open -> None (caller hits SQLite)."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    with _redis_lock:
        if _redis_client is None:
            try:
                import redis

                client = redis.Redis(
                    host=_REDIS_HOST,
                    port=_REDIS_PORT,
                    decode_responses=True,
                    socket_connect_timeout=0.5,
                    socket_timeout=0.5,
                )
                client.ping()
                _redis_client = client
            except Exception as exc:
                logger.warning("trade-profit Redis unavailable, fail-open: %s", exc)
                return None
        return _redis_client


def _cache_key(kind: str, params: dict) -> str:
    raw = json.dumps(params, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"{CACHE_PREFIX}:{kind}:{digest}"


def _cache_get(key: str) -> Optional[Any]:
    client = _get_redis()
    if client is None:
        return None
    try:
        cached = client.get(key)
        return json.loads(cached) if cached is not None else None
    except Exception as exc:
        logger.warning("trade-profit cache read failed: %s", exc)
        return None


def _cache_set(key: str, payload: Any) -> None:
    client = _get_redis()
    if client is None:
        return
    try:
        client.set(key, json.dumps(payload), ex=CACHE_TTL_S)
    except Exception as exc:
        logger.warning("trade-profit cache write failed: %s", exc)


def get_groups(p: GroupParams) -> tuple[list[dict], bool]:
    """compute_groups with Redis in front. Returns (groups, from_cache)."""
    key = _cache_key("groups", p._asdict())
    cached = _cache_get(key)
    if cached is not None:
        return cached, True
    groups = compute_groups(p)
    _cache_set(key, groups)
    return groups, False


def get_ip_ranking(date_from: str, date_to: str, public_ip_clients: int = 10) -> tuple[list[dict], bool]:
    key = _cache_key(
        "ips", {"from": date_from, "to": date_to, "public_ip_clients": public_ip_clients}
    )
    cached = _cache_get(key)
    if cached is not None:
        return cached, True
    rows = compute_ip_ranking(date_from, date_to, public_ip_clients)
    _cache_set(key, rows)
    return rows, False


def get_group_detail(group_id: str, p: GroupParams) -> Optional[dict]:
    """One group, enriched with per-IP window stats + cache-only geo.

    group_id is a hash of (window, thresholds, account list), so the detail
    can only be resolved under the SAME parameters that produced the list —
    which is exactly how the frontend calls it (it echoes the list's params).
    """
    groups, from_cache = get_groups(p)
    group = next((g for g in groups if g["group_id"] == group_id), None)
    if group is None:
        return None

    ips = [m["ip"] for m in group["member_ips"]]
    window_stats: dict[str, dict] = {}
    if ips:
        with login_ip_orders_db.get_connection() as conn:
            for part in _chunks(ips, _CHUNK):
                ph = ",".join("?" * len(part))
                for r in conn.execute(
                    f"""
                    SELECT open_ip AS ip,
                           COUNT(DISTINCT user_id) AS window_clients,
                           COUNT(DISTINCT close_date) AS window_active_days
                    FROM trade_ip_pnl
                    WHERE close_date BETWEEN ? AND ? AND open_ip IN ({ph})
                    GROUP BY open_ip
                    """,
                    (p.date_from, p.date_to, *part),
                ):
                    window_stats[r["ip"]] = dict(r)

    # Cache-only geo: never bill MaxMind from a page view.
    countries: dict[str, str] = {}
    try:
        from ..core import login_ip_db

        countries = login_ip_db.get_cached_countries(ips)
    except Exception as exc:  # noqa: BLE001 — geo is decoration
        logger.warning("trade-profit geo cache unavailable: %s", exc)

    member_ips = []
    for m in group["member_ips"]:
        ws = window_stats.get(m["ip"], {})
        member_ips.append({
            **m,
            "country": countries.get(m["ip"]),
            "window_clients": ws.get("window_clients"),
            "window_active_days": ws.get("window_active_days"),
        })

    return {
        "group": {k: v for k, v in group.items() if k != "member_ips"},
        "member_ips": member_ips,
        "params": {
            "from": p.date_from,
            "to": p.date_to,
            "min_clients": p.min_clients,
            "public_ip_clients": p.public_ip_clients,
            "include_same_client": p.include_same_client,
            "ip_min_clients": p.ip_min_clients,
        },
        "statistics": {"from_cache": from_cache},
    }
