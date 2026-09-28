"""Tool 7 — ``get_risk_alerts`` (docs/ai-agent/11-slice3-risk-control.md §2.1).

"Which accounts / clients did Risk Monitor tab X (or rule N) fire on in this
window?" — the GROUP-level entry into the Risk control pages. Reads the same
``alert_events`` rows the page reads, through the page's own filter builder
(``risk_monitor_db._build_alert_filters`` behind ``query_alert_events`` /
``count_alert_events_by_rule`` / ``aggregate_alert_events``), on a read-only
connection (the agent container mounts backend/data read-only).

Contract points that are easy to get wrong:

* Time column follows the page: ``intraday-return`` rows belong to an MT
  trading day (``time_field="trading_day"``, calendar-inclusive both ends);
  every other tab is selected by ``scanned_at`` in [since, until).
* Grouping happens in SQLite (``aggregate_alert_events``), never by folding a
  capped row page in Python — leverage alone is ~1,000 rows/day, so folding the
  first 500 rows would produce wrong counts.
* Totals come from ``count_alert_events_by_rule`` over the SAME filter, so they
  do not depend on any row cap.
* Output is an allow-list projection per band (``risk_bands.BAND_FIELDS``);
  names, zipcodes, account groups, IP lists and comments never leave.
* Restricted callers (``scope is not None``) — who by design never get this
  tool registered (11 §0 T1), but the impl is written for the day ``risk``
  joins SCOPED_MODULES — get every figure recomputed over in-scope clients
  only: a filtered list must never sit next to an unfiltered total.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.core import risk_monitor_db as rmdb
from app.core.config import Settings
from app.core.data_scope import cid_for_crm_user_ids
from app.core.sql_helpers import SID_MAP
from app.services.rule_intraday_return_service import _local_to_utc

from .common import (
    MAX_ALERTS,
    CallerCtx,
    error_envelope,
    is_error,
    mt_day_bounds_utc,
    ok_envelope,
    parse_date_range,
    run_sync_with_timeout,
    to_utc_iso,
    utc_now_iso,
)
from .risk_bands import (
    BAND_FIELDS,
    RULE_BANDS,
    TAB_BANDS,
    TAB_TIME_FIELD,
    bands_in_ranges,
    rule_band_name,
    tab_for_rule,
)

TOOL_NAME = "get_risk_alerts"

MAX_RANGE_DAYS = 31          # alert_events keeps 30 days
MAX_TOP_N = 50
MAX_CLIENT_IDS = 50
GROUP_BYS = ("alert", "account", "client", "rule")
SORTS = ("alerts", "lots", "profit", "metric")
LIVE_SIDS = (1, 5, 6)
# Restricted callers: the pre-pass that learns which clients exist in the
# window. Far above any real window (30 days × every tab ≈ 1.5k clients).
_SCOPE_PREPASS_LIMIT = 5000

_SERVER_BY_SID = {sid: label for label, sid in SID_MAP.items()}

# alert-mode sort: AGG_METRICS key → (sortable column, direction). lot_ratio_mg
# is not a page-sortable column, so martingale falls back to scan time.
_ALERT_METRIC_SORT = {
    "order_count": ("order_count", "desc"),
    "min_hold_sec": ("hold_duration_sec", "asc"),
    "total_profit_usd": ("total_profit_usd", "desc"),
    "net_usd": ("net_usd", "desc"),
    "total_lots": ("total_lots", "desc"),
    "equity_per_lot": ("equity_per_lot", "asc"),
    "margin_level": ("margin_level", "asc"),
    "return_pct": ("return_pct", "desc"),
}
_ALERT_SORT = {"alerts": ("scanned_at", "desc"), "lots": ("total_lots", "desc"), "profit": ("total_profit_usd", "desc")}


def _login_sid(server: Any, login: Any) -> Optional[str]:
    sid = SID_MAP.get(str(server or ""))
    return f"{sid}-{login}" if sid is not None and login is not None else None


def _num(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 4)
    return v


# ── argument handling ────────────────────────────────────────────────────────


def _int_list(raw: Any, name: str, *, max_len: int) -> list[int] | dict:
    if not isinstance(raw, (list, tuple)) or not raw:
        return error_envelope("invalid_argument", f"{name} must be a non-empty list of integers or null")
    try:
        out = sorted({int(v) for v in raw})
    except (TypeError, ValueError):
        return error_envelope("invalid_argument", f"{name} must be integers")
    if len(out) > max_len:
        return error_envelope("invalid_argument", f"{name} accepts at most {max_len} values", {"given": len(out)})
    return out


def resolve_rule_range(tab: Any, rule_ids: Any) -> tuple[str, int, int] | dict:
    """(tab, rule_id_min, rule_id_max) for the query, or an error envelope.

    ``tab`` alone → the tab's whole range (gap-trade = 71..90, both bands).
    ``rule_ids`` alone → must all fall in ONE tab (its time column decides the
    query) and be consecutive (the DB filter is one min..max range).
    Both → the ids must lie inside the tab's range (intersection).
    """
    tab_key = None if tab in (None, "") else str(tab).strip().lower()
    if tab_key is not None and tab_key not in TAB_BANDS:
        return error_envelope("invalid_argument", f"unknown tab {tab!r}; allowed: {list(TAB_BANDS)}")
    if rule_ids is None:
        if tab_key is None:
            return error_envelope("invalid_argument", "pass a tab (e.g. 'intraday-return') or rule_ids")
        ranges = TAB_BANDS[tab_key]
        return tab_key, min(lo for lo, _ in ranges), max(hi for _, hi in ranges)
    ids = _int_list(rule_ids, "rule_ids", max_len=50)
    if isinstance(ids, dict):
        return ids
    unknown = [r for r in ids if rule_band_name(r) == "unknown" or tab_for_rule(r) is None]
    if unknown:
        return error_envelope(
            "invalid_argument",
            f"rule_ids {unknown} are outside every Risk Monitor tab band "
            f"(bands: {[(lo, hi, n) for lo, hi, n in RULE_BANDS if n != 'rebate_arbitrage']})",
        )
    tabs = {tab_for_rule(r) for r in ids}
    if tab_key is not None:
        inside = [r for r in ids if tab_for_rule(r) == tab_key]
        if not inside:
            return error_envelope("invalid_argument", f"none of rule_ids {ids} belong to tab {tab_key!r} (empty intersection)")
        ids = inside
        tabs = {tab_key}
    if len(tabs) > 1:
        return error_envelope("invalid_argument", "rule_ids must belong to ONE tab; call once per tab", {"tabs": sorted(tabs)})
    if ids != list(range(ids[0], ids[-1] + 1)):
        return error_envelope("invalid_argument", "rule_ids must be consecutive (one range); call once per rule otherwise", {"rule_ids": ids})
    return tabs.pop(), ids[0], ids[-1]


def query_bounds(day_from, day_to, time_field: str) -> tuple[str, str]:
    """UTC ISO (since, until) for the chosen time column.

    scanned_at: half-open [MT day_from 00:00, MT day_to+1 00:00) in UTC.
    trading_day: the DB maps BOTH bounds to MT dates and treats the upper one
    as inclusive, so hand it an instant INSIDE day_to (its 00:00) — the
    half-open end (next day 00:00) would silently widen the window by a day.
    """
    if time_field == "trading_day":
        start = _local_to_utc(datetime(day_from.year, day_from.month, day_from.day))
        end = _local_to_utc(datetime(day_to.year, day_to.month, day_to.day))
        return to_utc_iso(start), to_utc_iso(end)  # type: ignore[return-value]
    return mt_day_bounds_utc(day_from, day_to)


# ── data access (monkeypatch targets) ────────────────────────────────────────


def _fetch_alert_rows(settings: Settings, q: dict, *, limit: int, sort_by: str, sort_order: str) -> dict:
    conn = rmdb.open_readonly()
    try:
        rows, total = rmdb.query_alert_events(
            q["since"], q["until"],
            rule_id_min=q["rule_id_min"], rule_id_max=q["rule_id_max"], symbol=q["symbol"],
            limit=limit, sort_by=sort_by, sort_order=sort_order, time_field=q["time_field"],
            servers=q["servers"], user_ids=q["user_ids"], include_user_id=True, conn=conn,
        )
        return {"rows": rows, "total": total}
    finally:
        conn.close()


def _fetch_aggregate(settings: Settings, q: dict, *, group_by: str, sort: str, metric: Optional[str], limit: int) -> dict:
    conn = rmdb.open_readonly()
    try:
        return rmdb.aggregate_alert_events(
            q["since"], q["until"], group_by=group_by,
            rule_id_min=q["rule_id_min"], rule_id_max=q["rule_id_max"],
            servers=q["servers"], symbol=q["symbol"], user_ids=q["user_ids"],
            time_field=q["time_field"], metric=metric, sort=sort, limit=limit, conn=conn,
        )
    finally:
        conn.close()


def _fetch_counts(settings: Settings, q: dict) -> dict:
    """Per-rule totals over the same filter + the newest scan time in the band
    (``source.as_of``: when the detector last wrote, not when we read)."""
    conn = rmdb.open_readonly()
    try:
        by_rule = rmdb.count_alert_events_by_rule(
            q["since"], q["until"], servers=q["servers"], user_ids=q["user_ids"], symbol=q["symbol"],
            rule_id_min=q["rule_id_min"], rule_id_max=q["rule_id_max"], time_field=q["time_field"], conn=conn,
        )
        row = conn.execute(
            "SELECT MAX(scanned_at) FROM alert_events WHERE rule_id BETWEEN ? AND ?",
            (q["rule_id_min"], q["rule_id_max"]),
        ).fetchone()
        return {"by_rule": by_rule, "latest_scan": row[0] if row else None}
    finally:
        conn.close()


def _fetch_cids(settings: Settings, user_ids: list[int]) -> dict:
    return cid_for_crm_user_ids(settings, user_ids) if user_ids else {}


# ── shaping ──────────────────────────────────────────────────────────────────


def _alert_summary(a: dict, band: str) -> str:
    bits = [str(a.get("rule_label") or a.get("rule_id"))]
    if a.get("symbol"):
        bits.append(str(a["symbol"]))
    if band == "intraday_return" and a.get("return_pct") is not None:
        bits.append(f"return {round(float(a['return_pct']), 2)}%")
    elif a.get("order_count") is not None:
        bits.append(f"{a['order_count']} orders")
    if a.get("total_lots") is not None and band != "intraday_return":
        bits.append(f"{round(float(a['total_lots']), 3)} lots")
    return " · ".join(bits)


def shape_alert(a: dict, *, c_leg_visible: bool = True) -> dict:
    """Allow-list projection of one alert row (the PII boundary)."""
    band = rule_band_name(a.get("rule_id"))
    fields = BAND_FIELDS.get(band, {}).get("fields", ())
    metrics: dict[str, Any] = {}
    for f in fields:
        if f in ("symbol",):
            continue
        if not c_leg_visible and f.startswith("c_"):
            continue
        metrics[f] = _num(a.get(f))
    if band == "gap_trade_profit" and isinstance(metrics.get("contributing_login_sids"), str):
        metrics["contributing_login_sids"] = [s.strip() for s in metrics["contributing_login_sids"].split(",") if s.strip()]
    if band in ("burst_open", "quick_open_close", "quick_profit", "hedge_open", "leverage_abuse", "martingale"):
        metrics["orders_in_alert"] = len(a.get("orders") or [])
    out = {
        "alert_id": a.get("id"),
        "rule_id": a.get("rule_id"),
        "rule_name": band,
        "rule_label": a.get("rule_label"),
        "tab": tab_for_rule(a.get("rule_id")),
        "fired_at": to_utc_iso(a.get("scanned_at")),
        "login_sid": _login_sid(a.get("server"), a.get("login")),
        "client_id": a.get("user_id"),
        "symbol": a.get("symbol"),
        "summary": _alert_summary(a, band),
        "metrics": metrics,
    }
    if band == "intraday_return":
        out["trading_day"] = a.get("trading_day")
    if not c_leg_visible:
        out["c_leg_masked_by_scope"] = True
    return out


def _shape_group(g: dict, group_by: str, single_band: Optional[str]) -> dict:
    rules = [int(r) for r in (g.get("rule_ids") or [])]
    common = {
        "alerts": g.get("alerts"),
        "rules": rules,
        "rule_names": sorted({rule_band_name(r) for r in rules}),
        "first_fired_at": to_utc_iso(g.get("first_fired_at")),
        "last_fired_at": to_utc_iso(g.get("last_fired_at")),
        "lots": _num(g.get("lots")),
        "profit": _num(g.get("profit")),
        "top_metric": _num(g.get("metric")) if single_band else None,
        "sample_alert_ids": list(g.get("sample_alert_ids") or [])[:3],
    }
    if group_by == "account":
        return {"login_sid": _login_sid(g.get("server"), g.get("login")), "client_id": g.get("user_id"), **common}
    # client
    login_sids = sorted(
        {s for s in (_login_sid(a.get("server"), a.get("login")) for a in (g.get("accounts") or [])) if s}
    )
    return {"client_id": g.get("user_id"), "login_sids": login_sids, **common}


def _shape_rule_group(g: dict) -> dict:
    return {
        "rule_id": g.get("rule_id"),
        "rule_name": rule_band_name(g.get("rule_id")),
        "rule_label": g.get("rule_label"),
        "tab": tab_for_rule(g.get("rule_id")),
        "alerts": g.get("alerts"),
        "accounts": g.get("accounts"),
        "clients": g.get("clients"),
    }


# ── the tool ─────────────────────────────────────────────────────────────────


async def get_risk_alerts(
    ctx: CallerCtx,
    tab: Any = None,
    rule_ids: Any = None,
    date_range: Any = None,
    group_by: Any = "client",
    top_n: Any = 20,
    sort: Any = "alerts",
    sids: Any = None,
    symbol: Any = None,
    client_ids: Any = None,
) -> dict:
    resolved = resolve_rule_range(tab, rule_ids)
    if isinstance(resolved, dict):
        return resolved
    tab_key, rid_min, rid_max = resolved

    rng = parse_date_range(date_range)
    if isinstance(rng, dict):
        return rng
    if rng.days > MAX_RANGE_DAYS:
        return error_envelope(
            "range_too_wide",
            f"date_range spans {rng.days} days; alerts are kept 30 days, so the limit is {MAX_RANGE_DAYS}.",
            {"days": rng.days, "max_days": MAX_RANGE_DAYS},
        )

    group_by = str(group_by or "client").strip().lower()
    if group_by not in GROUP_BYS:
        return error_envelope("invalid_argument", f"group_by must be one of {list(GROUP_BYS)}")
    sort = str(sort or "alerts").strip().lower()
    if sort not in SORTS:
        return error_envelope("invalid_argument", f"sort must be one of {list(SORTS)}")
    try:
        top_n_v = int(top_n)
    except (TypeError, ValueError):
        return error_envelope("invalid_argument", "top_n must be an integer")
    if not 1 <= top_n_v <= MAX_TOP_N:
        return error_envelope("invalid_argument", f"top_n must be between 1 and {MAX_TOP_N}")

    servers: Optional[list[str]] = None
    if sids is not None:
        sid_list = _int_list(sids, "sids", max_len=3)
        if isinstance(sid_list, dict):
            return sid_list
        bad = [s for s in sid_list if s not in LIVE_SIDS]
        if bad:
            return error_envelope("invalid_argument", f"sids {bad} are not live servers; allowed: {list(LIVE_SIDS)}")
        servers = [_SERVER_BY_SID[s] for s in sid_list]
    user_ids: Optional[list[int]] = None
    if client_ids is not None:
        cl = _int_list(client_ids, "client_ids", max_len=MAX_CLIENT_IDS)
        if isinstance(cl, dict):
            return cl
        user_ids = cl
    symbol_v = None if symbol in (None, "") else str(symbol).strip()

    bands = bands_in_ranges(((rid_min, rid_max),))
    single_band = bands[0] if len(bands) == 1 else None
    metric_key = BAND_FIELDS.get(single_band, {}).get("metric") if single_band else None
    if sort == "metric":
        if single_band is None:
            return error_envelope(
                "invalid_argument",
                f"sort='metric' needs ONE rule band, but this selection spans {bands} (each has a different main "
                "metric). Pass rule_ids for one band (gap-trade: 71-80 SO+AB or 81-90 excess profit) or sort by "
                "'alerts' / 'lots' / 'profit'.",
                {"bands": bands},
            )
        if metric_key is None:
            return error_envelope("invalid_argument", f"band {single_band} has no main metric (retired)")

    time_field = TAB_TIME_FIELD[tab_key]
    since, until = query_bounds(rng.day_from, rng.day_to, time_field)
    q = {
        "since": since, "until": until, "time_field": time_field,
        "rule_id_min": rid_min, "rule_id_max": rid_max,
        "servers": servers, "symbol": symbol_v, "user_ids": user_ids,
    }

    # ── restricted caller: learn the in-scope client set first (fail closed) ──
    masked_clients = 0
    null_user_alerts = 0
    unrestricted_units: Optional[int] = None
    prepass_capped = False
    if ctx.scope is not None:
        pre = await run_sync_with_timeout(
            _fetch_aggregate, ctx.settings, q, group_by="client", sort="alerts", metric=None,
            limit=_SCOPE_PREPASS_LIMIT, ctx=ctx,
        )
        if is_error(pre):
            return pre
        seen = [int(g["user_id"]) for g in pre.get("groups") or [] if g.get("user_id") is not None]
        prepass_capped = int(pre.get("groups_total") or 0) > len(pre.get("groups") or [])
        null_user_alerts = int(pre.get("alerts_without_user_id") or 0)
        cids = await run_sync_with_timeout(_fetch_cids, ctx.settings, seen, ctx=ctx)
        if is_error(cids):
            return cids
        allowed = [u for u in seen if cids.get(u) is not None and cids.get(u) in ctx.scope]
        masked_clients = len(seen) - len(allowed)
        # How many rows the requested grouping WOULD have had — so the masked
        # count is in the unit the caller asked for.
        if group_by in ("account",):
            full = await run_sync_with_timeout(_fetch_aggregate, ctx.settings, q, group_by="account", sort="alerts",
                                               metric=None, limit=1, ctx=ctx)
            if is_error(full):
                return full
            unrestricted_units = int(full.get("groups_total") or 0)
        elif group_by == "client":
            unrestricted_units = len(seen)
        else:  # alert / rule → alerts
            full_counts = await run_sync_with_timeout(_fetch_counts, ctx.settings, q, ctx=ctx)
            if is_error(full_counts):
                return full_counts
            unrestricted_units = sum(full_counts["by_rule"].values())
        q = {**q, "user_ids": allowed}

    empty_scope = ctx.scope is not None and not q["user_ids"]

    if empty_scope:
        counts = {"by_rule": {}, "latest_scan": None}
    else:
        counts = await run_sync_with_timeout(_fetch_counts, ctx.settings, q, ctx=ctx)
        if is_error(counts):
            return counts
    alerts_by_rule = {str(k): int(v) for k, v in sorted(counts["by_rule"].items())}
    alerts_total = sum(alerts_by_rule.values())

    truncated = False
    alerts_without_user_id: Optional[int] = None
    rows: list[dict] = []
    if group_by == "alert":
        if metric_key is not None and sort == "metric":
            sort_by, sort_order = _ALERT_METRIC_SORT.get(metric_key, ("scanned_at", "desc"))
        else:
            sort_by, sort_order = _ALERT_SORT.get(sort, ("scanned_at", "desc"))
        if not empty_scope:
            page = await run_sync_with_timeout(_fetch_alert_rows, ctx.settings, q, limit=MAX_ALERTS,
                                               sort_by=sort_by, sort_order=sort_order, ctx=ctx)
            if is_error(page):
                return page
            raw = page["rows"]
            # gap 71: the C leg can be ANOTHER client — scope it on its own.
            c_visible: dict[int, bool] = {}
            if ctx.scope is not None:
                c_ids = sorted({int(a["c_userid"]) for a in raw if a.get("c_userid") is not None})
                c_cids = await run_sync_with_timeout(_fetch_cids, ctx.settings, c_ids, ctx=ctx) if c_ids else {}
                if is_error(c_cids):
                    return c_cids
                c_visible = {u: (c_cids.get(u) is not None and c_cids.get(u) in ctx.scope) for u in c_ids}
            for a in raw:
                c_ok = True
                if ctx.scope is not None and rule_band_name(a.get("rule_id")) == "gap_trade_so_ab":
                    cu = a.get("c_userid")
                    c_ok = cu is not None and c_visible.get(int(cu), False)
                rows.append(shape_alert(a, c_leg_visible=c_ok))
            truncated = int(page["total"]) > len(raw)
        groups_total = alerts_total
    elif group_by == "rule":
        if not empty_scope:
            agg = await run_sync_with_timeout(_fetch_aggregate, ctx.settings, q, group_by="rule", sort="alerts",
                                              metric=None, limit=MAX_TOP_N, ctx=ctx)
            if is_error(agg):
                return agg
            rows = [_shape_rule_group(g) for g in agg.get("groups") or []]
            groups_total = int(agg.get("groups_total") or len(rows))
        else:
            groups_total = 0
    else:
        if not empty_scope:
            agg = await run_sync_with_timeout(
                _fetch_aggregate, ctx.settings, q, group_by=group_by, sort=sort,
                metric=metric_key if (sort == "metric" or single_band) else None, limit=top_n_v, ctx=ctx,
            )
            if is_error(agg):
                return agg
            rows = [_shape_group(g, group_by, single_band) for g in agg.get("groups") or []]
            groups_total = int(agg.get("groups_total") or len(rows))
            if group_by == "client":
                alerts_without_user_id = int(agg.get("alerts_without_user_id") or 0)
        else:
            groups_total = 0
        truncated = groups_total > len(rows)

    if ctx.scope is not None:
        rows_masked = max(int(unrestricted_units or 0) - (alerts_total if group_by in ("alert", "rule") else groups_total), 0)
    else:
        rows_masked = 0

    data: dict[str, Any] = {
        "tab": tab_key,
        "tab_rule_range": [rid_min, rid_max],
        "bands": bands,
        "date_range": rng.as_dict(),
        "time_field": time_field,
        "group_by": group_by,
        "sort": sort,
        "metric": {"key": metric_key, "label": BAND_FIELDS[single_band]["metric_label"]} if single_band else None,
        "rows": rows,
        "rows_returned": len(rows),
        "groups_total": groups_total,
        "alerts_total": alerts_total,
        "alerts_by_rule": alerts_by_rule,
        # Distinct accounts across the RETURNED rows, so "N alerts (M accounts)"
        # never has to be counted by the model (it miscounted in the live run).
        # Complete only when `truncated` is false.
        "accounts_in_rows": len(
            {r["login_sid"] for r in rows if r.get("login_sid")}
            | {sid for r in rows for sid in (r.get("login_sids") or [])}
        ),
        "rows_masked_by_scope": rows_masked,
        "verdict": None,
    }
    if alerts_without_user_id is not None:
        data["alerts_without_client_id"] = alerts_without_user_id

    caveats = [
        "signal ≠ violation: every row is a detector output; the conclusion is the analyst's.",
        "alert_events is kept 30 days — older alerts no longer exist here.",
        "`alerts` counts ALERT FIRINGS, not events: the same account can fire again on every scan round "
        "(burst-open and leverage-abuse especially). Say 'N alerts (M accounts)', never 'N events'.",
        "Time column matches the page: intraday-return by trading_day (MT calendar day, inclusive); every other "
        "tab by scanned_at inside the MT-day window converted to UTC (DST-aware). gap-trade scans the PREVIOUS MT "
        "day (daily 05:20 HKT), so its scanned_at is one day after the trading window_date.",
        "Alerts whose client id (user_id) is NULL can only be grouped by account, not by client "
        "(`alerts_without_client_id`).",
        "The rebate-arbitrage band (121-130) is retired and has no data.",
        "Figures are copied from the detector's stored row (money in USD, cent already /100 at detection).",
    ]
    if group_by == "alert":
        caveats.append(f"group_by='alert' returns at most {MAX_ALERTS} rows (top_n ignored); alerts_total / "
                       "alerts_by_rule always count every match.")
    if single_band is None and group_by in ("account", "client"):
        caveats.append(f"This selection spans several bands {bands}; top_metric is null because each band has its own metric.")
    if sort == "metric" and group_by == "alert" and metric_key not in _ALERT_METRIC_SORT:
        caveats.append(f"Rows cannot be sorted by {metric_key} per alert; they are ordered by scan time instead.")
    if ctx.scope is not None:
        caveats.append(
            f"Restricted data scope: every figure (rows, groups_total, alerts_total, alerts_by_rule) is computed over "
            f"in-scope clients only; {masked_clients} client(s) and {null_user_alerts} alert(s) without a client id "
            "were removed. rows_masked_by_scope counts the removed rows in the requested grouping. Do not infer them."
        )
        if prepass_capped:
            caveats.append("More clients than the scope pre-pass limit fired in this window; clients beyond it are "
                           "hidden (fail closed). Narrow the window.")
    definition = {
        "summary": "signal ≠ violation. Risk Monitor alerts for one tab / rule range over an MT-day window, grouped "
        f"by {group_by}, read from the page's own alert store with the page's own filters.",
        "caveats": caveats,
        "doc": "docs/ai-agent/11-slice3-risk-control.md §2.1; docs/features/risk-monitor.md",
    }
    source = {
        "service": "app.core.risk_monitor_db",
        "function": "query_alert_events" if group_by == "alert" else "aggregate_alert_events",
        "as_of": to_utc_iso(counts.get("latest_scan")) or utc_now_iso(),
    }
    return ok_envelope(data, definition=definition, source=source, ctx=ctx, truncated=truncated)
