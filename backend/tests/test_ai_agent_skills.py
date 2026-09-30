"""OPT-0069 — Agent Skills for the analyst agent.

What is pinned here (docs/optimization/items/OPT-0069-ai-agent-skills.md):
  * every skill directory is a valid skill the framework actually loads
    (MAF skips a SKILL.md with bad YAML with only a log line — a draft did
    exactly that), its name equals its directory, and SKILL_VISIBILITY names
    exactly the shipped directories (unknown skills are hidden, fail closed);
  * visibility follows the tool registration matrix: fxbackoffice-schema only
    with run_sql, risk-monitor-rules only with the Risk control tools;
  * SOURCES.md never reaches the model (not a resource, not in any listing);
  * no `run_skill_script`, no scripts, and the two read tools need no approval
    (the plain Agent has no approval middleware — an approval request would
    end the turn);
  * load / read events feed the audit row's skills_loaded;
  * "prompt / schema card facts are code" extends to skills: every SQL block
    passes the run_sql guard, every mt4_trades block filters an indexed
    column, key facts are present, no unresolved TODO is shipped, and no
    skill states the gap-trade scan time (owned by the tool caveat);
  * every fact the prompt tests pinned before OPT-0069 is still findable in
    the prompt or a skill;
  * the CEN caveats of run_sql, trade_activity and the prompt agree with the
    2026-09-30 data check (lots ×100 on cent SYMBOLS only).
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

pytest.importorskip("agent_framework")

from agent_framework import FileSkillsSource, SkillsSourceContext  # noqa: E402

from app.ai_agent import harness  # noqa: E402
from app.ai_agent.prompt import (  # noqa: E402
    ANALYST_SYSTEM_PROMPT,
    FORBIDDEN_WORDS,
    RISK_CONTROL_BLOCK,
    RUN_SQL_SCHEMA_BLOCK,
    system_prompt,
)
from app.ai_agent.tools.common import CallerCtx, risk_tools_enabled  # noqa: E402
from app.ai_agent.tools.run_sql import FIXED_CAVEATS, validate_sql  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
SKILLS_DIR = harness.SKILLS_DIR
SKILL_DIRS = sorted(p.name for p in SKILLS_DIR.iterdir() if p.is_dir() and not p.name.startswith(("_", ".")))
# Held back until the business answers; execution-and-slippage also because the
# DealerLogic mechanism is confidential (user decision after the cold review).
EXCLUDED_DRAFTS = ("a-book-b-book", "mt-manager-howto", "client-reply-drafting", "execution-and-slippage")
# The one private file shape next to SKILL.md (reviewers only).
PRIVATE_FILES = frozenset({"SOURCES.md"})
ALL_RISK_TABS = (
    "burst-open", "quick-open-close", "quick-profit", "gap-trade",
    "hedge-open", "leverage-abuse", "martingale", "intraday-return",
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _ctx(modules, scope=None) -> CallerCtx:
    return CallerCtx(user_id=7, email="staff@kohleservices.com", role="user", allowed_modules=tuple(modules),
                     scope=scope, trace_id="t-1", settings=SimpleNamespace())


def _model_visible_files() -> list[Path]:
    """Every file under skills/ the model can reach (SKILL.md + resources)."""
    return sorted(
        p for p in SKILLS_DIR.rglob("*.md")
        if p.name not in PRIVATE_FILES
    )


def _all_skill_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in _model_visible_files())


def _flat(text: str) -> str:
    return " ".join(text.split())


def _text(result) -> str:
    """FunctionTool.invoke returns a list of Content items."""
    if isinstance(result, list):
        return "".join(str(getattr(c, "text", None) or c) for c in result)
    return str(result)


async def _visible(ctx: CallerCtx, on_use=None):
    provider = harness.build_skills_provider(ctx, on_use=on_use)
    return await provider._create_context(SkillsSourceContext(agent=None))


# ── structure ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_every_skill_directory_loads_and_name_equals_directory():
    # A fresh, uncached source: exactly what the container discovers.
    skills = await FileSkillsSource(SKILLS_DIR, resource_extensions=(".md",), script_extensions=()).get_skills(
        SkillsSourceContext(agent=None)
    )
    names = sorted(s.frontmatter.name for s in skills)
    assert names == SKILL_DIRS, "a SKILL.md failed to parse (bad YAML frontmatter?) or its name differs from its directory"
    for s in skills:
        assert Path(s.path).name == s.frontmatter.name
        assert 20 <= len(s.frontmatter.description) <= 1024


def test_visibility_table_names_exactly_the_shipped_skills():
    assert sorted(harness.SKILL_VISIBILITY) == SKILL_DIRS
    assert set(harness.SKILL_VISIBILITY.values()) <= {"all", "run_sql", "risk"}


def test_unknown_skill_is_hidden():
    assert harness.skill_visible("not-a-skill", _ctx(["*"])) is False


def test_drafts_held_back_are_neither_shipped_nor_referenced():
    """Skeletons and drafts with open business questions stay out until the
    business answers (OPT-0069 AC 1); a shipped skill must not send the model
    to one of them."""
    text = _all_skill_text()
    for name in EXCLUDED_DRAFTS:
        assert name not in SKILL_DIRS
        assert name not in text, name


def test_no_unresolved_todo_is_shipped_to_the_model():
    for path in _model_visible_files():
        assert "TODO" not in path.read_text(encoding="utf-8"), path


def test_every_skill_has_a_sources_file_for_reviewers():
    for name in SKILL_DIRS:
        assert (SKILLS_DIR / name / "SOURCES.md").is_file(), name


# ── visibility follows the tool registration matrix ──────────────────────────

EVERYONE = {n for n, a in harness.SKILL_VISIBILITY.items() if a == "all"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "modules,scope,expected",
    [
        (["ai", "risk"], None, EVERYONE | {"fxbackoffice-schema", "risk-monitor-rules"}),
        (["*"], None, EVERYONE | {"fxbackoffice-schema", "risk-monitor-rules"}),
        (["ai"], None, EVERYONE | {"fxbackoffice-schema"}),
        (["ai", "cs"], frozenset({1}), EVERYONE),
        (["ai", "risk"], frozenset({1}), EVERYONE),
        (["*"], frozenset(), EVERYONE),          # empty scope is restricted, not "unset"
        (["risk"], None, set()),                 # no `ai` module: nothing at all
        ([], None, set()),
    ],
)
async def test_visible_skills_per_caller(modules, scope, expected):
    ctx = _ctx(modules, scope)
    assert harness.visible_skill_names(ctx) == expected
    skills, instructions, _tools = await _visible(ctx)
    assert {s.frontmatter.name for s in skills} == expected
    for hidden in set(SKILL_DIRS) - expected:
        assert instructions is None or f"<name>{hidden}</name>" not in instructions


@pytest.mark.anyio
@pytest.mark.parametrize(
    "modules,scope,expect_schema,expect_risk",
    [
        (["ai", "risk"], None, True, True),      # unrestricted with risk
        (["ai"], None, True, False),             # unrestricted without risk
        (["ai", "cs"], frozenset({1}), False, False),   # restricted (Global only)
        (["ai", "risk"], frozenset({1}), False, False),  # restricted holding risk: still no risk tools
        (["*"], frozenset(), False, False),      # empty scope is restricted, not "unset"
    ],
)
async def test_restricted_and_non_risk_callers_do_not_see_gated_skills(modules, scope, expect_schema, expect_risk):
    ctx = _ctx(modules, scope)
    skills, _, _ = await _visible(ctx)
    # the audiences agree with the tool gates they mirror
    assert ("fxbackoffice-schema" in {s.frontmatter.name for s in skills}) is harness.run_sql_enabled(ctx)
    assert ("risk-monitor-rules" in {s.frontmatter.name for s in skills}) is risk_tools_enabled(ctx)
    names = {s.frontmatter.name for s in skills}
    assert ("fxbackoffice-schema" in names) is expect_schema
    assert ("risk-monitor-rules" in names) is expect_risk


@pytest.mark.anyio
async def test_a_hidden_skill_cannot_be_loaded_by_name():
    events = []

    async def on_use(skill, resource):
        events.append((skill, resource))

    _, _, tools = await _visible(_ctx(["ai", "cs"], frozenset({1})), on_use)
    load = next(t for t in tools if t.name == "load_skill")
    read = next(t for t in tools if t.name == "read_skill_resource")
    out = _text(await load.invoke(arguments={"skill_name": "fxbackoffice-schema"}))
    assert "not found" in out
    out = _text(await read.invoke(arguments={"skill_name": "risk-monitor-rules", "resource_name": "references/gap-trade.md"}))
    assert "not found" in out
    assert events == []


# ── tools: read-only, no approval, no scripts ────────────────────────────────


@pytest.mark.anyio
async def test_only_the_two_read_tools_and_they_need_no_approval():
    skills, instructions, tools = await _visible(_ctx(["ai", "risk"]))
    assert sorted(t.name for t in tools) == ["load_skill", "read_skill_resource"]
    assert all(t.approval_mode == "never_require" for t in tools)
    assert "run_skill_script" not in instructions
    for s in skills:
        assert s._scripts == []  # noqa: SLF001 — script discovery is off


def test_no_script_files_are_shipped():
    assert [p for p in SKILLS_DIR.rglob("*") if p.is_file() and p.suffix != ".md"] == []


# ── SOURCES.md never reaches the model ───────────────────────────────────────


@pytest.mark.anyio
async def test_sources_md_is_not_a_resource_and_not_in_any_listing():
    skills, instructions, tools = await _visible(_ctx(["ai", "risk"]))
    assert "SOURCES" not in instructions
    read = next(t for t in tools if t.name == "read_skill_resource")
    for s in skills:
        assert all(r.name.lower() != "sources.md" for r in s._resources)  # noqa: SLF001
        assert "SOURCES" not in await s.get_content()
        for spelling in ("SOURCES.md", "sources.md", "./SOURCES.md"):
            out = _text(await read.invoke(arguments={"skill_name": s.frontmatter.name, "resource_name": spelling}))
            assert "not found" in out, (s.frontmatter.name, spelling)
            assert "Unsourced" not in out


# ── audit hook ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_load_and_read_report_the_skill_for_the_audit_row():
    events = []

    async def on_use(skill, resource):
        events.append((skill, resource))

    _, _, tools = await _visible(_ctx(["ai", "risk"]), on_use)
    load = next(t for t in tools if t.name == "load_skill")
    read = next(t for t in tools if t.name == "read_skill_resource")
    body = _text(await load.invoke(arguments={"skill_name": "margin-and-stopout"}))
    assert "Margin level" in body
    await read.invoke(arguments={"skill_name": "fxbackoffice-schema", "resource_name": "references/mt4_users.md"})
    await load.invoke(arguments={"skill_name": "no-such-skill"})
    await read.invoke(arguments={"skill_name": "fxbackoffice-schema", "resource_name": "references/nope.md"})
    assert events == [("margin-and-stopout", None), ("fxbackoffice-schema", "references/mt4_users.md")]


class _FakeAgent:
    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        _FakeAgent.last_kwargs = kwargs

    def run(self, message, *, session, stream):
        async def gen():
            if False:  # pragma: no cover
                yield None

        return gen()


@pytest.mark.anyio
async def test_run_turn_wires_a_filtered_skills_provider(monkeypatch):
    monkeypatch.setattr(harness, "Agent", _FakeAgent)
    monkeypatch.setattr(harness, "get_client", lambda model: object())
    monkeypatch.setattr(harness, "build_compaction_strategy", lambda: (lambda messages: False))
    restricted = _ctx(["ai", "risk"], frozenset({1}))
    [_ async for _ in harness.run_turn(restricted, "hi", "gpt-5.6-terra")]
    providers = _FakeAgent.last_kwargs["context_providers"]
    skill_providers = [p for p in providers if isinstance(p, harness.KcmSkillsProvider)]
    assert len(skill_providers) == 1
    skills, _, _ = await skill_providers[0]._create_context(SkillsSourceContext(agent=None))
    names = {s.frontmatter.name for s in skills}
    assert "fxbackoffice-schema" not in names and "risk-monitor-rules" not in names
    assert "kcm-metrics-definitions" in names


# ── facts are code: SQL blocks, key facts, ownership ─────────────────────────

_SQL_BLOCK = re.compile(r"```sql\n(.*?)```", re.S)


# A block that starts with WHERE is a filter fragment meant to be pasted into
# a SELECT over mt4_trades t JOIN mt4_users mu; it is checked inside one.
_FRAGMENT_HOST = "SELECT t.loginSid FROM mt4_trades t JOIN mt4_users mu ON mu.loginSid = t.loginSid "


def _sql_blocks() -> list[tuple[str, str]]:
    out = []
    for path in _model_visible_files():
        for sql in _SQL_BLOCK.findall(path.read_text(encoding="utf-8")):
            if sql.lstrip().upper().startswith("WHERE"):
                sql = _FRAGMENT_HOST + sql + "LIMIT 10\n"
            out.append((str(path.relative_to(SKILLS_DIR)), sql))
    return out


def test_skills_ship_sql_patterns():
    assert len(_sql_blocks()) >= 8


@pytest.mark.parametrize("where,sql", _sql_blocks(), ids=lambda v: v if isinstance(v, str) and v.endswith(".md") else None)
def test_every_sql_block_passes_the_run_sql_guard(where, sql):
    """The model copies examples verbatim (the 2026-09-29 CLOSE_TIME incident):
    a pattern the guard refuses is a pattern that wastes a turn. Placeholders
    like '<sid>-<login>' are string literals, so the guard still parses them."""
    assert validate_sql(sql, "fxbackoffice") is None, (where, validate_sql(sql, "fxbackoffice"))


@pytest.mark.parametrize("where,sql", [b for b in _sql_blocks() if "mt4_trades" in b[1]])
def test_every_mt4_trades_block_filters_an_indexed_column(where, sql):
    where_clause = sql.split("WHERE", 1)[1] if "WHERE" in sql else ""
    assert re.search(r"\b(closeDate|openDate|loginSid)\b", where_clause), where
    assert "CLOSE_TIME = '1970" not in sql and "OPEN_TIME = '1970" not in sql
    assert re.search(r"\bLIMIT\s+\d+", sql), where


def test_key_facts_are_present():
    def body(name, rel="SKILL.md"):
        return _flat((SKILLS_DIR / name / rel).read_text(encoding="utf-8"))

    schema = body("fxbackoffice-schema")
    assert "mt4_trades.loginSid = mt4_users.loginSid" in schema
    assert "mt4_users.userId = users.id" in schema
    assert "`users.cid` is the company flag (0 CN / 1 Global), **never** a client id" in schema
    assert "**Open positions = `closeDate = '1970-01-01'`**" in schema
    assert "`mu.MARGIN_LEVEL > 0`" in schema
    assert "Never put comments" in schema
    # the one-sided / margin-level SQL lives in ONE place (OPT-0069 dedupe)
    margin = body("margin-and-stopout")
    assert "```sql" not in (SKILLS_DIR / "margin-and-stopout" / "SKILL.md").read_text(encoding="utf-8")
    assert "fxbackoffice-schema" in margin and "P1" in margin and "P2b" in margin
    assert "0 when the account has no open positions" in schema or "margin level 0" in margin

    metrics = body("kcm-metrics-definitions")
    assert "`XAUUSD.c` is **not** a cent symbol" in metrics
    assert "net_gain = profit_all + floating_pl + rebate_all" in metrics
    assert "legacy net deposit (incl. IB withdrawal)" in metrics
    assert "only cent symbols have their lots divided" in metrics

    ib_lots = body("fxbackoffice-schema", "references/stats_ib_commissions.md")
    assert "Do not add up `lots` here" in ib_lots

    risk = body("risk-monitor-rules")
    for tab in ALL_RISK_TABS:
        assert tab in risk, tab
    assert "121-130" in risk and "retired" in risk
    for rel in ("references/" + t + ".md" for t in ALL_RISK_TABS if t not in ("gap-trade",)):
        assert (SKILLS_DIR / "risk-monitor-rules" / rel).is_file(), rel


def test_skills_never_state_the_gap_trade_scan_time():
    """The gap-trade scan time is owned by the get_risk_alerts caveat and the
    scheduler (OPT-0072 DST fix). A skill that states its own time will be
    wrong on one side of a DST change; it must send the model to the caveat."""
    text = _all_skill_text()
    assert not re.search(r"\b0[57]:20\b", text)
    assert "HKT" not in text
    assert "quote the get_risk_alerts time caveat" in _flat(
        (SKILLS_DIR / "risk-monitor-rules" / "references" / "gap-trade.md").read_text(encoding="utf-8")
    )


def test_skills_keep_the_forbidden_words_as_prohibitions_only():
    text = _all_skill_text().lower()
    for word in FORBIDDEN_WORDS:
        for m in re.finditer(re.escape(word.lower()), text):
            window = text[max(0, m.start() - 250): m.end() + 50]
            assert re.search(r"never|not |do not|不", window), (word, window)


# ── nothing the prompt tests pinned before OPT-0069 was lost ────────────────

PINNED_BEFORE_OPT_0069 = (
    '"Today" is the date given in the "Today" section',
    "remember the earlier turns of THIS conversation",
    "no file, shell or web capability",
    "if it is not in your tool list",
    "ONLY when no certified tool can answer",
    "There is no per-tool call limit",
    "未认证 / uncertified",
    "show the exact SQL you ran",
    "divide by 100",
    "isEmployee",
    "sid=5 closed rows have CMD inverted",
    "MT server days",
    'or from a figure you already stated earlier in this conversation, marked "(earlier in this conversation)"',
    "A follow-up that needs a NEW figure must call the tool again.",
    "use the subject from earlier in this conversation and say which one you assumed",
    "mt4_trades.loginSid = mt4_users.loginSid",
    "mt4_users.userId = users.id",
    "NO client-id column on mt4_trades",
    "`users.cid` is NOT a client id",
    "`closeDate = '1970-01-01'`",
    "CLOSE_TIME (MT server wall clock, NOT indexed)",
    "never add an openDate range",
    "rank_open_positions",
    "legacy net deposit (incl. IB withdrawal)",
    "net_gain question, not a net-deposit question",
    "NET lots",
    "15s",
    "The rebate-arbitrage band (121-130) is retired",
    *ALL_RISK_TABS,
    *FORBIDDEN_WORDS,
)


@pytest.mark.parametrize("fact", PINNED_BEFORE_OPT_0069)
def test_previously_pinned_prompt_fact_is_still_findable(fact):
    everything = _flat(system_prompt(run_sql=True, risk_tools=True)) + "\n" + _flat(_all_skill_text())
    assert _flat(fact) in everything, fact


def test_facts_the_guard_or_silent_failures_depend_on_stay_in_the_prompt():
    """A skill is read only when the model decides to load it. The facts whose
    absence produced silent garbage or a timeout (join path, users.cid, the
    open-order sentinel) must keep riding in the prompt itself."""
    card = _flat(RUN_SQL_SCHEMA_BLOCK)
    for fact in ("mt4_trades.loginSid = mt4_users.loginSid", "`users.cid` is NOT a client id", "`closeDate = '1970-01-01'`"):
        assert fact in card, fact
    assert "fxbackoffice-schema" in card  # and it points at the skill


def test_prompt_rule_one_allows_documented_skill_figures_only_when_marked():
    flat = _flat(ANALYST_SYSTEM_PROMPT)
    assert 'marked "(documented, <skill name>)"' in flat
    assert "never as a figure about the client, account or period being discussed" in flat


def test_gap_trade_scan_sentence_is_still_in_the_prompt_risk_block():
    # Owned by OPT-0072; OPT-0069 must not move or drop it.
    assert "gap-trade alerts are scanned the NEXT day" in RISK_CONTROL_BLOCK


# ── CEN caveat (2026-09-30 data check) ───────────────────────────────────────


def test_cen_caveats_agree_lots_are_x100_on_cent_symbols_only():
    """Data check 2026-09-30 (closed 2025-12-01..03, 2026-06-01..03,
    2026-09-21..28 and the open book): CEN accounts trade only .cent/.kcmc
    symbols; a non-CEN account can hold a cent symbol. Lots are ×100 because of
    the SYMBOL. run_sql's caveat used to say CEN accounts store lots ×100."""
    from app.ai_agent.tools import trade_activity  # noqa: F401 — caveat text lives in the module
    import inspect

    cen = next(c for c in FIXED_CAVEATS if c.startswith("CEN"))
    assert "lots are ×100 on .cent/.kcmc symbols only" in cen
    assert "money is ×100 on CEN accounts" in cen
    assert "(and lots)" not in cen
    ta_src = inspect.getsource(trade_activity)
    assert "Cent products (.cent / .kcmc) have lots AND money divided by 100; CEN accounts have money divided by 100." in ta_src
    flat = _flat(ANALYST_SYSTEM_PROMPT)
    assert "lots of .cent/.kcmc symbols are ×100" in flat


