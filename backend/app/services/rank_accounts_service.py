"""Group-level account ranking for the AI analyst agent (OPT-0065 item 4).

Answers "which accounts had the highest win rate last week" and its
siblings: one SQL aggregation grouped by account, ordered by the requested
metric. ``win_rate`` aggregates ``mt4_trades`` (``_RANK_SQL``); every other
metric aggregates the CRM's daily pre-aggregate ``stats_trading``
(``_STATS_RANK_SQL``), which is two orders of magnitude cheaper. The 口径 is the SAME universe and the SAME cent rule
as ``trade_activity_service`` (which pulls rows for one client); nothing here
is invented:

* account universe   — ``login_ip_trade_profit_service._ACCOUNT_FILTER_SQL``
                       (demo/test by GROUP/NAME) + INNER JOIN users with
                       ``isEmployee = 0`` + ``sid IN (1,5,6)`` + ``CMD IN (0,1)``
                       + ``isDeleted``;
* day boundary       — ``closeDate BETWEEN`` is an MT-server-day column, so
                       the window needs no timezone arithmetic;
* cent               — money ÷100 when the ACCOUNT currency is CEN or the
                       SYMBOL is a cent product (``.cent`` / ``.kcmc``; NOT
                       ``XAUUSD.c``); lots ÷100 only for cent SYMBOLS. Both
                       are the ``trade_activity_service.normalise_row`` rule,
                       expressed in SQL because the aggregation must happen on
                       the server (the universe is tens of thousands of orders
                       a week, not one client's);
* win rate           — ``PROFIT > 0`` wins / closed orders, swap and
                       commission excluded (02 §11).

``return_pct`` (net profit / opening equity) is in the contract's metric list
but has NO certified source: ``mt4_users`` holds the CURRENT equity only, and
no table records an account's equity at an arbitrary past day. Rather than
divide by a number that is not the opening equity, the service refuses to
RANK by it and reports it as ``None`` on every row; the tool turns that into
``invalid_argument`` with the reason. The caveat is fixed text so the model
cannot paper over it.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from app.core.config import Settings
from app.core.mysql_readonly import connect_readonly
from app.services.login_ip_trade_profit_service import _ACCOUNT_FILTER_SQL
from app.services.stats_trading_units import UNDIVIDED_GROUPS_SQL, UNDIVIDED_SINCE

# 02 §11 metric set. `return_pct` stays listed so a model asking for it gets
# the structured refusal below rather than "unknown metric".
METRICS = ("win_rate", "net_profit", "lots", "orders", "profit_factor", "return_pct")
RANKABLE_METRICS = ("win_rate", "net_profit", "lots", "orders", "profit_factor")
# Which table answers which metric (2026-10-06). The CRM's daily pre-aggregate
# `stats_trading` has orders, lots, net/gross P&L and the winning/losing PROFIT
# sums per account per day, so everything except the win COUNT comes from it in
# under a second per month. Only `win_rate` still needs the orders themselves.
TRADES_METRICS = ("win_rate",)
ORDERS = ("desc", "asc")
LIVE_SIDS = (1, 5, 6)

# Group scans are dearer than one client's rows (02 §11: ≤ 92 days).
MAX_RANGE_DAYS = 92
MAX_TOP_N = 50
# `min_orders` below this is refused unless the caller explicitly allowed it:
# one winning order is a 100% win rate and would top every list.
MIN_ORDERS_SOFT_FLOOR = 5
DEFAULT_MIN_ORDERS = 20
# Hard ceiling on rows pulled in one call, however large top_n * 3 is.
MAX_FETCH_ROWS = 500

# Same statement budget as the per-client activity query (db-timeout-guard).
# 30s since 2026-10-06 (was 15s). The mt4_trades statement (win_rate only) costs
# ~0.75s per closeDate day, so about a month fits; 92 days does not. The
# stats_trading statement (every other metric) takes ~6s for 92 days.
STATEMENT_BUDGET_MS = 30_000
READ_TIMEOUT_S = STATEMENT_BUDGET_MS // 1000 + 10

RETURN_PCT_CAVEAT = (
    "return_pct is null on every row: opening equity is not recorded historically "
    "(mt4_users holds current equity only), so net_profit / opening equity cannot be "
    "computed with a certified figure. Rank by net_profit instead."
)

# SQL fragments. `%%` because every query here runs with parameters, so a
# literal percent must be escaped for the driver (same as _ACCOUNT_FILTER_SQL).
_CENT_SYMBOL_SQL = "(LOWER(t.SYMBOL) LIKE '%%.cent' OR LOWER(t.SYMBOL) LIKE '%%.kcmc')"
# Outer-level divisors: `a.cent_sym` is the inner query's per-group cent-symbol flag.
_MONEY_DIV_SQL = "IF(UPPER(mu.CURRENCY) = 'CEN' OR a.cent_sym, 100, 1)"
_LOTS_DIV_SQL = "IF(a.cent_sym, 100, 1)"

# ORDER BY expression per metric — column ALIASES from the SELECT list, which
# MySQL allows in ORDER BY (and in HAVING) but not in WHERE.
_ORDER_EXPR = {
    "win_rate": "win_rate",
    "net_profit": "net_profit",
    "lots": "lots",
    "orders": "orders",
    "profit_factor": "profit_factor",
}

# Aggregate FIRST, join AFTER (2026-10-06). The inner query touches mt4_trades
# only and collapses the window to one row per (account, cent-symbol flag) — a
# few thousand rows — and only those are joined to mt4_users / users. Joining
# before aggregating did two eq_ref lookups for every one of ~70k orders a day
# and took twice as long for the same answer (measured on the replica: 14 days
# 19.6s -> 10.6s, 30 days 40.2s -> 21.5s; top 150 rows identical). The cent
# flag is part of the inner GROUP BY because the divisor depends on the SYMBOL
# of each order, so sums must be kept apart until the division. Dividing the
# SUM once instead of every order also drops the per-row rounding: against the
# old statement 4 of 1,500 compared rows moved by 0.01 in gross_profit (all
# cent accounts), nothing else changed. What is left
# (~0.75s per closeDate day) is the clustered-index lookup per order: the
# closeDate index is not covering. Only a covering index or a per-day
# pre-aggregate removes that; rewriting this statement further will not.
_RANK_SQL = f"""
    SELECT a.loginSid AS login_sid, a.sid AS sid, mu.userId AS client_id, u.cid AS cid,
           (UPPER(mu.CURRENCY) = 'CEN') AS cent_account,
           MAX(a.cent_sym) AS cent_symbol,
           SUM(a.n_orders) AS orders,
           SUM(a.n_wins) AS wins,
           SUM(a.n_wins) / SUM(a.n_orders) AS win_rate,
           SUM(a.sum_lots / {_LOTS_DIV_SQL}) AS lots,
           SUM(a.sum_total_profit / {_MONEY_DIV_SQL}) AS net_profit,
           SUM(a.sum_profit / {_MONEY_DIV_SQL}) AS gross_profit,
           SUM(a.sum_pos / {_MONEY_DIV_SQL}) / NULLIF(ABS(SUM(a.sum_neg / {_MONEY_DIV_SQL})), 0) AS profit_factor
    FROM (
        SELECT t.loginSid AS loginSid, t.sid AS sid,
               {_CENT_SYMBOL_SQL} AS cent_sym,
               COUNT(*) AS n_orders,
               SUM(t.PROFIT > 0) AS n_wins,
               SUM(t.lots) AS sum_lots,
               SUM(t.totalProfit) AS sum_total_profit,
               SUM(t.PROFIT) AS sum_profit,
               SUM(IF(t.PROFIT > 0, t.PROFIT, 0)) AS sum_pos,
               SUM(IF(t.PROFIT < 0, t.PROFIT, 0)) AS sum_neg
        FROM fxbackoffice.mt4_trades t
        WHERE t.closeDate BETWEEN %s AND %s
          AND t.sid IN ({{sids}})
          AND t.CMD IN (0, 1)
          AND (t.isDeleted = 0 OR t.isDeleted IS NULL)
        GROUP BY t.loginSid, t.sid, cent_sym
    ) a
    JOIN fxbackoffice.mt4_users mu ON mu.loginSid = a.loginSid
    JOIN fxbackoffice.users u ON u.id = mu.userId AND COALESCE(u.isEmployee, 0) = 0
    WHERE 1 = 1
