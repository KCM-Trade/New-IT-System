"""T1 — offline tests of the pure exec-compensation functions (04 §2).

Fixtures are real MT5 rows fetched once by primary key and frozen in
``tests/fixtures/exec_comp/fills.json``; nothing here touches a database.
Constructed variants use ``dataclasses.replace`` on a real row so every
field not under test keeps a realistic value.

Classification follows 02 §3 (first match wins; rows 12/13 — ``no_plugin`` /
``plugin_passthrough`` — only ever override a row-9 ``market``).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.services.exec_comp import calc, classify
from app.services.exec_comp.errors import ExecCompError
from app.services.exec_comp.models import Account, RawFill

_FIXTURE = Path(__file__).parent / "fixtures" / "exec_comp" / "fills.json"
_DT_FIELDS = ("time_msc", "time_setup_msc")


def _load() -> dict[str, tuple[RawFill, Account]]:
    out: dict[str, tuple[RawFill, Account]] = {}
    for item in json.loads(_FIXTURE.read_text()):
        raw = dict(item["fill"])
        for key in _DT_FIELDS:
            if raw.get(key):
                raw[key] = dt.datetime.fromisoformat(raw[key])
        out[item["tag"]] = (RawFill(**raw), Account(**item["account"]))
    return out


ROWS = _load()


def fill(tag: str) -> RawFill:
    return ROWS[tag][0]


def acct(tag: str) -> Account:
    return ROWS[tag][1]


def cls_of(tag: str, **changes) -> str:
    f, a = ROWS[tag]
    return classify.classify(replace(f, **changes), a)


# ---------------------------------------------------------------------------
# classification: every row of the 02 §3 table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag, expected",
    [
        ("close_by", "close_by"),
        ("cls_close_by", "close_by"),
        ("late_limit", "limit"),
        ("cls_limit", "limit"),
        ("cls_stop", "stop"),
        ("cls_sl", "sl"),
        ("cls_tp", "tp"),
        ("cls_so_first", "so_first"),
        ("cls_so_rest", "so_rest"),
        ("cls_dealer", "dealer"),
        ("dealer_non_so", "dealer"),
        ("cls_market", "market"),
        ("usd_market_worse", "market"),
        ("market_better", "market"),
        ("cen_sell_worse", "market"),
        ("cls_no_plugin", "no_plugin"),
    ],
)
def test_fixture_rows_classify_as_tagged(tag, expected):
    f, a = ROWS[tag]
    got = classify.classify(f, a)
    assert got == expected
    assert classify.is_eligible(got) is (expected == "market")


def test_close_by_direction_comes_from_action_not_type():
    """Type 8 would read as a sell under the prototype's Type-based side; the
    contract takes the side from deals.Action (02 §4)."""
    f = fill("close_by")
    assert f.order_type == 8
    assert calc.side(f) == "sell"               # Action = 1
    buy = replace(f, action=0)
    assert calc.side(buy) == "buy"              # same Type 8, Action = 0
    assert classify.classify(buy, acct("close_by")) == "close_by"


def test_close_by_wins_over_so_comment_and_client_reason():
    # Row 2 precedes row 3 and row 9.
    assert cls_of("close_by", comment="[so at 10%]", order_reason=5) == "close_by"
    assert cls_of("close_by", order_reason=1) == "close_by"


def test_late_filled_limit_stays_limit():
    f = fill("late_limit")
    # filled hours after it was placed, still a limit
    assert f.time_msc - f.time_setup_msc > dt.timedelta(hours=3)
    assert classify.classify(f, acct("late_limit")) == "limit"


def test_so_comment_beats_order_type():
    # Row 3 precedes rows 4-6: a stop-out closing via a limit-typed order.
    assert cls_of("cls_so_rest", order_type=2) == "so_rest"
    assert cls_of("cls_so_first", order_type=4) == "so_first"


def test_so_first_needs_reason_5_else_so_rest():
    assert cls_of("cls_so_first", order_reason=5) == "so_first"
    assert cls_of("cls_so_first", order_reason=2) == "so_rest"
    assert cls_of("cls_so_first", order_reason=1) == "so_rest"


def test_dealer_is_reason_2_without_so_comment():
    assert cls_of("cls_so_rest", comment="") == "dealer"
    assert cls_of("cls_so_rest", comment="closed [so at 1%]") == "dealer"  # not a prefix


@pytest.mark.parametrize("t", [4, 5])
def test_stop_types(t):
    assert cls_of("cls_market", order_type=t) == "stop"


@pytest.mark.parametrize("t", [2, 3])
def test_limit_types(t):
    assert cls_of("cls_market", order_type=t) == "limit"


@pytest.mark.parametrize("t", [6, 7, 9, 42])
def test_unknown_type_is_other_type(t):
    assert cls_of("cls_market", order_type=t) == "other_type"


@pytest.mark.parametrize("reason", [5, 6, 9, 13, 20, 99])
def test_unknown_reason_is_other_reason(reason):
    got = cls_of("cls_market", order_reason=reason)
    assert got == "other_reason"
    assert not classify.is_eligible(got)


def test_sl_and_tp_by_reason():
    assert cls_of("cls_market", order_reason=3) == "sl"
    assert cls_of("cls_market", order_reason=4) == "tp"


@pytest.mark.parametrize("reason", [0, 1, 16, 17])
@pytest.mark.parametrize("t", [0, 1])
def test_client_reasons_on_market_types_are_market(reason, t):
    got = cls_of("cls_market", order_reason=reason, order_type=t)
    assert got == "market"
    assert classify.is_eligible(got)


def test_client_reason_whitelist_is_exactly_the_documented_four():
    assert classify.CLIENT_REASONS == frozenset({0, 1, 16, 17})
    assert classify.ELIGIBLE_CLASSES == frozenset({"market"})


# rows 12 / 13 -------------------------------------------------------------


def test_dealer_1_market_is_no_plugin():
    assert cls_of("cls_market", dealer=1) == "no_plugin"
    assert not classify.is_eligible("no_plugin")


def test_dealer_1_limit_stays_limit():
    """Routing overrides only replace market (02 §3 row 12)."""
    assert cls_of("cls_limit", dealer=1) == "limit"
    assert cls_of("late_limit", dealer=1) == "limit"
    assert cls_of("cls_sl", dealer=1) == "sl"


def _market_in_group(group: str, dealer: int) -> str:
    f = replace(fill("usd_market_worse"), dealer=dealer)
    return classify.classify(f, Account(login=f.login, ccy="USD", group=group))


def test_5ls_group_via_769_market_is_plugin_passthrough():
    assert _market_in_group("KCM\\5LS_L10", 769) == "plugin_passthrough"
    assert _market_in_group("KCM\\5LSaf_L10", 769) == "plugin_passthrough"
    assert _market_in_group("KCM\\5LS_P24L10", 769) == "plugin_passthrough"


def test_5ls_group_via_other_dealer_is_market():
    assert _market_in_group("KCM\\5LS_L10", 771) == "market"
    assert _market_in_group("KCM\\5LS_L10", 776) == "market"


def test_passthrough_glob_is_anchored_and_case_sensitive():
    assert _market_in_group("KCMC\\5LS_L10", 769) == "market"   # KCMC != KCM
    assert _market_in_group("kcm\\5ls_L10", 769) == "market"
    assert _market_in_group("KCM\\5SD_L10", 769) == "market"
    assert _market_in_group("", 769) == "market"


def test_dealer_1_beats_passthrough_table():
    assert _market_in_group("KCM\\5LS_L10", 1) == "no_plugin"


def test_passthrough_does_not_override_non_market_classes():
    """The fixture tagged cls_plugin_passthrough is a real KCM\\5LSaf@769 row,
    but its order is a stop-loss (Reason 3), so it stays ``sl`` — overrides
    only ever replace ``market`` (02 §3 row 13)."""
    f, a = ROWS["cls_plugin_passthrough"]
    assert f.dealer == 769 and a.group.startswith("KCM\\5LS")
    assert classify.classify(f, a) == "sl"
    # The same row as a client market close is plugin_passthrough.
    assert classify.classify(replace(f, order_reason=16, comment=""), a) == "plugin_passthrough"
    # dealer_non_so: KCM\5LS@769 with Reason 2 stays dealer.
    assert classify.classify(fill("dealer_non_so"), acct("dealer_non_so")) == "dealer"


def test_missing_order_row_cannot_be_classified():
    f = replace(
        fill("cls_market"),
        order_found=False,
        order_type=None,
        order_reason=None,
        comment=None,
        price_current=None,
        price_order=None,
        time_setup_msc=None,
    )
    with pytest.raises(ValueError):
        classify.classify(f, acct("cls_market"))
    assert calc.ref_price(f) is None
    assert calc.worse_px(f) is None
    assert calc.comp_usd(f, "CEN") is None
    assert calc.delay_ms(f) is None


# ---------------------------------------------------------------------------
# calc: price gap and money (02 §3.1 / §4)
# ---------------------------------------------------------------------------


def test_usd_sell_worse_gives_positive_comp_not_divided():
    f = fill("usd_market_worse")
    assert calc.side(f) == "sell"
    assert f.price_order == 0.0
    assert calc.ref_price(f) == f.price_current == 4274.3
    assert calc.worse_px(f) == pytest.approx(0.03, abs=1e-12)
    assert calc.outcome(calc.worse_px(f)) == "worse"
    # 0.03 * 500/10000 * 100 * 1.0 = 0.15 USD, no /100
    assert calc.comp_usd(f, "USD") == pytest.approx(0.15, abs=1e-9)


def test_cen_sell_worse_is_divided_by_100():
    f = fill("cen_sell_worse")
    assert acct("cen_sell_worse").ccy == "CEN"
    assert calc.worse_px(f) == pytest.approx(0.06, abs=1e-12)
    acct_ccy = 0.06 * 100 / 10000 * 1.0 * 1.0
    assert calc.comp_usd(f, "CEN") == pytest.approx(acct_ccy / 100, rel=1e-9)
    assert calc.comp_usd(f, "USD") == pytest.approx(acct_ccy, rel=1e-9)


def test_better_fill_is_negative_and_not_truncated():
    f = fill("market_better")
    assert calc.worse_px(f) == pytest.approx(-0.05, abs=1e-12)
    assert calc.outcome(calc.worse_px(f)) == "better"
    assert calc.comp_usd(f, "USD") == pytest.approx(-0.05, abs=1e-9)


def test_buy_worse_is_fill_minus_ref():
    f = replace(fill("usd_market_worse"), action=0, price=4274.35)
    assert calc.worse_px(f) == pytest.approx(0.05, abs=1e-12)
    f = replace(f, price=4274.25)
    assert calc.worse_px(f) == pytest.approx(-0.05, abs=1e-12)


def test_same_price_is_zero_and_same():
    f = replace(fill("usd_market_worse"), price=4274.3)
    assert calc.worse_px(f) == 0
    assert calc.outcome(0.0) == "same"
    assert calc.outcome(None) is None
    assert calc.comp_usd(f, "USD") == 0


def test_unknown_currency_fails_closed():
    with pytest.raises(ExecCompError) as ei:
        calc.comp_usd(fill("usd_market_worse"), "EUR")
    assert ei.value.code == "UNKNOWN_CURRENCY"
    with pytest.raises(ExecCompError):
        calc.to_usd(1.0, "")


def test_price_order_above_zero_is_the_reference():
    f = fill("cls_stop")
    assert f.price_order == 4274.06 and f.price_current == 4273.84
    assert calc.ref_price(f) == 4274.06
    # sell stop filled at 4273.84 vs requested 4274.06 -> 0.22 worse
    assert calc.worse_px(f) == pytest.approx(0.22, abs=1e-12)


def test_price_order_zero_or_none_falls_back_to_price_current():
    f = fill("cls_market")
    assert calc.ref_price(f) == 84457.56
    assert calc.ref_price(replace(f, price_order=None)) == 84457.56


@pytest.mark.parametrize("action, expected", [(1, 0.01), (0, -0.01)])
def test_float_noise_is_rounded_away(action, expected):
    f = replace(
        fill("usd_market_worse"),
        action=action,
        price=4274.290000000001,
        price_current=4274.3,
        price_order=0.0,
    )
    assert (4274.3 - 4274.290000000001) != 0.01  # the noise is real
    assert calc.worse_px(f) == expected          # exact, not approx


def test_side_rejects_non_trade_action():
    with pytest.raises(ValueError):
        calc.side(replace(fill("cls_market"), action=2))


def test_comp_uses_rate_profit_and_contract_size():
    f = fill("cls_dealer")  # USDCAD.kcmc CEN, rate_profit 0.707
    wpx = calc.worse_px(f)
    assert wpx == pytest.approx(0.00001, abs=1e-12)  # sell 1.41442 vs 1.41443
    expected = wpx * 100 / 10000 * 100000.0 * f.rate_profit / 100
    assert calc.comp_usd(f, "CEN") == pytest.approx(expected, rel=1e-9)


# ---------------------------------------------------------------------------
# unit self-check (02 §4.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tag", ["close_buy_unitcheck", "close_sell_unitcheck"])
def test_unit_check_passes_on_real_closes(tag):
    assert calc.unit_check_ok(fill(tag)) is True


@pytest.mark.parametrize("tag", ["usd_market_worse", "market_better", "cls_so_first"])
def test_unit_check_passes_on_other_real_closes(tag):
    assert calc.unit_check_ok(fill(tag)) is True


@pytest.mark.parametrize("tag", ["close_buy_unitcheck", "close_sell_unitcheck"])
def test_unit_check_fails_when_profit_is_tampered(tag):
    f = fill(tag)
    assert calc.unit_check_ok(replace(f, profit=f.profit + 1.0)) is False
    assert calc.unit_check_ok(replace(f, profit=-f.profit)) is False


def test_unit_check_not_applicable_to_opens_and_close_by():
    assert calc.unit_check_ok(fill("cls_market")) is None       # entry 0
    assert calc.unit_check_ok(fill("close_by")) is None         # entry 3
    assert calc.unit_check_ok(replace(fill("close_sell_unitcheck"), price_position=0.0)) is None


# ---------------------------------------------------------------------------
# time (02 §2 / §3.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tag", sorted(ROWS))
def test_server_offset_is_three_hours_on_all_fixtures(tag):
    """All fixtures are late-September (summer, GMT+3)."""
    assert calc.server_offset(fill(tag)) == dt.timedelta(hours=3)


def test_server_offset_falls_back_to_calendar_on_garbage_timestamp():
    summer = replace(fill("cls_market"), timestamp_ft=0)
    assert calc.server_offset(summer) == dt.timedelta(hours=3)
    winter = replace(
        fill("cls_market"),
        time_msc=dt.datetime(2026, 12, 15, 10, 0, 0),
        timestamp_ft=12345,
    )
    assert calc.server_offset(winter) == dt.timedelta(hours=2)


def test_server_offset_uses_the_rows_own_offset_in_winter():
    srv = dt.datetime(2026, 12, 15, 10, 0, 0, 123000)
    ft = calc.utc_to_filetime(srv - dt.timedelta(hours=2) + dt.timedelta(milliseconds=7))
    assert calc.server_offset(replace(fill("cls_market"), time_msc=srv, timestamp_ft=ft)) == dt.timedelta(hours=2)


def test_filetime_round_trip():
    utc = dt.datetime(2026, 9, 24, 7, 16, 16, 781000)
    back = calc.filetime_to_utc(calc.utc_to_filetime(utc))
    assert abs((back - utc).total_seconds()) < 1e-3
    # close_by fixture: its Timestamp is the true UTC of 10:16:16.781 server time
    f = fill("close_by")
    assert abs((calc.filetime_to_utc(f.timestamp_ft) - utc).total_seconds()) < 0.01


def test_delay_ms():
    assert calc.delay_ms(fill("cls_market")) == 575        # 58.421 - 57.846
    assert calc.delay_ms(fill("close_by")) == 0             # close-by delay is 0
    assert calc.delay_ms(fill("usd_market_worse")) == 424
    assert calc.delay_ms(replace(fill("cls_market"), time_setup_msc=None)) is None


def test_time_formatting():
    t = dt.datetime(2026, 9, 24, 7, 16, 16, 781000)
    assert calc.iso_z(t) == "2026-09-24T07:16:16.781Z"
    assert calc.srv_str(t) == "2026-09-24 07:16:16.781"


def test_entry_kind_map():
    assert calc.ENTRY_KIND == {0: "open", 1: "close", 2: "inout", 3: "close_by"}


# --- CALC_VERSION guard (OPT-0068 cold review) -------------------------------
#
# Cached results are keyed by CALC_VERSION: a rule change that forgets to bump
# it serves stale numbers for a whole cache TTL, and API callers cannot tell
# which rules produced a figure. The hash covers the code of classify.py and
# calc.py (every top-level constant and function, as an AST with docstrings
# stripped — comment / docstring edits do not trip it; rule edits do).

PINNED_CALC_HASH = {
    1: "31a2e66ad2f315790304ee9561eef4b01b98cf1717b59e7a561047bc7d8c24d8",
}


def _rules_fingerprint() -> str:
    import ast
    import hashlib
    import inspect

    from app.services.exec_comp import calc as calc_mod
    from app.services.exec_comp import classify as classify_mod

    h = hashlib.sha256()
    for mod in (classify_mod, calc_mod):
        tree = ast.parse(inspect.getsource(mod))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant):
                continue   # module docstring
            for sub in ast.walk(node):
                body = getattr(sub, "body", None)
                if (
                    isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    sub.body = body[1:] or [ast.Pass()]
            h.update(ast.dump(node, annotate_fields=False, include_attributes=False).encode())
    return h.hexdigest()


def test_calc_version_is_bumped_when_the_rules_change():
    from app.schemas.exec_compensation import CALC_VERSION

    assert PINNED_CALC_HASH.get(CALC_VERSION) == _rules_fingerprint(), (
        "classification/calc changed: bump schemas.CALC_VERSION and update the pinned hash"
    )
