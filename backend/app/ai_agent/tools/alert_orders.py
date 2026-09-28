"""Tool 8 — ``get_alert_orders`` (docs/ai-agent/11-slice3-risk-control.md §2.2).

"Which orders are behind these alerts, and what do they look like?" — the
drill-down from ``get_risk_alerts`` (its ``sample_alert_ids``). Up to 3 alert
ids per call: 03 §3 allows each tool 2 calls per turn and "group by client,
then analyse the style" usually needs 2–3 representative alerts.

Steps per alert: read the stored alert (``get_alerts_by_ids`` on a read-only
connection) → single-subject scope check on its client (restricted callers) →
derive the order range from the band (table below) → fetch the orders with
prices through ``alert_orders_service`` (MySQL, three timeouts) → compute
DESCRIPTIVE features in Python. Features are statistics, never labels.

| band                              | order range                                                   |
|-----------------------------------|---------------------------------------------------------------|
| hedge / leverage                  | ``orders_json[].ticket`` exact; tickets not found (MT5 closed |
|                                   | positions are keyed by the exit deal) → open-time window of   |
|                                   | the missing ones, aligned on (symbol, open second)            |
| burst / martingale / qoc / qp     | same login, OPEN_TIME in [first_open, last_open] ±1s, same    |
|                                   | symbol; aligned against ``orders_json``                       |
| intraday 131–140                  | positions OPENED on ``trading_day`` (the rule's trades_today) |
| gap 71 (SO+AB)                    | ``l_ticket`` + ``c_ticket`` (open-time fallback on MT5); the  |
|                                   | C leg can be another client → scoped on its own               |
| gap 81 (excess profit)            | ``contributing_login_sids`` orders opened on ``window_date``  |
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import pymysql

from app.core import risk_monitor_db as rmdb
from app.core.config import Settings
from app.core.data_scope import cid_for_crm_user_ids
from app.core.sql_helpers import SID_MAP
from app.services import alert_orders_service as aos

from .common import (
    CallerCtx,
    Subject,
    connect_mysql,
    error_envelope,
    is_error,
    mysql_timeout_envelope,
    ok_envelope,
    run_sync_with_timeout,
    scope_denied,
    to_utc_iso,
    utc_now_iso,
)
from .risk_bands import rule_band_name, tab_for_rule

TOOL_NAME = "get_alert_orders"

MAX_ALERT_IDS = 3
MAX_ORDERS_PER_ALERT = 100
DEFAULT_ORDERS_PER_ALERT = 60
MAX_ORDERS_PER_CALL = 200
HOLD_SHORT_SEC = 60
ESCALATION_RATIO = 1.5

_TICKET_BANDS = ("hedge_open", "leverage_abuse")
_WINDOW_BANDS = ("burst_open", "martingale", "quick_open_close", "quick_profit")


def _login_sid(server: Any, login: Any) -> Optional[str]:
    sid = SID_MAP.get(str(server or ""))
    return f"{sid}-{login}" if sid is not None and login is not None else None


def _parse_utc(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _second_key(symbol: Any, t: Any) -> tuple[str, str]:
    dt = _parse_utc(t)
    return str(symbol or ""), dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else ""


# Matching stored alert orders to fetched rows happens on the MT WALL-CLOCK
# second: stored times were written with a fixed +03:00 while fetched
# `open_time` is DST-aware UTC, so comparing the two UTC strings would miss by
# exactly one hour all winter (cold review #1).
def _stored_mt_key(symbol: Any, stored_utc: Any) -> tuple[str, str]:
    mt = aos.stored_alert_time_to_mt(stored_utc)
    return str(symbol or ""), mt.strftime("%Y-%m-%d %H:%M:%S") if mt else ""


def _fetched_mt_key(row: dict) -> tuple[str, str]:
    return str(row.get("symbol") or ""), str(row.get("open_time_mt") or "")[:19]


# ── data access (monkeypatch targets) ────────────────────────────────────────


def _fetch_alerts(settings: Settings, ids: list[int]) -> list[dict]:
    conn = rmdb.open_readonly()
    try:
        return rmdb.get_alerts_by_ids(ids, conn=conn, include_user_id=True)
    finally:
        conn.close()


def _fetch_cids(settings: Settings, user_ids: list[int]) -> dict:
    return cid_for_crm_user_ids(settings, user_ids) if user_ids else {}


def _mysql(fn, ctx: CallerCtx):
    """Run one alert_orders_service call; a MySQL statement/read timeout
    becomes an ``upstream_timeout`` envelope, anything else propagates to
    run_sync_with_timeout (→ ``internal``)."""
    try:
        return fn()
    except pymysql.MySQLError as exc:
        env = mysql_timeout_envelope(exc, ctx)
        if env is not None:
            return env
        raise


def _fetch_by_tickets(settings: Settings, ctx: CallerCtx, sid: int, tickets: list[int]) -> Any:
    return _mysql(lambda: aos.fetch_orders_by_tickets(settings, sid=sid, tickets=tickets, connect=connect_mysql), ctx)


def _fetch_by_window(settings: Settings, ctx: CallerCtx, login_sids: list[str], first_utc: str, last_utc: str,
                     symbol: Optional[str], limit: int) -> Any:
    mt_from, mt_to = aos.mt_window_around(first_utc, last_utc, pad_seconds=1)
    return _mysql(lambda: aos.fetch_orders_by_open_window(
        settings, login_sids=login_sids, mt_from=mt_from, mt_to=mt_to, symbol=symbol, limit=limit,
        connect=connect_mysql), ctx)


def _fetch_at_seconds(settings: Settings, ctx: CallerCtx, login_sids: list[str], stored: list[dict],
                      symbol: Optional[str]) -> Any:
    """The alert's own stored open seconds (±1s for mirror jitter) → orders."""
    secs: set = set()
    for o in stored:
        mt = aos.stored_alert_time_to_mt(o.get("open_time"))
        if mt is not None:
            secs.update({mt - timedelta(seconds=1), mt, mt + timedelta(seconds=1)})
    return _mysql(lambda: aos.fetch_orders_at_open_seconds(
        settings, login_sids=login_sids, mt_seconds=sorted(secs), symbol=symbol, limit=aos.MAX_ORDERS_HARD,
        connect=connect_mysql), ctx)


