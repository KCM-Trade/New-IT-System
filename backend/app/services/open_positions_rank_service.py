"""Current open-position exposure by symbol for the AI analyst agent.

Answers "who holds the most XAUUSD right now" — the question that, without
this, the model tried to answer with run_sql and timed out twice
(2026-09-29: it found open orders with ``CLOSE_TIME = '1970-01-01'``, which
is not indexed, instead of ``closeDate = '1970-01-01'``, which is). One SQL
aggregation per (account, symbol) over the still-open sentinel, rolled up in
Python to accounts or clients. The 口径 is borrowed, not invented:

* open vs closed     — ``closeDate = '1970-01-01'`` (kcm-risk-pipeline skill;
                       ``INDEX_CLOSEDATE``, ~50k rows, sub-second);
* account universe   — ``login_ip_trade_profit_service._ACCOUNT_FILTER_SQL``
                       + INNER JOIN users with ``isEmployee = 0`` + ``sid IN
                       (1,5,6)`` + ``CMD IN (0,1)`` + ``isDeleted`` — the same
                       universe as ``rank_accounts_service`` and
                       ``trade_activity_service``;
* direction          — open rows carry the POSITION side in CMD on every
                       server (the sid=5 inversion applies to CLOSED rows
                       only), so CMD 0 = buy, 1 = sell as stored;
* cent               — money ÷100 when the ACCOUNT is CEN or the SYMBOL is a
                       cent product (``.cent`` / ``.kcmc``; NOT ``XAUUSD.c``);
                       lots ÷100 only for cent SYMBOLS;
* floating P/L       — ``totalProfit`` of the open row (PROFIT + SWAPS +
                       COMMISSION as last synced by the back office).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Optional, Sequence

from app.core.config import Settings
from app.core.mysql_readonly import connect_readonly
from app.services.login_ip_trade_profit_service import _ACCOUNT_FILTER_SQL

LIVE_SIDS = (1, 5, 6)
GROUP_BY_VALUES = ("client", "account")
SORT_VALUES = ("net_lots", "gross_lots", "floating_profit", "floating_loss")
SYMBOL_MATCH_VALUES = ("family", "exact")
MAX_TOP_N = 50

# (account, symbol) rows pulled in one call. The whole open book is ~50k
# ORDERS across every symbol; one symbol family aggregates to a few thousand
# account rows. Above this the result is flagged truncated, never silently cut.
MAX_FETCH_ROWS = 20_000

STATEMENT_BUDGET_MS = 30_000
READ_TIMEOUT_S = STATEMENT_BUDGET_MS // 1000 + 10

# A symbol is a short token; anything else is refused before it reaches SQL
# (it is parameterised anyway — this keeps LIKE wildcards out of the token).
SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.#_-]{1,31}$")

_CENT_SYMBOL_SQL = "(LOWER(t.SYMBOL) LIKE '%%.cent' OR LOWER(t.SYMBOL) LIKE '%%.kcmc')"
_MONEY_DIV_SQL = f"IF(UPPER(mu.CURRENCY) = 'CEN' OR {_CENT_SYMBOL_SQL}, 100, 1)"
_LOTS_DIV_SQL = f"IF({_CENT_SYMBOL_SQL}, 100, 1)"

_OPEN_SQL = f"""
    SELECT t.loginSid AS login_sid, t.sid AS sid, mu.userId AS client_id, u.cid AS cid,
           t.SYMBOL AS symbol,
           MAX(UPPER(mu.CURRENCY) = 'CEN' OR {_CENT_SYMBOL_SQL}) AS is_cent,
           COUNT(*) AS orders,
           SUM(IF(t.CMD = 0, t.lots / {_LOTS_DIV_SQL}, 0)) AS buy_lots,
           SUM(IF(t.CMD = 1, t.lots / {_LOTS_DIV_SQL}, 0)) AS sell_lots,
           SUM(t.totalProfit / {_MONEY_DIV_SQL}) AS floating_pl,
           MIN(t.OPEN_TIME) AS oldest_open
    FROM fxbackoffice.mt4_trades t
    JOIN fxbackoffice.mt4_users mu ON mu.loginSid = t.loginSid
    JOIN fxbackoffice.users u ON u.id = mu.userId AND COALESCE(u.isEmployee, 0) = 0
    WHERE t.closeDate = '1970-01-01'
      AND {{symbol_sql}}
      AND t.sid IN ({{sids}})
      AND t.CMD IN (0, 1)
      AND (t.isDeleted = 0 OR t.isDeleted IS NULL)
""" + _ACCOUNT_FILTER_SQL + """
    GROUP BY t.loginSid, t.sid, mu.userId, u.cid, t.SYMBOL
    LIMIT %s
