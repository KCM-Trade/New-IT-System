"""``get_window_scan`` (docs/ai-agent/11 §2.3) with the page's service stubbed.

Pinned: the page's own service is called with the agent's timeout-guarded
connection and the tool's anchor translated to the page format; argument
validation (anchor format / age, domains, top_n, include_trades gate);
sort + top_n after the service; scope filter before top_n with the masked
count; trades[] only when top_n ≤ 5 and ≤ 200 in total; statement timeout →
upstream_timeout; verdict None.
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

ws = importlib.import_module("app.ai_agent.tools.window_scan")
from app.ai_agent.tools import common
from app.ai_agent.tools.common import CallerCtx
from app.services import window_scan_service as wss

HK = ZoneInfo("Asia/Hong_Kong")
ANCHOR = (datetime.now(HK) - timedelta(days=2)).strftime("%Y-%m-%d") + " 20:30"
CIDS = {1: 0, 2: 1, 3: 1, 4: None}

ROW_KEYS = {"client_id", "login_sids", "country", "status_tag", "closed_orders", "open_orders", "lots_sum",
            "closed_profit", "floating_profit", "win_rate", "avg_hold_sec", "symbols", "net_deposit",
            "total_rebate", "pl_plus_rebate", "net_gain", "rank"}


def run(coro):
    return asyncio.run(coro)


def ctx(scope=None) -> CallerCtx:
    return CallerCtx(user_id=7, email="staff@kohleservices.com", role="user", allowed_modules=("ai", "risk"),
                     scope=scope, trace_id="t-1", settings=SimpleNamespace())


def client(cid, closed_profit, net_gain, lots, n_trades=2):
    return {
        "client_id": cid, "login_sids": [f"1-{cid}"], "country": "X", "status_tag": "all_closed",
        "closed_orders": n_trades, "open_orders": 0, "lots_sum": lots, "closed_profit": closed_profit,
        "floating_profit": None, "win_rate": 1.0, "avg_hold_sec": 60.0, "symbols": ["XAUUSD"],
        "net_deposit": 100.0, "total_rebate": 1.0, "pl_plus_rebate": 5.0, "net_gain": net_gain,
        "history_profit": 4.0, "email": "leak@example.com",
        "trades": [{"ticket_sid": f"1-{cid}{k}", "login_sid": f"1-{cid}", "symbol": "XAUUSD", "status": "closed",
                    "direction": "buy", "lots": 1.0, "is_cent": False, "open_time_utc": "2026-09-26T12:30:00Z",
                    "close_time_utc": "2026-09-26T12:31:00Z", "hold_sec": 60, "hold_bucket": "lt30m",
                    "profit": 1.0, "open_time_mt": "x"} for k in range(n_trades)],
    }


STATS = {"anchor_hk": "a", "anchor_mt": "b", "range_mt_from": "c", "range_mt_to": "d", "sids": [1, 5, 6],
         "clients_scanned": 9, "clients_profitable": 4, "trades_scanned": 20, "employees_excluded": 1,
         "truncated": False, "enrichment_ok": True}


@pytest.fixture
def svc(monkeypatch):
    calls = {}
    clients = [client(1, 500.0, 10.0, 1.0), client(2, 400.0, None, 9.0), client(3, 300.0, 90.0, 2.0),
               client(4, 200.0, 50.0, 3.0)]

    def fake(settings, **kw):
        calls.update(kw)
        return [dict(c) for c in calls.get("_clients", clients)], dict(calls.get("_stats", STATS))

    monkeypatch.setattr(wss, "query_window_scan", fake)
    monkeypatch.setattr(ws, "_fetch_cids", lambda s, ids: {u: CIDS.get(u) for u in ids})
    return calls


def call(c=None, **kw):
    kw.setdefault("anchor_hk", ANCHOR)
    return run(ws.get_window_scan(c or ctx(), **kw))


def test_calls_the_page_service_with_the_guarded_connection(svc):
    env = call(window_min=15, scan_by="close", hold_bucket="lt30m", sids=[5, 1], symbol="XAUUSD")
    assert env["ok"] is True
    assert svc["anchor"] == ANCHOR.replace(" ", "T")
    assert svc["window_min"] == 15 and svc["scan_by"] == "close" and svc["hold_bucket"] == "lt30m"
    assert svc["sids"] == [1, 5] and svc["symbol"] == "XAUUSD"
    assert svc["connect"] is common.connect_mysql
    assert env["source"]["function"] == "query_window_scan" and env["source"]["certified"] is True
    assert env["data"]["verdict"] is None


def test_default_sort_is_closed_profit_and_rows_are_projected(svc):
    d = call()["data"]
    assert [r["client_id"] for r in d["rows"]] == [1, 2, 3, 4]
    for r in d["rows"]:
        assert set(r) == ROW_KEYS
        assert "trades" not in r
    assert d["rows_masked_by_scope"] == 0
    assert d["stats"]["employees_excluded"] == 1


def test_net_gain_sort_puts_null_last(svc):
    d = call(sort="net_gain")["data"]
    assert [r["client_id"] for r in d["rows"]] == [3, 4, 1, 2]


def test_lots_sort_and_top_n(svc):
    env = call(sort="lots", top_n=2)
    assert [r["client_id"] for r in env["data"]["rows"]] == [2, 4]
    assert env["truncated"] is True


def test_restricted_scope_filters_before_top_n(svc):
    env = call(ctx(frozenset({1})), top_n=1)
    d = env["data"]
    assert [r["client_id"] for r in d["rows"]] == [2]
    assert d["rows_masked_by_scope"] == 2  # CN client 1 + unresolvable client 4
    assert d["profitable_clients_total"] == 2


def test_empty_scope_masks_everything(svc):
    d = call(ctx(frozenset()))["data"]
    assert d["rows"] == [] and d["rows_masked_by_scope"] == 4


def test_trades_only_when_top_n_at_most_5(svc):
    env = call(include_trades=True, top_n=6)
    assert all("trades" not in r for r in env["data"]["rows"])
    assert any("top_n ≤ 5" in c for c in env["definition"]["caveats"])
    env = call(include_trades=True, top_n=5)
    rows = env["data"]["rows"]
    assert all("trades" in r for r in rows)
    assert "open_time_mt" not in rows[0]["trades"][0]


def test_trades_capped_at_200_in_total(svc):
    svc["_clients"] = [client(i, 1000.0 - i, 1.0, 1.0, n_trades=90) for i in (2, 3, 5)]
    env = call(include_trades=True, top_n=3)
    rows = env["data"]["rows"]
    assert sum(len(r["trades"]) for r in rows) == 200
    assert [r["trades_total"] for r in rows] == [90, 90, 90]
    assert env["truncated"] is True


@pytest.mark.parametrize(
    "kw,code",
    [
        ({"anchor_hk": "2026-09-01 20:30:00"}, "invalid_argument"),
        ({"anchor_hk": "2026-02-30 20:30"}, "invalid_argument"),
        ({"anchor_hk": "nonsense"}, "invalid_argument"),
        ({"anchor_hk": (datetime.now(HK) + timedelta(days=2)).strftime("%Y-%m-%d %H:%M")}, "range_too_wide"),
        ({"window_min": 7}, "invalid_argument"),
        ({"scan_by": "entry"}, "invalid_argument"),
        ({"hold_bucket": "lt1m"}, "invalid_argument"),
        ({"sort": "profit"}, "invalid_argument"),
        ({"top_n": 0}, "invalid_argument"),
        ({"top_n": 51}, "invalid_argument"),
        ({"sids": [2]}, "invalid_argument"),
        ({"sids": []}, "invalid_argument"),
    ],
)
def test_bad_arguments(svc, kw, code):
    env = call(**kw)
    assert env["ok"] is False and env["error"]["code"] == code, kw
    assert "anchor" not in svc  # never reached the service


def test_statement_timeout_is_upstream_timeout(monkeypatch):
    import pymysql

    def boom(settings, **kw):
        raise pymysql.err.OperationalError(3024, "max statement time exceeded")

    monkeypatch.setattr(wss, "query_window_scan", boom)
    env = call()
    assert env["ok"] is False and env["error"]["code"] == "upstream_timeout"


def test_caveats_state_dst_and_net_deposit_scope(svc):
    text = " ".join(call()["definition"]["caveats"])
    assert "DST" in text and "IB commission" in text and "Employees are excluded" in text


def test_truncated_scan_is_said(svc):
    svc["_stats"] = {**STATS, "truncated": True}
    env = call()
    assert env["truncated"] is True and env["data"]["stats"]["truncated"] is True
    assert any("INCOMPLETE" in c or "row cap" in c for c in env["definition"]["caveats"])


def test_restricted_callers_get_no_firm_wide_totals(svc):
    """Cold review #3: unfiltered totals next to a filtered list leak the
    out-of-scope population by subtraction."""
    d = call(ctx(frozenset({1})), top_n=50)["data"]
    st = d["stats"]
    assert st["clients_scanned"] is None and st["trades_scanned"] is None and st["employees_excluded"] is None
    assert st["clients_profitable"] == d["profitable_clients_total"] == len(d["rows"])
    full = call(ctx(None), top_n=50)["data"]["stats"]
    assert full["clients_scanned"] is not None and full["employees_excluded"] is not None
