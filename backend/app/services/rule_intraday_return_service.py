"""
Intraday Return detection (即日高收益, rule_id 131-140, OPT-0062).

Flags accounts whose SAME-DAY return on initial equity crosses a threshold
(100% / 300% tiers by default) — the "small deposit + lock + ladder" pattern
the risk desk reported (VN clients turning $50 into $500 inside one MT day).

═══ FORMULA v3 (SSOT: docs/optimization/items/OPT-0062-intraday-return-rule.md) ═══

Per MT trading day D, per candidate account:

    prev_eq / prev_bal / prev_credit = yesterday's (D-1, or the latest earlier
                                       daily row over a weekend) end-of-day values
    carried_float0 = prev_eq − prev_bal − prev_credit   (floating of overnight
                                                          positions at yesterday's EOD)
    dep_in         = real deposits today   (Action=2 / CMD=6, Profit>0,
                                            Comment not in the adjustment denylist)
    cred_in        = credit / bonus in today (Action=3 / CMD=7, Profit>0)
    initial_equity = prev_eq + dep_in + cred_in   (include_deposits_in_base=false → prev_eq)

    Positions split by open time < today's day start:
      same_day_pnl = positions opened TODAY: realized (PROFIT+SWAPS+COMMISSION)
                     + current floating (PROFIT+SWAPS) — counted in full
      carried_now  = OVERNIGHT positions: realized today + current floating
      carried_gain = max(carried_now, 0) − max(carried_float0, 0)
                     (only the part that is NEW inside the profit zone today;
                      a loss recovering toward zero counts 0; yesterday's
                      floating profit is already in initial_equity so it is
                      not counted twice)

    intraday_profit = same_day_pnl + carried_gain
    return_pct      = 100 × intraday_profit / initial_equity
    net_7d          = realized P&L over the last net_window_days days
                      + current floating of ALL open positions

    trigger = initial_equity ≥ min_initial_equity_usd
            ∧ intraday_profit ≥ min_profit_usd
            ∧ return_pct ≥ min_return_pct
            ∧ net_7d ≥ min_net_7d_usd
            ∧ [optional] lock_pct ≥ min_lock_pct ∧ median_hold_min ≤ max_median_hold_min

Why v3 (not "equity delta" or "closed + all floating"): the equity-delta
numerator counted an overnight loss recovering (−62k → −40k) as +22k profit;
"closed + all floating" re-fires a profitable overnight position every day;
"same-day positions only" misses "$50 opened yesterday, +$500 floating today".
The account-level clip at zero handles all three without per-position
day-start prices (which do not exist in the MT tables).

CEN: ratios are currency-immune; every USD threshold and lots are ÷100 using
the account currency from fxbackoffice.mt4_users (resolved BEFORE detection).
initial_equity ≤ 0 is skipped outright (no ÷0).

Trading day: derived from `mt5_daily` MAX(Datetime) + 1s (follows the server's
real rollover, including DST) and shared by the MT4 servers. Never CURDATE().

Dedup key: (rule_id, server, login, trading_day). The scheduler re-seeds the
already-alerted map from SQLite EVERY tick; a re-hit updates the existing
detail row (UPSERT, peak_return_pct = max) instead of inserting a new alert.
A higher tier (larger min_return_pct) suppresses the lower tiers for the same
account on the same day.
"""

from __future__ import annotations

import logging
import statistics
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import pymysql

from ..core.config import Settings
from ..core.sql_helpers import (
    FILETIME_EPOCH_OFFSET,
    FILETIME_TICKS_PER_SEC,
    SID_MAP,
    demo_test_filter_sql,
)
from .account_enrichment import (
    get_account_info_map,
    get_net_deposit_hist_map,
    lots_divisor,
)

logger = logging.getLogger(__name__)


INTRADAY_RETURN_RULE_ID_BASE = 131
INTRADAY_RETURN_RULE_ID_MAX = 140
MAX_INTRADAY_RETURN_RULES = 10

# Positive balance operations whose Comment starts with one of these are
# operations-side adjustments (bulk "Balance Adjustment Zero" up to $779k a
# row, "Adjustment - …", "Initial balance" on account creation) — never
# client money, so they must not enter the deposit base. Everything else
# positive on Action=2 / CMD=6 (DEPOSIT, D-…, IT-D, IB Wallet Transfer,
# XTHB-Deposit-, OnefinVA#) is a real deposit, internal transfers included.
NON_DEPOSIT_COMMENT_PREFIXES: Tuple[str, ...] = (
    "Balance Adjustment",
    "Adjustment",
    "Initial",
)

# Per-statement ceiling on the shared read replica (db-timeout-guard). The
# heaviest statement here is the same-day deals pull (~30k rows); 60s is
# several times the worst observed tick, and the tick is skipped, not
# queued, when it fires.
_STATEMENT_TIMEOUT_MS = 60_000

# A withdrawal larger than half of the same day's deposits is the evasion
# path the cold review flagged (deposit → profit → pull most of it out before
# the tick): flag it on the detail row so the digest shows it.
_WITHDRAW_FLAG_RATIO = 0.5

# Weekend / holiday gap: how many earlier day-ends to try when yesterday has
# no daily row (Sat + Sun + one holiday).
_PREV_DAY_LOOKBACK_DAYS = 4

# MT server wall clock. NOT the fixed +03:00 the rest of risk-monitor uses:
# the servers follow EU DST (GMT+3 in summer, GMT+2 in winter — probed
# 2026-09-18: a January mt5_deals row has Timestamp − UNIX_TIMESTAMP(Time as
# +03:00) = +3600, July/September rows 0). This rule is the only one that
# converts a wall-clock DAY boundary into MT5 FILETIME instants, so a fixed
# offset would leak the last winter hour of D-1 into D (those closes are
# already inside prev_eq → double counted). Europe/Athens matches the observed
# switch dates; mt5_daily.Datetime is "wall-time day end taken as UTC" and is
# unaffected.
MT_SERVER_TZ = ZoneInfo("Europe/Athens")

# Sanity bound on the derived day start: mt5_daily is written every day
# (weekends included, probed 2026-09-18), so "today" is never more than ~24h
# old. A stale MAX(Datetime) means the daily job / replica is behind and
# every account would be measured across two days against a D-2 baseline —
# skip the tick loudly instead.
_MAX_DAY_AGE = timedelta(hours=26)

