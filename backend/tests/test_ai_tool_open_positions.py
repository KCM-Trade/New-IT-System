"""``rank_open_positions`` tool + ``open_positions_rank_service`` pure parts.

Pinned: the SQL uses the indexed open sentinel (closeDate) and never
CLOSE_TIME, and carries the certified universe + cent rule; family vs exact
symbol matching and LIKE escaping; rollup to client / account; net vs gross
sorting (a locked client does not top the net list); OUTPUT scope filtering
before top_n with totals over visible rows only; argument validation.
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.ai_agent.tools.common import CallerCtx
from app.services import open_positions_rank_service as ops

op = importlib.import_module("app.ai_agent.tools.open_positions")


def run(coro):
    return asyncio.run(coro)


def ctx(scope=None) -> CallerCtx:
    return CallerCtx(user_id=7, email="staff@kohleservices.com", role="user", allowed_modules=("ai",),
                     scope=scope, trace_id="t-1", settings=SimpleNamespace())


def acct(login_sid, client_id, cid, buy, sell, *, symbol="XAUUSD", orders=2, pl=0.0, opened=None):
    return {"login_sid": login_sid, "sid": int(login_sid.split("-")[0]), "client_id": client_id, "cid": cid,
            "symbol": symbol, "is_cent": False, "orders": orders, "buy_lots": buy, "sell_lots": sell,
            "floating_pl": pl, "oldest_open": opened}


# ── SQL text ─────────────────────────────────────────────────────────────────


def test_sql_uses_the_indexed_open_sentinel_and_the_certified_universe():
    sql = ops.build_open_sql("family", (1, 5, 6))
    assert "t.closeDate = '1970-01-01'" in sql
    assert "CLOSE_TIME" not in sql
    assert "COALESCE(u.isEmployee, 0) = 0" in sql
    assert "NOT LIKE '%%demo%%'" in sql and "NOT LIKE '%%test%%'" in sql
    assert "t.CMD IN (0, 1)" in sql
    assert "t.SYMBOL LIKE %s" in sql
    assert ".kcmc" in sql and ".cent" in sql and "UPPER(mu.CURRENCY) = 'CEN'" in sql
    assert "t.SYMBOL = %s" in ops.build_open_sql("exact", (1,))


def test_symbol_param_family_escapes_like_wildcards():
    assert ops.symbol_param("XAUUSD", "family") == "XAUUSD%"
    assert ops.symbol_param("XAU_USD", "family") == "XAU\\_USD%"
    assert ops.symbol_param("XAU_USD", "exact") == "XAU_USD"


def test_fetch_open_passes_params_and_flags_truncation(monkeypatch):
    monkeypatch.setattr(ops, "MAX_FETCH_ROWS", 2)
    seen = {}

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            seen["sql"], seen["params"] = sql, params

        def fetchall(self):
            return [{"login_sid": f"1-{i}", "sid": 1, "client_id": i, "cid": 1, "symbol": "XAUUSD", "is_cent": 0,
                     "orders": 1, "buy_lots": 1, "sell_lots": 0, "floating_pl": 0, "oldest_open": None}
                    for i in range(3)]

    class Conn:
        closed = False

        def cursor(self):
            return Cur()

        def close(self):
            Conn.closed = True

    out = ops.fetch_open(None, symbol="XAUUSD", symbol_match="family", sids=[5], connect=lambda s: Conn())
    assert seen["params"] == ["XAUUSD%", 5, 3]
    assert out["truncated"] is True and len(out["rows"]) == 2
    assert Conn.closed


# ── rollup / sort / totals ───────────────────────────────────────────────────


def test_rollup_by_client_merges_accounts_and_symbols():
    rows = [acct("1-1", 10, 1, 2.0, 0.5, opened=datetime(2026, 9, 3)),
            acct("5-2", 10, 1, 1.0, 0.0, symbol="XAUUSD.c", opened=datetime(2026, 9, 1)),
            acct("1-3", 11, 1, 0.0, 4.0)]
    by_client = {r["client_id"]: r for r in ops.rollup(rows, "client")}
    c = by_client[10]
    assert c["login_sids"] == ["1-1", "5-2"] and c["symbols"] == ["XAUUSD", "XAUUSD.c"]
    assert (c["buy_lots"], c["sell_lots"], c["net_lots"], c["gross_lots"]) == (3.0, 0.5, 2.5, 3.5)
    assert c["oldest_open"] == datetime(2026, 9, 1)
    assert by_client[11]["net_lots"] == -4.0
    assert len(ops.rollup(rows, "account")) == 3


def test_net_sort_puts_a_locked_client_below_a_smaller_one_way_client():
    rows = ops.rollup([acct("5-1", 1, 1, 17.0, 17.0), acct("1-2", 2, 1, 14.0, 0.0), acct("1-3", 3, 1, 0.0, 15.0)],
                      "client")
    assert [r["client_id"] for r in ops.sort_rows(rows, "net_lots")] == [3, 2, 1]
    assert [r["client_id"] for r in ops.sort_rows(rows, "gross_lots")][0] == 1


def test_floating_sorts():
    rows = ops.rollup([acct("1-1", 1, 1, 1, 0, pl=-50.0), acct("1-2", 2, 1, 1, 0, pl=80.0)], "client")
    assert ops.sort_rows(rows, "floating_profit")[0]["client_id"] == 2
    assert ops.sort_rows(rows, "floating_loss")[0]["client_id"] == 1


# ── tool ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def book(monkeypatch):
    calls = {}
    rows = [acct("5-1", 1, 1, 17.0, 17.0, pl=10.0), acct("1-2", 2, 0, 14.0, 0.0, pl=-5.0),
            acct("1-3", 3, 1, 0.0, 15.0, pl=3.0), acct("1-4", 4, None, 1.0, 0.0)]

    def fake(settings, **kw):
        calls.update(kw)
        return {"rows": [dict(r) for r in rows], "truncated": False}

    monkeypatch.setattr(ops, "fetch_open", fake)
    return calls


def test_unrestricted_caller_sees_everything_ranked_by_net(book):
    env = run(op.rank_open_positions(ctx(), "XAUUSD"))
    assert env["ok"] is True and env["source"]["certified"] is True
    d = env["data"]
    assert [r["client_id"] for r in d["rows"]] == [3, 2, 4, 1]
    assert d["rows"][0]["rank"] == 1 and d["rows_masked_by_scope"] == 0
    assert d["totals"]["net_lots"] == round(32.0 - 32.0, 3) and d["totals"]["clients"] == 4
    assert book == {"symbol": "XAUUSD", "symbol_match": "family", "sids": None}
    assert any("XAUUSD" in c and "family" in c for c in env["definition"]["caveats"])


def test_restricted_caller_masks_before_top_n_and_totals_cover_visible_only(book):
    env = run(op.rank_open_positions(ctx(scope=frozenset({1})), "XAUUSD", top_n=1))
    d = env["data"]
    assert [r["client_id"] for r in d["rows"]] == [3]
    # client 2 (cid 0) and client 4 (cid unknown) are masked, fail closed.
    assert d["rows_masked_by_scope"] == 2
    assert d["totals"]["clients"] == 2
    assert d["totals"]["buy_lots"] == 17.0 and d["totals"]["sell_lots"] == 32.0
    assert d["groups_total"] == 2


def test_empty_scope_masks_everything(book):
    d = run(op.rank_open_positions(ctx(scope=frozenset()), "XAUUSD"))["data"]
    assert d["rows"] == [] and d["rows_masked_by_scope"] == 4 and d["totals"]["clients"] == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"symbol": ""},
        {"symbol": "XAU%"},
        {"symbol": "XAUUSD", "symbol_match": "prefix"},
        {"symbol": "XAUUSD", "group_by": "ib"},
        {"symbol": "XAUUSD", "sort": "profit"},
        {"symbol": "XAUUSD", "top_n": 0},
        {"symbol": "XAUUSD", "top_n": 51},
        {"symbol": "XAUUSD", "sids": [2]},
        {"symbol": "XAUUSD", "sids": []},
    ],
)
def test_bad_arguments_are_invalid_argument_and_never_reach_the_db(book, kwargs):
    env = run(op.rank_open_positions(ctx(), **kwargs))
    assert env["ok"] is False and env["error"]["code"] == "invalid_argument"
    assert book == {}


def test_db_timeout_becomes_upstream_timeout(monkeypatch):
    import pymysql

    def boom(settings, **kw):
        raise pymysql.err.OperationalError(3024, "Query execution was interrupted, maximum statement execution time exceeded")

    monkeypatch.setattr(ops, "fetch_open", boom)
    env = run(op.rank_open_positions(ctx(), "XAUUSD"))
    assert env["ok"] is False and env["error"]["code"] == "upstream_timeout"
