"""02 §3 classification of one MT5 fill — pure, first match wins.

Compensation is a whitelist: only ``market`` is eligible (01 D8 / D12). Every
other class is still returned so the fill can be shown and counted, never
compensated. Only ``orders_history`` Reason/Type/Comment are consulted — never
``deals.Reason`` (a different enum, IMTDeal::EnDealReason).
"""

from __future__ import annotations

from fnmatch import fnmatchcase

from .models import Account, RawFill

# IMTOrder::EnOrderReason values that mean "the client pressed the button":
# 0 CLIENT, 1 EXPERT, 16 MOBILE, 17 WEB. Anything else — including values that
# appear in the future — is not compensable.
CLIENT_REASONS = frozenset({0, 1, 16, 17})

# deals.Dealer of the oneZero MT5 gateway (A-book): no DealerLogic delay, the
# price gap is LP slippage (01 D19, T0 2026-09-29).
NO_PLUGIN_DEALERS = frozenset({1})

# (account-group glob, deals.Dealer) pairs where the plugin confirms at its own
# request price but PriceCurrent sits one tick off, so the formula would invent
# compensation (01 D20, T0 2026-09-29). Case-sensitive glob on mt4_users.GROUP.
PASSTHROUGH_EXCLUSIONS: tuple[tuple[str, int], ...] = (
    ("KCM\\5LS*", 769),
)

ELIGIBLE_CLASSES = frozenset({"market"})


def classify(fill: RawFill, account: Account) -> str:
    """Return the 02 §3 class. The caller must have checked ``order_found``;
    a fill without its order row is unmatched, not classifiable.

    The plugin-routing overrides (``no_plugin`` / ``plugin_passthrough``) only
    replace ``market``: every other class is already non-compensable, and
    keeping e.g. ``limit`` / ``sl`` tells the reader more than the routing."""
    base = _base_class(fill)
    if base != "market":
        return base
    if fill.dealer in NO_PLUGIN_DEALERS:
        return "no_plugin"
    for pattern, dealer in PASSTHROUGH_EXCLUSIONS:
        if fill.dealer == dealer and fnmatchcase(account.group or "", pattern):
            return "plugin_passthrough"
    return "market"


def _base_class(fill: RawFill) -> str:
    if not fill.order_found:
        raise ValueError(f"deal {fill.deal_id}: order row missing; cannot classify")
    t, reason, comment = fill.order_type, fill.order_reason, fill.comment or ""
    if t == 8:
        return "close_by"
    if comment.startswith("[so at"):
        return "so_first" if reason == 5 else "so_rest"
    if t in (2, 3):
        return "limit"
    if t in (4, 5):
        return "stop"
    if t not in (0, 1):
        return "other_type"
    if reason == 3:
        return "sl"
    if reason == 4:
        return "tp"
    if reason in CLIENT_REASONS:
        return "market"
    if reason == 2:
        return "dealer"
    return "other_reason"


def is_eligible(cls: str) -> bool:
    return cls in ELIGIBLE_CLASSES
