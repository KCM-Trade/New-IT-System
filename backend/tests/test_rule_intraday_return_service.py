"""Tests for OPT-0062 Intraday Return detection (即日高收益, rule_id 131-140).

Formula v3 (docs/optimization/items/OPT-0062-intraday-return-rule.md §公式 v3):
same-day positions count in full; overnight positions count only the part
that is NEW inside the profit zone today (max(now,0) − max(yesterday EOD,0));
net_7d = realized over the window + all current floating.

Locked behaviours (each a concrete regression guard):
- the five §公式 v3 scenarios (same-day / overnight burst + no re-hit /
  carried loss recovering = 0 / loss-to-profit counts only the above-zero
  part / overnight floating counts only today's increment)
- initial_equity ≤ 0 → never evaluated (no ÷0)
- the four min_* 门槛 gate independently
- CEN money ÷100 before the 门槛 compare, ratio currency-immune
- deposit denylist (Balance Adjustment / Adjustment / Initial), withdrawals,
  credit into the base, include_deposits_in_base=False
- withdrawal > half of deposits flag
- behaviour features (trades / lots / median hold / lock_pct / top symbol)
- optional behaviour conditions (min_lock_pct / max_median_hold_min, None = off)
- tier suppression (highest min_return_pct wins) + per-day dedup via `alerted`
- normalize_rules band guard (OPT-0008-class) + label format
- assemble_mt5_accounts / assemble_mt4_accounts same-day vs carried split
- replay of the three watch accounts at the 300% tier

Every timestamp derives from datetime.now() (OPT-0041) — never a literal date.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import rule_intraday_return_service as svc
from app.services.rule_intraday_return_service import (
    INTRADAY_RETURN_RULE_ID_BASE,
    _apply_flow,
    assemble_mt4_accounts,
    assemble_mt5_accounts,
    compute_account_metrics,
    compute_behavior_features,
    is_real_flow,
    normalize_rules,
    rule_intraday_return_detect,
)

# Broker-local "today" anchor. DAY_START is the MT day boundary; NOW_LOCAL is
# ten hours into the day so intraday timelines have room on both sides.
NOW_UTC = datetime.now(timezone.utc).replace(microsecond=0)
DAY_START = (NOW_UTC + timedelta(hours=3)).replace(
    tzinfo=None, hour=0, minute=0, second=0,
)
NOW_LOCAL = DAY_START + timedelta(hours=10)
SCANNED_AT = NOW_UTC.isoformat(timespec="seconds").replace("+00:00", "Z")
TRADING_DAY = DAY_START.date().isoformat()


# ── builders ───────────────────────────────────────────────────────────

def _rule(**over):
    base = {
        "name": "tier", "enabled": True, "min_return_pct": 100.0,
        "min_initial_equity_usd": 50.0, "min_profit_usd": 30.0,
        "min_net_7d_usd": 0.0, "net_window_days": 7,
        "include_deposits_in_base": True, "min_lock_pct": None,
        "max_median_hold_min": None, "lock_ratio_min": 0.5,
    }
    base.update(over)
    return base


def _rules(*pcts):
    return normalize_rules([_rule(name=f"≥{p}%", min_return_pct=p) for p in pcts])


def _raw(**over):
    raw = {
        "prev_eq": 0.0, "prev_bal": 0.0, "prev_credit": 0.0,
        "dep_in": 0.0, "cred_in": 0.0, "withdrawals_out": 0.0, "adj_excluded": 0.0,
        "same_day_pnl": 0.0, "carried_now": 0.0, "realized_7d": 0.0,
        "floating_all_now": 0.0, "balance_now": 0.0, "credit_now": 0.0,
    }
    raw.update(over)
    return raw


def _pos(symbol="XAUUSD", direction="B", lots=1.0, open_min=60, close_min=None):
    """Position opened `open_min` minutes after day start (negative = overnight)."""
    return {
        "symbol": symbol, "direction": direction, "lots": lots,
        "open_time": DAY_START + timedelta(minutes=open_min),
        "close_time": None if close_min is None else DAY_START + timedelta(minutes=close_min),
    }


def _account(login=60011522, *, raw, currency="USD", positions=None, server="MT5"):
    raw = dict(raw)
    return {
        "server": server, "login": login, "currency": currency,
        "group": "real\\vn", "zipcode": None,
        "raw": raw,
        "raw_realized_7d_by_window": {7: raw.get("realized_7d", 0.0)},
        "positions": positions if positions is not None else [_pos(close_min=90)],
        "day_start": DAY_START, "now_local": NOW_LOCAL,
    }


def _detect(accounts, rules, alerted=None):
    return rule_intraday_return_detect(
        accounts, rules, alerted=alerted, scanned_at=SCANNED_AT, trading_day=TRADING_DAY,
    )


# ── 1. the five §公式 v3 scenarios ──────────────────────────────────────

WATCH = [
    # (login, prev_eq, dep_in, same_day_pnl, initial, return_pct)
    (60011522, 0.19, 50.0, 309.60, 50.19, 617),
    (60006521, 0.79, 75.0, 6079.22, 75.79, 8021),
    (60011522, 359.79, 0.0, 3388.25, 359.79, 942),
    (67044208, 0.00, 50.0, 478.27, 50.00, 957),
]


def test_scenario_same_day_open_and_close_counts_in_full():
    for login, prev_eq, dep_in, pnl, initial, pct in WATCH:
        m = compute_account_metrics(_raw(
            prev_eq=prev_eq, prev_bal=prev_eq, dep_in=dep_in,
            same_day_pnl=pnl, realized_7d=pnl,
        ))
        assert m["initial_equity"] == pytest.approx(initial, abs=0.005), login
        assert m["carried_float0"] == 0.0
        assert m["carried_now"] == 0.0
        assert m["carried_gain"] == 0.0
        assert m["intraday_profit"] == pytest.approx(pnl, abs=0.005)
        assert m["return_pct"] == pytest.approx(pct, abs=0.6), login
        assert m["net_7d"] == pytest.approx(pnl, abs=0.005)


def test_scenario_overnight_burst_hits_then_does_not_rehit_next_day():
    # Day 1: $50 opened yesterday, +20 floating at yesterday EOD, +500 now.
    m = compute_account_metrics(_raw(prev_eq=70.0, prev_bal=50.0, carried_now=500.0,
                                     floating_all_now=500.0))
    assert m["carried_float0"] == 20.0
    assert m["carried_gain"] == 480.0
    assert m["initial_equity"] == 70.0
    assert m["intraday_profit"] == 480.0
    assert m["return_pct"] == pytest.approx(685.7, abs=0.05)
    # Day 2: floating did not grow → numerator 0, no re-hit.
    m2 = compute_account_metrics(_raw(prev_eq=550.0, prev_bal=50.0, carried_now=500.0,
                                      floating_all_now=500.0))
    assert m2["carried_float0"] == 500.0
    assert m2["carried_gain"] == 0.0
    assert m2["intraday_profit"] == 0.0
    assert m2["return_pct"] == 0.0
    alerts, _ = _detect([_account(raw=_raw(prev_eq=550.0, prev_bal=50.0, carried_now=500.0,
                                            floating_all_now=500.0))], _rules(100, 300))
    assert alerts == []


def test_scenario_carried_loss_recovering_is_zero_profit_and_negative_net_7d():
    # 8611807: 3-lot XAUUSD held since 8/28, −62,471 → −39,763, no trades today.
    raw = _raw(prev_eq=10000.0 - 62471.0, prev_bal=10000.0, carried_now=-39763.0,
               floating_all_now=-24350.0, realized_7d=0.0)
    m = compute_account_metrics(raw)
    assert m["carried_float0"] == -62471.0
    assert m["carried_gain"] == 0.0
    assert m["intraday_profit"] == 0.0
    assert m["net_7d"] == -24350.0
    alerts, _ = _detect([_account(login=8611807, raw=raw, server="MT4_Live", positions=[_pos(open_min=-3000)])],
                        _rules(100, 300))
    assert alerts == []


def test_scenario_overnight_loss_turning_profit_counts_only_above_zero():
    # 8521502: five overnight XAGUSD longs, −403 at EOD → closed 74.64 + floating 645.60.
    raw = _raw(prev_eq=548.0, prev_bal=951.0, carried_now=74.64 + 645.60,
               floating_all_now=645.60, realized_7d=74.64)
    m = compute_account_metrics(raw)
    assert m["carried_float0"] == pytest.approx(-403.0)
    assert m["carried_gain"] == pytest.approx(720.24)
    assert m["intraday_profit"] == pytest.approx(720.24)
    assert m["initial_equity"] == 548.0
    assert m["return_pct"] == pytest.approx(131.4, abs=0.05)
    acct = _account(login=8521502, raw=raw, server="MT4_Live", positions=[_pos(open_min=-1440)])
    alerts, _ = _detect([acct], _rules(100, 300))
    assert [a["rule_id"] for a in alerts] == [INTRADAY_RETURN_RULE_ID_BASE]  # 100% tier only
    alerts_300, _ = _detect([acct], _rules(300))
    assert alerts_300 == []


def test_scenario_overnight_floating_profit_counts_only_todays_increment():
    m = compute_account_metrics(_raw(prev_eq=1100.0, prev_bal=1000.0, carried_now=150.0,
                                     realized_7d=150.0))
    assert m["carried_float0"] == 100.0
    assert m["carried_now"] == 150.0
    assert m["carried_gain"] == 50.0
    assert m["intraday_profit"] == 50.0


# ── 2. ÷0 guard ────────────────────────────────────────────────────────

def test_initial_equity_non_positive_is_skipped_not_divided():
    for prev_eq in (0.0, -25.0):
        m = compute_account_metrics(_raw(prev_eq=prev_eq, same_day_pnl=500.0))
        assert m["return_pct"] is None
        alerts, updates = _detect([_account(raw=_raw(prev_eq=prev_eq, same_day_pnl=500.0))],
                                  _rules(100))
        assert alerts == [] and updates == []


# ── 3. the 门槛 gate independently ─────────────────────────────────────

def test_each_threshold_gates_independently():
    good = _raw(prev_eq=60.0, prev_bal=60.0, same_day_pnl=120.0, realized_7d=120.0)  # 200%
    assert len(_detect([_account(raw=good)], _rules(100))[0]) == 1

    # initial_equity below min_initial_equity_usd
    assert _detect([_account(raw=good)],
                   normalize_rules([_rule(min_initial_equity_usd=61.0)]))[0] == []
    # profit below min_profit_usd
    assert _detect([_account(raw=good)],
                   normalize_rules([_rule(min_profit_usd=121.0)]))[0] == []
    # return below min_return_pct
    assert _detect([_account(raw=good)],
                   normalize_rules([_rule(min_return_pct=201.0)]))[0] == []
    # positive intraday but negative net_7d (lost 500 earlier this week)
    bad_week = dict(good, realized_7d=120.0 - 500.0)
    assert _detect([_account(raw=bad_week)], _rules(100))[0] == []
    assert _detect([_account(raw=bad_week)],
                   normalize_rules([_rule(min_net_7d_usd=-1000.0)]))[0] != []


# ── 4. CEN ────────────────────────────────────────────────────────────

def test_cen_money_divided_by_100_before_thresholds_ratio_unchanged():
    cents = _raw(prev_eq=5000.0, prev_bal=5000.0, same_day_pnl=3000.0, realized_7d=3000.0)
    usd = compute_account_metrics(cents, divisor=1.0)
    cen = compute_account_metrics(cents, divisor=100.0)
    assert usd["initial_equity"] == 5000.0 and cen["initial_equity"] == 50.0
    assert usd["intraday_profit"] == 3000.0 and cen["intraday_profit"] == 30.0
    assert usd["return_pct"] == cen["return_pct"] == 60.0

    # 60% never fires; drop the ratio 门槛 to isolate the money 门槛.
    rules = normalize_rules([_rule(min_return_pct=10.0, min_initial_equity_usd=60.0,
                                   min_profit_usd=30.0)])
    assert len(_detect([_account(raw=cents, currency="USD")], rules)[0]) == 1
    assert _detect([_account(raw=cents, currency="CEN")], rules)[0] == []
    rules2 = normalize_rules([_rule(min_return_pct=10.0, min_initial_equity_usd=50.0,
                                    min_profit_usd=31.0)])
    assert _detect([_account(raw=cents, currency="CEN")], rules2)[0] == []
    alerts, _ = _detect([_account(raw=cents, currency="CEN")],
                        normalize_rules([_rule(min_return_pct=10.0)]))
    assert alerts[0]["initial_equity"] == 50.0 and alerts[0]["intraday_profit"] == 30.0


# ── 5. deposit denylist / flows ──────────────────────────────────────────

def test_deposit_denylist_withdrawals_credit_and_base_toggle():
    for c in ("Balance Adjustment Zero", "Adjustment - #12", "Initial balance"):
        assert is_real_flow(c) is False, c
    for c in ("DEPOSIT", "D-123", "IT-D #5", "IB Wallet Transfer", "XTHB-Deposit-1"):
        assert is_real_flow(c) is True, c

    raw = _raw()
    _apply_flow(raw, is_balance_op=True, profit=779000.0, comment="Balance Adjustment Zero")
    _apply_flow(raw, is_balance_op=True, profit=10.0, comment="Adjustment - #12")
    _apply_flow(raw, is_balance_op=True, profit=100.0, comment="Initial balance")
    assert raw["dep_in"] == 0.0 and raw["adj_excluded"] == 779110.0
    for c in ("DEPOSIT", "D-123", "IT-D #5", "IB Wallet Transfer", "XTHB-Deposit-1"):
        _apply_flow(raw, is_balance_op=True, profit=10.0, comment=c)
    assert raw["dep_in"] == 50.0
    _apply_flow(raw, is_balance_op=True, profit=-20.0, comment="W-77")
    assert raw["withdrawals_out"] == 20.0
    _apply_flow(raw, is_balance_op=False, profit=25.0, comment="Credit In")
    _apply_flow(raw, is_balance_op=False, profit=-5.0, comment="Credit Out")
    assert raw["cred_in"] == 25.0

    raw.update(prev_eq=10.0, prev_bal=10.0)
    m = compute_account_metrics(raw, include_deposits_in_base=True)
    assert m["initial_equity"] == 85.0            # 10 + 50 deposits + 25 credit
    assert m["adj_excluded"] == 779110.0
    m2 = compute_account_metrics(raw, include_deposits_in_base=False)
    assert m2["initial_equity"] == 10.0


# ── 6. withdrawal flag ─────────────────────────────────────────────────

def test_withdraw_flag_when_more_than_half_of_deposits_pulled():
    assert compute_account_metrics(_raw(dep_in=100.0, withdrawals_out=51.0))["flag_withdraw_gt_half_deposit"] == 1
    assert compute_account_metrics(_raw(dep_in=100.0, withdrawals_out=50.0))["flag_withdraw_gt_half_deposit"] == 0
    assert compute_account_metrics(_raw(dep_in=0.0, withdrawals_out=0.0))["flag_withdraw_gt_half_deposit"] == 0
    # no deposit but a withdrawal still flags (0 > 0 × 0.5 is false only at zero)
    assert compute_account_metrics(_raw(dep_in=0.0, withdrawals_out=10.0))["flag_withdraw_gt_half_deposit"] == 1


# ── 7. behaviour features ──────────────────────────────────────────────

def test_behavior_features_counts_and_lock_pct():
    positions = [
        _pos("XAUUSD", "B", 1.0, open_min=60, close_min=120),    # 60 min hold
        _pos("XAUUSD", "B", 0.5, open_min=130, close_min=140),   # 10 min hold
        _pos("EURUSD", "S", 2.0, open_min=150, close_min=180),   # 30 min hold
        _pos("XAUUSD", "S", 1.0, open_min=200),                  # still open
    ]
    f = compute_behavior_features(positions, day_start=DAY_START, now_local=NOW_LOCAL)
    assert f["trades_today"] == 4
    assert f["lots_today"] == 4.5
    assert f["median_hold_sec"] == 30 * 60
    assert f["top_symbol"] == "XAUUSD"
    assert f["first_open"] == DAY_START + timedelta(minutes=60)
    assert f["last_open"] == DAY_START + timedelta(minutes=200)
    assert f["lock_pct"] == 0.0

    # Buy 1.0 for [60,120) and Sell 1.0 for [90,120): active 60 min, locked 30 → 50%.
    locked = [
        _pos("XAUUSD", "B", 1.0, open_min=60, close_min=120),
        _pos("XAUUSD", "S", 1.0, open_min=90, close_min=120),
    ]
    f2 = compute_behavior_features(locked, day_start=DAY_START, now_local=NOW_LOCAL)
    assert f2["lock_pct"] == pytest.approx(50.0, abs=1.0)
    # min/max = 1.0 → a ratio 门槛 above 1 disables the lock
    f3 = compute_behavior_features(locked, day_start=DAY_START, now_local=NOW_LOCAL,
                                   lock_ratio_min=1.01)
    assert f3["lock_pct"] == 0.0
    # CEN lots ÷100
    assert compute_behavior_features(locked, day_start=DAY_START, now_local=NOW_LOCAL,
                                     divisor=100.0)["lots_today"] == 0.02

    # Overnight position: not a trade today, but it is on the timeline.
    overnight = [_pos("XAUUSD", "B", 1.0, open_min=-600, close_min=120),
                 _pos("XAUUSD", "S", 1.0, open_min=60, close_min=120)]
    f4 = compute_behavior_features(overnight, day_start=DAY_START, now_local=NOW_LOCAL)
    assert f4["trades_today"] == 1
    assert f4["lots_today"] == 1.0
    assert f4["first_open"] == DAY_START + timedelta(minutes=60)
    # active [0,120) = 120 min, locked [60,120) = 60 min → 50%
    assert f4["lock_pct"] == pytest.approx(50.0, abs=1.0)


# ── 8. optional behaviour conditions ───────────────────────────────────

def test_optional_behavior_conditions_lock_and_median_hold():
    raw = _raw(prev_eq=60.0, prev_bal=60.0, same_day_pnl=120.0, realized_7d=120.0)
    unlocked = [_pos("XAUUSD", "B", 1.0, open_min=60, close_min=80)]
    locked = [_pos("XAUUSD", "B", 1.0, open_min=60, close_min=120),
              _pos("XAUUSD", "S", 1.0, open_min=90, close_min=120)]
    lock_rule = normalize_rules([_rule(min_lock_pct=30.0)])
    assert _detect([_account(raw=raw, positions=unlocked)], lock_rule)[0] == []
    assert len(_detect([_account(raw=raw, positions=locked)], lock_rule)[0]) == 1

    hold_rule = normalize_rules([_rule(max_median_hold_min=15.0)])
    slow = [_pos("XAUUSD", "B", 1.0, open_min=60, close_min=80)]     # 20 min
    fast = [_pos("XAUUSD", "B", 1.0, open_min=60, close_min=65)]     # 5 min
    assert _detect([_account(raw=raw, positions=slow)], hold_rule)[0] == []
    assert len(_detect([_account(raw=raw, positions=fast)], hold_rule)[0]) == 1
    # no closed round-trip today → median unknown → the condition cannot pass
    assert _detect([_account(raw=raw, positions=[_pos(open_min=60)])], hold_rule)[0] == []

    # None = not applied: both accounts pass a rule with no behaviour knobs.
    plain = normalize_rules([_rule()])
    assert plain[0]["min_lock_pct"] is None and plain[0]["max_median_hold_min"] is None
    assert len(_detect([_account(raw=raw, positions=unlocked)], plain)[0]) == 1
    assert len(_detect([_account(raw=raw, positions=slow)], plain)[0]) == 1


# ── 9. tier suppression + dedup ────────────────────────────────────────

def test_tier_suppression_and_per_day_dedup_updates():
    key = ("MT5", 60011522)
    r500 = _raw(prev_eq=100.0, prev_bal=100.0, same_day_pnl=500.0, realized_7d=500.0)
    rules = _rules(100, 300)

    alerts, updates = _detect([_account(raw=r500)], rules)
    assert [a["rule_id"] for a in alerts] == [132]      # 300% suppresses 100%
    assert updates == []

    alerts, updates = _detect([_account(raw=r500)], rules, alerted={key: {132: 77}})
    assert alerts == []
    assert len(updates) == 1
    assert updates[0]["alert_id"] == 77
    assert updates[0]["return_pct"] == 500.0
    assert updates[0]["trading_day"] == TRADING_DAY

    alerts, updates = _detect([_account(raw=r500)], rules, alerted={key: {131: 55}})
    assert [a["rule_id"] for a in alerts] == [132]
    assert [u["alert_id"] for u in updates] == [55]
    assert updates[0]["return_pct"] == 500.0

    r150 = _raw(prev_eq=100.0, prev_bal=100.0, same_day_pnl=150.0, realized_7d=150.0)
    alerts, updates = _detect([_account(raw=r150)], rules)
    assert [a["rule_id"] for a in alerts] == [131]
    assert alerts[0]["rule_label"].startswith("Rule 1")


# ── 10. normalize_rules ────────────────────────────────────────────────

def test_normalize_rules_band_guard_and_label():
    norm = normalize_rules([
        {"id": 2, "name": "first", "enabled": True, "min_return_pct": 100},
        {"id": 3, "name": "off", "enabled": False, "min_return_pct": 200},
        {"id": 999, "name": "third", "min_return_pct": 300, "min_lock_pct": ""},
        {"id": 135, "name": "kept", "min_return_pct": 400},
    ])
    assert [r["id"] for r in norm] == [131, 133, 135]
    assert [r["idx"] for r in norm] == [0, 2, 3]
    assert norm[0]["label"] == "Rule 1 — first"
    assert norm[1]["min_lock_pct"] is None and norm[1]["max_median_hold_min"] is None
    assert norm[0]["min_initial_equity_usd"] == 50.0
    assert norm[0]["min_profit_usd"] == 30.0
    assert norm[0]["net_window_days"] == 7
    assert norm[0]["include_deposits_in_base"] is True
    assert normalize_rules([]) == []


# ── 11. assemble_* ─────────────────────────────────────────────────────

def _t(minutes):
    return DAY_START + timedelta(minutes=minutes)


def test_assemble_mt5_accounts_same_day_vs_carried_split():
    L = 60011522
    deals = [
        # same-day position 1: open + close today
        {"login": L, "deal": 1, "position_id": 11, "action": 0, "entry": 0, "symbol": "XAUUSD",
         "lots": 0.1, "profit": 0.0, "storage": 0.0, "commission": 0.0, "comment": "", "time_local": _t(60)},
        {"login": L, "deal": 2, "position_id": 11, "action": 1, "entry": 1, "symbol": "XAUUSD",
         "lots": 0.1, "profit": 100.0, "storage": -1.0, "commission": -2.0, "comment": "", "time_local": _t(90)},
        # carried position 22: only the close is in today's deals
        {"login": L, "deal": 3, "position_id": 22, "action": 1, "entry": 1, "symbol": "XAUUSD",
         "lots": 0.2, "profit": 40.0, "storage": 0.0, "commission": 0.0, "comment": "", "time_local": _t(100)},
        # deposit + adjustment + credit
        {"login": L, "deal": 4, "position_id": 0, "action": 2, "entry": 0, "symbol": "",
         "lots": 0.0, "profit": 50.0, "storage": 0.0, "commission": 0.0, "comment": "DEPOSIT", "time_local": _t(5)},
        {"login": L, "deal": 5, "position_id": 0, "action": 2, "entry": 0, "symbol": "",
         "lots": 0.0, "profit": 999.0, "storage": 0.0, "commission": 0.0, "comment": "Balance Adjustment Zero", "time_local": _t(6)},
        {"login": L, "deal": 6, "position_id": 0, "action": 3, "entry": 0, "symbol": "",
         "lots": 0.0, "profit": 10.0, "storage": 0.0, "commission": 0.0, "comment": "bonus", "time_local": _t(7)},
        # same-day position 33 still open (opening deal only)
        {"login": L, "deal": 7, "position_id": 33, "action": 0, "entry": 0, "symbol": "EURUSD",
         "lots": 0.3, "profit": 0.0, "storage": 0.0, "commission": 0.0, "comment": "", "time_local": _t(120)},
    ]
    positions = [
        {"login": L, "position_id": 33, "symbol": "EURUSD", "action": 0, "lots": 0.3,
         "profit": 7.0, "storage": 0.0, "open_time_local": _t(120)},
        {"login": L, "position_id": 44, "symbol": "XAUUSD", "action": 1, "lots": 1.0,
         "profit": -30.0, "storage": -0.5, "open_time_local": _t(-600)},
    ]
    out = assemble_mt5_accounts(deals=deals, positions=positions, day_start=DAY_START, now_local=NOW_LOCAL)
    raw = out[L]["raw"]
    assert raw["same_day_pnl"] == pytest.approx(97.0 + 7.0)     # closed 100-1-2 + floating 7
    assert raw["carried_now"] == pytest.approx(40.0 - 30.5)     # carried close + carried floating
    assert raw["floating_all_now"] == pytest.approx(7.0 - 30.5)
    assert raw["realized_today"] == pytest.approx(97.0 + 40.0)
    assert raw["dep_in"] == 50.0
    assert raw["adj_excluded"] == 999.0
    assert raw["cred_in"] == 10.0
    pos = {(p["symbol"], p["direction"], p["lots"]): p for p in out[L]["positions"]}
    assert pos[("XAUUSD", "B", 0.1)]["open_time"] == _t(60)
    assert pos[("XAUUSD", "B", 0.1)]["close_time"] == _t(90)
    assert pos[("EURUSD", "B", 0.3)]["close_time"] is None
    assert pos[("XAUUSD", "S", 1.0)]["open_time"] == _t(-600)
    carried_closed = pos[("XAUUSD", "B", 0.2)]     # closing deal was a sell → position was a buy
    assert carried_closed["open_time"] < DAY_START and carried_closed["close_time"] == _t(100)
    assert "_open_meta" not in out[L]


def test_assemble_mt4_accounts_same_day_vs_carried_split():
    L = 8521502
    rows = [
        {"login": L, "ticket": 1, "cmd": 0, "symbol": "XAGUSD", "lots": 0.5,
         "open_time_local": _t(-1440), "close_time_local": _t(30),
         "profit": 74.64, "storage": 0.0, "commission": 0.0, "comment": ""},
        {"login": L, "ticket": 2, "cmd": 1, "symbol": "XAUUSD", "lots": 0.1,
         "open_time_local": _t(60), "close_time_local": _t(70),
         "profit": 20.0, "storage": 0.0, "commission": -1.0, "comment": ""},
        {"login": L, "ticket": 3, "cmd": 6, "symbol": "", "lots": 0.0,
         "open_time_local": _t(5), "close_time_local": _t(5),
         "profit": 100.0, "storage": 0.0, "commission": 0.0, "comment": "D-123"},
        {"login": L, "ticket": 4, "cmd": 6, "symbol": "", "lots": 0.0,
         "open_time_local": _t(6), "close_time_local": _t(6),
         "profit": -30.0, "storage": 0.0, "commission": 0.0, "comment": "W-1"},
        {"login": L, "ticket": 5, "cmd": 7, "symbol": "", "lots": 0.0,
         "open_time_local": _t(7), "close_time_local": _t(7),
         "profit": 15.0, "storage": 0.0, "commission": 0.0, "comment": "Credit In"},
    ]
    positions = [
        {"login": L, "ticket": 6, "cmd": 0, "symbol": "XAGUSD", "lots": 2.0,
         "open_time_local": _t(-1440), "profit": 645.60, "storage": 0.0},
        {"login": L, "ticket": 7, "cmd": 1, "symbol": "XAUUSD", "lots": 0.1,
         "open_time_local": _t(200), "profit": 3.0, "storage": 0.0},
    ]
    out = assemble_mt4_accounts(today_rows=rows, positions=positions, day_start=DAY_START, now_local=NOW_LOCAL)
    raw = out[L]["raw"]
    assert raw["carried_now"] == pytest.approx(74.64 + 645.60)
    assert raw["same_day_pnl"] == pytest.approx(19.0 + 3.0)
    assert raw["floating_all_now"] == pytest.approx(648.60)
    assert raw["realized_today"] == pytest.approx(74.64 + 19.0)
    assert raw["dep_in"] == 100.0 and raw["withdrawals_out"] == 30.0 and raw["cred_in"] == 15.0
    assert len(out[L]["positions"]) == 4
    f = compute_behavior_features(out[L]["positions"], day_start=DAY_START, now_local=NOW_LOCAL)
    assert f["trades_today"] == 2 and f["lots_today"] == 0.2


# ── 12. replay: three watch accounts at the 300% tier ──────────────────

def test_replay_watch_accounts_hit_300_tier():
    accounts = []
    for login, prev_eq, dep_in, pnl, _initial, _pct in WATCH:
        accounts.append(_account(
            login=login,
            raw=_raw(prev_eq=prev_eq, prev_bal=prev_eq, dep_in=dep_in,
                     same_day_pnl=pnl, realized_7d=pnl, balance_now=prev_eq + dep_in + pnl),
            positions=[_pos("XAUUSD", "B", 0.1, open_min=60, close_min=70),
                       _pos("XAUUSD", "S", 0.1, open_min=65, close_min=75)],
        ))
    alerts, updates = _detect(accounts, _rules(100, 300))
    assert updates == []
    assert len(alerts) == 4
    for a, (login, prev_eq, dep_in, pnl, initial, pct) in zip(alerts, WATCH):
        assert a["rule_id"] == 132
        assert a["rule_label"].startswith("Rule 2")
        assert a["server"] == "MT5" and a["login"] == login
        assert a["symbol"] == a["top_symbol"] == "XAUUSD"
        assert a["order_count"] == a["trades_today"] == 2
        assert a["total_lots"] == a["lots_today"] == 0.2
        assert a["peak_return_pct"] == a["return_pct"] == pytest.approx(pct, abs=0.6)
        assert a["initial_equity"] == pytest.approx(initial, abs=0.005)
        assert a["intraday_profit"] == pytest.approx(pnl, abs=0.005)
        assert a["scanned_at"] == SCANNED_AT
        assert a["trading_day"] == TRADING_DAY
        assert a["currency"] == "USD"
        assert a["orders"] == []
        assert a["first_open"] is not None and a["last_open"] is not None
