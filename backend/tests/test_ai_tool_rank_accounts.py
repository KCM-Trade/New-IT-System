"""``rank_accounts`` tool (02 §11) — assembly with the service monkeypatched.

Pinned: argument validation as structured errors; the 92-day group limit;
the min_orders soft floor and its explicit override; OUTPUT scope filtering
(rows outside the caller's cid set are removed and counted, top_n is taken
AFTER the filter, an unresolvable cid is masked for a restricted caller,
an empty scope masks everything); certified envelope shape.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import importlib

import pytest

# The package re-exports the FUNCTION under the module's name, so a plain
# `from app.ai_agent.tools import rank_accounts` would hand back the coroutine.
ra = importlib.import_module("app.ai_agent.tools.rank_accounts")
from app.ai_agent.tools.common import CallerCtx
from app.services import rank_accounts_service as ras

WEEK = {"from": "2026-09-21", "to": "2026-09-27"}


def run(coro):
    return asyncio.run(coro)


def ctx(scope=None) -> CallerCtx:
    return CallerCtx(user_id=7, email="staff@kohleservices.com", role="user", allowed_modules=("ai",),
                     scope=scope, trace_id="t-1", settings=SimpleNamespace())


def row(login, cid, wr, orders=30):
    return {"login_sid": login, "client_id": int(login.split("-")[1]), "cid": cid, "sid": int(login.split("-")[0]),
            "is_cent": False, "orders": orders, "wins": int(orders * wr), "win_rate": wr, "lots": 1.0,
            "net_profit": 10.0, "gross_profit": 12.0, "return_pct": None, "metric_value": wr}


@pytest.fixture
def ranked(monkeypatch):
    calls = {}
    rows = [row("1-1", 1, 0.9), row("1-2", 0, 0.85), row("5-3", 1, 0.8), row("1-4", None, 0.75), row("6-5", 1, 0.7),
            row("1-6", 0, 0.6)]

    def fake_rank(settings, **kw):
        calls.update(kw)
        return [dict(r) for r in rows[: kw["limit"]]]

    monkeypatch.setattr(ras, "rank", fake_rank)
    return calls


# ── validation ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("metric", ["", "profit", None, "WINRATE"])
def test_unknown_metric_is_invalid_argument(ranked, metric):
    env = run(ra.rank_accounts(ctx(), metric, WEEK))
    assert env["ok"] is False and env["error"]["code"] == "invalid_argument"


def test_return_pct_is_refused_with_the_reason(ranked):
    env = run(ra.rank_accounts(ctx(), "return_pct", WEEK))
    assert env["ok"] is False and env["error"]["code"] == "invalid_argument"
    assert "opening equity" in env["error"]["message"]
    assert ranked == {}  # never reached the service


def test_93_days_is_range_too_wide_but_92_is_fine(ranked):
    wide = run(ra.rank_accounts(ctx(), "win_rate", {"from": "2026-06-27", "to": "2026-09-27"}))
    assert wide["ok"] is False and wide["error"]["code"] == "range_too_wide"
    assert wide["error"]["detail"] == {"days": 93, "max_days": 92}
    ok = run(ra.rank_accounts(ctx(), "win_rate", {"from": "2026-06-28", "to": "2026-09-27"}))
    assert ok["ok"] is True


@pytest.mark.parametrize("top_n", [0, 51, "x"])
def test_top_n_bounds(ranked, top_n):
    env = run(ra.rank_accounts(ctx(), "win_rate", WEEK, top_n=top_n))
    assert env["ok"] is False and env["error"]["code"] == "invalid_argument"


def test_min_orders_below_five_needs_the_explicit_flag(ranked):
    refused = run(ra.rank_accounts(ctx(), "win_rate", WEEK, min_orders=1))
    assert refused["ok"] is False and refused["error"]["code"] == "invalid_argument"
    assert "allow_low_min_orders" in refused["error"]["message"]
    allowed = run(ra.rank_accounts(ctx(), "win_rate", WEEK, min_orders=1, allow_low_min_orders=True))
    assert allowed["ok"] is True and ranked["min_orders"] == 1
    zero = run(ra.rank_accounts(ctx(), "win_rate", WEEK, min_orders=0, allow_low_min_orders=True))
    assert zero["ok"] is False  # >= 1 is a hard floor


def test_bad_order_and_bad_sids(ranked):
    assert run(ra.rank_accounts(ctx(), "lots", WEEK, order="sideways"))["error"]["code"] == "invalid_argument"
    assert run(ra.rank_accounts(ctx(), "lots", WEEK, sids=[4]))["error"]["code"] == "invalid_argument"
    assert run(ra.rank_accounts(ctx(), "lots", WEEK, sids=[]))["error"]["code"] == "invalid_argument"
    ok = run(ra.rank_accounts(ctx(), "lots", WEEK, sids=[5, 1, 5]))
    assert ok["ok"] is True and ranked["sids"] == [1, 5]


# ── scope ────────────────────────────────────────────────────────────────────

def test_unrestricted_caller_gets_top_n_unfiltered_and_fetches_exactly_top_n(ranked):
    env = run(ra.rank_accounts(ctx(), "win_rate", WEEK, top_n=3))
    assert env["ok"] is True
    d = env["data"]
    assert [r["login_sid"] for r in d["rows"]] == ["1-1", "1-2", "5-3"]
    assert [r["rank"] for r in d["rows"]] == [1, 2, 3]
    assert d["rows_masked_by_scope"] == 0 and ranked["limit"] == 3
    assert env["scope"]["cids_applied"] == "all"
    assert env["source"]["certified"] is True and env["source"]["service"] == "app.services.rank_accounts_service"


def test_restricted_caller_rows_are_filtered_then_topped(ranked):
    env = run(ra.rank_accounts(ctx(scope=frozenset({1})), "win_rate", WEEK, top_n=3))
    d = env["data"]
    # CN rows (cid 0) and the unresolvable cid (None) are masked; top_n AFTER
    assert [r["login_sid"] for r in d["rows"]] == ["1-1", "5-3", "6-5"]
    assert d["rows_masked_by_scope"] == 3  # 1-2 / 1-6 (cid 0) and 1-4 (None); all 6 fit in the fetch window
    assert ranked["limit"] == 9  # top_n * 3 fetch window
    assert env["scope"]["cids_applied"] == [1]
    assert any("rows_masked_by_scope" in c for c in env["definition"]["caveats"])


def test_empty_scope_masks_everything(ranked):
    env = run(ra.rank_accounts(ctx(scope=frozenset()), "win_rate", WEEK, top_n=2))
    assert env["ok"] is True
    assert env["data"]["rows"] == [] and env["data"]["rows_masked_by_scope"] == 6
    assert env["scope"]["cids_applied"] == []


def test_service_error_is_an_envelope(monkeypatch):
    def boom(settings, **kw):
        raise RuntimeError("replica down")

    monkeypatch.setattr(ras, "rank", boom)
    env = run(ra.rank_accounts(ctx(), "orders", WEEK))
    assert env["ok"] is False and env["error"]["code"] == "internal"


def test_definition_caveats_state_the_fixed_definitions(ranked):
    env = run(ra.rank_accounts(ctx(), "net_profit", WEEK, min_orders=25))
    text = " ".join(env["definition"]["caveats"])
    assert "at least 25 closed orders" in text
    assert "PROFIT > 0" in text and "XAUUSD.c" in text and "Demo/test" in text and "close DAY" in text
    assert ras.RETURN_PCT_CAVEAT in env["definition"]["caveats"]