# ── packaging ────────────────────────────────────────────────────────────────


def test_skills_reach_the_ai_agent_image_and_the_dev_mount():
    ignore = [ln.strip() for ln in (BACKEND / ".dockerignore").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    for probe in ("app/ai_agent/skills/kcm-metrics-definitions/SKILL.md", "app/ai_agent/skills/fxbackoffice-schema/references/mt4_trades.md"):
        for pattern in ignore:
            assert not fnmatch.fnmatch(probe, pattern) and not probe.startswith(pattern.rstrip("/") + "/"), (probe, pattern)
    assert "COPY . /app" in (BACKEND / "Dockerfile.ai-agent").read_text()
    assert "./app/ai_agent:/app/app/ai_agent:ro" in (BACKEND / "docker-compose.dev.yml").read_text()
    assert harness.SKILLS_DIR == BACKEND / "app" / "ai_agent" / "skills"


# ── cold review 2026-09-30: resource allowlist ───────────────────────────────


def test_resource_allowlist_accepts_only_references_md():
    ok = harness.skill_resource_allowed
    assert ok("x", "references/mt4_trades.md")
    for bad in ("SOURCES.md", "sources.md", "notes.md", "references/sub/deep.md", "references/../SOURCES.md",
                "references/x.txt", "other/x.md", "references/.hidden.md"):
        assert not ok("x", bad), bad


@pytest.mark.anyio
async def test_every_markdown_file_is_skill_md_a_discovered_resource_or_private():
    """Nothing is silently undiscoverable (e.g. nested deeper than MAF scans,
    or outside references/): every .md is SKILL.md, SOURCES.md, or a resource
    the model can actually read."""
    skills = await harness._file_source().get_skills(SkillsSourceContext(agent=None))
    discovered = {
        (Path(s.path).name, r.name) for s in skills for r in s._resources  # noqa: SLF001
    }
    for path in SKILLS_DIR.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(SKILLS_DIR)
        skill, inner = rel.parts[0], PurePosixPath(*rel.parts[1:]).as_posix()
        if inner == "SKILL.md" or inner in PRIVATE_FILES:
            continue
        assert (skill, inner) in discovered, f"{rel} is neither SKILL.md, private, nor a discoverable resource"


# ── cold review 2026-09-30: startup self-check ───────────────────────────────


@pytest.mark.anyio
async def test_self_check_passes_on_the_shipped_tree(caplog):
    missing, extra = await harness.skills_self_check()
    assert (missing, extra) == (frozenset(), frozenset())
    assert not [r for r in caplog.records if r.levelname == "CRITICAL"]


@pytest.mark.anyio
async def test_self_check_names_missing_and_extra_skills_at_critical(monkeypatch, caplog):
    table = dict(harness.SKILL_VISIBILITY)
    table.pop("ib-and-rebate")
    table["ghost-skill"] = "all"
    monkeypatch.setattr(harness, "SKILL_VISIBILITY", table)
    missing, extra = await harness.skills_self_check()
    assert missing == {"ghost-skill"} and extra == {"ib-and-rebate"}
    crit = [r.getMessage() for r in caplog.records if r.levelname == "CRITICAL"]
    assert crit and "ghost-skill" in crit[0] and "ib-and-rebate" in crit[0]


def test_ai_agent_startup_runs_the_self_check_and_keeps_serving(monkeypatch):
    """Runs in the lifespan; a mismatch is logged, the app still starts
    (restart: unless-stopped + no healthcheck = refusing would restart-loop)."""
    from fastapi.testclient import TestClient

    from app.ai_agent import server

    calls = []

    async def fake_check():
        calls.append(1)
        return frozenset({"ghost"}), frozenset()

    monkeypatch.setattr(harness, "skills_self_check", fake_check)
    with TestClient(server.app) as client:
        assert calls == [1]
        assert client.get("/nope").status_code == 404  # the process is up


# ── cold review 2026-09-30: no leaks from gated audiences into "all" skills ──

# Strings that only a gated audience may know. Each list is checked against the
# gated skill that owns it (so a stale marker is caught) and must not appear in
# any skill the "all" audience can load.
RISK_ONLY_MARKERS = (
    "rule_ids", "rule 71", "rule 81", "71-80", "81-90", "91-100", "101-110", "111-120", "121-130",
    "131-140", "51-60", "61-70", "200 %, 150 %, 125 %",
    "burst_window_sec", "min_order_count", "min_lots_per_order", "max_hold_seconds", "min_closed_orders",
    "lookback_min", "min_profit_usd", "min_orders_per_side", "min_total_lots", "max_margin_level",
    "floating_loss_floor_usd", "min_add_count", "lot_multiplier", "min_l_loss_usd", "neg_deposit", "ratio_x1",
    "禁止出金(風控)", "Withdrawal Notice", "lot_ratio_mg", "shared_ip_count", "open_diff_sec",
    "same_second_open_groups", "lot_escalation_steps", "opposite_side_overlap_pct", "peak_return_pct",
    "1.4 million", "±300 seconds", "lot ratio min/max > 0.8",
)
RUN_SQL_ONLY_MARKERS = (
    "```sql", "mt4_trades", "mt4_users", "stats_ib_commissions", "closeDate", "MARGIN_LEVEL", "IB-WALLET",
    "partnerId", "ibId", "refId",
)
HELD_BACK_MARKERS = ("DealerLogic", "oneZero", "plugin", "request price or worse", "0.35 s", "220 ms")


def _skill_text(name: str) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8") for p in (SKILLS_DIR / name).rglob("*.md") if p.name not in PRIVATE_FILES
    )


def test_markers_are_real_facts_of_their_gated_skill():
    risk = _flat(_skill_text("risk-monitor-rules"))
    schema = _skill_text("fxbackoffice-schema")
    assert [m for m in RISK_ONLY_MARKERS if _flat(m) not in risk] == []
    assert [m for m in RUN_SQL_ONLY_MARKERS if m not in schema] == []


@pytest.mark.parametrize("name", sorted(n for n, a in harness.SKILL_VISIBILITY.items() if a == "all"))
def test_all_audience_skills_carry_no_gated_or_held_back_facts(name):
    text = _flat(_skill_text(name))
    leaks = [m for m in (*RISK_ONLY_MARKERS, *RUN_SQL_ONLY_MARKERS, *HELD_BACK_MARKERS) if _flat(m) in text]
    assert leaks == [], (name, leaks)


def test_no_shipped_skill_describes_the_held_back_mechanism():
    text = _all_skill_text()
    assert [m for m in HELD_BACK_MARKERS if m in text] == []
