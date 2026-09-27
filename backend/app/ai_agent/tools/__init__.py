"""The three certified tools. Framework-free: nothing here imports the agent
framework, so the assembly logic is unit-testable with monkeypatched data
access. ``harness.build_tools`` wraps these into framework tools per request.
"""

from .client_overview import get_client_overview
from .common import CallerCtx, ctx_from_request
from .risk_signals import get_risk_signals
from .trade_activity import get_trade_activity

TOOL_IMPLS = {
    "get_client_overview": get_client_overview,
    "get_trade_activity": get_trade_activity,
    "get_risk_signals": get_risk_signals,
}

__all__ = [
    "CallerCtx",
    "TOOL_IMPLS",
    "ctx_from_request",
    "get_client_overview",
    "get_risk_signals",
    "get_trade_activity",
]
