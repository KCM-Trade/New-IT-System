"""Orders behind a Risk Monitor alert (OPT-0066 slice 3, ``get_alert_orders``).

``alert_events`` stores what a rule SAW (counts, lots, a few tickets) but no
prices and, for most bands, no tickets at all. This module is the one place
that goes back to ``fxbackoffice.mt4_trades`` for the orders themselves, with
prices, so the agent can describe the trading pattern behind an alert. It is
shared by the main API and the ai-agent container; the MySQL connection is
injectable (``connect=``) exactly like ``rank_accounts_service``.

口径 (nothing invented — each rule is an existing service's):

* universe   — ``login_ip_trade_profit_service._ACCOUNT_FILTER_SQL`` (demo /
               test by GROUP/NAME) + ``INNER JOIN users`` with
               ``isEmployee = 0`` + ``sid IN (1,5,6)`` + ``CMD IN (0,1)`` +
               ``isDeleted`` — the ``trade_activity_service`` universe;
* cent       — money ÷100 when the ACCOUNT currency is CEN or the SYMBOL is a
               cent product (``.cent`` / ``.kcmc``; ``XAUUSD.c`` is NOT); lots
               ÷100 only for cent SYMBOLS (``trade_activity_service.normalise_row``);
* direction  — ``window_scan_service.resolve_direction`` (sid=5 CLOSED rows
               store the exit side in CMD);
* open rows  — ``closeDate = '1970-01-01'`` / CLOSE_TIME at the 1970 sentinel;
* time       — ``OPEN_TIME`` / ``CLOSE_TIME`` are MT wall clock; converted to
               UTC with the DST-aware ``rule_intraday_return_service._local_to_utc``.

⚠ Ticket semantics differ by server (verified 2026-09-28 on the replica):

* MT4 (sid 1 / 6): ``ticketSid = '{sid}-{TICKET}'`` and the alert's
  ``orders_json[].ticket`` IS that ticket → exact lookup works.
* MT5 (sid 5): an OPEN position is ``'5-p{position}'``; a CLOSED position's
  row is keyed by the **exit deal** ticket (``'5-{exit_deal}'``), which is not
  the position id the alert recorded. Example: alert ticket 37239458 (opened
  05:09:03 MT) has no row under either key; the closed row is ``5-37239474``.
  So ``fetch_orders_by_tickets`` on sid 5 only finds still-open positions (and
  the rare closed row whose exit ticket coincides). Callers must detect the
  missing tickets and fall back to ``fetch_orders_by_open_window`` + an
  ``(symbol, lots, open_time)`` alignment for MT5 alerts.

EXPLAIN (probed 2026-09-28 on the replica). mt4_trades indexes: PRIMARY
(ticketSid), ``loginSid``, ``IDX_IB_COMMISSION2`` (loginSid, closeDate),
``IDX_OPEN_DATE`` (openDate), ``INDEX_CLOSEDATE`` (closeDate):

* by tickets     → t range PRIMARY (rows 2), mu eq_ref LOGIN_SID, u eq_ref
                   PRIMARY. 2 tickets: 0.04s.
* by open window → mu const LOGIN_SID, u const PRIMARY, t index_merge
                   intersect(IDX_OPEN_DATE, loginSid) rows≈40. 0.11s.
* trading day    → mu range LOGIN_SID, t ref IDX_IB_COMMISSION2 (loginSid
                   prefix) rows≈230, u eq_ref. 134 orders counted: 0.11s.
No full scan on any path; every path is bounded by the account list.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional, Sequence

from app.core.config import Settings
from app.core.mysql_readonly import connect_readonly
from app.core.sql_helpers import BROKER_TZ_OFFSET
from app.services.login_ip_trade_profit_service import _ACCOUNT_FILTER_SQL
from app.services.rule_intraday_return_service import _local_to_utc
from app.services.window_scan_service import (
    is_cent_symbol,
    is_open_trade,
    resolve_direction,
)

MAX_ORDERS_HARD = 200
LIVE_SIDS = (1, 5, 6)
STATEMENT_BUDGET_MS = 15_000

_COLUMNS = """
    t.ticketSid AS ticket_sid, t.TICKET AS ticket, t.sid AS sid, t.LOGIN AS login,
    t.loginSid AS login_sid, t.SYMBOL AS symbol, t.CMD AS cmd, t.lots AS lots,
    t.OPEN_PRICE AS open_price, t.CLOSE_PRICE AS close_price,
    t.OPEN_TIME AS open_time, t.CLOSE_TIME AS close_time, t.closeDate AS close_date,
    t.PROFIT AS profit, t.SWAPS AS swaps, t.COMMISSION AS commission,
    t.totalProfit AS total_profit, UPPER(mu.CURRENCY) AS currency
