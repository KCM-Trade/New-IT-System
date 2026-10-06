"""Per-client trade activity for the AI analyst agent (OPT-0064, tool 2).

``trade_summary_service`` is firm-wide by symbol and has no client parameter,
so this is the one genuinely new query the first slice adds. Everything about
its 口径 is borrowed, not invented:

* account universe   — ``login_ip_trade_profit_service._ACCOUNT_FILTER_SQL``
                       (demo/test by GROUP/NAME) + INNER JOIN users with
                       ``isEmployee = 0`` + ``sid IN (1,5,6)`` + ``CMD IN (0,1)``
                       + ``isDeleted``;
* open vs closed     — ``closeDate = '1970-01-01'`` is the still-open sentinel
                       (kcm-risk-pipeline skill); closed rows are selected by
                       ``closeDate BETWEEN`` which is an MT-server-day column
                       and needs no timezone arithmetic;
* direction          — ``window_scan_service.resolve_direction`` (sid=5 closed
                       rows store the EXIT side);
* cent products      — ``window_scan_service.is_cent_symbol`` (``.cent`` /
                       ``.kcmc`` only; ``XAUUSD.c`` is NOT cent) — BOTH lots
                       and money ÷100 — plus ``mu.CURRENCY = 'CEN'`` for money
                       on cent ACCOUNTS (the login_ip_trade_profit rule);
* hold buckets       — ``window_scan_service.classify_hold_bucket`` edges.

The query pulls rows (not SQL aggregates) because win rate, median hold and
the night-window flag are row-level facts; a hard row cap keeps a mistyped
366-day range on a hyperactive account from pulling the replica's memory
into this process.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Optional, Sequence

from app.core.config import Settings
from app.core.mysql_readonly import connect_readonly
from app.services.login_ip_trade_profit_service import _ACCOUNT_FILTER_SQL
from app.services.window_scan_service import (
    classify_hold_bucket,
    is_cent_symbol,
    is_open_trade,
    resolve_direction,
)

GROUP_BY_VALUES = ("symbol", "day", "hold_bucket")

# Row cap on one call. Well above any real client's yearly volume (the
# busiest accounts in the 2026-09 IP backtest closed ~30k orders/year) and
# well below what would trouble the process.
MAX_TRADE_ROWS = 60_000

# Contract bucket labels (02 §3.2), keyed by window_scan's frozen codes.
HOLD_BUCKET_LABELS = {"lt30m": "<30min", "m30_2h": "30min-2h", "gt2h": ">2h"}
_BUCKET_ORDER = ("lt30m", "m30_2h", "gt2h")

# Flag rules — facts, not conclusions. Each threshold is repeated verbatim in
# the tool's definition.caveats so the model can quote it.
FLAG_RULES = {
    "single_symbol_concentration": "top symbol holds >= 90% of closed lots and orders >= 5",
    "night_window_scalping": ">= 30% of orders opened 00:00-02:00 MT and median hold < 30 min and orders >= 10",
    "short_hold_dominant": ">= 50% of closed orders held < 30 min and orders >= 10",
}

_CLOSED_SQL = """
    SELECT t.loginSid AS login_sid, t.sid AS sid, t.SYMBOL AS symbol, t.CMD AS cmd,
           t.lots AS lots, t.PROFIT AS profit, t.COMMISSION AS commission, t.SWAPS AS swaps,
           t.totalProfit AS total_profit,
           t.OPEN_TIME AS open_time, t.CLOSE_TIME AS close_time, t.closeDate AS close_date,
           UPPER(mu.CURRENCY) AS currency
    FROM fxbackoffice.mt4_trades t
    JOIN fxbackoffice.mt4_users mu ON mu.loginSid = t.loginSid
    JOIN fxbackoffice.users u ON u.id = mu.userId AND COALESCE(u.isEmployee, 0) = 0
    WHERE t.loginSid IN ({placeholders})
      AND t.closeDate BETWEEN %s AND %s
      AND t.sid IN (1, 5, 6)
      AND t.CMD IN (0, 1)
      AND (t.isDeleted = 0 OR t.isDeleted IS NULL)
""" + _ACCOUNT_FILTER_SQL + """
    ORDER BY t.CLOSE_TIME
    LIMIT %s
"""

_OPEN_SQL = """
    SELECT t.loginSid AS login_sid, t.sid AS sid, t.SYMBOL AS symbol, t.CMD AS cmd,
           t.lots AS lots, t.PROFIT AS profit, t.COMMISSION AS commission, t.SWAPS AS swaps,
           t.totalProfit AS total_profit,
           t.OPEN_TIME AS open_time, t.CLOSE_TIME AS close_time,
           UPPER(mu.CURRENCY) AS currency
    FROM fxbackoffice.mt4_trades t
    JOIN fxbackoffice.mt4_users mu ON mu.loginSid = t.loginSid
    JOIN fxbackoffice.users u ON u.id = mu.userId AND COALESCE(u.isEmployee, 0) = 0
    WHERE t.loginSid IN ({placeholders})
      AND t.closeDate = '1970-01-01'
      AND t.sid IN (1, 5, 6)
      AND t.CMD IN (0, 1)
      AND (t.isDeleted = 0 OR t.isDeleted IS NULL)