""" + _ACCOUNT_FILTER_SQL + """
    GROUP BY a.loginSid, a.sid, mu.userId, u.cid, cent_account
    HAVING orders >= %s{having_extra}
    ORDER BY {order_expr} {direction}, orders DESC, login_sid
    LIMIT %s
"""


# The same ranking from the CRM's pre-aggregate. Aggregate-first as above. Two
# things differ from the mt4_trades statement:
#
# * units — stats_trading money and lots are ALREADY divided for cent accounts
#   (app/services/stats_trading_units.py), so there is no cent divisor here
#   except for the one group the CRM leaves in cents, from UNDIVIDED_SINCE on.
#   That divisor depends on the row's date, hence `late` in the inner GROUP BY.
# * no win count — the table has the winning and losing PROFIT sums but not how
#   many orders won, so `wins` is NULL and win_rate cannot be ranked from it.
#
# Reconciled against the mt4_trades statement on the replica (2026-10-06,
# September, min_orders 1): the same 3,527 accounts, every order count equal.
_STATS_MONEY_DIV_SQL = f"IF(a.late = 1 AND mu.`GROUP` IN ({UNDIVIDED_GROUPS_SQL}), 100, 1)"

_STATS_RANK_SQL = f"""
    SELECT a.loginSid AS login_sid, mu.sid AS sid, mu.userId AS client_id, u.cid AS cid,
           (UPPER(mu.CURRENCY) = 'CEN') AS cent_account,
           0 AS cent_symbol,
           SUM(a.n_orders) AS orders,
           NULL AS wins,
           NULL AS win_rate,
           SUM(a.sum_lots) AS lots,
           SUM(a.sum_total_profit / {_STATS_MONEY_DIV_SQL}) AS net_profit,
           SUM(a.sum_profit / {_STATS_MONEY_DIV_SQL}) AS gross_profit,
           SUM(a.sum_pos / {_STATS_MONEY_DIV_SQL}) / NULLIF(ABS(SUM(a.sum_neg / {_STATS_MONEY_DIV_SQL})), 0) AS profit_factor
    FROM (
        SELECT st.loginSid AS loginSid,
               (st.date >= '{UNDIVIDED_SINCE}') AS late,
               SUM(st.tradeCnt) AS n_orders,
               SUM(st.lots) AS sum_lots,
               SUM(st.totalPlClosed) AS sum_total_profit,
               SUM(st.totalProfit) AS sum_profit,
               SUM(st.positiveProfit) AS sum_pos,
               SUM(st.negativeProfit) AS sum_neg
        FROM fxbackoffice.stats_trading st
        WHERE st.date BETWEEN %s AND %s
          AND st.tradeCnt > 0
        GROUP BY st.loginSid, late
    ) a
    JOIN fxbackoffice.mt4_users mu ON mu.loginSid = a.loginSid
    JOIN fxbackoffice.users u ON u.id = mu.userId AND COALESCE(u.isEmployee, 0) = 0
    WHERE mu.sid IN ({{sids}})
