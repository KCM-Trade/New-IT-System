"""The certified tools (three single-subject, two group-level since OPT-0065, three Risk control since OPT-0066). Framework-free: nothing here imports the agent
framework, so the assembly logic is unit-testable with monkeypatched data
access. ``harness.build_tools`` wraps these into framework tools per request.
"""

from .alert_orders import get_alert_orders
from .client_overview import get_client_overview
from .common import CallerCtx, ctx_from_request
from .economic_calendar import get_economic_calendar
from .rank_accounts import rank_accounts
from .risk_alerts import get_risk_alerts
from .risk_signals import get_risk_signals
from .trade_activity import get_trade_activity
from .window_scan import get_window_scan

TOOL_IMPLS = {
    "get_client_overview": get_client_overview,
    "get_trade_activity": get_trade_activity,
    "get_risk_signals": get_risk_signals,
    "rank_accounts": rank_accounts,
    "get_economic_calendar": get_economic_calendar,
    # Slice 3 (OPT-0066) — registered by harness.build_tools ONLY when
    # common.risk_tools_enabled(ctx) (holds `risk` AND scope is None).
    "get_risk_alerts": get_risk_alerts,
    "get_alert_orders": get_alert_orders,
    "get_window_scan": get_window_scan,
}

# The three tools that need the `risk` module on top of `ai` (11 §0 T1).
RISK_TOOL_NAMES = ("get_risk_alerts", "get_alert_orders", "get_window_scan")

__all__ = [
    "CallerCtx",
    "RISK_TOOL_NAMES",
    "TOOL_IMPLS",
    "ctx_from_request",
    "get_alert_orders",
    "get_client_overview",
    "get_economic_calendar",
    "get_risk_alerts",
    "get_risk_signals",
    "get_trade_activity",
    "get_window_scan",
    "rank_accounts",
]