"""

_FROM = """
    FROM fxbackoffice.mt4_trades t
    JOIN fxbackoffice.mt4_users mu ON mu.loginSid = t.loginSid
    JOIN fxbackoffice.users u ON u.id = mu.userId AND COALESCE(u.isEmployee, 0) = 0
"""

_UNIVERSE = """
      AND t.sid IN (1, 5, 6)
      AND t.CMD IN (0, 1)
      AND (t.isDeleted = 0 OR t.isDeleted IS NULL)
""" + _ACCOUNT_FILTER_SQL



def _fixed_tz(offset: str) -> timezone:
    sign = -1 if offset.startswith("-") else 1
    hh, mm = offset.lstrip("+-").split(":")
    return timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))


# The fixed offset the alert detectors used when they stored alert times.
_DETECTOR_TZ = _fixed_tz(BROKER_TZ_OFFSET)

def _placeholders(n: int) -> str:
    return ", ".join(["%s"] * n)


def _default_connect(settings: Settings):
    """Replica connection with the db-timeout-guard three timeouts."""
    return connect_readonly(settings, max_execution_ms=STATEMENT_BUDGET_MS)


def _f(v: Any) -> float:
    return float(v) if v is not None else 0.0


def _utc_iso(mt_wall: datetime) -> str:
    return _local_to_utc(mt_wall).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalise_order(raw: dict, *, as_of_utc: Optional[datetime] = None) -> dict:
    """One mt4_trades row → the contract order dict (all keys always present)."""
    symbol = str(raw.get("symbol") or "")
    cent_symbol = is_cent_symbol(symbol)
    cent_account = (raw.get("currency") or "") == "CEN"
    money_div = 100.0 if (cent_symbol or cent_account) else 1.0
    lots_div = 100.0 if cent_symbol else 1.0

    sid = int(raw.get("sid") or 0)
    login = int(raw.get("login") or 0)
    open_time: datetime = raw["open_time"]
    close_time = raw.get("close_time")
    still_open = is_open_trade(close_time)

    open_utc = _local_to_utc(open_time)
    if still_open:
        now = as_of_utc or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        hold = max((now - open_utc).total_seconds(), 0.0)
        close_iso = None
        close_price = None
    else:
        hold = max((close_time - open_time).total_seconds(), 0.0)
        close_iso = _utc_iso(close_time)
        close_price = round(float(raw["close_price"]), 6) if raw.get("close_price") is not None else None

    try:
        ticket = int(str(raw.get("ticket") or "0").lstrip("p"))
    except ValueError:
        ticket = 0
    return {
        "ticket_sid": str(raw.get("ticket_sid") or ""),
        "ticket": ticket,
        "sid": sid,
        "login": login,
        "login_sid": f"{sid}-{login}",
        "symbol": symbol,
        "direction": resolve_direction(raw.get("cmd") or 0, sid, not still_open),
        "lots": round(_f(raw.get("lots")) / lots_div, 4),
        "open_price": round(_f(raw.get("open_price")), 6),
        "close_price": close_price,
        "open_time": open_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "close_time": close_iso,
        "open_time_mt": open_time.strftime("%Y-%m-%d %H:%M:%S"),
        "hold_sec": int(hold),
        "profit_usd": round(_f(raw.get("profit")) / money_div, 2),
        "swap_usd": round(_f(raw.get("swaps")) / money_div, 2),
        "commission_usd": round(_f(raw.get("commission")) / money_div, 2),
        "is_cent": cent_symbol or cent_account,
        "open": still_open,
    }


def _run(conn, sql: str, params: Sequence[Any]) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, list(params))
        return list(cur.fetchall())


def _clean_login_sids(login_sids: Sequence[str]) -> list[str]:
    out = []
    for ls in login_sids:
        s = str(ls).strip()
        sid_s, _, login_s = s.partition("-")
        if sid_s.isdigit() and login_s.isdigit() and int(sid_s) in LIVE_SIDS:
            out.append(f"{int(sid_s)}-{int(login_s)}")
    return sorted(set(out))


def fetch_orders_by_tickets(
    settings: Settings,
    *,
    sid: int,
    tickets: Sequence[int],
    connect: Optional[Callable[[Settings], Any]] = None,
    as_of_utc: Optional[datetime] = None,
) -> list[dict]:
    """Exact lookup by ticket on one server (hedge / leverage ``orders_json``,
    gap-SO ``l_ticket`` / ``c_ticket``). Primary-key range on ``ticketSid``.

    Candidates per ticket: ``'{sid}-{t}'`` and, on sid 5, ``'5-p{t}'`` (open
    MT5 position). A CLOSED MT5 position is keyed by its exit deal and will
    NOT be found — see the module docstring; the caller compares the returned
    ``ticket`` / ``ticket_sid`` against what it asked for. Capped at
    ``MAX_ORDERS_HARD`` tickets.
    """
    sid = int(sid)
    if sid not in LIVE_SIDS:
        raise ValueError(f"sid {sid} is not a live server")
    uniq = sorted({int(t) for t in tickets})[:MAX_ORDERS_HARD]
    if not uniq:
        return []
    keys = [f"{sid}-{t}" for t in uniq]
    if sid == 5:
        keys += [f"5-p{t}" for t in uniq]
    sql = (
        f"SELECT {_COLUMNS} {_FROM} WHERE t.ticketSid IN ({_placeholders(len(keys))})"
        f"{_UNIVERSE} ORDER BY t.OPEN_TIME, t.ticketSid LIMIT %s"
    )
    conn = (connect or _default_connect)(settings)
    try:
        rows = _run(conn, sql, [*keys, MAX_ORDERS_HARD])
    finally:
        conn.close()
    return [normalise_order(r, as_of_utc=as_of_utc) for r in rows]


def _fetch_capped(
    settings: Settings,
    where_sql: str,
    params: list[Any],
    *,
    limit: int,
    connect: Optional[Callable[[Settings], Any]],
    as_of_utc: Optional[datetime],
) -> tuple[list[dict], int]:
    limit = max(1, min(int(limit), MAX_ORDERS_HARD))
    conn = (connect or _default_connect)(settings)
    try:
        total_rows = _run(conn, f"SELECT COUNT(*) AS n {_FROM} WHERE {where_sql}{_UNIVERSE}", params)
        total = int((total_rows[0] or {}).get("n") or 0) if total_rows else 0
        rows = _run(
            conn,
            f"SELECT {_COLUMNS} {_FROM} WHERE {where_sql}{_UNIVERSE} "
            f"ORDER BY t.OPEN_TIME, t.ticketSid LIMIT %s",
            [*params, limit],
        )
    finally:
        conn.close()
    return [normalise_order(r, as_of_utc=as_of_utc) for r in rows], total


def fetch_orders_by_open_window(
    settings: Settings,
    *,
    login_sids: Sequence[str],
    mt_from: datetime,
    mt_to: datetime,
    symbol: Optional[str] = None,
    limit: int = 100,
    connect: Optional[Callable[[Settings], Any]] = None,
    as_of_utc: Optional[datetime] = None,
) -> tuple[list[dict], int]:
    """Orders of these accounts whose OPEN_TIME (MT wall clock) is in
    ``[mt_from, mt_to]`` inclusive — burst / martingale / quick-OC /
    quick-profit alerts, which store ``first_open`` / ``last_open`` but no
    tickets (the caller widens by ±1s and aligns on symbol/lots/open_time).
    ``openDate BETWEEN`` is added so the MT day column narrows the scan too.
    Returns ``(orders[:limit], uncapped_total)``, ordered by OPEN_TIME.
    """
    ls = _clean_login_sids(login_sids)
    if not ls:
        return [], 0
    if mt_to < mt_from:
        raise ValueError("mt_to is before mt_from")
    where = (
        f"t.loginSid IN ({_placeholders(len(ls))})"
        " AND t.openDate BETWEEN %s AND %s AND t.OPEN_TIME BETWEEN %s AND %s"
    )
    params: list[Any] = [*ls, mt_from.date(), mt_to.date(), mt_from, mt_to]
    if symbol:
        where += " AND t.SYMBOL = %s"
        params.append(symbol)
    return _fetch_capped(settings, where, params, limit=limit, connect=connect, as_of_utc=as_of_utc)


def fetch_orders_at_open_seconds(
    settings: Settings,
    *,
    login_sids: Sequence[str],
    mt_seconds: Sequence[datetime],
    symbol: Optional[str] = None,
    limit: int = MAX_ORDERS_HARD,
    connect: Optional[Callable[[Settings], Any]] = None,
    as_of_utc: Optional[datetime] = None,
) -> tuple[list[dict], int]:
    """Orders of these accounts opened at exactly one of ``mt_seconds`` (MT
    wall clock, ±``pad`` handled by the caller passing neighbouring seconds).

    Exists because an alert's ``[first_open, last_open]`` can span weeks (a
    martingale anchor + today's add): fetching that window capped at 200 rows
    by OPEN_TIME ascending silently drops the NEWEST adds — the very orders
    the alert is about (OPT-0066 cold review #4). Asking for the alert's own
    seconds is exact and small. ``openDate IN`` keeps IDX_OPEN_DATE usable.
    """
    ls = _clean_login_sids(login_sids)
    secs = sorted({s.replace(microsecond=0) for s in mt_seconds if s is not None})
    if not ls or not secs:
        return [], 0
    days = sorted({s.date() for s in secs})
    where = (
        f"t.loginSid IN ({_placeholders(len(ls))})"
        f" AND t.openDate IN ({_placeholders(len(days))})"
        f" AND t.OPEN_TIME IN ({_placeholders(len(secs))})"
    )
    params: list[Any] = [*ls, *days, *secs]
    if symbol:
        where += " AND t.SYMBOL = %s"
        params.append(symbol)
    return _fetch_capped(settings, where, params, limit=limit, connect=connect, as_of_utc=as_of_utc)


def fetch_orders_for_trading_day(
    settings: Settings,
    *,
    login_sids: Sequence[str],
    trading_day: date,
    limit: int = 100,
    connect: Optional[Callable[[Settings], Any]] = None,
    as_of_utc: Optional[datetime] = None,
) -> tuple[list[dict], int]:
    """Positions OPENED on MT trading day ``trading_day`` (closed since or
    still open) — the set the intraday-return rule counts as ``trades_today``
    (rule_intraday_return_service.compute_behavior_features: "trades_today /
    lots_today / first_open / last_open: positions opened today", i.e.
    ``open_time >= day_start``). ``openDate`` is the MT-server-day column, so
    no timezone arithmetic is needed.

    Caveats for the caller: the rule reads MT5 from ``mt5_deals`` while this
    reads the ``mt4_trades`` mirror, so counts can drift slightly (partial
    closes); OVERNIGHT positions (which feed ``carried_gain`` in formula v3)
    are NOT included — they were opened on an earlier day.
    """
    ls = _clean_login_sids(login_sids)
    if not ls:
        return [], 0
    where = f"t.loginSid IN ({_placeholders(len(ls))}) AND t.openDate = %s"
    params: list[Any] = [*ls, trading_day]
    return _fetch_capped(settings, where, params, limit=limit, connect=connect, as_of_utc=as_of_utc)


def stored_alert_time_to_mt(value: Any) -> Optional[datetime]:
    """A time stored on an alert row → the naive MT wall clock it came from.

    ⚠ NOT DST-aware on purpose. The detectors write ``first_open`` /
    ``last_open`` / ``orders_json[].open_time`` through
    ``sql_helpers.broker_time_to_utc_iso`` (``CONVERT_TZ(col, BROKER_TZ_OFFSET,
    '+00:00')``, a FIXED +03:00) and gap SO+AB writes ``l_/c_open_time`` via
    ``_iso_z`` (MT − 3h, fixed). Undoing that exact offset recovers the MT
    wall-clock second the order really has in ``mt4_trades.OPEN_TIME``, in
    winter too. Converting with the DST-aware ``MT_SERVER_TZ`` instead would
    land one hour early from November to March and every window would come
    back empty (OPT-0066 cold review #1).
    """
    if value in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_DETECTOR_TZ).replace(tzinfo=None)


def mt_window_around(first_open_utc: str, last_open_utc: str, *, pad_seconds: int = 1) -> tuple[datetime, datetime]:
    """Alert ``first_open`` / ``last_open`` (UTC ISO as stored) → naive MT
    wall-clock bounds widened by ``pad_seconds`` (see stored_alert_time_to_mt)."""
    lo = stored_alert_time_to_mt(first_open_utc)
    hi = stored_alert_time_to_mt(last_open_utc)
    if lo is None or hi is None:
        raise ValueError(f"unparseable alert times {first_open_utc!r} / {last_open_utc!r}")
    pad = timedelta(seconds=pad_seconds)
    return lo - pad, hi + pad