# A candidate whose previous-day row is missing gets (0, 0, 0) — correct for
# an account created today, wrong for a funded account whose daily row is
# late. Past this share of misses on one server the data is not trustworthy
# (MT4 rollover ≠ derived boundary, replica lag) → that server is skipped.
_MAX_PREV_DAY_MISS_RATIO = 0.20
# Inside this window after the day start, a fallback to an OLDER daily row
# (not D-1) is not cached: the daily batch may still be landing.
_PREV_DAY_SETTLE = timedelta(minutes=60)

_SERVERS: List[Dict[str, str]] = [
    {"key": "mt4_live", "type": "mt4", "db": "mt4_live", "label": "MT4_Live"},
    {"key": "mt4_live2", "type": "mt4", "db": "mt4_live2", "label": "MT4_Live2"},
    {"key": "mt5", "type": "mt5", "db": "mt5_live", "label": "MT5"},
]


# ── helpers ─────────────────────────────────────────────────────────────────

def is_real_flow(comment: Any) -> bool:
    """True when a balance-operation comment marks client money, not an
    operations adjustment (see NON_DEPOSIT_COMMENT_PREFIXES)."""
    c = str(comment or "").strip()
    return not any(c.startswith(p) for p in NON_DEPOSIT_COMMENT_PREFIXES)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _local_to_utc(local_naive: datetime) -> datetime:
    """MT wall-clock (naive) → aware UTC, DST-aware (see MT_SERVER_TZ)."""
    return local_naive.replace(tzinfo=MT_SERVER_TZ).astimezone(timezone.utc)


def _utc_to_local(utc_dt: datetime) -> datetime:
    """Aware UTC → MT wall-clock naive."""
    return utc_dt.astimezone(MT_SERVER_TZ).replace(tzinfo=None)


def _local_to_utc_iso(local_naive: Optional[datetime]) -> Optional[str]:
    """MT wall-clock naive datetime → UTC ISO 'Z' string (derived first/last
    open of the day; row-level timestamps never go through here)."""
    if local_naive is None:
        return None
    return _iso(_local_to_utc(local_naive))


def _filetime(local_naive: datetime) -> int:
    """MT wall-clock naive datetime → Windows FILETIME (true UTC instant, as
    mt5_deals.Timestamp stores it)."""
    utc = _local_to_utc(local_naive)
    return (int(utc.timestamp()) + FILETIME_EPOCH_OFFSET) * FILETIME_TICKS_PER_SEC


def _mt5_daily_datetime(local_day_end: datetime) -> int:
    """mt5_daily.Datetime = server-local day-end wall time stored as if UTC."""
    return int(local_day_end.replace(tzinfo=timezone.utc).timestamp())


