"""Group-level account ranking for the AI analyst agent (OPT-0065 item 4).

Answers "which accounts had the highest win rate last week" and its
siblings: one SQL aggregation over ``mt4_trades`` grouped by account, ordered
by the requested metric. The 口径 is the SAME universe and the SAME cent rule
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

# 02 §11 metric set. `return_pct` stays listed so a model asking for it gets
# the structured refusal below rather than "unknown metric".
METRICS = ("win_rate", "net_profit", "lots", "orders", "return_pct")
RANKABLE_METRICS = ("win_rate", "net_profit", "lots", "orders")
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
# 30s since 2026-10-06 (was 15s): this scan costs ~1.35s per closeDate day, so
# 15s could not finish even a 14-day window (measured 19s).
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
_MONEY_DIV_SQL = f"IF(UPPER(mu.CURRENCY) = 'CEN' OR {_CENT_SYMBOL_SQL}, 100, 1)"
_LOTS_DIV_SQL = f"IF({_CENT_SYMBOL_SQL}, 100, 1)"

# ORDER BY expression per metric — column ALIASES from the SELECT list, which
# MySQL allows in ORDER BY (and in HAVING) but not in WHERE.
_ORDER_EXPR = {
    "win_rate": "win_rate",
    "net_profit": "net_profit",
    "lots": "lots",
    "orders": "orders",
}

_RANK_SQL = f"""
    SELECT t.loginSid AS login_sid, t.sid AS sid, mu.userId AS client_id, u.cid AS cid,
           (UPPER(mu.CURRENCY) = 'CEN') AS cent_account,
           MAX({_CENT_SYMBOL_SQL}) AS cent_symbol,
           COUNT(*) AS orders,
           SUM(t.PROFIT > 0) AS wins,
           SUM(t.PROFIT > 0) / COUNT(*) AS win_rate,
           SUM(t.lots / {_LOTS_DIV_SQL}) AS lots,
           SUM(t.totalProfit / {_MONEY_DIV_SQL}) AS net_profit,
           SUM(t.PROFIT / {_MONEY_DIV_SQL}) AS gross_profit
    FROM fxbackoffice.mt4_trades t
    JOIN fxbackoffice.mt4_users mu ON mu.loginSid = t.loginSid
    JOIN fxbackoffice.users u ON u.id = mu.userId AND COALESCE(u.isEmployee, 0) = 0
    WHERE t.closeDate BETWEEN %s AND %s
      AND t.sid IN ({{sids}})
      AND t.CMD IN (0, 1)
      AND (t.isDeleted = 0 OR t.isDeleted IS NULL)
""" + _ACCOUNT_FILTER_SQL + """
    GROUP BY t.loginSid, t.sid, mu.userId, u.cid, cent_account
    HAVING orders >= %s
    ORDER BY {order_expr} {direction}, orders DESC, login_sid
    LIMIT %s
"""


def _placeholders(n: int) -> str:
    return ", ".join(["%s"] * n)


def build_rank_sql(metric: str, order: str, sids: Sequence[int]) -> str:
    """The statement text for one ranking. Separate so tests can pin the
    universe filters and the cent rule without a database."""
    assert metric in RANKABLE_METRICS, metric
    assert order in ORDERS, order
    return _RANK_SQL.format(
        sids=_placeholders(len(sids)),
        order_expr=_ORDER_EXPR[metric],
        direction=order.upper(),
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
    wins = int(raw.get("wins") or 0)
    win_rate = round(wins / orders, 4) if orders else 0.0
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
