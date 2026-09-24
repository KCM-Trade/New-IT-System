"""Shared subject-line builder for every Alert Mail Center source.

All registered trade-risk digests share one two-layer prefix so recipients
can filter at department level or per module:

    [交易风控 Trade Risk] [{源中文}] {源英文} — {英文 tail}

Use `build_trade_risk_subject()` + `english_count_tail()` — do not hand-roll
subjects in template builders. The legacy `SUBJECT_PREFIX = "[风控告警]"` remains
for non-trade-risk callers of `build_subject()` only.

The Chinese module tag belongs to the *source*, not the rule: a band holds up
to 10 rules and they share one tag, so mailbox filters survive new rules.
Subject `label_en` is English-only; bilingual names live in registry `"label"`
for the UI dropdown only.

This module deliberately imports nothing from the package: registry imports
alert_mail_dispatcher at top level while the dispatcher imports registry
lazily inside functions, so a shared helper living in either one would
reintroduce that cycle for the other importer.
"""

from __future__ import annotations

# Department tag shared by every Alert Mail Center source (2026-09-24 unified).
TRADE_RISK_DEPARTMENT_PREFIX = "[交易风控 Trade Risk]"

# Legacy default kept for callers outside the trade-risk digest family.
SUBJECT_PREFIX = "[风控告警]"
TEST_PREFIX = "[TEST]"


def trade_risk_prefix(source_tag_zh: str) -> str:
    """Per-source prefix: department tag + Chinese module tag (no brackets in arg)."""
    return f"{TRADE_RISK_DEPARTMENT_PREFIX} [{source_tag_zh}]"


def english_count_tail(
    n: int,
    noun: str,
    *,
    plural: str | None = None,
    suffix: str | None = None,
) -> str:
    """Build an English hit summary: ``1 account`` or ``2 accounts suspected``."""
    word = noun if n == 1 else (plural if plural is not None else f"{noun}s")
    base = f"{n} {word}"
    return f"{base} {suffix}" if suffix else base


def build_subject(
    label: str,
    tail: str,
    test: bool = False,
    prefix: str = SUBJECT_PREFIX,
) -> str:
    """Compose one digest subject: "{prefix} {label} — {tail}".

    `label` is the module label ("Hedge Open"), `tail` the per-source hit
    summary ("3 accounts suspected"). `prefix` defaults to the shared
    "[风控告警]" tag; trade-risk digests should use `build_trade_risk_subject`
    instead. Test-sends get a leading "[TEST] " so they never read as a live
    alert.

    The space after [TEST] is deliberate — "[TEST][对冲刷单] …" reads as one
    run-on token at a glance, which is the opposite of what the marker is for.
    """
    subject = f"{prefix} {label} — {tail}"
    return f"{TEST_PREFIX} {subject}" if test else subject


def build_trade_risk_subject(
    source_tag_zh: str,
    label_en: str,
    tail: str,
    test: bool = False,
) -> str:
    """Standard Alert Mail Center subject for all registered trade-risk sources.

    Example: ``[交易风控 Trade Risk] [即日高收益] Intraday Return — 2 accounts``
    """
    return build_subject(
        label_en,
        tail,
        test,
        prefix=trade_risk_prefix(source_tag_zh),
    )
