"""The agent's system prompt must carry today's date (05 §5.8): without it the
model resolves "last 90 days" from its training cut-off."""

from datetime import datetime, timezone

from app.ai_agent.prompt import TOOL_DOCSTRINGS, ANALYST_SYSTEM_PROMPT, system_prompt, today_block


def test_today_block_uses_mt_server_day_not_utc():
    # 2026-01-15 22:30 UTC is already 2026-01-16 00:30 on the MT server (winter, UTC+2)
    block = today_block(datetime(2026, 1, 15, 22, 30, tzinfo=timezone.utc))
    assert "MT server date (use this for all date ranges): 2026-01-16 (Friday)" in block
    assert "UTC+0200" in block
    assert "Hong Kong: 2026-01-16 06:30" in block
    assert "UTC: 2026-01-15 22:30" in block


def test_today_block_summer_offset():
    block = today_block(datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc))
    assert "2026-07-01 (Wednesday), server clock 15:00 UTC+0300" in block


def test_system_prompt_is_static_prompt_plus_dated_tail():
    text = system_prompt(datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc))
    assert text.startswith(ANALYST_SYSTEM_PROMPT)
    assert text.count("## Today") == 1
    assert "2026-09-27" in text
    # the rule that points the model at the block must still be there
    assert '"Today" is the date given in the "Today" section' in ANALYST_SYSTEM_PROMPT


def test_system_prompt_defaults_to_now():
    text = system_prompt()
    assert datetime.now(timezone.utc).strftime("%Y") in text


def test_prompt_no_longer_claims_amnesia_but_still_denies_other_capabilities():
    """Slice 2 (02 §8.6): the "no memory" sentence is gone, the capability
    denials stay. The model must know it MAY use earlier turns and MUST NOT
    invent file/web/SQL abilities."""
    assert "no memory" not in ANALYST_SYSTEM_PROMPT
    assert "Preview" not in ANALYST_SYSTEM_PROMPT
    assert "remember the earlier turns of THIS conversation" in ANALYST_SYSTEM_PROMPT
    assert "no file, shell or web capability" in ANALYST_SYSTEM_PROMPT
    # SQL is conditional since slice 2 item 3: run_sql exists only for
    # unrestricted callers, and the prompt must say what its absence means.
    assert "if it is not in your tool list" in ANALYST_SYSTEM_PROMPT


def test_prompt_carries_the_run_sql_rules():
    """02 §10.3: only when no certified tool can answer, <= 2 calls per turn,
    the answer says uncertified, shows the SQL, and lists the 口径 pitfalls."""
    flat = " ".join(ANALYST_SYSTEM_PROMPT.split())
    assert "## run_sql" in ANALYST_SYSTEM_PROMPT
    assert "ONLY when no certified tool can answer" in flat
    assert "at most 2 calls per turn" in flat
    assert "未认证 / uncertified" in flat
    assert "show the exact SQL you ran" in flat
    for pitfall in ("divide by 100", "isEmployee", "sid=5 closed rows have CMD inverted", "MT server days"):
        assert pitfall in flat, pitfall
    assert "run_sql" in TOOL_DOCSTRINGS and "UNCERTIFIED" in TOOL_DOCSTRINGS["run_sql"]


def test_rule_one_allows_restated_figures_only_when_marked():
    """The exact wording 02 §8.6 froze; a paraphrase would change what the
    model is allowed to do with an earlier number."""
    flat = " ".join(ANALYST_SYSTEM_PROMPT.split())  # the prompt is hard-wrapped
    assert (
        "or from a figure you already stated earlier in this conversation, marked "
        '"(earlier in this conversation)"' in flat
    )
    assert "A follow-up that needs a NEW figure must call the tool again." in flat


def test_prompt_tells_the_model_how_to_resolve_an_omitted_id():
    flat = " ".join(ANALYST_SYSTEM_PROMPT.split())
    assert "use the subject from earlier in this conversation and say which one you assumed" in flat