def _fetch_by_day(settings: Settings, ctx: CallerCtx, login_sids: list[str], day: date, limit: int) -> Any:
    return _mysql(lambda: aos.fetch_orders_for_trading_day(
        settings, login_sids=login_sids, trading_day=day, limit=limit, connect=connect_mysql), ctx)


# ── features (descriptive statistics, not labels) ───────────────────────────


def _interval(o: dict, now: datetime) -> Optional[tuple[float, float]]:
    start = _parse_utc(o.get("open_time"))
    end = _parse_utc(o.get("close_time")) or now
    if start is None:
        return None
    return start.timestamp(), max(end.timestamp(), start.timestamp())


def _union_len(intervals: list[tuple[float, float]]) -> float:
    total, cur_s, cur_e = 0.0, None, None
    for s, e in sorted(intervals):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(a, b) for a, b in out]


def _intersection_len(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    a, b = _merge(a), _merge(b)
    i = j = 0
    total = 0.0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            total += hi - lo
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return total


def compute_features(orders: list[dict], *, now: Optional[datetime] = None) -> dict:
    """Descriptive statistics over the returned orders.

    * hold stats use CLOSED orders only (an open order's hold is still growing);
    * ``same_second_open_groups`` = groups of ≥2 orders opened in the same
      second (same symbol);
    * ``lot_escalation_steps`` = consecutive same-symbol same-direction opens
      where lots ≥ 1.5× the previous one; ``max_consecutive_lot_ratio`` the
      largest such ratio (1-2-4-8 → 3 steps, 2.0);
    * ``opposite_side_overlap_pct`` = time with BOTH a buy and a sell open on
      the same symbol / time with anything open, over all symbols (%);
    * ``win_rate`` / ``net_profit_usd`` over closed orders
      (net = profit + swap + commission).
    """
    now = now or datetime.now(timezone.utc)
    closed = [o for o in orders if not o.get("open")]
    holds = [int(o.get("hold_sec") or 0) for o in closed]
    feats: dict[str, Any] = {
        "orders": len(orders),
        "closed_orders": len(closed),
        "open_orders": len(orders) - len(closed),
        "symbols": sorted({str(o.get("symbol")) for o in orders}),
        "total_lots": round(sum(float(o.get("lots") or 0) for o in orders), 4),
        "median_hold_sec": int(statistics.median(holds)) if holds else None,
        "pct_hold_lt_60s": round(100.0 * sum(1 for h in holds if h < HOLD_SHORT_SEC) / len(holds), 1) if holds else None,
    }
    per_second = Counter(_second_key(o.get("symbol"), o.get("open_time")) for o in orders)
    feats["same_second_open_groups"] = sum(1 for n in per_second.values() if n >= 2)
    feats["orders_in_same_second_groups"] = sum(n for n in per_second.values() if n >= 2)

    seqs: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for o in sorted(orders, key=lambda x: (str(x.get("open_time") or ""), str(x.get("ticket_sid") or ""))):
        seqs[(str(o.get("symbol")), str(o.get("direction")))].append(o)
    steps, max_ratio = 0, None
    for seq in seqs.values():
        for prev, cur in zip(seq, seq[1:]):
            p, c = float(prev.get("lots") or 0), float(cur.get("lots") or 0)
            if p <= 0:
                continue
            ratio = c / p
            max_ratio = ratio if max_ratio is None else max(max_ratio, ratio)
            if ratio >= ESCALATION_RATIO:
                steps += 1
    feats["lot_escalation_steps"] = steps
    feats["max_consecutive_lot_ratio"] = round(max_ratio, 3) if max_ratio is not None else None

    both = union = 0.0
    by_symbol: dict[str, dict[str, list]] = defaultdict(lambda: {"buy": [], "sell": []})
    for o in orders:
        iv = _interval(o, now)
        if iv and str(o.get("direction")) in ("buy", "sell"):
            by_symbol[str(o.get("symbol"))][str(o.get("direction"))].append(iv)
    for sides in by_symbol.values():
        union += _union_len(sides["buy"] + sides["sell"])
        both += _intersection_len(sides["buy"], sides["sell"]) if sides["buy"] and sides["sell"] else 0.0
    feats["opposite_side_overlap_pct"] = round(100.0 * both / union, 1) if union > 0 else None

    wins = sum(1 for o in closed if float(o.get("profit_usd") or 0) > 0)
    feats["win_rate"] = round(wins / len(closed), 4) if closed else None
    feats["net_profit_usd"] = round(
        sum(float(o.get("profit_usd") or 0) + float(o.get("swap_usd") or 0) + float(o.get("commission_usd") or 0)
            for o in closed), 2)
    feats["floating_profit_usd"] = round(sum(float(o.get("profit_usd") or 0) for o in orders if o.get("open")), 2)
    return feats


def _project_order(o: dict) -> dict:
    return {
        "ticket_sid": o.get("ticket_sid"),
        "login_sid": o.get("login_sid"),
        "symbol": o.get("symbol"),
        "direction": o.get("direction"),
        "lots": o.get("lots"),
        "open_price": o.get("open_price"),
        "close_price": o.get("close_price"),
        "open_time": o.get("open_time"),
        "close_time": o.get("close_time"),
        "hold_sec": o.get("hold_sec"),
        "profit_usd": o.get("profit_usd"),
        "swap_usd": o.get("swap_usd"),
        "commission_usd": o.get("commission_usd"),
        # 02 §2.2: cent conversion stays visible (lots / money already /100).
        "is_cent": o.get("is_cent"),
        "open": o.get("open"),
    }


def _lots_close(stored_lots: Any, fetched: dict) -> bool:
    """orders_json keeps the RAW lots the rule saw; the service divides cent
    products by 100 — accept either reading."""
    try:
        s = float(stored_lots)
    except (TypeError, ValueError):
        return False
    f = float(fetched.get("lots") or 0)
    return abs(s - f) < 1e-6 or abs(s / 100.0 - f) < 1e-6


def match_stored(stored: list[dict], candidates: list[dict]) -> list[dict]:
    """Pair each stored order (orders_json) with one candidate row: same symbol
    and open second, preferring equal lots; each candidate used at most once.
    Returns the matched candidates (never drops or invents rows silently —
    the caller reports stored-vs-matched counts)."""
    pool: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for c in candidates:
        pool[_fetched_mt_key(c)].append(c)
    out = []
    for s in stored:
        bucket = pool.get(_stored_mt_key(s.get("symbol"), s.get("open_time"))) or []
        if not bucket:
            continue
        pick = next((c for c in bucket if _lots_close(s.get("lots"), c)), bucket[0])
        bucket.remove(pick)
        out.append(pick)
    return out


def _align(stored: list[dict], fetched: list[dict]) -> dict:
    """How many of the alert's stored orders have a fetched order with the same
    symbol + open second (lots preferred). Reported, never used to drop rows."""
    return {"orders_in_alert": len(stored), "matched": len(match_stored(stored, fetched))}


# ── per-band order fetch (sync; runs inside run_sync_with_timeout) ───────────


def _orders_for_alert(settings: Settings, ctx: CallerCtx, a: dict, band: str, limit: int,
                      c_leg_visible: bool) -> dict:
    """Returns {"orders", "orders_total", "notes", "alignment"?} or an error envelope."""
    notes: list[str] = []
    login_sid = _login_sid(a.get("server"), a.get("login"))
    sid = SID_MAP.get(str(a.get("server") or ""))
    stored = a.get("orders") or []

    def _dedupe(rows: list[dict]) -> list[dict]:
        seen, out = set(), []
        for o in rows:
            k = o.get("ticket_sid")
            if k in seen:
                continue
            seen.add(k)
            out.append(o)
        return sorted(out, key=lambda o: (str(o.get("open_time") or ""), str(o.get("ticket_sid") or "")))

    if band in _TICKET_BANDS:
        tickets = sorted({int(o["ticket"]) for o in stored if o.get("ticket")})
        found: list[dict] = []
        if tickets and sid is not None:
            r = _fetch_by_tickets(settings, ctx, sid, tickets)
            if is_error(r):
                return r
            found = list(r)
        got = {int(o.get("ticket") or 0) for o in found}
        missing = [o for o in stored if o.get("ticket") and int(o["ticket"]) not in got]
        if missing and login_sid:
            if any(o.get("open_time") for o in missing):
                r = _fetch_at_seconds(settings, ctx, [login_sid], missing, a.get("symbol") or None)
                if is_error(r):
                    return r
                window_rows, _ = r
                matched = match_stored(missing, window_rows)
                found.extend(matched)
                notes.append(f"{len(missing)} stored ticket(s) had no row under that ticket (MT5 closed positions are "
                             f"keyed by the exit deal); {len(matched)} matched by symbol + open second + lots instead"
                             + ("" if len(matched) == len(missing) else f", {len(missing) - len(matched)} unmatched")
                             + ".")
        rows = _dedupe(found)
        align = _align(stored, rows)
        return {"orders": rows[:limit], "orders_total": len(rows), "notes": notes, "alignment": align}

    if band in _WINDOW_BANDS:
        if not (login_sid and a.get("first_open") and a.get("last_open")):
            return {"orders": [], "orders_total": 0, "notes": ["alert has no open-time window stored"]}
        # Fetch the whole window (hard cap), then return the alert's OWN orders
        # (matched to orders_json) rather than the first N rows of the window:
        # a martingale anchor can be weeks older than the last add, so the
        # window holds many unrelated orders.
        # With stored orders, fetch exactly their seconds (cold review #4: a
        # weeks-wide martingale window capped at 200 rows ascending would drop
        # the newest adds). Only without stored orders fall back to the window.
        if stored and any(o.get("open_time") for o in stored):
            r = _fetch_at_seconds(settings, ctx, [login_sid], stored, a.get("symbol") or None)
        else:
            r = _fetch_by_window(settings, ctx, [login_sid], a["first_open"], a["last_open"],
                                 a.get("symbol") or None, aos.MAX_ORDERS_HARD)
        if is_error(r):
            return r
        rows, window_total = r
        matched = match_stored(stored, rows) if stored else []
        align = {"orders_in_alert": len(stored), "matched": len(matched), "window_orders_total": window_total}
        if matched:
            chosen = sorted(matched, key=lambda o: (str(o.get("open_time") or ""), str(o.get("ticket_sid") or "")))
            total = len(stored)
            if len(matched) != len(stored):
                notes.append(f"{len(stored) - len(matched)} of the alert's {len(stored)} stored order(s) had no "
                             "order with the same symbol + open second (partial close / mirror lag).")
            if window_total > len(matched):
                notes.append(f"{window_total} order(s) on {a.get('symbol')} opened at the alert's own seconds (±1s); "
                             "only the ones matching the alert's stored orders are listed.")
        else:
            chosen, total = rows, window_total
            notes.append(f"No stored order could be matched; listing every order in the window ±1s on {a.get('symbol')}.")
        return {"orders": chosen[:limit], "orders_total": total, "notes": notes, "alignment": align}

    if band == "intraday_return":
        day = a.get("trading_day")
        if not (login_sid and day):
            return {"orders": [], "orders_total": 0, "notes": ["alert has no trading_day stored"]}
        r = _fetch_by_day(settings, ctx, [login_sid], date.fromisoformat(str(day)[:10]), limit)
        if is_error(r):
            return r
        rows, total = r
        if a.get("trades_today") is not None and int(a["trades_today"]) != total:
            notes.append(f"The rule counted trades_today={a['trades_today']}; the mt4_trades mirror has {total} "
                         "positions opened that MT day (MT5 is read from mt5_deals by the rule — partial closes can differ).")
        notes.append("Overnight positions (opened before trading_day) feed the formula's carried_gain but are NOT listed.")
        return {"orders": rows, "orders_total": total, "notes": notes}

    if band == "gap_trade_so_ab":
        rows: list[dict] = []
        legs = [("L", a.get("l_login_sid"), a.get("l_ticket"), a.get("l_open_time"), a.get("l_lots"))]
        if c_leg_visible:
            legs.append(("C", a.get("c_login_sid"), a.get("c_ticket"), a.get("c_open_time"), a.get("c_lots")))
        for leg, ls, ticket, open_utc, leg_lots in legs:
            if not ls or not ticket:
                continue
            leg_sid = int(str(ls).split("-", 1)[0])
            r = _fetch_by_tickets(settings, ctx, leg_sid, [int(ticket)])
            if is_error(r):
                return r
            leg_rows = [o for o in r if int(o.get("ticket") or 0) == int(ticket)]
            if not leg_rows and open_utc:
                w = _fetch_by_window(settings, ctx, [str(ls)], open_utc, open_utc, a.get("symbol") or None, 10)
                if is_error(w):
                    return w
                leg_rows = match_stored(
                    [{"symbol": a.get("symbol"), "open_time": open_utc, "lots": leg_lots}], w[0])
                notes.append(f"{leg} leg ticket not found by ticket (MT5 closed position); "
                             + ("matched by symbol + open second + lots." if leg_rows else "no order matched — leg missing."))
            for o in leg_rows:
                rows.append({**o, "leg": leg})
        return {"orders": rows[:limit], "orders_total": len(rows), "notes": notes}

    if band == "gap_trade_profit":
        raw = a.get("contributing_login_sids") or ""
        lss = [s.strip() for s in str(raw).split(",") if s.strip()]
        day = a.get("window_date")
        if not (lss and day):
            return {"orders": [], "orders_total": 0, "notes": ["alert has no contributing accounts / window_date"]}
        r = _fetch_by_day(settings, ctx, lss, date.fromisoformat(str(day)[:10]), limit)
        if is_error(r):
            return r
        rows, total = r
        notes.append("Orders OPENED on window_date on the contributing accounts; the rule measures the gap window's "
                     "excess profit, so this is the day's context, not an exact replay.")
        return {"orders": rows, "orders_total": total, "notes": notes}

    return {"orders": [], "orders_total": 0, "notes": [f"band {band} has no order drill-down (retired / unknown)"]}


# ── the tool ─────────────────────────────────────────────────────────────────


async def get_alert_orders(ctx: CallerCtx, alert_ids: Any, max_orders_per_alert: Any = DEFAULT_ORDERS_PER_ALERT) -> dict:
    if not isinstance(alert_ids, (list, tuple)) or not alert_ids:
        return error_envelope("invalid_argument", f"alert_ids must be a list of 1..{MAX_ALERT_IDS} integers")
    try:
        ids = list(dict.fromkeys(int(v) for v in alert_ids))
    except (TypeError, ValueError):
        return error_envelope("invalid_argument", "alert_ids must be integers")
    if len(ids) > MAX_ALERT_IDS:
        return error_envelope("invalid_argument", f"at most {MAX_ALERT_IDS} alert_ids per call", {"given": len(ids)})
    try:
        per_alert = int(max_orders_per_alert)
    except (TypeError, ValueError):
        return error_envelope("invalid_argument", "max_orders_per_alert must be an integer")
    if not 1 <= per_alert <= MAX_ORDERS_PER_ALERT:
        return error_envelope("invalid_argument", f"max_orders_per_alert must be between 1 and {MAX_ORDERS_PER_ALERT}")

    alerts = await run_sync_with_timeout(_fetch_alerts, ctx.settings, ids, ctx=ctx)
    if is_error(alerts):
        return alerts
    by_id = {int(a["id"]): a for a in alerts}
    missing = [i for i in ids if i not in by_id]
    if not by_id:
        return error_envelope(
            "subject_not_found",
            "No such alert id. alert_events keeps 30 days — an older alert has passed its retention period.",
            {"alert_ids": ids},
        )

    # ── scope: the alert's own client (single-subject rule), then the gap C leg
    c_visible: dict[int, bool] = {}
    if ctx.scope is not None:
        own = [int(a["user_id"]) for a in by_id.values() if a.get("user_id") is not None]
        cus = [int(a["c_userid"]) for a in by_id.values() if a.get("c_userid") is not None]
        cids = await run_sync_with_timeout(_fetch_cids, ctx.settings, sorted(set(own + cus)), ctx=ctx)
        if is_error(cids):
            return cids
        for aid in ids:
            a = by_id.get(aid)
            if a is None:
                continue
            uid = a.get("user_id")
            if uid is None or cids.get(int(uid)) is None or cids.get(int(uid)) not in ctx.scope:
                subj = (Subject("client_id", str(int(uid))) if uid is not None
                        else Subject("login_sid", _login_sid(a.get("server"), a.get("login")) or "0-0"))
                return scope_denied(ctx, subj, tool=TOOL_NAME)
        c_visible = {u: (cids.get(u) is not None and cids.get(u) in ctx.scope) for u in cus}

    remaining = MAX_ORDERS_PER_CALL
    out_alerts: list[dict] = []
    truncated = False
    all_notes: list[str] = []
    for aid in ids:
        a = by_id.get(aid)
        if a is None:
            continue
        band = rule_band_name(a.get("rule_id"))
        c_ok = True
        if ctx.scope is not None and band == "gap_trade_so_ab":
            cu = a.get("c_userid")
            c_ok = cu is not None and c_visible.get(int(cu), False)
        limit = max(0, min(per_alert, remaining))
        if limit == 0:
            truncated = True
            fetched = {"orders": [], "orders_total": None, "notes": ["per-call order budget (200) exhausted"]}
        else:
            fetched = await run_sync_with_timeout(_orders_for_alert, ctx.settings, ctx, a, band, limit, c_ok, ctx=ctx)
            if is_error(fetched):
                return fetched
        orders = list(fetched["orders"])[:limit]
        remaining -= len(orders)
        total = fetched.get("orders_total")
        if total is not None and total > len(orders):
            truncated = True
        entry: dict[str, Any] = {
            "alert_id": aid,
            "rule_id": a.get("rule_id"),
            "rule_name": band,
            "rule_label": a.get("rule_label"),
            "tab": tab_for_rule(a.get("rule_id")),
            "fired_at": to_utc_iso(a.get("scanned_at")),
            "login_sid": _login_sid(a.get("server"), a.get("login")),
            "client_id": a.get("user_id"),
            "orders": [{**_project_order(o), **({"leg": o["leg"]} if "leg" in o else {})} for o in orders],
            "orders_total": total,
            "features": compute_features(orders),
            "notes": fetched.get("notes") or [],
        }
        if fetched.get("alignment") is not None:
            entry["alignment"] = fetched["alignment"]
        if band == "gap_trade_so_ab":
            entry["legs_masked_by_scope"] = 0 if c_ok else 1
        out_alerts.append(entry)

    if missing:
        all_notes.append(f"alert id(s) {missing} not found (30-day retention or wrong id).")
    data = {"alerts": out_alerts, "alerts_not_found": missing, "verdict": None}
    caveats = [
        "signal ≠ violation: `features` are descriptive statistics of the orders, not a finding. Describe them "
        "(e.g. '4 same-direction adds at 2.0×'), never name the behaviour as a verdict.",
        "Money in USD: cent accounts (CURRENCY=CEN) and cent products (.cent/.kcmc) already /100; lots /100 only for "
        "cent products; XAUUSD.c is NOT cent.",
        "Direction is the position side: sid=5 closed rows store the exit side in CMD and were normalised.",
        "Times are UTC ISO; demo/test accounts and employees are excluded.",
        "Order range per band: hedge/leverage by stored ticket (MT5 closed positions matched by symbol + open "
        "second); burst/martingale/quick-OC/quick-profit by the alert's first_open..last_open ±1s on its symbol; "
        "intraday-return = positions opened on trading_day; gap 71 = the L and C tickets; gap 81 = contributing "
        "accounts' orders opened on window_date.",
        "features are computed over the RETURNED orders (after the per-alert cap); hold stats and win_rate use closed "
        "orders only; net_profit_usd = profit + swap + commission of closed orders.",
        "One account's orders cannot show an AB/opposite-account pair (only gap 71 stores the counterpart). Rebates "
        "are not here — use get_client_overview for rebate_all.",
        f"At most {MAX_ORDERS_PER_ALERT} orders per alert (default {DEFAULT_ORDERS_PER_ALERT}) and "
        f"{MAX_ORDERS_PER_CALL} per call; orders_total is the uncapped count.",
    ]
    if ctx.scope is not None and any(e.get("legs_masked_by_scope") for e in out_alerts):
        caveats.append("A gap-trade counterpart (C) leg belongs to a client outside your data scope and was removed "
                       "whole (legs_masked_by_scope). Do not infer it.")
    caveats.extend(all_notes)
    definition = {
        "summary": "signal ≠ violation. The orders behind up to 3 Risk Monitor alerts, with prices, from the "
        "fxbackoffice replica, plus descriptive features per alert.",
        "caveats": caveats,
        "doc": "docs/ai-agent/11-slice3-risk-control.md §2.2; app/services/alert_orders_service.py",
    }
    source = {
        "service": "app.services.alert_orders_service + app.core.risk_monitor_db",
        "function": "get_alerts_by_ids / fetch_orders_*",
        "as_of": utc_now_iso(),
    }
    return ok_envelope(data, definition=definition, source=source, ctx=ctx, truncated=truncated)
