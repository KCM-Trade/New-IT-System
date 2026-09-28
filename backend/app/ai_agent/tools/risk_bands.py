"""Risk Monitor rule bands, tab keys and per-band output allow-lists.

Shared by ``get_risk_signals`` (single subject) and the slice-3 Risk control
tools (``get_risk_alerts`` / ``get_alert_orders``). Kept as DATA here — not
imported from ``routes/risk_monitor.py`` — so the agent container never
imports FastAPI route code with its scheduler side effects.

Three tables, one source:

* ``RULE_BANDS``   — rule_id range → band name (mirrors the allocation comment
                     in routes/risk_monitor.py).
* ``TAB_BANDS``    — the Risk Monitor page's tab keys (``?tab=`` in the URL,
                     ``RISK_MONITOR_TABS`` in RiskMonitor.tsx) → rule_id ranges.
                     ``gap-trade`` is the one tab spanning TWO bands (SO+AB and
                     excess profit). An anti-drift test compares the keys with
                     the frontend source.
* ``BAND_FIELDS``  — per band: which alert fields may leave the tool (PII
                     allow-list, docs/ai-agent/11 §2.1) and which
                     ``risk_monitor_db.AGG_METRICS`` key is its main metric.
"""

from __future__ import annotations

from typing import Any, Optional

RULE_BANDS: tuple[tuple[int, int, str], ...] = (
    (1, 50, "burst_open"),
    (51, 60, "quick_open_close"),
    (61, 70, "quick_profit"),
    (71, 80, "gap_trade_so_ab"),
    (81, 90, "gap_trade_profit"),
    (91, 100, "hedge_open"),
    (101, 110, "leverage_abuse"),
    (111, 120, "martingale"),
    (121, 130, "rebate_arbitrage"),
    (131, 140, "intraday_return"),
)

# Tab key → (rule_id ranges, 中文名). Order = the page's tab order.
TAB_BANDS: dict[str, tuple[tuple[int, int], ...]] = {
    "burst-open": ((1, 50),),
    "quick-open-close": ((51, 60),),
    "quick-profit": ((61, 70),),
    "gap-trade": ((71, 80), (81, 90)),
    "hedge-open": ((91, 100),),
    "leverage-abuse": ((101, 110),),
    "martingale": ((111, 120),),
    "intraday-return": ((131, 140),),
}

TAB_LABELS_ZH: dict[str, str] = {
    "burst-open": "批量下单",
    "quick-open-close": "快开快平",
    "quick-profit": "快速获利",
    "gap-trade": "Gap Trade",
    "hedge-open": "对冲刷单",
    "leverage-abuse": "滥用杠杆",
    "martingale": "马丁策略",
    "intraday-return": "即日高收益",
}

# The page filters every tab by scan time EXCEPT intraday-return, whose rows
# belong to an MT trading day and are updated all day (OPT-0062).
TAB_TIME_FIELD: dict[str, str] = {tab: "scanned_at" for tab in TAB_BANDS}
TAB_TIME_FIELD["intraday-return"] = "trading_day"

# Fields every alert row may carry regardless of band (no PII in any of them).
COMMON_ALERT_FIELDS: tuple[str, ...] = ("order_count", "total_lots", "symbol")

