"""Unit tests for alert_mail/subject.py — trade-risk subject unification."""

from app.services.alert_mail.subject import (
    build_trade_risk_subject,
    english_count_tail,
    trade_risk_prefix,
)


def test_trade_risk_prefix():
    assert trade_risk_prefix("对冲刷单") == "[交易风控 Trade Risk] [对冲刷单]"


def test_english_count_tail_plural_and_suffix():
    assert english_count_tail(1, "account", suffix="suspected") == "1 account suspected"
    assert english_count_tail(2, "account", suffix="suspected") == "2 accounts suspected"
    assert english_count_tail(2, "client") == "2 clients"


def test_build_trade_risk_subject_test_marker():
    subject = build_trade_risk_subject(
        "即日高收益",
        "Intraday Return",
        "1 account",
        test=True,
    )
    assert subject == "[TEST] [交易风控 Trade Risk] [即日高收益] Intraday Return — 1 account"


def test_gap_trade_crm_digest_subject_aligned():
    from app.services.gap_trade_crm_tag_service import build_digest_subject

    rows = [{"result": "tagged"}]
    subject = build_digest_subject(rows, "intraday 06:45 HKT", live=True)
    assert subject == (
        "[交易风控 Trade Risk] [Gap交易] Gap Trade CRM — "
        "1 tagged — intraday 06:45 HKT (LIVE)"
    )

    failed_rows = [{"result": "failed"}]
    subject_failed = build_digest_subject(failed_rows, "final 07:20 HKT", live=True)
    assert subject_failed.startswith("[FAILED] [交易风控 Trade Risk] [Gap交易]")
    assert "1 FAILED" in subject_failed
