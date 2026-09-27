"""The agent's system prompt must carry today's date (05 §5.8): without it the
model resolves "last 90 days" from its training cut-off."""

from datetime import datetime, timezone

from app.ai_agent.prompt import ANALYST_SYSTEM_PROMPT, system_prompt, today_block


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