def _get_connection(settings: Settings):
    conn = pymysql.connect(
        host=settings.DB_HOST,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        port=int(settings.DB_PORT),
        charset=settings.DB_CHARSET,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=120,
        autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute(f"SET SESSION MAX_EXECUTION_TIME={_STATEMENT_TIMEOUT_MS}")
    return conn


def _format_rule_label(rule_idx: int, rule: Dict[str, Any]) -> str:
    name = str(rule.get("name") or "").strip()
    base = f"Rule {rule_idx + 1}"
    return f"{base} — {name}" if name else base


def normalize_rules(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Coerce stored rule dicts into the evaluation shape; disabled rules are
    dropped; rule ids are forced into the band (OPT-0008-class guard: a
    SQLite PK leaking in as `id` must never become the alert's rule_id)."""
    out: List[Dict[str, Any]] = []
    for i, r in enumerate(rules[:MAX_INTRADAY_RETURN_RULES]):
        if not r.get("enabled", True):
            continue
        raw_id = r.get("id")
        try:
            rid = int(raw_id) if raw_id is not None else INTRADAY_RETURN_RULE_ID_BASE + i
        except (TypeError, ValueError):
            rid = INTRADAY_RETURN_RULE_ID_BASE + i
        if rid < INTRADAY_RETURN_RULE_ID_BASE or rid > INTRADAY_RETURN_RULE_ID_MAX:
            rid = INTRADAY_RETURN_RULE_ID_BASE + i

        def _opt_float(key: str) -> Optional[float]:
            v = r.get(key)
            if v is None or v == "":
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        out.append({
            "id": rid,
            "idx": i,
            "name": str(r.get("name") or ""),
            "label": _format_rule_label(i, r),
            "min_return_pct": float(r.get("min_return_pct", 100.0) or 0.0),
            "min_initial_equity_usd": float(r.get("min_initial_equity_usd", 50.0) or 0.0),
            "min_profit_usd": float(r.get("min_profit_usd", 30.0) or 0.0),
            "min_net_7d_usd": float(r.get("min_net_7d_usd", 0.0) or 0.0),
            "net_window_days": max(1, int(r.get("net_window_days", 7) or 7)),
            "include_deposits_in_base": bool(r.get("include_deposits_in_base", True)),
            "min_lock_pct": _opt_float("min_lock_pct"),
            "max_median_hold_min": _opt_float("max_median_hold_min"),
            "lock_ratio_min": float(r.get("lock_ratio_min", 0.5) or 0.5),
        })
    return out


# ── pure formula core (unit-tested) ─────────────────────────────────────────

def compute_account_metrics(
    raw: Dict[str, Any],
    *,
    divisor: float = 1.0,
    include_deposits_in_base: bool = True,
) -> Dict[str, Any]:
    """Formula v3 on one account's raw (broker-unit) inputs → USD metrics.

    ``raw`` keys (missing → 0): prev_eq, prev_bal, prev_credit, dep_in,
    cred_in, withdrawals_out, adj_excluded, same_day_pnl, carried_now,
    realized_7d, floating_all_now, balance_now, credit_now.

    ``divisor`` is 100 for CEN accounts (money in cents), 1 otherwise.
    Returns every detail-table money column plus ``return_pct`` (None when
    initial_equity ≤ 0 — the caller skips those accounts).
    """
    def g(key: str) -> float:
        v = raw.get(key)
        return float(v or 0.0) / divisor

    prev_eq = g("prev_eq")
    prev_bal = g("prev_bal")
    prev_credit = g("prev_credit")
    dep_in = g("dep_in")
    cred_in = g("cred_in")
    withdrawals_out = g("withdrawals_out")
    adj_excluded = g("adj_excluded")
    same_day_pnl = g("same_day_pnl")
    carried_now = g("carried_now")
    realized_7d = g("realized_7d")
    floating_all_now = g("floating_all_now")
    balance_now = g("balance_now")
    credit_now = g("credit_now")

    carried_float0 = prev_eq - prev_bal - prev_credit
    initial_equity = prev_eq + ((dep_in + cred_in) if include_deposits_in_base else 0.0)
    carried_gain = max(carried_now, 0.0) - max(carried_float0, 0.0)
    intraday_profit = same_day_pnl + carried_gain
    equity_now = balance_now + credit_now + floating_all_now
    net_7d = realized_7d + floating_all_now
    return_pct: Optional[float] = (
        100.0 * intraday_profit / initial_equity if initial_equity > 0 else None
    )
    flag_withdraw = withdrawals_out > 0 and withdrawals_out > dep_in * _WITHDRAW_FLAG_RATIO

    r2 = lambda v: round(v, 2)
    return {
        "prev_day_equity": r2(prev_eq),
        "deposits_in": r2(dep_in),
        "credit_in": r2(cred_in),
        "withdrawals_out": r2(withdrawals_out),
        "adj_excluded": r2(adj_excluded),
        "initial_equity": r2(initial_equity),
        "equity_now": r2(equity_now),
        "same_day_pnl": r2(same_day_pnl),
        "carried_float0": r2(carried_float0),
        "carried_now": r2(carried_now),
        "carried_gain": r2(carried_gain),
        "intraday_profit": r2(intraday_profit),
        "return_pct": r2(return_pct) if return_pct is not None else None,
        "net_7d": r2(net_7d),
        "realized_7d": r2(realized_7d),
        "floating_all_now": r2(floating_all_now),
        "flag_withdraw_gt_half_deposit": 1 if flag_withdraw else 0,
        "balance_now": r2(balance_now),
    }


def compute_behavior_features(
    positions: List[Dict[str, Any]],
    *,
    day_start: datetime,
    now_local: datetime,
    lock_ratio_min: float = 0.5,
    divisor: float = 1.0,
) -> Dict[str, Any]:
    """Same-day behaviour features from an account's position list.

    Each position dict: ``symbol``, ``direction`` ('B'|'S'), ``lots`` (broker
    lots), ``open_time`` (broker-local naive datetime), ``close_time``
    (None while open). Positions that were open at any moment of today are
    expected (opened today, or overnight and closed today / still open).

    - trades_today / lots_today / first_open / last_open: positions opened today
    - median_hold_sec: positions opened AND closed today
    - lock_pct: share of today's "active" time (any position open) during which
      the account held BOTH sides of one symbol with min/max ≥ lock_ratio_min
    - top_symbol: most common symbol among today's opens (fallback: all)
    """
    opened_today = [p for p in positions if p["open_time"] >= day_start]
    holds = [
        (p["close_time"] - p["open_time"]).total_seconds()
        for p in opened_today
        if p.get("close_time") is not None
    ]
    events: List[Tuple[datetime, int, str, str, float]] = []
    for p in positions:
        o = max(p["open_time"], day_start)
        c = p.get("close_time") or now_local
        c = min(c, now_local)
        if c <= o:
            continue
        events.append((o, 1, p["symbol"], p["direction"], float(p["lots"])))
        events.append((c, -1, p["symbol"], p["direction"], float(p["lots"])))
    events.sort(key=lambda e: (e[0], e[1]))
    book: Dict[str, Dict[str, float]] = defaultdict(lambda: {"B": 0.0, "S": 0.0})
    locked = active = 0.0
    last: Optional[datetime] = None
    for t, delta, sym, direction, lots in events:
        if last is not None:
            gap = (t - last).total_seconds()
            if any(v["B"] > 1e-9 or v["S"] > 1e-9 for v in book.values()):
                active += gap
            if any(
                v["B"] > 1e-9 and v["S"] > 1e-9
                and min(v["B"], v["S"]) / max(v["B"], v["S"]) >= lock_ratio_min
                for v in book.values()
            ):
                locked += gap
        book[sym][direction] = max(0.0, book[sym][direction] + delta * lots)
        last = t
    syms = Counter(p["symbol"] for p in (opened_today or positions))
    return {
        "trades_today": len(opened_today),
        "lots_today": round(sum(float(p["lots"]) for p in opened_today) / divisor, 2),
        "median_hold_sec": int(round(statistics.median(holds))) if holds else None,
        "lock_pct": round(100.0 * locked / active, 1) if active > 0 else 0.0,
        "top_symbol": syms.most_common(1)[0][0] if syms else None,
        "first_open": min((p["open_time"] for p in opened_today), default=None),
        "last_open": max((p["open_time"] for p in opened_today), default=None),
    }


def rule_intraday_return_detect(
    accounts: List[Dict[str, Any]],
    rules: List[Dict[str, Any]],
    *,
    alerted: Optional[Dict[Tuple[str, int], Dict[int, int]]] = None,
    scanned_at: str,
    trading_day: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Evaluate normalized rules against prepared accounts.

    Each account dict carries: server, login, currency, group, zipcode,
    ``raw`` (broker-unit inputs for compute_account_metrics — realized_7d
    keyed per window in ``raw_realized_7d_by_window``), and ``positions``
    (for compute_behavior_features), plus day_start / now_local.

    ``alerted`` = {(server, login): {rule_id: alert_events.id}} already
    persisted for this trading day (re-seeded from SQLite every tick).

    Returns (new_alerts, updates). ``updates`` carry ``alert_id`` + the
    refreshed detail columns for rows that already exist. Tier suppression:
    among the rules an account matches, only the one with the highest
    min_return_pct is emitted; every already-alerted row for that account
    (any tier) is refreshed.
    """
    alerted = alerted or {}
    new_alerts: List[Dict[str, Any]] = []
    updates: List[Dict[str, Any]] = []
    if not rules:
        return new_alerts, updates

    for acct in accounts:
        server = str(acct["server"])
        login = int(acct["login"])
        divisor = lots_divisor(acct.get("currency"))
        raw = dict(acct.get("raw") or {})
        realized_by_window: Dict[int, float] = acct.get("raw_realized_7d_by_window") or {}
        key = (server, login)
        already = alerted.get(key, {})

        matched: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
        # Metrics + features per rule for EVERY rule (not just matches): rows
        # already on file for this account are refreshed with their own rule's
        # numbers even when the account no longer matches, so the grid never
        # freezes at the last matching value.
        per_rule: Dict[int, Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = {}
        feature_cache: Dict[float, Dict[str, Any]] = {}
        for rule in rules:
            raw["realized_7d"] = realized_by_window.get(
                rule["net_window_days"], raw.get("realized_7d", 0.0)
            )
            m = compute_account_metrics(
                raw, divisor=divisor,
                include_deposits_in_base=rule["include_deposits_in_base"],
            )
            ratio = rule["lock_ratio_min"]
            if ratio not in feature_cache:
                feature_cache[ratio] = compute_behavior_features(
                    acct.get("positions") or [],
                    day_start=acct["day_start"], now_local=acct["now_local"],
                    lock_ratio_min=ratio, divisor=divisor,
                )
            feats = feature_cache[ratio]
            per_rule[rule["id"]] = (rule, m, feats)
            if m["return_pct"] is None:
                continue  # initial_equity ≤ 0 → never evaluated (no ÷0)
            if m["initial_equity"] < rule["min_initial_equity_usd"]:
                continue
            if m["intraday_profit"] < rule["min_profit_usd"]:
                continue
            if m["return_pct"] < rule["min_return_pct"]:
                continue
            if m["net_7d"] < rule["min_net_7d_usd"]:
                continue
            if rule["min_lock_pct"] is not None and (feats["lock_pct"] or 0.0) < rule["min_lock_pct"]:
                continue
            if rule["max_median_hold_min"] is not None:
                if feats["median_hold_sec"] is None:
                    continue
                if feats["median_hold_sec"] / 60.0 > rule["max_median_hold_min"]:
                    continue
            matched.append((rule, m, feats))

        if not matched and not already:
            continue
        # Highest tier wins; ties resolve to the earlier rule (stable sort).
        matched.sort(key=lambda t: -t[0]["min_return_pct"])
        top_rule, top_m, top_feats = matched[0] if matched else (None, None, None)

        def _detail(m: Dict[str, Any], feats: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "trading_day": trading_day,
                **{k: v for k, v in m.items() if k != "balance_now"},
                "trades_today": feats["trades_today"],
                "lots_today": feats["lots_today"],
                "median_hold_sec": feats["median_hold_sec"],
                "lock_pct": feats["lock_pct"],
                "top_symbol": feats["top_symbol"],
                "order_count": feats["trades_today"],
                "total_lots": feats["lots_today"],
                "symbol": feats["top_symbol"] or "",
                "first_open": _local_to_utc_iso(feats["first_open"]),
                "last_open": _local_to_utc_iso(feats["last_open"]),
                "equity": m["equity_now"],
                "balance": m["balance_now"],
            }

        # Tier decay: an account already on file at a HIGHER (or equal) tier
        # today must not spawn a lower-tier row on the way back down (350% →
        # 150% would otherwise mail "≥100%" while the account is giving profit
        # back). Only a genuinely higher tier than anything on file is new.
        highest_on_file = max(
            (per_rule[rid][0]["min_return_pct"] for rid in already if rid in per_rule),
            default=None,
        )
        is_new_tier = (
            top_rule is not None
            and top_rule["id"] not in already
            and (highest_on_file is None or top_rule["min_return_pct"] > highest_on_file)
        )
        if is_new_tier:
            alert = {
                "rule_id": top_rule["id"],
                "rule_label": top_rule["label"],
                "server": server,
                "login": login,
                "orders": [],
                "equity_per_lot": None,
                "total_open_lots": None,
                "leverage": None,
                "group": acct.get("group"),
                "currency": acct.get("currency"),
                "zipcode": acct.get("zipcode"),
                "scanned_at": scanned_at,
                "peak_return_pct": top_m["return_pct"],
                **_detail(top_m, top_feats),
            }
            new_alerts.append(alert)
        # Refresh every row already on file for this account today (any tier,
        # matching or not) with that rule's own metrics: the row shows the
        # current state, peak_return_pct keeps the high-water mark in SQLite.
        for rid, alert_id in already.items():
            src = per_rule.get(rid)
            if src is None:
                # Rule was deleted/disabled mid-day — refresh with the top
                # tier's numbers if any, else leave the row as is.
                if top_rule is None:
                    continue
                src = (top_rule, top_m, top_feats)
            updates.append({"alert_id": int(alert_id), **_detail(src[1], src[2])})
    return new_alerts, updates


# ── SQL collectors ──────────────────────────────────────────────────────────

def _query_trading_day_start(conn) -> datetime:
    """Today's MT day start (broker-local naive) = latest mt5_daily day-end + 1s.

    Follows the server's real rollover (DST included) instead of CURDATE().
    """
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(Datetime) AS mx FROM mt5_live.mt5_daily")
        row = cur.fetchone()
    mx = row and row.get("mx")
    if not mx:
        raise RuntimeError("mt5_daily is empty — cannot derive the trading day")
    return datetime.fromtimestamp(int(mx), tz=timezone.utc).replace(tzinfo=None) + timedelta(seconds=1)


def _query_mt5_today_deals(conn, *, day_start: datetime) -> List[Dict[str, Any]]:
    """All of today's MT5 deals (trades + balance/credit ops) for non-demo
    logins. Range on Timestamp (FILETIME, indexed) — never on Time."""
    sql = f"""
        SELECT d.Login AS login, d.Deal AS deal, d.PositionID AS position_id,
               d.Action AS action, d.Entry AS entry, d.Symbol AS symbol,
               d.Volume / 10000 AS lots,
               d.Profit AS profit, d.Storage AS storage, d.Commission AS commission,
               d.Comment AS comment, d.Time AS time_local
        FROM mt5_live.mt5_deals d
        INNER JOIN mt5_live.mt5_users u ON u.Login = d.Login
        WHERE d.Timestamp >= %s
          AND d.Action IN (0, 1, 2, 3)
          {demo_test_filter_sql('u.`Group`', 'u.Name', login_col='d.Login', server_label='MT5')}
    """
    with conn.cursor() as cur:
        cur.execute(sql, (_filetime(day_start),))
        return list(cur.fetchall())


def _query_mt5_open_positions(conn) -> List[Dict[str, Any]]:
    sql = f"""
        SELECT p.Login AS login, p.Position AS position_id, p.Symbol AS symbol,
               p.Action AS action, p.Volume / 10000 AS lots,
               p.Profit AS profit, p.Storage AS storage,
               p.TimeCreate AS open_time_local
        FROM mt5_live.mt5_positions p
        INNER JOIN mt5_live.mt5_users u ON u.Login = p.Login
        WHERE 1 = 1
          {demo_test_filter_sql('u.`Group`', 'u.Name', login_col='p.Login', server_label='MT5')}
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def _query_mt5_prev_day(
    conn, *, day_start: datetime, logins: Iterable[int]
) -> Dict[int, Tuple[float, float, float]]:
    """{login: (equity, balance, credit)} from the latest mt5_daily row among
    the previous _PREV_DAY_LOOKBACK_DAYS day-ends (weekend-safe). PK-prefixed
    (Datetime IN (...) AND Login IN (...)) — a bare Login lookup times out."""
    login_list = [int(l) for l in logins]
    if not login_list:
        return {}
    stamps = [
        _mt5_daily_datetime(day_start - timedelta(days=k, seconds=1))
        for k in range(1, _PREV_DAY_LOOKBACK_DAYS + 1)
    ]
    out: Dict[int, Tuple[int, float, float, float]] = {}
    for chunk in _chunks(login_list, 800):
        sql = f"""
            SELECT Datetime AS dt, Login AS login, ProfitEquity AS eq,
                   Balance AS bal, Credit AS credit
            FROM mt5_live.mt5_daily
            WHERE Datetime IN ({','.join(['%s'] * len(stamps))})
              AND Login IN ({','.join(['%s'] * len(chunk))})
        """
        with conn.cursor() as cur:
            cur.execute(sql, (*stamps, *chunk))
            for r in cur.fetchall():
                login = int(r["login"])
                prev = out.get(login)
                if prev is None or int(r["dt"]) > prev[0]:
                    out[login] = (
                        int(r["dt"]), float(r["eq"] or 0.0),
                        float(r["bal"] or 0.0), float(r["credit"] or 0.0),
                    )
    return {k: (v[1], v[2], v[3], datetime.fromtimestamp(v[0], tz=timezone.utc).replace(tzinfo=None)) for k, v in out.items()}


def _query_mt5_realized_prior(
    conn, *, day_start: datetime, window_days: int, logins: Iterable[int]
) -> Dict[int, float]:
    """Realized P&L (Profit+Storage+Commission) of closing deals inside
    [day_start − (window−1) days, day_start) per login."""
    login_list = [int(l) for l in logins]
    if not login_list or window_days <= 1:
        return {}
    lo = _filetime(day_start - timedelta(days=window_days - 1))
    hi = _filetime(day_start)
    out: Dict[int, float] = {}
    for chunk in _chunks(login_list, 800):
        sql = f"""
            SELECT Login AS login,
                   SUM(COALESCE(Profit, 0) + COALESCE(Storage, 0) + COALESCE(Commission, 0)) AS pnl
            FROM mt5_live.mt5_deals
            WHERE Timestamp >= %s AND Timestamp < %s
              AND Action IN (0, 1) AND Entry IN (1, 2, 3)
              AND Login IN ({','.join(['%s'] * len(chunk))})
            GROUP BY Login
        """
        with conn.cursor() as cur:
            cur.execute(sql, (lo, hi, *chunk))
            for r in cur.fetchall():
                out[int(r["login"])] = float(r["pnl"] or 0.0)
    return out


def _query_mt5_users_now(conn, *, logins: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    login_list = [int(l) for l in logins]
    if not login_list:
        return {}
    out: Dict[int, Dict[str, Any]] = {}
    for chunk in _chunks(login_list, 800):
        sql = f"""
            SELECT Login AS login, Balance AS balance, Credit AS credit,
                   EquityPrevDay AS eq_prev, BalancePrevDay AS bal_prev
            FROM mt5_live.mt5_users
            WHERE Login IN ({','.join(['%s'] * len(chunk))})
        """
        with conn.cursor() as cur:
            cur.execute(sql, tuple(chunk))
            for r in cur.fetchall():
                out[int(r["login"])] = {
                    "balance": float(r["balance"] or 0.0),
                    "credit": float(r["credit"] or 0.0),
                    "eq_prev": float(r["eq_prev"] or 0.0),
                    "bal_prev": float(r["bal_prev"] or 0.0),
                }
    return out


def _query_mt4_today_rows(
    conn, *, db_name: str, server_label: str, day_start: datetime
) -> List[Dict[str, Any]]:
    """Today's closed trades + balance/credit ops (CLOSE_TIME ≥ day start)."""
    sql = f"""
        SELECT t.LOGIN AS login, t.TICKET AS ticket, t.CMD AS cmd, t.SYMBOL AS symbol,
               t.VOLUME / 100 AS lots, t.OPEN_TIME AS open_time_local,
               t.CLOSE_TIME AS close_time_local,
               t.PROFIT AS profit, t.SWAPS AS storage, t.COMMISSION AS commission,
               t.COMMENT AS comment
        FROM {db_name}.mt4_trades t
        INNER JOIN {db_name}.mt4_users u ON u.LOGIN = t.LOGIN
        WHERE t.CLOSE_TIME >= %s
          AND t.CMD IN (0, 1, 6, 7)
          AND t.LOGIN NOT LIKE '7%%'
          {demo_test_filter_sql('u.`GROUP`', 'u.NAME', login_col='t.LOGIN', server_label=server_label)}
    """
    with conn.cursor() as cur:
        cur.execute(sql, (day_start,))
        return list(cur.fetchall())


def _query_mt4_open_positions(
    conn, *, db_name: str, server_label: str
) -> List[Dict[str, Any]]:
    sql = f"""
        SELECT t.LOGIN AS login, t.TICKET AS ticket, t.CMD AS cmd, t.SYMBOL AS symbol,
               t.VOLUME / 100 AS lots, t.OPEN_TIME AS open_time_local,
               t.PROFIT AS profit, t.SWAPS AS storage
        FROM {db_name}.mt4_trades t
        INNER JOIN {db_name}.mt4_users u ON u.LOGIN = t.LOGIN
        WHERE t.CLOSE_TIME = '1970-01-01 00:00:00'
          AND t.CMD IN (0, 1)
          AND t.LOGIN NOT LIKE '7%%'
          {demo_test_filter_sql('u.`GROUP`', 'u.NAME', login_col='t.LOGIN', server_label=server_label)}
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def _query_mt4_prev_day(
    conn, *, db_name: str, day_start: datetime, logins: Iterable[int]
) -> Dict[int, Tuple[float, float, float]]:
    """{login: (equity, balance, credit)} — PK (LOGIN, TIME) point lookups
    over the previous day-ends only. mt4_daily must NEVER be range-scanned
    on TIME (full table > 300s, killed)."""
    login_list = [int(l) for l in logins]
    if not login_list:
        return {}
    times = [
        day_start - timedelta(days=k, seconds=1)
        for k in range(1, _PREV_DAY_LOOKBACK_DAYS + 1)
    ]
    out: Dict[int, Tuple[datetime, float, float, float]] = {}
    for chunk in _chunks(login_list, 800):
        sql = f"""
            SELECT LOGIN AS login, TIME AS t, EQUITY AS eq, BALANCE AS bal, CREDIT AS credit
            FROM {db_name}.mt4_daily
            WHERE LOGIN IN ({','.join(['%s'] * len(chunk))})
              AND TIME IN ({','.join(['%s'] * len(times))})
        """
        with conn.cursor() as cur:
            cur.execute(sql, (*chunk, *times))
            for r in cur.fetchall():
                login = int(r["login"])
                prev = out.get(login)
                if prev is None or r["t"] > prev[0]:
                    out[login] = (
                        r["t"], float(r["eq"] or 0.0),
                        float(r["bal"] or 0.0), float(r["credit"] or 0.0),
                    )
    return {k: (v[1], v[2], v[3], v[0]) for k, v in out.items()}


def _query_mt4_realized_prior(
    conn, *, db_name: str, day_start: datetime, window_days: int, logins: Iterable[int]
) -> Dict[int, float]:
    login_list = [int(l) for l in logins]
    if not login_list or window_days <= 1:
        return {}
    lo = day_start - timedelta(days=window_days - 1)
    out: Dict[int, float] = {}
    for chunk in _chunks(login_list, 800):
        sql = f"""
            SELECT LOGIN AS login,
                   SUM(COALESCE(PROFIT, 0) + COALESCE(SWAPS, 0) + COALESCE(COMMISSION, 0)) AS pnl
            FROM {db_name}.mt4_trades
            WHERE CLOSE_TIME >= %s AND CLOSE_TIME < %s
              AND CMD IN (0, 1)
              AND LOGIN IN ({','.join(['%s'] * len(chunk))})
            GROUP BY LOGIN
        """
        with conn.cursor() as cur:
            cur.execute(sql, (lo, day_start, *chunk))
            for r in cur.fetchall():
                out[int(r["login"])] = float(r["pnl"] or 0.0)
    return out


def _query_mt4_users_now(
    conn, *, db_name: str, logins: Iterable[int]
) -> Dict[int, Dict[str, Any]]:
    login_list = [int(l) for l in logins]
    if not login_list:
        return {}
    out: Dict[int, Dict[str, Any]] = {}
    for chunk in _chunks(login_list, 800):
        sql = f"""
            SELECT LOGIN AS login, BALANCE AS balance, CREDIT AS credit
            FROM {db_name}.mt4_users
            WHERE LOGIN IN ({','.join(['%s'] * len(chunk))})
        """
        with conn.cursor() as cur:
            cur.execute(sql, tuple(chunk))
            for r in cur.fetchall():
                out[int(r["login"])] = {
                    "balance": float(r["balance"] or 0.0),
                    "credit": float(r["credit"] or 0.0),
                }
    return out


def _chunks(seq: List[int], size: int) -> Iterable[List[int]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# ── per-server assembly ─────────────────────────────────────────────────────

def _empty_raw() -> Dict[str, float]:
    return {
        "prev_eq": 0.0, "prev_bal": 0.0, "prev_credit": 0.0,
        "dep_in": 0.0, "cred_in": 0.0, "withdrawals_out": 0.0, "adj_excluded": 0.0,
        "same_day_pnl": 0.0, "carried_now": 0.0,
        "realized_today": 0.0, "floating_all_now": 0.0,
        "balance_now": 0.0, "credit_now": 0.0,
    }


def _apply_flow(raw: Dict[str, float], *, is_balance_op: bool, profit: float, comment: Any) -> None:
    """Fold one balance (Action=2/CMD=6) or credit (Action=3/CMD=7) row."""
    if is_balance_op:
        if is_real_flow(comment):
            if profit > 0:
                raw["dep_in"] += profit
            else:
                raw["withdrawals_out"] += -profit
        else:
            raw["adj_excluded"] += profit
    else:
        if profit > 0:
            raw["cred_in"] += profit


def assemble_mt5_accounts(
    *,
    deals: List[Dict[str, Any]],
    positions: List[Dict[str, Any]],
    day_start: datetime,
    now_local: datetime,
) -> Dict[int, Dict[str, Any]]:
    """Today's MT5 deals + open positions → {login: {"raw", "positions"}}.

    Same-day vs overnight is decided by the position's opening deal
    (Entry 0/2 with a same-day stamp) for closed positions, and by
    TimeCreate for open ones.
    """
    accounts: Dict[int, Dict[str, Any]] = {}

    def slot(login: int) -> Dict[str, Any]:
        s = accounts.get(login)
        if s is None:
            s = {"raw": _empty_raw(), "positions": [], "_open_meta": {}}
            accounts[login] = s
        return s

    same_day_pos: Set[Tuple[int, int]] = set()
    for d in deals:
        if int(d["action"]) in (0, 1) and int(d["entry"]) in (0, 2):
            same_day_pos.add((int(d["login"]), int(d["position_id"])))
            slot(int(d["login"]))["_open_meta"][int(d["position_id"])] = {
                "symbol": d["symbol"],
                "direction": "B" if int(d["action"]) == 0 else "S",
                "lots": float(d["lots"] or 0.0),
                "open_time": d["time_local"],
            }

    closes_by_pos: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for d in deals:
        login = int(d["login"])
        action = int(d["action"])
        profit = float(d["profit"] or 0.0)
        if action in (2, 3):
            _apply_flow(slot(login)["raw"], is_balance_op=(action == 2),
                        profit=profit, comment=d.get("comment"))
            continue
        if int(d["entry"]) not in (1, 2, 3):
            continue
        pnl = profit + float(d["storage"] or 0.0) + float(d["commission"] or 0.0)
        raw = slot(login)["raw"]
        raw["realized_today"] += pnl
        pos_key = (login, int(d["position_id"]))
        if pos_key in same_day_pos:
            raw["same_day_pnl"] += pnl
        else:
            raw["carried_now"] += pnl
        meta = closes_by_pos.setdefault(pos_key, {
            "symbol": d["symbol"],
            "direction": "S" if action == 0 else "B",  # closing deal is opposite side
            "lots": 0.0, "close_time": d["time_local"],
        })
        meta["lots"] += float(d["lots"] or 0.0)
        if d["time_local"] > meta["close_time"]:
            meta["close_time"] = d["time_local"]

    open_pos_ids: Set[Tuple[int, int]] = set()
    for p in positions:
        login = int(p["login"])
        s = slot(login)
        floating = float(p["profit"] or 0.0) + float(p["storage"] or 0.0)
        s["raw"]["floating_all_now"] += floating
        open_time = p["open_time_local"]
        if open_time >= day_start:
            s["raw"]["same_day_pnl"] += floating
        else:
            s["raw"]["carried_now"] += floating
        open_pos_ids.add((login, int(p["position_id"])))
        s["positions"].append({
            "symbol": p["symbol"],
            "direction": "B" if int(p["action"]) == 0 else "S",
            "lots": float(p["lots"] or 0.0),
            "open_time": open_time,
            "close_time": None,
        })

    # Closed-today positions: opened today (we know the open leg) or carried
    # (open leg is older — approximate open_time as the day start for the
    # lock timeline; it only needs to be "before today").
    for (login, pid), meta in closes_by_pos.items():
        if (login, pid) in open_pos_ids:
            # Partial close: the remainder is still in the positions snapshot
            # and already represents this position on the timeline — a second
            # entry would double count trades_today / lots_today.
            continue
        s = slot(login)
        open_meta = s["_open_meta"].get(pid)
        if open_meta is not None:
            s["positions"].append({
                "symbol": open_meta["symbol"], "direction": open_meta["direction"],
                "lots": open_meta["lots"], "open_time": open_meta["open_time"],
                "close_time": meta["close_time"],
            })
        else:
            s["positions"].append({
                "symbol": meta["symbol"], "direction": meta["direction"],
                "lots": meta["lots"], "open_time": day_start - timedelta(seconds=1),
                "close_time": meta["close_time"],
            })
    # Opened today, not closed, but missing from the positions snapshot (race
    # between the deals and positions queries) — keep it as an open trade so
    # trades_today counts it.
    for login, s in accounts.items():
        for pid, om in s["_open_meta"].items():
            if (login, pid) in open_pos_ids or (login, pid) in closes_by_pos:
                continue
            s["positions"].append({**om, "close_time": None})
        del s["_open_meta"]
    return accounts


def assemble_mt4_accounts(
    *,
    today_rows: List[Dict[str, Any]],
    positions: List[Dict[str, Any]],
    day_start: datetime,
    now_local: datetime,
) -> Dict[int, Dict[str, Any]]:
    """Today's MT4 closed trades / balance ops + open positions → accounts."""
    accounts: Dict[int, Dict[str, Any]] = {}

    def slot(login: int) -> Dict[str, Any]:
        s = accounts.get(login)
        if s is None:
            s = {"raw": _empty_raw(), "positions": []}
            accounts[login] = s
        return s

    for t in today_rows:
        login = int(t["login"])
        cmd = int(t["cmd"])
        profit = float(t["profit"] or 0.0)
        if cmd in (6, 7):
            _apply_flow(slot(login)["raw"], is_balance_op=(cmd == 6),
                        profit=profit, comment=t.get("comment"))
            continue
        pnl = profit + float(t["storage"] or 0.0) + float(t["commission"] or 0.0)
        s = slot(login)
        s["raw"]["realized_today"] += pnl
        if t["open_time_local"] >= day_start:
            s["raw"]["same_day_pnl"] += pnl
        else:
            s["raw"]["carried_now"] += pnl
        s["positions"].append({
            "symbol": t["symbol"], "direction": "B" if cmd == 0 else "S",
            "lots": float(t["lots"] or 0.0), "open_time": t["open_time_local"],
            "close_time": t["close_time_local"],
        })
    for p in positions:
        login = int(p["login"])
        s = slot(login)
        floating = float(p["profit"] or 0.0) + float(p["storage"] or 0.0)
        s["raw"]["floating_all_now"] += floating
        if p["open_time_local"] >= day_start:
            s["raw"]["same_day_pnl"] += floating
        else:
            s["raw"]["carried_now"] += floating
        s["positions"].append({
            "symbol": p["symbol"], "direction": "B" if int(p["cmd"]) == 0 else "S",
            "lots": float(p["lots"] or 0.0), "open_time": p["open_time_local"],
            "close_time": None,
        })
    return accounts


# ── per-day cache (denominator + prior realized) ────────────────────────────

_cache: Dict[str, Any] = {"trading_day": None, "prev": {}, "realized_prior": {}}


def reset_cache() -> None:
    _cache["trading_day"] = None
    _cache["prev"] = {}
    _cache["realized_prior"] = {}


def _cache_for_day(trading_day: date) -> None:
    if _cache["trading_day"] != trading_day:
        reset_cache()
        _cache["trading_day"] = trading_day


# ── scan orchestration ──────────────────────────────────────────────────────

AlertedFetcher = Callable[[str], Dict[Tuple[str, int], Dict[int, int]]]


def scan_intraday_return(
    settings: Settings,
    *,
    rules: List[Dict[str, Any]],
    alerted_keys_fetcher: Optional[AlertedFetcher] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """One tick across the three servers. Returns alerts + detail updates."""
    start = time.time()
    now = now or datetime.now(timezone.utc)
    scanned_at = _iso(now)
    norm_rules = normalize_rules(rules)
    if not norm_rules:
        return _empty_result(start, scanned_at)
    windows = sorted({r["net_window_days"] for r in norm_rules})

    conn = _get_connection(settings)
    servers_failed: List[str] = []
    try:
        day_start = _query_trading_day_start(conn)
        trading_day = day_start.date()
        trading_day_str = trading_day.isoformat()
        # "now" in MT wall time for the behaviour timeline + day sanity.
        now_local = _utc_to_local(now)
        day_age = now_local - day_start
        if day_age > _MAX_DAY_AGE or day_age < -timedelta(hours=1):
            reason = (
                f"derived trading day {trading_day_str} is {day_age} old — "
                "mt5_daily is stale (daily job / replica lag); tick skipped"
            )
            logger.error("Intraday-return: %s", reason)
            return {**_empty_result(start, scanned_at), "status": "skipped",
                    "skipped_reason": reason, "trading_day": trading_day_str}
        _cache_for_day(trading_day)

        alerted = alerted_keys_fetcher(trading_day_str) if alerted_keys_fetcher else {}

        prepared: List[Dict[str, Any]] = []
        for srv in _SERVERS:
            try:
                prepared.extend(_prepare_server(
                    conn, srv, day_start=day_start, now_local=now_local, windows=windows,
                ))
            except Exception:
                servers_failed.append(srv["label"])
                logger.error(
                    "Intraday-return collection failed for %s", srv["label"], exc_info=True,
                )

        # Currency authority + display enrichment for every candidate, BEFORE
        # the thresholds are compared (CEN thresholds are ÷100).
        info_map = get_account_info_map(conn, prepared) if prepared else {}
        if prepared and not info_map:
            # get_account_info_map fails open ({}): evaluating 1,500 accounts
            # as USD would compare CEN accounts against thresholds 100× too
            # loose and persist cent amounts as dollars. Fail closed instead.
            reason = "currency lookup (fxbackoffice.mt4_users) returned nothing; tick skipped"
            logger.error("Intraday-return: %s", reason)
            return {**_empty_result(start, scanned_at), "status": "skipped",
                    "skipped_reason": reason, "trading_day": trading_day_str,
                    "servers_failed": servers_failed}
        for acct in prepared:
            sid = SID_MAP.get(acct["server"])
            info = info_map.get(f"{sid}-{acct['login']}") if sid is not None else None
            acct["currency"] = (info or {}).get("currency") or "USD"
            acct["group"] = (info or {}).get("group")
            acct["zipcode"] = (info or {}).get("zipcode")

        alerts, updates = rule_intraday_return_detect(
            prepared, norm_rules, alerted=alerted,
            scanned_at=scanned_at, trading_day=trading_day_str,
        )
        if alerts:
            net_map = get_net_deposit_hist_map(conn, alerts)
            for a in alerts:
                sid = SID_MAP.get(str(a["server"]))
                a["net_deposit_hist"] = net_map.get(f"{sid}-{a['login']}") if sid is not None else None
    finally:
        conn.close()

    elapsed_ms = int((time.time() - start) * 1000)
    return {
        "alerts": alerts,
        "updates": updates,
        "trading_day": trading_day_str,
        "accounts_evaluated": len(prepared),
        "scan_time_ms": elapsed_ms,
        "scanned_at": scanned_at,
        "status": "partial" if servers_failed else "ok",
        "servers_failed": servers_failed,
        "skipped_reason": None,
    }


def _prepare_server(
    conn, srv: Dict[str, str], *, day_start: datetime, now_local: datetime,
    windows: List[int],
) -> List[Dict[str, Any]]:
    label = srv["label"]
    if srv["type"] == "mt5":
        deals = _query_mt5_today_deals(conn, day_start=day_start)
        positions = _query_mt5_open_positions(conn)
        accounts = assemble_mt5_accounts(
            deals=deals, positions=positions, day_start=day_start, now_local=now_local,
        )
    else:
        rows = _query_mt4_today_rows(conn, db_name=srv["db"], server_label=label, day_start=day_start)
        positions = _query_mt4_open_positions(conn, db_name=srv["db"], server_label=label)
        accounts = assemble_mt4_accounts(
            today_rows=rows, positions=positions, day_start=day_start, now_local=now_local,
        )
    if not accounts:
        return []
    logins = sorted(accounts)

    # Denominator + prior realized: per (server, login, day) cache; only the
    # logins not yet cached hit MySQL (first tick of the day pays for all).
    if srv["type"] == "mt5":
        users_now = _query_mt5_users_now(conn, logins=logins)
    else:
        users_now = _query_mt4_users_now(conn, db_name=srv["db"], logins=logins)

    prev_cache: Dict[Tuple[str, int], Tuple[float, float, float]] = _cache["prev"]
    prev_local: Dict[int, Tuple[float, float, float]] = {}
    missing = [l for l in logins if (label, l) not in prev_cache]
    if missing:
        if srv["type"] == "mt5":
            fetched = _query_mt5_prev_day(conn, day_start=day_start, logins=missing)
        else:
            fetched = _query_mt4_prev_day(conn, db_name=srv["db"], day_start=day_start, logins=missing)
        settling = (now_local - day_start) < _PREV_DAY_SETTLE
        expected_end = day_start - timedelta(seconds=1)
        misses = 0
        for l in missing:
            row = fetched.get(l)
            if row is not None:
                eq, bal, credit, row_end = row
                prev_local[l] = (eq, bal, credit)
                # An OLDER row (weekend / holiday) is fine once the daily batch
                # has surely landed; inside the settle window it may just be
                # late → use it now, re-check next tick.
                if row_end == expected_end or not settling:
                    prev_cache[(label, l)] = (eq, bal, credit)
                continue
            u = users_now.get(l) or {}
            if srv["type"] == "mt5" and (u.get("eq_prev") or u.get("bal_prev")):
                # mt5_users carries yesterday's EOD too (verified equal to
                # mt5_daily.ProfitEquity); credit falls back to the current one.
                trio = (float(u["eq_prev"]), float(u["bal_prev"]), float(u.get("credit") or 0.0))
                prev_local[l] = trio
                if not settling:
                    prev_cache[(label, l)] = trio
                continue
            misses += 1
            prev_local[l] = (0.0, 0.0, 0.0)
            if not settling:
                prev_cache[(label, l)] = (0.0, 0.0, 0.0)
        if misses and misses / max(len(logins), 1) > _MAX_PREV_DAY_MISS_RATIO:
            raise RuntimeError(
                f"{label}: {misses}/{len(logins)} candidates have no previous-day "
                f"row (mt4_daily/mt5_daily) — day boundary or replica lag suspect; "
                f"server skipped this tick"
            )
    realized_cache: Dict[Tuple[str, int, int], float] = _cache["realized_prior"]
    for w in windows:
        missing_w = [l for l in logins if (label, l, w) not in realized_cache]
        if not missing_w:
            continue
        if srv["type"] == "mt5":
            fetched_r = _query_mt5_realized_prior(conn, day_start=day_start, window_days=w, logins=missing_w)
        else:
            fetched_r = _query_mt4_realized_prior(
                conn, db_name=srv["db"], day_start=day_start, window_days=w, logins=missing_w,
            )
        for l in missing_w:
            realized_cache[(label, l, w)] = fetched_r.get(l, 0.0)

    out: List[Dict[str, Any]] = []
    for login in logins:
        s = accounts[login]
        raw = s["raw"]
        prev_eq, prev_bal, prev_credit = prev_cache.get((label, login)) or prev_local[login]
        raw["prev_eq"], raw["prev_bal"], raw["prev_credit"] = prev_eq, prev_bal, prev_credit
        u = users_now.get(login) or {}
        raw["balance_now"] = float(u.get("balance") or 0.0)
        raw["credit_now"] = float(u.get("credit") or 0.0)
        out.append({
            "server": label,
            "login": login,
            "raw": raw,
            "raw_realized_7d_by_window": {
                w: realized_cache[(label, login, w)] + raw["realized_today"] for w in windows
            },
            "positions": s["positions"],
            "day_start": day_start,
            "now_local": now_local,
        })
    return out


def _empty_result(start: float, scanned_at: str) -> Dict[str, Any]:
    return {
        "alerts": [], "updates": [], "trading_day": None, "accounts_evaluated": 0,
        "scan_time_ms": int((time.time() - start) * 1000), "scanned_at": scanned_at,
        "status": "ok", "servers_failed": [], "skipped_reason": None,
    }
