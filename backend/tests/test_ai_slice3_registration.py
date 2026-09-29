"""OPT-0066 slice 3 — who gets the Risk control tools, and the anti-drift pins.

docs/ai-agent/11 §0 T1: the three Risk control tools exist only for a caller
holding BOTH `ai` and `risk` ("*" counts) AND an unrestricted scope. It is a
structural gate like run_sql's: an ineligible caller does not see the tools
at all (not a refusal). A restricted caller who somehow holds `risk` is not
registered either (fail-closed, mirrors the page side's 403).

Anti-drift:
  * TOOL_IMPLS keys == TOOL_DOCSTRINGS keys == build_tools names for a caller
    who gets everything;
  * TAB_BANDS keys == RISK_MONITOR_TABS in frontend RiskMonitor.tsx;
  * the prompt's Risk control block appears iff risk_tools, names all 8 tabs,
    and FORBIDDEN_WORDS ⊇ rule 5's words.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai_agent.tools.common import CallerCtx, has_module, risk_tools_enabled

RISK_TOOLS = {"get_risk_alerts", "get_alert_orders", "get_window_scan"}
REPO = Path(__file__).resolve().parents[2]


def ctx(modules, scope=None) -> CallerCtx:
    return CallerCtx(user_id=7, email="staff@kohleservices.com", role="user", allowed_modules=tuple(modules),
                     scope=scope, trace_id="t-1", settings=SimpleNamespace())


# ── has_module / risk_tools_enabled ──────────────────────────────────────────


@pytest.mark.parametrize(
    "modules,scope,expected",
    [
        ([], None, False),
        (["*"], None, True),
        (["risk"], None, True),
        (["ai", "risk"], None, True),
        (["cs"], None, False),
        (["ai", "cs"], None, False),
        (["risk"], frozenset({1}), False),
        (["*"], frozenset({1}), False),
        (["risk"], frozenset(), False),  # empty scope is RESTRICTED, not "unset"
    ],
)
def test_registration_gate(modules, scope, expected):
    assert risk_tools_enabled(ctx(modules, scope)) is expected


def test_has_module_star_means_all_and_empty_means_none():
    assert has_module(ctx(["*"]), "risk") is True
    assert has_module(ctx(["*"]), "anything") is True
    assert has_module(ctx([]), "risk") is False
    assert has_module(ctx(["cs", "ai"]), "risk") is False
    assert has_module(ctx(["cs", "risk"]), "risk") is True


def test_restricted_caller_holding_risk_logs_a_warning(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        assert risk_tools_enabled(ctx(["ai", "risk"], frozenset({1}))) is False
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_unrestricted_or_riskless_callers_do_not_warn(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        risk_tools_enabled(ctx(["ai", "risk"]))
        risk_tools_enabled(ctx(["ai"], frozenset({1})))
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ── build_tools: three lists ─────────────────────────────────────────────────

BASE = ["get_client_overview", "get_trade_activity", "get_risk_signals", "rank_accounts", "get_economic_calendar",
        "rank_open_positions"]


def _names(c):
    pytest.importorskip("agent_framework")
    from app.ai_agent import harness

    async def _emit(_e, _d):
        pass

    return [t.name for t in harness.build_tools(c, _emit)]


def test_build_tools_three_lists():
    restricted = _names(ctx(["ai", "risk"], frozenset({1})))
    no_risk = _names(ctx(["ai"]))
    with_risk = _names(ctx(["ai", "risk"]))
    star = _names(ctx(["*"]))
    assert restricted == BASE
    assert no_risk == BASE + ["run_sql"]
    assert set(with_risk) == set(BASE) | {"run_sql"} | RISK_TOOLS
    assert with_risk[: len(BASE)] == BASE
    assert set(star) == set(with_risk)
    # cs-only + unrestricted: still no risk tools
    assert not RISK_TOOLS & set(_names(ctx(["ai", "cs"])))
    assert not RISK_TOOLS & set(_names(ctx([])))


def test_tool_registries_agree():
    from app.ai_agent.prompt import TOOL_DOCSTRINGS
    from app.ai_agent.tools import TOOL_IMPLS

    everything = set(_names(ctx(["*"])))
    # run_sql is registered straight from its module, not via TOOL_IMPLS.
    assert set(TOOL_IMPLS) | {"run_sql"} == everything
    assert set(TOOL_DOCSTRINGS) == everything
    assert RISK_TOOLS <= set(TOOL_IMPLS)


# ── TAB_BANDS ↔ frontend ─────────────────────────────────────────────────────


def _frontend_tabs() -> list[str]:
    src = (REPO / "frontend" / "src" / "pages" / "RiskMonitor.tsx").read_text(encoding="utf-8")
    m = re.search(r"const RISK_MONITOR_TABS = \[(.*?)\]\s*as const", src, re.S)
    assert m, "RISK_MONITOR_TABS literal not found in RiskMonitor.tsx"
    return re.findall(r'"([a-z-]+)"', m.group(1))


def test_tab_bands_match_the_frontend_tab_list():
    from app.ai_agent.tools.risk_bands import TAB_BANDS, TAB_TIME_FIELD

    tabs = _frontend_tabs()
    assert len(tabs) == 8
    assert set(TAB_BANDS) == set(tabs)
    assert set(TAB_TIME_FIELD) == set(tabs)
    assert TAB_TIME_FIELD["intraday-return"] == "trading_day"
    assert {v for k, v in TAB_TIME_FIELD.items() if k != "intraday-return"} == {"scanned_at"}


def test_tab_bands_cover_known_bands_only():
    from app.ai_agent.tools.risk_bands import RULE_BANDS, TAB_BANDS

    band_ranges = {(lo, hi) for lo, hi, _ in RULE_BANDS}
    for ranges in TAB_BANDS.values():
        assert set(ranges) <= band_ranges
    assert TAB_BANDS["gap-trade"] == ((71, 80), (81, 90))
    # rebate arbitrage (121–130) is retired: no tab
    assert all((121, 130) not in r for r in TAB_BANDS.values())


def test_band_fields_carry_no_pii():
    from app.ai_agent.tools.risk_bands import BAND_FIELDS

    banned = {"l_name", "c_name", "client_name", "zipcode", "account_group", "group", "shared_ips",
              "so_comment", "account_remarks", "COMMENT", "l_userid", "c_userid", "client_userid"}
    for band, spec in BAND_FIELDS.items():
        assert not banned & set(spec["fields"]), band


def test_band_metrics_exist_in_the_aggregate_whitelist():
    from app.ai_agent.tools.risk_bands import BAND_FIELDS
    from app.core.risk_monitor_db import AGG_METRICS

    for band, spec in BAND_FIELDS.items():
        if spec["metric"] is not None:
            assert spec["metric"] in AGG_METRICS, band


def test_risk_signals_still_exports_the_band_helpers():
    from app.ai_agent.tools import risk_bands
    from app.ai_agent.tools import risk_signals

    assert risk_signals.RULE_BANDS == risk_bands.RULE_BANDS
    assert risk_signals.rule_band_name(135) == "intraday_return"


# ── prompt ───────────────────────────────────────────────────────────────────

NOW = datetime(2026, 9, 28, 4, 0, tzinfo=timezone.utc)


def test_risk_block_follows_the_flag():
    from app.ai_agent.prompt import RISK_CONTROL_BLOCK, system_prompt

    off = system_prompt(NOW)
    on = system_prompt(NOW, risk_tools=True)
    assert "## Risk control pages" not in off
    assert RISK_CONTROL_BLOCK not in off
    assert "## Risk control pages" in on and RISK_CONTROL_BLOCK in on
    assert on.count("## Today") == 1 and on.rstrip().endswith(off.rstrip().split("## Today", 1)[1].rstrip())
    assert system_prompt(NOW, risk_tools=False) == off


def test_risk_block_maps_every_tab_and_names_the_tools():
    from app.ai_agent.prompt import RISK_CONTROL_BLOCK

    for tab in _frontend_tabs():
        assert tab in RISK_CONTROL_BLOCK, tab
    for tool in RISK_TOOLS:
        assert tool in RISK_CONTROL_BLOCK


def test_forbidden_words_superset_of_rule_5():
    from app.ai_agent.prompt import FORBIDDEN_WORDS, RISK_CONTROL_BLOCK

    words = {w.lower() for w in FORBIDDEN_WORDS}
    assert {"fraudster", "abuser", "violator"} <= words
    assert {"cheater", "scammer"} <= words
    for zh in ("作弊", "欺诈", "违规者", "套利者"):
        assert zh in FORBIDDEN_WORDS
    # the block tells the model about them
    assert "cheater" in RISK_CONTROL_BLOCK


def test_harness_passes_risk_flag_to_the_prompt(monkeypatch):
    pytest.importorskip("agent_framework")
    import asyncio

    from app.ai_agent import harness

    seen = {}

    class _Boom(Exception):
        pass

    def fake_prompt(*a, **k):
        seen.update(k)
        raise _Boom()

    monkeypatch.setattr(harness, "system_prompt", fake_prompt)

    async def drain(c):
        async for _ in harness.run_turn(c, "hi", harness.default_model()):
            pass

    for c, expected in ((ctx(["ai", "risk"]), True), (ctx(["ai"]), False), (ctx(["*"], frozenset({1})), False)):
        seen.clear()
        with pytest.raises(_Boom):
            asyncio.run(drain(c))
        assert seen.get("risk_tools") is expected


def test_tool_calls_are_not_capped_per_turn(monkeypatch):
    """The per-tool call budget was removed on 2026-09-28 (user decision): a
    ten-client question must reach the impl ten times, with no refusal."""
    pytest.importorskip("agent_framework")
    import asyncio

    from app.ai_agent import harness

    hits: list[dict] = []

    async def fake_impl(_ctx, **kw):
        hits.append(kw)
        return {"ok": True, "data": {}, "source": {"certified": True}}

    monkeypatch.setitem(harness.TOOL_IMPLS, "get_alert_orders", fake_impl)
    events: list[tuple[str, dict]] = []

    async def _emit(e, d):
        events.append((e, d))

    assert not hasattr(harness, "calls_allowed") and not hasattr(harness, "MAX_CALLS_PER_TOOL_PER_TURN")
    tools = {t.name: t for t in harness.build_tools(ctx(["*"]), _emit)}
    for i in range(1, 11):
        asyncio.run(tools["get_alert_orders"].invoke(alert_ids=[i], max_orders_per_alert=10))
    assert len(hits) == 10
    assert [d["ok"] for e, d in events if e == "tool_done"] == [True] * 10


def test_alert_orders_tool_done_carries_the_resolved_clients(monkeypatch):
    pytest.importorskip("agent_framework")
    import asyncio

    from app.ai_agent import harness

    async def fake_impl(_ctx, **kw):
        return {"ok": True, "data": {"alerts": [{"client_id": 20}, {"client_id": 10}, {"client_id": None}, {"client_id": 20}]},
                "source": {"certified": True}}

    monkeypatch.setitem(harness.TOOL_IMPLS, "get_alert_orders", fake_impl)
    events: list = []

    async def _emit(e, d):
        events.append((e, d))

    tools = {t.name: t for t in harness.build_tools(ctx(["*"]), _emit)}
    asyncio.run(tools["get_alert_orders"].invoke(alert_ids=[1], max_orders_per_alert=10))
    done = [d for e, d in events if e == "tool_done"][0]
    assert done["subjects"] == ["client:10", "client:20"]
