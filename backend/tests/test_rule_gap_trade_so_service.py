"""Pin the rule-71 AB pair scope filter (`_pair_scope_filter`).

2026-09-15: the scope was widened from "different userid in the same
groupsid" (cross-client only) to ALSO keep same-client pairs — one client
mirroring a position across two of their own accounts (the Perfect
Edafiogho 5-67043827/5-67043828 weekend-gap case that the old filter
dropped). These tests exist so a future "cleanup" cannot silently revert
the filter to the old exclusion form.
"""

from app.services.rule_gap_trade_so_service import (
    _pair_scope_filter,
    _passes_dust_floor,
)


def test_default_scope_keeps_same_client_pairs():
    """Same userid on a different account must be a valid counterpart."""
    frag = _pair_scope_filter(True)
    assert "Cu.userid = Ls.L_userid" in frag


def test_default_scope_keeps_cross_client_same_group_pairs():
    """The original collusion form (same group, other client) still pairs."""
    frag = _pair_scope_filter(True)
    assert "Cu.groupsid = Ls.L_groupsid" in frag


def test_default_scope_is_a_disjunction_not_old_exclusion():
    """Regression guard against the pre-2026-09-15 filter.

    The old form required `Cu.userid != Ls.L_userid` (which excluded
    same-client mirrors) and applied `groupsid =` unconditionally. The new
    form must OR the two branches and must not exclude same-userid rows.
    """
    frag = _pair_scope_filter(True)
    assert "!=" not in frag
    assert "OR" in frag


def test_scope_filter_disabled_returns_empty():
    frag = _pair_scope_filter(False)
    assert frag == ""


# ── min_l_loss_usd dust floor and its two bypasses ──────────

FLOOR = 100.0


def _alert(l_profit_usd=-5.0, shared_ip_count=0, l_userid=1, c_userid=2):
    return {
        "l_profit_usd": l_profit_usd,
        "shared_ip_count": shared_ip_count,
        "l_userid": l_userid,
        "c_userid": c_userid,
    }


def test_dust_floor_drops_small_unrelated_pair():
    assert not _passes_dust_floor(_alert(l_profit_usd=-5.0), FLOOR)


def test_dust_floor_keeps_big_loss():
    assert _passes_dust_floor(_alert(l_profit_usd=-150.0), FLOOR)


def test_dust_floor_bypassed_by_shared_ip():
    assert _passes_dust_floor(_alert(l_profit_usd=-5.0, shared_ip_count=1), FLOOR)


def test_dust_floor_bypassed_by_same_client():
    """Same-client mirrors must surface even when per-order loss is dust.

    The Perfect Edafiogho pair (67043827/67043828, 2026-09-14) had
    per-order losses ≤ $19 against the $100 floor and shared an IP only on
    the close day — whose login_ip file does not exist yet at the 07:20
    cron. Without this bypass the same-client scope widening is dead code
    at scan time.
    """
    assert _passes_dust_floor(
        _alert(l_profit_usd=-18.77, l_userid=175460, c_userid=175460), FLOOR
    )


def test_dust_floor_same_client_requires_non_null_userid():
    """Two NULL userids must NOT read as \"same client\"."""
    assert not _passes_dust_floor(
        _alert(l_profit_usd=-5.0, l_userid=None, c_userid=None), FLOOR
    )