# band → {"metric": AGG_METRICS key, "metric_label": human text, "fields": allow-list of alert dict keys}
# Keys are those of risk_monitor_db._row_to_alert_dict. NEVER add: l_name, c_name,
# client_name, zipcode, group/account_group, shared_ips, so_comment, account
# remarks, COMMENT, l_userid/c_userid/client_userid (ids are exposed only through
# the scope-checked client_id).
BAND_FIELDS: dict[str, dict[str, Any]] = {
    "burst_open": {
        "metric": "order_count",
        "metric_label": "order_count (orders in the burst, higher = stronger)",
        "fields": ("order_count", "total_lots", "first_open", "last_open", "symbol"),
    },
    "quick_open_close": {
        "metric": "min_hold_sec",
        "metric_label": "shortest hold_duration_sec (lower = stronger)",
        "fields": ("order_count", "total_lots", "hold_duration_sec", "total_profit_usd", "symbol"),
    },
    "quick_profit": {
        "metric": "total_profit_usd",
        "metric_label": "total_profit_usd (higher = stronger)",
        "fields": ("total_profit_usd", "order_count", "total_lots", "realized_profit", "position_status", "symbol"),
    },
    "gap_trade_so_ab": {
        "metric": "net_usd",
        "metric_label": "net_usd of the L/C pair (higher = stronger)",
        "fields": (
            "l_login_sid", "l_lots", "l_profit_usd", "c_login_sid", "c_lots", "c_profit_usd",
            "open_diff_sec", "lot_ratio", "net_usd", "shared_ip_count", "window_date", "symbol",
        ),
    },
    "gap_trade_profit": {
        "metric": "total_profit_usd",
        "metric_label": "excess gap-window profit total_profit_usd (higher = stronger)",
        "fields": (
            "contributing_login_sids", "contributing_account_count", "symbols", "symbol_count",
            "total_profit_usd", "profit_ratio", "triggered_by", "window_date",
        ),
    },
    "hedge_open": {
        "metric": "total_lots",
        "metric_label": "total_lots (higher = stronger)",
        "fields": ("order_count", "total_lots", "total_open_lots", "buy_count", "sell_count", "buy_lots", "sell_lots", "symbol"),
    },
    # The leverage rule fires on margin level; it never fills equity_per_lot /
    # total_open_lots (0 of 30,471 rows, CORE fork 2026-09-28), so those are
    # not offered.
    "leverage_abuse": {
        "metric": "margin_level",
        "metric_label": "margin_level %, ascending (lower = less free margin = stronger)",
        "fields": ("leverage", "equity", "margin_level", "margin_used", "free_margin", "streak_count", "symbol"),
    },
    "martingale": {
        "metric": "lot_ratio_mg",
        "metric_label": "lot_ratio_mg = largest add-on lots / anchor lots (higher = stronger)",
        "fields": (
            "order_count", "total_lots", "direction", "anchor_lots", "new_lots", "lot_ratio_mg",
            "add_count", "floating_pnl", "symbol",
        ),
    },
    "rebate_arbitrage": {
        "metric": None,
        "metric_label": "retired band (no data since 2026-07-24)",
        "fields": (),
    },
    "intraday_return": {
        # peak, not return_pct: the alert fired on the day's high; return_pct is
        # the latest tick and can sit below the threshold (live 2026-09-28: an
        # alerted account at 53% latest was shown as "highest return").
        "metric": "peak_return_pct",
        "metric_label": "peak_return_pct (highest intraday return % seen that MT day, rule formula v3 — the value "
        "that fired the alert; return_pct in alert rows is the latest tick and can be lower)",
        "fields": (
            "trading_day", "return_pct", "peak_return_pct", "initial_equity", "intraday_profit",
            "trades_today", "lots_today", "median_hold_sec", "lock_pct", "top_symbol",
        ),
    },
}


def rule_band_name(rule_id: Any) -> str:
    try:
        rid = int(rule_id)
    except (TypeError, ValueError):
        return "unknown"
    for lo, hi, name in RULE_BANDS:
        if lo <= rid <= hi:
            return name
    return "unknown"


def band_range(name: str) -> Optional[tuple[int, int]]:
    for lo, hi, n in RULE_BANDS:
        if n == name:
            return lo, hi
    return None


def tab_for_rule(rule_id: Any) -> Optional[str]:
    try:
        rid = int(rule_id)
    except (TypeError, ValueError):
        return None
    for tab, ranges in TAB_BANDS.items():
        if any(lo <= rid <= hi for lo, hi in ranges):
            return tab
    return None


def bands_in_ranges(ranges: tuple[tuple[int, int], ...]) -> list[str]:
    """Band names touched by a set of rule_id ranges, in RULE_BANDS order."""
    out = []
    for lo, hi, name in RULE_BANDS:
        if any(not (hi < a or lo > b) for a, b in ranges):
            out.append(name)
    return out
