"""Tool 9 — ``get_window_scan`` (docs/ai-agent/11-slice3-risk-control.md §2.3).

"Who opened (or closed) positions within ±N minutes of this moment, and who
made money on it?" — the ``/window-scan`` page's own query
(``window_scan_service.query_window_scan``), run with the agent's read-only
MySQL connection (``connect=``: MAX_EXECUTION_TIME 15s / read / connect
timeouts). The page and this tool therefore answer the same question the same
way: the service picks the rows, rolls them up per client, keeps clients whose
CLOSED rollup is > 0 and enriches them with lifetime money legs from PG.
Sorting and ``top_n`` are applied here, after the service returns.

Typical chain: ``get_economic_calendar`` gives a release's HK time, this tool
scans around it.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pymysql

from app.core.config import Settings
from app.core.data_scope import cid_for_crm_user_ids
from app.services import window_scan_service as wss

from .common import (
    MAX_ROWS,
    CallerCtx,
    connect_mysql,
    error_envelope,
    is_error,
    mysql_timeout_envelope,
    ok_envelope,
    run_sync_with_timeout,
    utc_now_iso,
)

TOOL_NAME = "get_window_scan"

MAX_TOP_N = 50
MAX_ANCHOR_AGE_DAYS = 366
TRADES_MAX_TOP_N = 5
SORTS = {"net_gain": "net_gain", "closed_profit": "closed_profit", "lots": "lots_sum"}
_ANCHOR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})$")
_HK = ZoneInfo("Asia/Hong_Kong")

_ROW_KEYS = (
    "client_id", "login_sids", "country", "status_tag", "closed_orders", "open_orders", "lots_sum",
    "closed_profit", "floating_profit", "win_rate", "avg_hold_sec", "symbols", "net_deposit",
    "total_rebate", "pl_plus_rebate", "net_gain",
)
_TRADE_KEYS = (
    "ticket_sid", "login_sid", "symbol", "status", "direction", "lots", "is_cent",
    "open_time_utc", "close_time_utc", "hold_sec", "hold_bucket", "profit",
)


# ── data access (monkeypatch targets) ────────────────────────────────────────


def _fetch_scan(settings: Settings, ctx: CallerCtx, **kwargs: Any) -> Any:
    try:
        return wss.query_window_scan(settings, connect=connect_mysql, **kwargs)
    except ValueError as exc:
        return error_envelope("invalid_argument", str(exc))
    except pymysql.MySQLError as exc:
        env = mysql_timeout_envelope(exc, ctx)
        if env is not None:
            return env
        raise


def _fetch_cids(settings: Settings, user_ids: list[int]) -> dict:
    return cid_for_crm_user_ids(settings, user_ids) if user_ids else {}


def _round(v: Any) -> Any:
    return round(v, 4) if isinstance(v, float) else v


async def get_window_scan(
    ctx: CallerCtx,
    anchor_hk: Any,
    window_min: Any = 5,
    scan_by: Any = "open",
    hold_bucket: Any = "total",
    sids: Any = None,
    symbol: Any = None,
    top_n: Any = 20,
    sort: Any = "closed_profit",
    include_trades: Any = False,
) -> dict:
    m = _ANCHOR_RE.match(str(anchor_hk or "").strip())
    if not m:
        return error_envelope("invalid_argument", "anchor_hk must be 'YYYY-MM-DD HH:MM' (Hong Kong wall clock)")
    anchor = f"{m.group(1)}T{m.group(2)}"
    try:
        anchor_dt = datetime.strptime(anchor, "%Y-%m-%dT%H:%M").replace(tzinfo=_HK)
    except ValueError:
        return error_envelope("invalid_argument", "anchor_hk is not a real date/time")
    now_hk = datetime.now(_HK)
    if anchor_dt > now_hk + timedelta(minutes=1) or now_hk - anchor_dt > timedelta(days=MAX_ANCHOR_AGE_DAYS):
        return error_envelope(
            "range_too_wide",
            f"anchor_hk must be in the past and within {MAX_ANCHOR_AGE_DAYS} days.",
            {"anchor_hk": str(anchor_hk)},
        )

    try:
        window_v = int(window_min)
    except (TypeError, ValueError):
        return error_envelope("invalid_argument", "window_min must be an integer")
    if window_v not in wss.ALLOWED_WINDOW_MIN:
        return error_envelope("invalid_argument", f"window_min must be one of {list(wss.ALLOWED_WINDOW_MIN)}")
    scan_by = str(scan_by or "open").strip().lower()
    if scan_by not in wss.ALLOWED_SCAN_BY:
        return error_envelope("invalid_argument", f"scan_by must be one of {list(wss.ALLOWED_SCAN_BY)}")
    hold_bucket = str(hold_bucket or "total").strip().lower()
    if hold_bucket not in wss.HOLD_BUCKETS:
        return error_envelope("invalid_argument", f"hold_bucket must be one of {list(wss.HOLD_BUCKETS)}")
    sort = str(sort or "closed_profit").strip().lower()
    if sort not in SORTS:
        return error_envelope("invalid_argument", f"sort must be one of {list(SORTS)}")
    try:
        top_n_v = int(top_n)
    except (TypeError, ValueError):
        return error_envelope("invalid_argument", "top_n must be an integer")
    if not 1 <= top_n_v <= MAX_TOP_N:
        return error_envelope("invalid_argument", f"top_n must be between 1 and {MAX_TOP_N}")
    sid_list: Optional[list[int]] = None
    if sids is not None:
        if not isinstance(sids, (list, tuple)) or not sids:
            return error_envelope("invalid_argument", "sids must be a non-empty list of server ids or null")
        try:
            sid_list = sorted({int(s) for s in sids})
        except (TypeError, ValueError):
            return error_envelope("invalid_argument", "sids must be integers")
        if any(s not in wss.ALLOWED_SIDS for s in sid_list):
            return error_envelope("invalid_argument", f"sids must be a subset of {list(wss.ALLOWED_SIDS)}")
    symbol_v = None if symbol in (None, "") else str(symbol).strip() or None
    want_trades = bool(include_trades)
    trades_refused = want_trades and top_n_v > TRADES_MAX_TOP_N
    give_trades = want_trades and not trades_refused

    result = await run_sync_with_timeout(
        _fetch_scan, ctx.settings, ctx,
        anchor=anchor, window_min=window_v, hold_bucket=hold_bucket, sids=sid_list,
        symbol=symbol_v, scan_by=scan_by, ctx=ctx,
    )
    if is_error(result):
        return result
    clients, stats = result

    key = SORTS[sort]
    # None sorts last whichever way (net_gain is null when a PG leg is unknown).
    ordered = sorted(clients, key=lambda c: (c.get(key) is None, -(float(c.get(key) or 0.0))))

    masked = 0
    if ctx.scope is not None:
        ids = [int(c["client_id"]) for c in ordered]
        cids = await run_sync_with_timeout(_fetch_cids, ctx.settings, ids, ctx=ctx)
        if is_error(cids):
            return cids
        kept = [c for c in ordered if cids.get(int(c["client_id"])) is not None and cids.get(int(c["client_id"])) in ctx.scope]
        masked = len(ordered) - len(kept)
        ordered = kept

    visible = ordered[:top_n_v]
    rows: list[dict] = []
    trades_budget = MAX_ROWS
    trades_cut = False
    for i, c in enumerate(visible, start=1):
        row = {k: _round(c.get(k)) for k in _ROW_KEYS}
        row["rank"] = i
        if give_trades:
            t = [{k: _round(tr.get(k)) for k in _TRADE_KEYS} for tr in (c.get("trades") or [])]
            if len(t) > trades_budget:
                trades_cut = True
            row["trades"] = t[:trades_budget]
            row["trades_total"] = len(t)
            trades_budget -= len(row["trades"])
        rows.append(row)

    data = {
        "anchor_hk": stats.get("anchor_hk"),
        "anchor_mt": stats.get("anchor_mt"),
        "range_mt": {"from": stats.get("range_mt_from"), "to": stats.get("range_mt_to")},
        "window_min": window_v,
        "scan_by": scan_by,
        "hold_bucket": hold_bucket,
        "sids": stats.get("sids"),
        "symbol": symbol_v,
        "sort": sort,
        "rows": rows,
        "rows_returned": len(rows),
        "profitable_clients_total": len(ordered),
        "rows_masked_by_scope": masked,
        # A filtered list never sits next to an unfiltered total (cold review
        # #3): for a restricted caller the firm-wide counts would give away the
        # out-of-scope population by subtraction, so they are withheld (null)
        # and clients_profitable is the in-scope count.
        "stats": {
            "clients_scanned": stats.get("clients_scanned") if ctx.scope is None else None,
            "clients_profitable": stats.get("clients_profitable") if ctx.scope is None else len(ordered),
            "trades_scanned": stats.get("trades_scanned") if ctx.scope is None else None,
            "employees_excluded": stats.get("employees_excluded") if ctx.scope is None else None,
            "truncated": bool(stats.get("truncated")),
            "enrichment_ok": stats.get("enrichment_ok"),
        },
        "verdict": None,
    }
    caveats = [
        "Only clients whose CLOSED orders in the window sum to > 0 are listed (the page's rule); floating P/L never "
        "makes a client 'profitable'.",
        "The window is ±window_min around a Hong Kong minute; it is converted to MT server time DST-aware "
        "(UTC+3 summer / UTC+2 winter) before matching OPEN_TIME (scan_by=open) or CLOSE_TIME (scan_by=close).",
        "closed_profit / floating_profit are the window's orders only (USD, cent products /100). net_deposit, "
        "total_rebate, pl_plus_rebate and net_gain are the client's LIFETIME legs; net_deposit is the TRADING net "
        "deposit and does NOT include IB commission withdrawals; net_gain = equity − trading net deposit + full-chain "
        "rebate (null when a leg is unknown).",
        (f"Employees are excluded and counted (stats.employees_excluded = {stats.get('employees_excluded')}); demo/test "
         "accounts are excluded." if ctx.scope is None else
         "Employees and demo/test accounts are excluded. Firm-wide scan counts are withheld for a data-scope-restricted "
         "caller (they would reveal the out-of-scope population)."),
        "Direction is the position side (sid=5 closed CMD normalised); hold_sec of open orders runs to now.",
        f"The scan reads at most {wss.MAX_TRADE_ROWS} orders; stats.truncated=true means the answer is INCOMPLETE — "
        "narrow the window or add a symbol.",
        "Sorted here by the chosen column, then top_n; ties keep the page's order (closed_profit desc).",
    ]
    if stats.get("truncated"):
        caveats.append("THIS scan hit the row cap: the list is incomplete.")
    if stats.get("enrichment_ok") is False:
        caveats.append("The lifetime money legs (PG) were unavailable: net_deposit / total_rebate / net_gain are null "
                       "for that reason, not zero.")
    if trades_refused:
        caveats.append(f"include_trades is only honoured when top_n ≤ {TRADES_MAX_TOP_N}; no trades[] were returned.")
    if trades_cut:
        caveats.append(f"trades[] were cut at {MAX_ROWS} orders in total; trades_total per row is the full count.")
    if masked:
        caveats.append(f"{masked} profitable client(s) outside the caller's data scope were removed BEFORE top_n "
                       "(rows_masked_by_scope). Do not infer them.")
    definition = {
        "summary": "Clients who opened (scan_by=open) or closed (scan_by=close) orders within ±window_min minutes of "
        "a Hong Kong moment and whose closed orders in that window are net profitable — the /window-scan page's "
        "own query.",
        "caveats": caveats,
        "doc": "docs/features/window-scan.md; docs/ai-agent/11-slice3-risk-control.md §2.3",
    }
    source = {
        "service": "app.services.window_scan_service",
        "function": "query_window_scan",
        "as_of": utc_now_iso(),
    }
    truncated = bool(stats.get("truncated")) or len(ordered) > len(visible) or trades_cut
    return ok_envelope(data, definition=definition, source=source, ctx=ctx, truncated=truncated)
