"""alert_orders_service (OPT-0066 slice 3 B-svc) with a fake MySQL connection.

Pinned: normalise_order's 口径 (cent money ÷100 for CEN accounts and cent
SYMBOLS, lots ÷100 only for cent symbols, XAUUSD.c is NOT cent; sid=5 closed
rows have the CMD inverted; open rows have no close fields and hold to as_of;
MT wall clock → UTC is DST-aware), the three fetchers' SQL shape (universe
filters present, parameters bound, limit clamped, connection closed) and
mt_window_around.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services import alert_orders_service as aos

OPEN_SENTINEL = datetime(1970, 1, 1, 0, 0, 0)

ORDER_KEYS = {
    "ticket_sid", "ticket", "sid", "login", "login_sid", "symbol", "direction", "lots", "open_price",
    "close_price", "open_time", "close_time", "open_time_mt", "hold_sec", "profit_usd", "swap_usd",
    "commission_usd", "is_cent", "open",
}


def raw(**kw):
    base = {
        "ticket_sid": "1-1001", "ticket": 1001, "sid": 1, "login": 8522845, "login_sid": "1-8522845",
        "symbol": "XAUUSD", "cmd": 0, "lots": 1.0, "open_price": 2650.5, "close_price": 2651.5,
        "open_time": datetime(2026, 9, 10, 10, 0, 0), "close_time": datetime(2026, 9, 10, 10, 1, 30),
        "close_date": date(2026, 9, 10), "profit": 100.0, "swaps": -2.0, "commission": -7.0,
        "total_profit": 91.0, "currency": "USD",
    }
    base.update(kw)
    return base


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.calls.append((sql, list(params or [])))

    def fetchall(self):
        return self.conn.results.pop(0) if self.conn.results else []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, *results):
        self.results = list(results)
        self.calls: list = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def connector(conn):
    seen = []

    def _connect(settings):
        seen.append(settings)
        return conn

    _connect.seen = seen
    return _connect


# ── normalise_order ──────────────────────────────────────────────────────────


def test_every_key_is_present_and_summer_utc_is_minus_three():
    o = aos.normalise_order(raw())
    assert set(o) == ORDER_KEYS
    assert o["open_time"] == "2026-09-10T07:00:00Z"
    assert o["close_time"] == "2026-09-10T07:01:30Z"
    assert o["open_time_mt"] == "2026-09-10 10:00:00"
    assert o["hold_sec"] == 90
    assert o["direction"] == "buy" and o["open"] is False and o["is_cent"] is False
    assert o["profit_usd"] == 100.0 and o["swap_usd"] == -2.0 and o["commission_usd"] == -7.0
    assert o["open_price"] == 2650.5 and o["close_price"] == 2651.5
    assert o["login_sid"] == "1-8522845"


def test_winter_utc_is_minus_two():
    o = aos.normalise_order(raw(open_time=datetime(2026, 11, 10, 6, 0), close_time=datetime(2026, 11, 10, 6, 5)))
    assert o["open_time"] == "2026-11-10T04:00:00Z"
    assert o["close_time"] == "2026-11-10T04:05:00Z"


def test_cent_account_divides_money_but_not_lots():
    o = aos.normalise_order(raw(currency="CEN", lots=1.0, profit=10000.0, swaps=-200.0, commission=-700.0))
    assert o["profit_usd"] == 100.0 and o["swap_usd"] == -2.0 and o["commission_usd"] == -7.0
    assert o["lots"] == 1.0
    assert o["is_cent"] is True


@pytest.mark.parametrize("symbol", ["XAUUSD.kcmc", "EURUSD.cent"])
def test_cent_symbol_divides_money_and_lots(symbol):
    o = aos.normalise_order(raw(symbol=symbol, lots=100.0, profit=10000.0))
    assert o["lots"] == 1.0 and o["profit_usd"] == 100.0 and o["is_cent"] is True


def test_xauusd_c_is_not_a_cent_product():
    o = aos.normalise_order(raw(symbol="XAUUSD.c", lots=1.0, profit=100.0))
    assert o["lots"] == 1.0 and o["profit_usd"] == 100.0 and o["is_cent"] is False


def test_sid5_closed_row_direction_is_inverted_but_open_is_not():
    closed = aos.normalise_order(raw(sid=5, cmd=1, ticket_sid="5-37239474"))
    assert closed["direction"] == "buy"  # stored exit side SELL → position was BUY
    still_open = aos.normalise_order(
        raw(sid=5, cmd=1, ticket="p37239458", ticket_sid="5-p37239458", close_time=OPEN_SENTINEL, close_price=0),
        as_of_utc=datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc),
    )
    assert still_open["direction"] == "sell"
    assert still_open["ticket"] == 37239458  # "p" prefix stripped
    mt4 = aos.normalise_order(raw(sid=1, cmd=1))
    assert mt4["direction"] == "sell"


def test_open_row_has_no_close_fields_and_holds_to_as_of():
    o = aos.normalise_order(
        raw(close_time=OPEN_SENTINEL, close_price=0.0),
        as_of_utc=datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc),  # MT 11:00
    )
    assert o["open"] is True
    assert o["close_time"] is None and o["close_price"] is None
    assert o["hold_sec"] == 3600


# ── fetchers ─────────────────────────────────────────────────────────────────


def test_by_tickets_looks_up_both_mt5_keys_and_closes_the_connection():
    conn = FakeConn([raw(sid=5, ticket="p9", ticket_sid="5-p9", close_time=OPEN_SENTINEL)])
    connect = connector(conn)
    out = aos.fetch_orders_by_tickets("S", sid=5, tickets=[9, 9, 8], connect=connect,
                                      as_of_utc=datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc))
    assert connect.seen == ["S"] and conn.closed
    sql, params = conn.calls[0]
    assert "t.ticketSid IN" in sql and "isEmployee" in sql and "CMD IN (0, 1)" in sql
    assert set(params[:-1]) == {"5-8", "5-9", "5-p8", "5-p9"}
    assert params[-1] == aos.MAX_ORDERS_HARD
    assert len(out) == 1 and set(out[0]) == ORDER_KEYS


def test_by_tickets_mt4_uses_plain_keys_only_and_empty_input_skips_the_db():
    conn = FakeConn([])
    aos.fetch_orders_by_tickets("S", sid=1, tickets=[5], connect=connector(conn))
    assert conn.calls[0][1][:-1] == ["1-5"]
    untouched = FakeConn()
    assert aos.fetch_orders_by_tickets("S", sid=1, tickets=[], connect=connector(untouched)) == []
    assert untouched.calls == []


def test_by_tickets_rejects_a_non_live_sid():
    with pytest.raises(ValueError):
        aos.fetch_orders_by_tickets("S", sid=2, tickets=[1], connect=connector(FakeConn()))


def test_open_window_returns_capped_rows_and_the_uncapped_total():
    conn = FakeConn([{"n": 7}], [raw(), raw(ticket_sid="1-1002", ticket=1002)])
    orders, total = aos.fetch_orders_by_open_window(
        "S", login_sids=["1-8522845", "bad", "9-1"], mt_from=datetime(2026, 9, 10, 9, 59, 59),
        mt_to=datetime(2026, 9, 10, 10, 0, 1), symbol="XAUUSD", limit=2, connect=connector(conn),
    )
    assert total == 7 and len(orders) == 2 and conn.closed
    count_sql, count_params = conn.calls[0]
    assert count_sql.lstrip().upper().startswith("SELECT COUNT(*)")
    assert count_params[0] == "1-8522845" and len([p for p in count_params if p == "9-1"]) == 0
    assert "t.OPEN_TIME BETWEEN" in count_sql and "t.SYMBOL = %s" in count_sql
    _, row_params = conn.calls[1]
    assert row_params[-1] == 2


def test_limit_is_clamped_to_the_hard_cap():
    conn = FakeConn([{"n": 0}], [])
    aos.fetch_orders_by_open_window("S", login_sids=["1-1"], mt_from=datetime(2026, 9, 10), mt_to=datetime(2026, 9, 10, 1),
                                    limit=10_000, connect=connector(conn))
    assert conn.calls[1][1][-1] == aos.MAX_ORDERS_HARD


def test_open_window_with_no_valid_accounts_skips_the_db():
    conn = FakeConn()
    assert aos.fetch_orders_by_open_window("S", login_sids=["x"], mt_from=datetime(2026, 9, 10),
                                           mt_to=datetime(2026, 9, 10, 1), connect=connector(conn)) == ([], 0)
    assert conn.calls == []


def test_open_window_rejects_a_reversed_window():
    with pytest.raises(ValueError):
        aos.fetch_orders_by_open_window("S", login_sids=["1-1"], mt_from=datetime(2026, 9, 10, 2),
                                        mt_to=datetime(2026, 9, 10, 1), connect=connector(FakeConn()))


def test_trading_day_filters_on_open_date():
    conn = FakeConn([{"n": 1}], [raw()])
    orders, total = aos.fetch_orders_for_trading_day("S", login_sids=["1-8522845"], trading_day=date(2026, 9, 10),
                                                     connect=connector(conn))
    assert total == 1 and len(orders) == 1
    sql, params = conn.calls[0]
    assert "t.openDate = %s" in sql
    assert date(2026, 9, 10) in params


def test_default_connect_is_connect_readonly_with_a_15s_budget(monkeypatch):
    seen = {}

    def fake(settings, **kw):
        seen.update(kw)
        return FakeConn([])

    monkeypatch.setattr(aos, "connect_readonly", fake)
    aos.fetch_orders_by_tickets("S", sid=1, tickets=[1])
    assert seen["max_execution_ms"] == 15000


def test_connection_is_closed_even_when_the_query_fails():
    class Boom(FakeConn):
        def cursor(self):
            raise RuntimeError("down")

    conn = Boom()
    with pytest.raises(RuntimeError):
        aos.fetch_orders_for_trading_day("S", login_sids=["1-1"], trading_day=date(2026, 9, 10), connect=connector(conn))
    assert conn.closed


# ── mt_window_around ─────────────────────────────────────────────────────────


def test_mt_window_around_is_dst_aware_and_padded():
    lo, hi = aos.mt_window_around("2026-09-10T07:00:00Z", "2026-09-10T07:00:05Z")
    assert lo == datetime(2026, 9, 10, 9, 59, 59) and hi == datetime(2026, 9, 10, 10, 0, 6)
    lo, hi = aos.mt_window_around("2026-11-10T04:00:00Z", "2026-11-10T04:00:00Z", pad_seconds=0)
    assert lo == hi == datetime(2026, 11, 10, 6, 0)