""" + _ACCOUNT_FILTER_SQL + """
    LIMIT %s
"""


def _placeholders(n: int) -> str:
    return ", ".join(["%s"] * n)


def fetch_closed_rows(conn, login_sids: Sequence[str], day_from: str, day_to: str) -> list[dict]:
    """Closed orders of these accounts whose MT close day falls in [from, to]."""
    if not login_sids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            _CLOSED_SQL.format(placeholders=_placeholders(len(login_sids))),
            (*login_sids, day_from, day_to, MAX_TRADE_ROWS + 1),
        )
        return list(cur.fetchall())


def fetch_open_rows(conn, login_sids: Sequence[str]) -> list[dict]:
    if not login_sids:
        return []
    with conn.cursor() as cur:
        cur.execute(_OPEN_SQL.format(placeholders=_placeholders(len(login_sids))), (*login_sids, MAX_TRADE_ROWS + 1))
        return list(cur.fetchall())


# ── normalisation ────────────────────────────────────────────────────────────


def _f(v: Any) -> float:
    return float(v) if v is not None else 0.0


def normalise_row(raw: dict) -> dict:
    """One DB row → one USD/standard-lot fact with direction and hold bucket.

    Money ÷100 when the ACCOUNT is CEN or the SYMBOL is a cent product; lots
    ÷100 only for cent SYMBOLS (that is where lots are stored in cents —
    window_scan_service's rule). Both are idempotent per row: a CEN account
    trading a `.cent` symbol is divided once, not twice.
    """
    symbol = raw.get("symbol") or ""
    cent_symbol = is_cent_symbol(symbol)
    cent_account = (raw.get("currency") or "") == "CEN"
    money_div = 100.0 if (cent_symbol or cent_account) else 1.0
    lots_div = 100.0 if cent_symbol else 1.0
    open_time = raw.get("open_time")
    close_time = raw.get("close_time")
    closed = not is_open_trade(close_time)
    hold_sec: Optional[float] = None
    if closed and isinstance(open_time, datetime) and isinstance(close_time, datetime):
        hold_sec = max(0.0, (close_time - open_time).total_seconds())
    profit = _f(raw.get("profit")) / money_div
    commission = _f(raw.get("commission")) / money_div
    swaps = _f(raw.get("swaps")) / money_div
    total = raw.get("total_profit")
    net = _f(total) / money_div if total is not None else profit + commission + swaps
    return {
        "login_sid": raw.get("login_sid"),
        "sid": int(raw.get("sid") or 0),
        "symbol": symbol,
        "direction": resolve_direction(raw.get("cmd") or 0, raw.get("sid") or 0, closed),
        "lots": _f(raw.get("lots")) / lots_div,
        "gross_profit": profit,
        "commission": commission,
        "swap": swaps,
        "net_profit": net,
        "open_time": open_time,
        "close_time": close_time if closed else None,
        "close_day": str(raw.get("close_date")) if raw.get("close_date") is not None else None,
        "hold_sec": hold_sec,
        "hold_bucket": classify_hold_bucket(hold_sec) if hold_sec is not None else None,
        "is_cent": cent_symbol or cent_account,
    }


def _group_key(row: dict, group_by: str) -> str:
    if group_by == "symbol":
        return row["symbol"]
    if group_by == "day":
        return row["close_day"] or (row["close_time"].date().isoformat() if row["close_time"] else "unknown")
    return HOLD_BUCKET_LABELS.get(row["hold_bucket"] or "", "unknown")


def aggregate(rows: list[dict], group_by: str, *, max_rows: int = 200) -> dict:
    """Totals + grouped rows + fact flags over normalised CLOSED rows."""
    if group_by not in GROUP_BY_VALUES:
        raise ValueError(f"group_by must be one of {GROUP_BY_VALUES}")
    n = len(rows)
    lots = sum(r["lots"] for r in rows)
    wins = sum(1 for r in rows if r["net_profit"] > 0)
    holds = [r["hold_sec"] for r in rows if r["hold_sec"] is not None]
    totals = {
        "orders": n,
        "lots": round(lots, 3),
        "gross_profit": round(sum(r["gross_profit"] for r in rows), 2),
        "commission": round(sum(r["commission"] for r in rows), 2),
        "swap": round(sum(r["swap"] for r in rows), 2),
        "net_profit": round(sum(r["net_profit"] for r in rows), 2),
        "win_rate": round(wins / n, 4) if n else 0.0,
        "avg_hold_minutes": round(statistics.fmean(holds) / 60, 1) if holds else 0.0,
        "median_hold_minutes": round(statistics.median(holds) / 60, 1) if holds else 0.0,
        "symbols_traded": len({r["symbol"] for r in rows}),
    }

    groups: dict[str, dict] = defaultdict(lambda: {"orders": 0, "lots": 0.0, "net_profit": 0.0, "wins": 0})
    for r in rows:
        g = groups[_group_key(r, group_by)]
        g["orders"] += 1
        g["lots"] += r["lots"]
        g["net_profit"] += r["net_profit"]
        g["wins"] += 1 if r["net_profit"] > 0 else 0
    out_rows = [
        {
            "key": k,
            "orders": g["orders"],
            "lots": round(g["lots"], 3),
            "net_profit": round(g["net_profit"], 2),
            "win_rate": round(g["wins"] / g["orders"], 4) if g["orders"] else 0.0,
        }
        for k, g in groups.items()
    ]
    if group_by == "hold_bucket":
        order = {HOLD_BUCKET_LABELS[b]: i for i, b in enumerate(_BUCKET_ORDER)}
        out_rows.sort(key=lambda r: order.get(r["key"], 99))
    elif group_by == "day":
        out_rows.sort(key=lambda r: r["key"])
    else:
        out_rows.sort(key=lambda r: (-r["lots"], r["key"]))
    truncated = len(out_rows) > max_rows
    out_rows = out_rows[:max_rows]

    flags: list[str] = []
    if n >= 5 and lots > 0:
        top_lots = max(sum(r["lots"] for r in rows if r["symbol"] == s) for s in {r["symbol"] for r in rows})
        if top_lots / lots >= 0.90:
            flags.append("single_symbol_concentration")
    if n >= 10:
        night = sum(1 for r in rows if isinstance(r["open_time"], datetime) and r["open_time"].hour < 2)
        if night / n >= 0.30 and totals["median_hold_minutes"] < 30:
            flags.append("night_window_scalping")
        short = sum(1 for r in rows if r["hold_bucket"] == "lt30m")
        if short / n >= 0.50:
            flags.append("short_hold_dominant")

    return {"totals": totals, "rows": out_rows, "flags": flags, "truncated": truncated}


def summarise_open(open_rows: list[dict]) -> dict:
    """Snapshot of still-open positions (this instant, date_range-independent)."""
    if not open_rows:
        return {"count": 0, "lots": 0.0, "floating_pl": 0.0, "oldest_open_at": None}
    oldest = min((r["open_time"] for r in open_rows if isinstance(r["open_time"], datetime)), default=None)
    return {
        "count": len(open_rows),
        "lots": round(sum(r["lots"] for r in open_rows), 3),
        "floating_pl": round(sum(r["net_profit"] for r in open_rows), 2),
        "oldest_open_at": oldest,  # MT wall clock; the tool converts to UTC
    }


# 30s (15s before 2026-10-06): the aggregation over a full year of a
# high-frequency account is the heaviest per-client read any AI tool makes;
# strictly below the read_timeout derived from it.
STATEMENT_BUDGET_MS = 30_000
READ_TIMEOUT_S = STATEMENT_BUDGET_MS // 1000 + 10


def _default_connect(settings: Settings):
    """The replica connection this service uses unless a test injects one."""
    return connect_readonly(settings, max_execution_ms=STATEMENT_BUDGET_MS, read_timeout=READ_TIMEOUT_S)


def by_subject(
    settings: Settings,
    *,
    login_sids: Sequence[str],
    date_from: str,
    date_to: str,
    group_by: str,
    connect: Callable[[Settings], Any] = _default_connect,
) -> dict:
    """Full result for one client: closed-order aggregates over the MT-day
    window + open-position snapshot. ``connect`` is injectable for tests, but
    its DEFAULT carries the db-timeout-guard three timeouts (30s statement
    budget) so a new caller cannot bypass them by simply not passing one."""
    conn = connect(settings)
    try:
        closed_raw = fetch_closed_rows(conn, login_sids, date_from, date_to)
        open_raw = fetch_open_rows(conn, login_sids)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    rows_truncated = len(closed_raw) > MAX_TRADE_ROWS
    closed = [normalise_row(r) for r in closed_raw[:MAX_TRADE_ROWS]]
    opened = [normalise_row(r) for r in open_raw[:MAX_TRADE_ROWS]]
    agg = aggregate(closed, group_by)
    return {
        **agg,
        "open_positions": summarise_open(opened),
        "truncated": agg["truncated"] or rows_truncated,
        "rows_truncated": rows_truncated,
    }
