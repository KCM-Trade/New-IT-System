"""The certified tools (three single-subject, two group-level since OPT-0065). Framework-free: nothing here imports the agent
framework, so the assembly logic is unit-testable with monkeypatched data
access. ``harness.build_tools`` wraps these into framework tools per request.
"""

from .client_overview import get_client_overview
from .common import CallerCtx, ctx_from_request
from .economic_calendar import get_economic_calendar
from .rank_accounts import rank_accounts
from .risk_signals import get_risk_signals
from .trade_activity import get_trade_activity

TOOL_IMPLS = {
    "get_client_overview": get_client_overview,
    "get_trade_activity": get_trade_activity,
    "get_risk_signals": get_risk_signals,
    "rank_accounts": rank_accounts,
    "get_economic_calendar": get_economic_calendar,
}

__all__ = [
    "CallerCtx",
    "TOOL_IMPLS",
    "ctx_from_request",
    "get_client_overview",
    "get_economic_calendar",
    "get_risk_signals",
    "get_trade_activity",
    "rank_accounts",
]
