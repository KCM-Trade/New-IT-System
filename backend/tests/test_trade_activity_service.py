"""trade_activity_service — normalisation and aggregation on fake rows.

The SQL itself is not executed here (the replica is not a unit-test
dependency); what is pinned is the 口径 applied to what the SQL returns:
cent handling, the MT5 direction flip, hold buckets, grouping order, the
fact-only flags and the row cap.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services import trade_activity_service as tas


def row(symbol="XAUUSD", cmd=0, sid=1, lots=1.0, profit=10.0, open_t=None, hold_min=10, currency="USD", commission=-1.0, swaps=0.0, closed=True):
    open_t = open_t or datetime(2026, 9, 10, 12, 0)
    close_t = open_t + timedelta(minutes=hold_min) if closed else datetime(1970, 1, 1)
    return {
        "login_sid": f"{sid}-1", "sid": sid, "symbol": symbol, "cmd": cmd, "lots": lots, "profit": profit,
        "commission": commission, "swaps": swaps, "total_profit": profit + commission + swaps,
        "open_time": open_t, "close_time": close_t, "close_date": close_t.date() if closed else None, "currency": currency,
    }


def test_normalise_usd_row():
    r = tas.normalise_row(row())
    assert r["net_profit"] == 9.0 and r["gross_profit"] == 10.0 and r["commission"] == -1.0
    assert r["lots"] == 1.0 and r["hold_sec"] == 600 and r["hold_bucket"] == "lt30m" and r["direction"] == "buy"


def test_normalise_cent_account_divides_money_only():
    r = tas.normalise_row(row(currency="CEN", lots=5.0, profit=500.0, commission=0.0))
    assert r["net_profit"] == 5.0 and r["lots"] == 5.0 and r["is_cent"]


def test_normalise_cent_symbol_divides_lots_and_money_once():
    r = tas.normalise_row(row(symbol="XAUUSD.kcmc", currency="CEN", lots=200.0, profit=300.0, commission=0.0))
    assert r["lots"] == 2.0 and r["net_profit"] == 3.0
    plain = tas.normalise_row(row(symbol="XAUUSD.c", lots=2.0, profit=3.0, commission=0.0))
    assert plain["lots"] == 2.0 and plain["net_profit"] == 3.0  # .c is NOT cent


def test_hold_bucket_edges():
    assert tas.normalise_row(row(hold_min=29.99))["hold_bucket"] == "lt30m"
    assert tas.normalise_row(row(hold_min=30))["hold_bucket"] == "m30_2h"
    assert tas.normalise_row(row(hold_min=119.99))["hold_bucket"] == "m30_2h"
    assert tas.normalise_row(row(hold_min=120))["hold_bucket"] == "gt2h"


def test_aggregate_symbol_sorted_by_lots_and_flags():
    rows = [tas.normalise_row(row(symbol="XAUUSD", lots=3.0, profit=5.0)) for _ in range(5)]
    rows.append(tas.normalise_row(row(symbol="EURUSD", lots=0.5, profit=-2.0)))
    out = tas.aggregate(rows, "symbol")
    assert [r["key"] for r in out["rows"]] == ["XAUUSD", "EURUSD"]
    assert out["totals"]["orders"] == 6 and out["totals"]["symbols_traded"] == 2
    assert out["totals"]["win_rate"] == round(5 / 6, 4)
    assert "single_symbol_concentration" in out["flags"]  # 15 / 15.5 lots ≥ 90%
    assert "night_window_scalping" not in out["flags"]  # < 10 orders


def test_night_window_and_short_hold_flags():
    night = [tas.normalise_row(row(open_t=datetime(2026, 9, 10, 0, 30), hold_min=5)) for _ in range(6)]
    day = [tas.normalise_row(row(open_t=datetime(2026, 9, 10, 15, 0), hold_min=5)) for _ in range(6)]
    out = tas.aggregate(night + day, "day")
    assert "night_window_scalping" in out["flags"] and "short_hold_dominant" in out["flags"]
    assert out["rows"][0]["key"] == "2026-09-10" and out["rows"][0]["orders"] == 12


def test_aggregate_hold_bucket_order_and_labels():
    rows = [tas.normalise_row(row(hold_min=m)) for m in (200, 5, 60)]
    out = tas.aggregate(rows, "hold_bucket")
    assert [r["key"] for r in out["rows"]] == ["<30min", "30min-2h", ">2h"]


def test_aggregate_row_cap_marks_truncated():
    rows = [tas.normalise_row(row(symbol=f"S{i}")) for i in range(5)]
    out = tas.aggregate(rows, "symbol", max_rows=3)
    assert out["truncated"] is True and len(out["rows"]) == 3


def test_summarise_open_and_by_subject_wiring(monkeypatch):
    opens = [row(symbol="EURUSD", lots=0.5, profit=-3.0, commission=0.0, closed=False, open_t=datetime(2026, 9, 27, 9, 0)),
             row(symbol="EURUSD", lots=0.5, profit=1.0, commission=0.0, closed=False, open_t=datetime(2026, 9, 26, 9, 0))]
    closed = [row() for _ in range(3)]
    monkeypatch.setattr(tas, "fetch_closed_rows", lambda conn, sids, f, t: closed)
    monkeypatch.setattr(tas, "fetch_open_rows", lambda conn, sids: opens)
    closed_conn = []
    connect = lambda settings: SimpleNamespace(close=lambda: closed_conn.append(True))  # noqa: E731
    out = tas.by_subject(SimpleNamespace(), login_sids=["1-1"], date_from="2026-09-01", date_to="2026-09-27", group_by="symbol", connect=connect)
    assert out["open_positions"] == {"count": 2, "lots": 1.0, "floating_pl": -2.0, "oldest_open_at": datetime(2026, 9, 26, 9, 0)}
    assert out["totals"]["orders"] == 3 and out["truncated"] is False and closed_conn == [True]


def test_by_subject_row_cap(monkeypatch):
    monkeypatch.setattr(tas, "MAX_TRADE_ROWS", 2)
    monkeypatch.setattr(tas, "fetch_closed_rows", lambda conn, sids, f, t: [row() for _ in range(3)])
    monkeypatch.setattr(tas, "fetch_open_rows", lambda conn, sids: [])
    out = tas.by_subject(SimpleNamespace(), login_sids=["1-1"], date_from="a", date_to="b", group_by="symbol",
                         connect=lambda s: SimpleNamespace(close=lambda: None))
    assert out["rows_truncated"] is True and out["truncated"] is True and out["totals"]["orders"] == 2


def test_closed_sql_carries_the_canonical_filters():
    sql = tas._CLOSED_SQL
    for fragment in ("COALESCE(u.isEmployee, 0) = 0", "t.sid IN (1, 5, 6)", "t.CMD IN (0, 1)", "closeDate BETWEEN", "NOT LIKE '%%demo%%'", "LIMIT %s"):
        assert fragment in sql
    assert "closeDate = '1970-01-01'" in tas._OPEN_SQL