"""


def _placeholders(n: int) -> str:
    return ", ".join(["%s"] * n)


def like_escape(token: str) -> str:
    """Escape LIKE wildcards so 'XAU_USD' matches literally."""
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_open_sql(symbol_match: str, sids: Sequence[int]) -> str:
    """Statement text for one call. Separate so tests can pin the open
    sentinel, the universe filters and the cent rule without a database."""
    assert symbol_match in SYMBOL_MATCH_VALUES, symbol_match
    symbol_sql = "t.SYMBOL = %s" if symbol_match == "exact" else "t.SYMBOL LIKE %s"
    return _OPEN_SQL.format(symbol_sql=symbol_sql, sids=_placeholders(len(sids)))


def symbol_param(symbol: str, symbol_match: str) -> str:
    return symbol if symbol_match == "exact" else like_escape(symbol) + "%"


def _f(v: Any) -> float:
    return float(v) if v is not None else 0.0


def normalise_row(raw: dict) -> dict:
    """One aggregated (account, symbol) DB row → plain types. Lots and money
    were already divided in SQL."""
    buy = _f(raw.get("buy_lots"))
    sell = _f(raw.get("sell_lots"))
    return {
        "login_sid": raw.get("login_sid"),
        "sid": int(raw.get("sid") or 0),
        "client_id": int(raw["client_id"]) if raw.get("client_id") is not None else None,
        "cid": int(raw["cid"]) if raw.get("cid") is not None else None,
        "symbol": raw.get("symbol") or "",
        "is_cent": bool(raw.get("is_cent")),
        "orders": int(raw.get("orders") or 0),
        "buy_lots": buy,
        "sell_lots": sell,
        "floating_pl": _f(raw.get("floating_pl")),
        "oldest_open": raw.get("oldest_open"),
    }


def rollup(rows: Iterable[dict], group_by: str) -> list[dict]:
    """(account, symbol) rows → one row per account or per client.

    Unsorted and unrounded-in-between: rounding happens once at the end so a
    client with many accounts does not accumulate rounding error.
    """
    assert group_by in GROUP_BY_VALUES, group_by
    groups: dict[Any, dict] = {}
    for r in rows:
        key = r["client_id"] if group_by == "client" else r["login_sid"]
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "client_id": r["client_id"],
                "cid": r["cid"],
                "login_sids": set(),
                "symbols": set(),
                "is_cent": False,
                "orders": 0,
                "buy_lots": 0.0,
                "sell_lots": 0.0,
                "floating_pl": 0.0,
                "oldest_open": None,
            }
        g["login_sids"].add(r["login_sid"])
        g["symbols"].add(r["symbol"])
        g["is_cent"] = g["is_cent"] or r["is_cent"]
        g["orders"] += r["orders"]
        g["buy_lots"] += r["buy_lots"]
        g["sell_lots"] += r["sell_lots"]
        g["floating_pl"] += r["floating_pl"]
        if r["oldest_open"] is not None and (g["oldest_open"] is None or r["oldest_open"] < g["oldest_open"]):
            g["oldest_open"] = r["oldest_open"]
        # A client's cid is one value; keep a known one over None.
        if g["cid"] is None:
            g["cid"] = r["cid"]

    out = []
    for g in groups.values():
        buy, sell = g["buy_lots"], g["sell_lots"]
        out.append(
            {
                "client_id": g["client_id"],
                "cid": g["cid"],
                "login_sids": sorted(g["login_sids"]),
                "symbols": sorted(g["symbols"]),
                "is_cent": g["is_cent"],
                "orders": g["orders"],
                "buy_lots": round(buy, 3),
                "sell_lots": round(sell, 3),
                "net_lots": round(buy - sell, 3),
                "gross_lots": round(buy + sell, 3),
                "floating_pl": round(g["floating_pl"], 2),
                "oldest_open": g["oldest_open"],
            }
        )
    return out


_SORT_KEYS: dict[str, Callable[[dict], tuple]] = {
    # Ties: bigger book first, then a stable id so the order is deterministic.
    "net_lots": lambda r: (-abs(r["net_lots"]), -r["gross_lots"], r["login_sids"][0]),
    "gross_lots": lambda r: (-r["gross_lots"], -abs(r["net_lots"]), r["login_sids"][0]),
    "floating_profit": lambda r: (-r["floating_pl"], r["login_sids"][0]),
    "floating_loss": lambda r: (r["floating_pl"], r["login_sids"][0]),
}


def sort_rows(rows: list[dict], sort: str) -> list[dict]:
    assert sort in SORT_VALUES, sort
    return sorted(rows, key=_SORT_KEYS[sort])


def book_totals(rows: Iterable[dict]) -> dict:
    """Totals over the rows given — the caller passes the SCOPE-VISIBLE rows,
    never the unfiltered ones (a filtered list beside an unfiltered total
    leaks the difference; CLAUDE.md data-scope rule)."""
    rows = list(rows)
    buy = sum(r["buy_lots"] for r in rows)
    sell = sum(r["sell_lots"] for r in rows)
    return {
        "clients": len({r["client_id"] for r in rows}),
        "accounts": len({s for r in rows for s in r["login_sids"]}),
        "orders": sum(r["orders"] for r in rows),
        "buy_lots": round(buy, 3),
        "sell_lots": round(sell, 3),
        "net_lots": round(buy - sell, 3),
        "floating_pl": round(sum(r["floating_pl"] for r in rows), 2),
    }


def _default_connect(settings: Settings):
    return connect_readonly(settings, max_execution_ms=STATEMENT_BUDGET_MS, read_timeout=READ_TIMEOUT_S)


def fetch_open(
    settings: Settings,
    *,
    symbol: str,
    symbol_match: str,
    sids: Optional[Sequence[int]],
    connect: Callable[[Settings], Any] = _default_connect,
) -> dict:
    """``{"rows": [(account, symbol) rows], "truncated": bool}``. Raises on DB
    errors; the tool's ``run_sync_with_timeout`` converts them."""
    sids = tuple(sids) if sids else LIVE_SIDS
    sql = build_open_sql(symbol_match, sids)
    params: list[Any] = [symbol_param(symbol, symbol_match), *[int(s) for s in sids], MAX_FETCH_ROWS + 1]
    conn = connect(settings)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            raw = list(cur.fetchall())
    finally:
        try:
            conn.close()
        except Exception:
            pass
    truncated = len(raw) > MAX_FETCH_ROWS
    return {"rows": [normalise_row(r) for r in raw[:MAX_FETCH_ROWS]], "truncated": truncated}