""" + _ACCOUNT_FILTER_SQL + """
    GROUP BY a.loginSid, mu.sid, mu.userId, u.cid, cent_account
    HAVING orders >= %s{having_extra}
    ORDER BY {order_expr} {direction}, orders DESC, login_sid
    LIMIT %s
"""


def source_table(metric: str) -> str:
    """The table a ranking by ``metric`` is computed from."""
    return "mt4_trades" if metric in TRADES_METRICS else "stats_trading"


def _placeholders(n: int) -> str:
    return ", ".join(["%s"] * n)


def build_rank_sql(metric: str, order: str, sids: Sequence[int]) -> str:
    """The statement text for one ranking. Separate so tests can pin the
    universe filters and the cent rule without a database."""
    assert metric in RANKABLE_METRICS, metric
    assert order in ORDERS, order
    template = _RANK_SQL if source_table(metric) == "mt4_trades" else _STATS_RANK_SQL
    return template.format(
        sids=_placeholders(len(sids)),
        order_expr=_ORDER_EXPR[metric],
        direction=order.upper(),
        # An account with no losing order has no profit factor; it cannot be
        # placed in a profit-factor ranking, so it is left out of that one.
        having_extra=" AND profit_factor IS NOT NULL" if metric == "profit_factor" else "",
    )


def fetch_ranked(
    conn,
    *,
    metric: str,
    order: str,
    day_from: str,
    day_to: str,
    min_orders: int,
    sids: Sequence[int],
    limit: int,
) -> list[dict]:
    sql = build_rank_sql(metric, order, sids)
    params: list[Any] = [day_from, day_to, *[int(s) for s in sids], int(min_orders), int(limit)]
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def _f(v: Any) -> float:
    return float(v) if v is not None else 0.0


def normalise_row(raw: dict, metric: str) -> dict:
    """One aggregated DB row → the contract row (02 §11 `rows[]`).

    Everything money/lots related was already divided in SQL; this only
    rounds, types and picks `metric_value`.
    """
    orders = int(raw.get("orders") or 0)
    # `wins` present but NULL = the stats_trading statement, which has no win
    # count. Report it as unknown, never as zero wins.
    if "wins" in raw and raw["wins"] is None:
        wins = None
        win_rate = None
    else:
        wins = int(raw.get("wins") or 0)
        win_rate = round(wins / orders, 4) if orders else 0.0
    profit_factor = raw.get("profit_factor")
    row = {
        "login_sid": raw.get("login_sid"),
        "client_id": int(raw["client_id"]) if raw.get("client_id") is not None else None,
        "cid": int(raw["cid"]) if raw.get("cid") is not None else None,
        "sid": int(raw.get("sid") or 0),
        "is_cent": bool(raw.get("cent_account")) or bool(raw.get("cent_symbol")),
        "orders": orders,
        "wins": wins,
        "win_rate": win_rate,
        "lots": round(_f(raw.get("lots")), 3),
        "net_profit": round(_f(raw.get("net_profit")), 2),
        "gross_profit": round(_f(raw.get("gross_profit")), 2),
        "profit_factor": round(float(profit_factor), 4) if profit_factor is not None else None,
        "return_pct": None,
    }
    row["metric_value"] = row[metric] if metric in row else None
    return row


def _default_connect(settings: Settings):
    """The replica connection this service uses unless a test injects one.
    Carries the db-timeout-guard three timeouts (30s statement budget)."""
    return connect_readonly(settings, max_execution_ms=STATEMENT_BUDGET_MS, read_timeout=READ_TIMEOUT_S)


def rank(
    settings: Settings,
    *,
    metric: str,
    order: str,
    day_from: str,
    day_to: str,
    min_orders: int,
    sids: Optional[Sequence[int]],
    limit: int,
    connect: Callable[[Settings], Any] = _default_connect,
) -> list[dict]:
    """Ranked, normalised account rows. ``limit`` is the FETCH size — the
    caller (the tool) trims to ``top_n`` after scope filtering. Raises on
    DB errors; the tool's ``run_sync_with_timeout`` converts them."""
    if metric not in RANKABLE_METRICS:
        raise ValueError(f"metric {metric!r} is not rankable")
    sids = tuple(sids) if sids else LIVE_SIDS
    limit = max(1, min(int(limit), MAX_FETCH_ROWS))
    conn = connect(settings)
    try:
        raw = fetch_ranked(
            conn,
            metric=metric,
            order=order,
            day_from=day_from,
            day_to=day_to,
            min_orders=min_orders,
            sids=sids,
            limit=limit,
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return [normalise_row(r, metric) for r in raw]
