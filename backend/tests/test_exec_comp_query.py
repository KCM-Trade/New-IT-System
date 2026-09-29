"""T3 — the query core (``services/exec_comp/query.py``) against a fake source.

No database: ``FakeSource`` implements ``models.FillSource`` over an in-memory
list of ``RawFill``s. The dataset below is hand-built so every expected number
can be recomputed by eye (USD account, ContractSize 100, 1 lot, RateProfit 1
-> comp_usd = worse_px * 100).

Covers 04 §4: subject / date / as_of errors, clipping, default-as_of step-back,
reproducibility, open-position exclusion (02 §6.1), anomalies, unmatched
order rows, class breakdown, netting, groupings, deal cap, paging, views,
sort whitelist and the ``basis`` block.

query.py takes keyword-only ``clock`` (aware instant) and ``cache`` (a
redis-py-like client, default None); tests use a dict-backed ``FakeRedis``.
"""

from __future__ import annotations

import datetime as dt
import inspect
from typing import Optional, Sequence

import pytest

from app.schemas.exec_compensation import CALC_VERSION
from app.services.exec_comp import calc
from app.services.exec_comp import query as Q
from app.services.exec_comp.errors import ExecCompError
from app.services.exec_comp.models import Account, PositionLeg, RawFill, ReplicaHead

pytestmark = pytest.mark.skipif(
    "raise NotImplementedError" in inspect.getsource(Q.summary),
    reason="query.py is still the frozen interface stub",
)

A_USD = 60000001
B_CEN = 60000002
CLIENT = 1001
OFFSET = dt.timedelta(hours=3)  # September = summer, GMT+3

ACCOUNTS = {
    A_USD: Account(login=A_USD, ccy="USD", group="KCM\\5SD_L10"),
    B_CEN: Account(login=B_CEN, ccy="CEN", group="KCMC\\5c_L10"),
}


def T(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)


def mk(
    deal: int,
    pos: int,
    when: str,
    *,
    login: int = A_USD,
    entry: int = 0,
    action: int = 0,
    price: float = 100.0,
    ref: float = 100.0,
    volume: int = 10000,
    reason: Optional[int] = 1,
    otype: Optional[int] = None,
    dealer: int = 776,
    order_found: bool = True,
    comment: str = "",
    symbol: str = "XAUUSD",
    delay_ms: int = 300,
) -> RawFill:
    t = T(when)
    return RawFill(
        deal_id=deal,
        order_id=deal + 500_000,
        position_id=pos,
        login=login,
        action=action,
        entry=entry,
        price=price,
        volume=volume,
        contract_size=100.0,
        rate_profit=1.0,
        symbol=symbol,
        dealer=dealer,
        time_msc=t,
        timestamp_ft=calc.utc_to_filetime(t - OFFSET),
        profit=0.0,
        price_position=0.0,
        order_found=order_found,
        order_type=(action if otype is None else otype) if order_found else None,
        order_reason=reason if order_found else None,
        comment=comment if order_found else None,
        price_current=ref if order_found else None,
        price_order=0.0 if order_found else None,
        time_setup_msc=(t - dt.timedelta(milliseconds=delay_ms)) if order_found else None,
    )


# Positions (login A, USD unless noted). Range under test: 09-01 .. 09-28.
P1, P2, P3, P4, P5, P6, P7, P8, P9 = range(9001, 9010)
FILLS: list[RawFill] = [
    # P1: open + close in range, both worse -> counted 10.0 + 5.0
    mk(101, P1, "2026-09-10T10:00:00", action=0, price=100.10),
    mk(102, P1, "2026-09-11T10:00:00", entry=1, action=1, price=99.95),
    # P2: opened BEFORE date_from, closed in range (better) -> close counted -2.0
    mk(103, P2, "2026-08-20T10:00:00", action=0, price=100.00),
    mk(104, P2, "2026-09-05T10:00:00", entry=1, action=1, price=100.02),
    # P3: opened 2 lots, partially closed 1 lot, still open -> both excluded
    mk(105, P3, "2026-09-12T10:00:00", action=0, price=100.01, volume=20000),
    mk(106, P3, "2026-09-13T10:00:00", entry=1, action=1, price=100.00),
    # P4: opened in range, closed 09-29 01:00 (after as_of 09-28) -> open excluded
    mk(107, P4, "2026-09-14T10:00:00", action=0, price=100.03),
    mk(108, P4, "2026-09-29T01:00:00", entry=1, action=1, price=100.00),
    # P5 (CEN): Entry 2 in the lifecycle -> anomaly, nothing counted
    mk(109, P5, "2026-09-15T10:00:00", login=B_CEN, action=0),
    mk(110, P5, "2026-09-16T10:00:00", login=B_CEN, entry=2, action=1),
    # P6: market open (same price) counted 0.0; stop-loss close -> class sl
    mk(111, P6, "2026-09-17T10:00:00", action=0),
    mk(112, P6, "2026-09-18T10:00:00", entry=1, action=1, reason=3),
    # P7: limit open -> class limit; market close worse -> counted 1.0
    mk(113, P7, "2026-09-19T09:00:00", action=0, otype=2, reason=16),
    mk(114, P7, "2026-09-19T10:00:00", entry=1, action=1, price=99.99),
    # P8: order row missing -> unmatched
    mk(115, P8, "2026-09-20T10:00:00", action=0, order_found=False),
    # P9: Dealer 1 (oneZero) -> no_plugin x2
    mk(116, P9, "2026-09-21T10:00:00", action=0, dealer=1, price=100.05),
    mk(117, P9, "2026-09-21T11:00:00", entry=1, action=1, dealer=1),
    # precedence class > anomaly > open (G11):
    # a stop-order add-on inside still-open P3 -> not_counted_by_class, not excluded_open
    mk(118, P3, "2026-09-12T11:00:00", action=0, otype=4, reason=1),
    # a take-profit leg inside anomalous P5 -> not_counted_by_class, not anomaly
    mk(119, P5, "2026-09-16T12:00:00", login=B_CEN, entry=1, action=1, reason=4),
]

COUNTED = {101: 10.0, 102: 5.0, 104: -2.0, 111: 0.0, 114: 1.0}
EXCLUDED_OPEN = {105, 106, 107}

NOW_0929 = T("2026-09-29T10:00:00")  # MT server wall clock
HEAD_0929 = T("2026-09-29T09:59:00")


class FakeSource:
    def __init__(self, fills: Sequence[RawFill], head: Optional[dt.datetime] = HEAD_0929):
        self._fills = {f.deal_id: f for f in fills}
        self._head = head
        self.calls: dict[str, int] = {}

    def _hit(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def accounts_for_client(self, client_id: int) -> list[Account]:
        self._hit("accounts_for_client")
        return list(ACCOUNTS.values()) if client_id == CLIENT else []

    def account(self, login: int) -> Optional[Account]:
        self._hit("account")
        return ACCOUNTS.get(login)

    def replica_head(self) -> Optional[ReplicaHead]:
        self._hit("replica_head")
        if self._head is None:
            return None
        last = max(self._fills) if self._fills else 0
        return ReplicaHead(deal_id=last, time_srv=self._head, time_utc=self._head - OFFSET)

    def deal_ids(self, logins, srv_from, srv_to) -> list[int]:
        self._hit("deal_ids")
        ls = set(logins)
        return sorted(
            d for d, f in self._fills.items()
            if f.login in ls and srv_from <= f.time_msc < srv_to
        )

    def fills(self, deal_ids) -> list[RawFill]:
        self._hit("fills")
        return [self._fills[d] for d in deal_ids if d in self._fills]

    def position_legs(self, positions) -> list[PositionLeg]:
        self._hit("position_legs")
        want = set(positions)
        return [
            PositionLeg(
                login=f.login, position_id=f.position_id, deal_id=f.deal_id,
                entry=f.entry, volume=f.volume, time_msc=f.time_msc,
            )
            for f in self._fills.values()
            if (f.login, f.position_id) in want
        ]


# --- clock / cache injection (query.py keyword-only params) -----------------


def srv_clock(now_srv: dt.datetime) -> dt.datetime:
    """query.py reads ``clock`` as an aware instant (naive = UTC); the tests
    think in MT server wall clock (GMT+3 in September)."""
    return (now_srv - OFFSET).replace(tzinfo=dt.timezone.utc)


def _run(fn, *args, now: dt.datetime = NOW_0929, **kw):
    kw.setdefault("clock", srv_clock(now))
    if "cache" in inspect.signature(fn).parameters:
        kw.setdefault("cache", None)
    return fn(*args, **kw)


class FakeRedis:
    """The subset of redis-py query.py uses (decode_responses=True)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value
        self.ttl[key] = int(ttl)
        return True


def q(
    date_from: str = "2026-09-01",
    date_to: str = "2026-09-28",
    *,
    client_id: Optional[int] = CLIENT,
    login_sid: Optional[str] = None,
    as_of: Optional[str] = None,
) -> Q.Query:
    return Q.Query(
        client_id=client_id,
        login_sid=login_sid,
        date_from=dt.date.fromisoformat(date_from),
        date_to=dt.date.fromisoformat(date_to),
        as_of=dt.date.fromisoformat(as_of) if as_of else None,
    )


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch, tmp_path):
    """Pin every EXEC_COMP_* threshold this file depends on (config reads
    backend/.env) and keep query slots in a per-test directory."""
    monkeypatch.setenv("REDIS_HOST", "127.0.0.1")
    monkeypatch.setenv("REDIS_PORT", "1")
    monkeypatch.setenv("EXEC_COMP_CACHE_TTL_S", "3600")
    monkeypatch.setenv("EXEC_COMP_SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setenv("EXEC_COMP_MAX_DEALS", "500000")
    monkeypatch.setenv("EXEC_COMP_QUERY_BUDGET_S", "60")
    monkeypatch.setenv("EXEC_COMP_MAX_CONCURRENT", "2")
    monkeypatch.setenv("EXEC_COMP_PAGE_SIZE_MAX", "1000")
    monkeypatch.delenv("EXEC_COMP_CACHE_MAX_BYTES", raising=False)


@pytest.fixture
def src() -> FakeSource:
    return FakeSource(FILLS)


def summ(src, query=None, **kw):
    return _run(Q.summary, query or q(), source=src, **kw)


def orders(src, query=None, **kw):
    return _run(Q.orders, query or q(), source=src, **kw)


def _err(fn) -> ExecCompError:
    with pytest.raises(ExecCompError) as ei:
        fn()
    return ei.value


# ---------------------------------------------------------------------------
# subject
# ---------------------------------------------------------------------------


def test_subject_required(src):
    assert _err(lambda: summ(src, q(client_id=None))).code == "SUBJECT_REQUIRED"


def test_subject_ambiguous(src):
    e = _err(lambda: summ(src, q(client_id=CLIENT, login_sid=f"5-{A_USD}")))
    assert e.code == "SUBJECT_AMBIGUOUS"


@pytest.mark.parametrize("sid", ["1-60000001", "5-", "5-abc", "60000001", "5-6000-1", " 5-60000001x"])
def test_invalid_login_sid(src, sid):
    assert _err(lambda: summ(src, q(client_id=None, login_sid=sid))).code == "INVALID_LOGIN_SID"


def test_login_sid_not_found(src):
    e = _err(lambda: summ(src, q(client_id=None, login_sid="5-99999999")))
    assert e.code == "SUBJECT_NOT_FOUND"


def test_login_sid_limits_to_that_account(src):
    r = summ(src, q(client_id=None, login_sid=f"5-{B_CEN}"))
    assert r.data.subject.logins == [B_CEN]
    assert r.data.subject.login_sid == f"5-{B_CEN}"
    assert r.data.deals == 0
    assert r.data.anomaly_positions == 1


def test_client_subject_covers_all_its_logins(src):
    r = summ(src)
    assert r.data.subject.client_id == CLIENT
    assert sorted(r.data.subject.logins) == [A_USD, B_CEN]


# ---------------------------------------------------------------------------
# dates and as_of
# ---------------------------------------------------------------------------


def test_default_as_of_is_yesterday_mt_day(src):
    r = summ(src)
    assert r.as_of == dt.date(2026, 9, 28)
    assert r.data.query.as_of == dt.date(2026, 9, 28)
    assert r.coverage.complete is True


def test_date_to_after_as_of_is_clipped_and_flagged(src):
    r = summ(src, q(date_to="2026-09-30"))
    echo = r.data.query
    assert echo.date_to == dt.date(2026, 9, 28)
    assert echo.date_to_requested == dt.date(2026, 9, 30)
    assert echo.date_to_clipped is True
    assert summ(src).data.query.date_to_clipped is False


def test_as_of_later_than_default_is_rejected(src):
    e = _err(lambda: summ(src, q(as_of="2026-09-29")))
    assert e.code == "AS_OF_TOO_LATE"
    assert e.status == 422


def test_date_from_before_coverage(src):
    e = _err(lambda: summ(src, q(date_from="2023-02-19", date_to="2023-03-01")))
    assert e.code == "RANGE_BEFORE_COVERAGE"
    # the first MT5 day itself is fine
    summ(src, q(date_from="2023-02-20", date_to="2023-03-01"))


def test_range_invalid(src):
    e = _err(lambda: summ(src, q(date_from="2026-09-20", date_to="2026-09-10")))
    assert e.code == "RANGE_INVALID"


def test_replica_behind_steps_default_as_of_back_one_day():
    lagging = FakeSource(FILLS, head=T("2026-09-28T23:10:00"))
    r = summ(lagging)
    assert r.as_of == dt.date(2026, 9, 27)
    assert r.coverage.ready_through_srv_date == dt.date(2026, 9, 27)
    assert r.coverage.reason
    assert r.data.query.date_to == dt.date(2026, 9, 27)


def test_same_as_of_twice_is_identical(src):
    a = summ(src, q(as_of="2026-09-28"))
    b = summ(src, q(as_of="2026-09-28"))
    assert a.data.model_dump() == b.data.model_dump()
    assert a.coverage.model_dump() == b.coverage.model_dump()
    oa = orders(src, q(as_of="2026-09-28"), view="all", page_size=500)
    ob = orders(src, q(as_of="2026-09-28"), view="all", page_size=500)
    assert [r.model_dump() for r in oa.data] == [r.model_dump() for r in ob.data]


def test_pushing_as_of_later_moves_position_from_excluded_to_counted():
    before = summ(FakeSource(FILLS), q(as_of="2026-09-28"))
    later = _run(
        Q.summary, q(as_of="2026-09-29"),
        source=FakeSource(FILLS, head=T("2026-09-30T09:00:00")),
        now=T("2026-09-30T10:00:00"),
    )
    assert later.as_of == dt.date(2026, 9, 29)
    # P4's open (09-14, 3.0 USD) closes on 09-29
    assert later.data.deals == before.data.deals + 1
    assert later.data.comp_net_usd == pytest.approx(before.data.comp_net_usd + 3.0)
    assert later.data.excluded_open.positions == before.data.excluded_open.positions - 1
    assert later.data.excluded_open.deals == before.data.excluded_open.deals - 1


# ---------------------------------------------------------------------------
# counting rules (02 §6.1, 01 D12 / D13)
# ---------------------------------------------------------------------------


def test_headline_numbers(src):
    d = summ(src).data
    assert d.deals == len(COUNTED) == 5
    assert d.lots == pytest.approx(5.0)
    assert d.comp_net_usd == pytest.approx(14.0)
    assert d.comp_positive_usd == pytest.approx(16.0)
    assert d.max_single_comp_usd == pytest.approx(10.0)
    assert (d.outcomes.worse, d.outcomes.same, d.outcomes.better) == (3, 1, 1)


def test_counted_rows_are_exactly_the_expected_deals(src):
    r = orders(src, view="counted", page_size=500)
    got = {row.deal_id: row.comp_usd for row in r.data}
    assert set(got) == set(COUNTED)
    for deal, amount in COUNTED.items():
        assert got[deal] == pytest.approx(amount)
    assert all(row.counted and row.eligible and row.cls == "market" for row in r.data)


def test_position_opened_before_range_closed_inside_is_counted(src):
    rows = {r.deal_id: r for r in orders(src, view="all", page_size=500).data}
    assert rows[104].counted is True
    assert 103 not in rows  # its open is outside the date range


def test_partially_closed_position_excludes_both_legs(src):
    d = summ(src).data
    ex = d.excluded_open
    assert ex.positions == 2                  # P3 + P4
    assert ex.deals == 3                      # 105, 106, 107
    assert ex.lots == pytest.approx(4.0)      # 2 + 1 + 1
    assert ex.comp_net_usd_if_counted == pytest.approx(5.0)  # 2.0 + 0.0 + 3.0
    rows = orders(src, view="excluded_open", page_size=500)
    assert {r.deal_id for r in rows.data} == EXCLUDED_OPEN
    assert rows.total == 3
    assert all(r.not_counted_reason == "position_open_at_as_of" for r in rows.data)
    assert all(not r.counted for r in rows.data)


def test_entry_2_is_an_anomaly_and_not_counted(src):
    d = summ(src).data
    assert d.anomaly_positions == 1
    rows = {r.deal_id: r for r in orders(src, view="all", page_size=500).data}
    assert rows[109].counted is False and rows[109].not_counted_reason == "position_anomaly"
    assert rows[110].counted is False
    assert rows[110].entry == "inout"


def test_position_id_zero_is_an_anomaly():
    fills = FILLS + [mk(120, 0, "2026-09-22T10:00:00", action=0, price=100.5)]
    d = summ(FakeSource(fills)).data
    assert d.anomaly_positions == 2
    assert d.deals == 5


def test_unmatched_order_row_is_counted_in_coverage_not_in_results(src):
    r = summ(src)
    assert r.coverage.unmatched_deals == 1
    counted = {row.deal_id for row in orders(src, view="counted", page_size=500).data}
    assert 115 not in counted


def test_non_market_classes_are_broken_out(src):
    by = {row.cls: row for row in summ(src).data.not_counted_by_class}
    assert by["sl"].deals == 1
    assert by["limit"].deals == 1
    assert by["no_plugin"].deals == 2
    assert by["stop"].deals == 1
    assert by["tp"].deals == 1
    assert "market" not in by
    rows = {r.deal_id: r for r in orders(src, view="all", page_size=500).data}
    assert rows[112].cls == "sl" and rows[112].not_counted_reason == "not_eligible_class"
    assert rows[113].cls == "limit" and rows[113].eligible is False
    assert rows[116].cls == "no_plugin" and rows[116].comp_usd == pytest.approx(5.0)


def test_legs_after_as_of_are_ignored_for_closure(src):
    # P4's close happens 09-29 01:00, i.e. after as_of 09-28 -> P4 still open
    rows = {r.deal_id: r for r in orders(src, view="all", page_size=500).data}
    assert rows[107].not_counted_reason == "position_open_at_as_of"
    assert 108 not in rows  # outside the date range


def test_cen_amounts_are_divided_by_100():
    fills = [
        mk(201, 1, "2026-09-10T10:00:00", login=B_CEN, action=0, price=100.10),
        mk(202, 1, "2026-09-10T11:00:00", login=B_CEN, entry=1, action=1, price=100.10),
    ]
    d = summ(FakeSource(fills)).data
    assert d.deals == 2
    # 0.10 * 100 = 10 cents = 0.10 USD for the open; close sell at 100.10 vs ref 100 = better -0.10
    assert d.comp_positive_usd == pytest.approx(0.10)
    assert d.comp_net_usd == pytest.approx(0.0)


def test_unknown_currency_fails_closed(monkeypatch):
    monkeypatch.setitem(ACCOUNTS, A_USD, Account(login=A_USD, ccy="EUR", group="KCM\\5SD_L10"))
    e = _err(lambda: summ(FakeSource(FILLS)))
    assert e.code == "UNKNOWN_CURRENCY"


# ---------------------------------------------------------------------------
# groupings
# ---------------------------------------------------------------------------


def test_by_entry(src):
    by = {g.key: g for g in summ(src).data.by_entry}
    assert set(by) == {"open", "close"}
    assert by["open"].deals == 2 and by["open"].comp_net_usd == pytest.approx(10.0)
    assert by["close"].deals == 3 and by["close"].comp_net_usd == pytest.approx(4.0)


def test_by_day_uses_mt_server_day(src):
    by = {g.key: g.comp_net_usd for g in summ(src).data.by_day}
    assert by == pytest.approx(
        {"2026-09-05": -2.0, "2026-09-10": 10.0, "2026-09-11": 5.0,
         "2026-09-17": 0.0, "2026-09-19": 1.0}
    )


def test_by_account_keys_are_login_sid(src):
    keys = {g.key for g in summ(src).data.by_account}
    assert f"5-{A_USD}" in keys
    assert all(k.startswith("5-") for k in keys)


def test_mt_day_boundary_is_server_wall_clock():
    # 23:59 server time on 09-28 is still 09-28 even though it is 20:59 UTC;
    # 00:30 server time on 09-01 is 09-01 although UTC is still 08-31.
    fills = [
        mk(301, 1, "2026-09-01T00:30:00", action=0, price=100.01),
        mk(302, 1, "2026-09-28T23:59:00", entry=1, action=1, price=99.99),
    ]
    by = {g.key for g in summ(FakeSource(fills)).data.by_day}
    assert by == {"2026-09-01", "2026-09-28"}


# ---------------------------------------------------------------------------
# thresholds
# ---------------------------------------------------------------------------


def test_deal_cap_exceeded_is_query_too_large(monkeypatch):
    monkeypatch.setenv("EXEC_COMP_MAX_DEALS", "3")
    from app.core.config import get_settings

    get_settings.cache_clear()
    e = _err(lambda: summ(FakeSource(FILLS)))
    assert e.code == "QUERY_TOO_LARGE"
    assert e.status == 422


# ---------------------------------------------------------------------------
# orders(): paging / views / sorting
# ---------------------------------------------------------------------------


def test_paging(src):
    r = orders(src, view="counted", page=3, page_size=2)
    assert r.total == 5
    assert r.total_pages == 3
    assert r.page == 3 and r.page_size == 2
    assert len(r.data) == 1


def test_page_past_the_end_is_empty(src):
    r = orders(src, view="counted", page=9, page_size=2)
    assert r.data == [] and r.total == 5


def test_default_sort_is_fill_time_ascending(src):
    r = orders(src, view="counted", page_size=500)
    assert [row.deal_id for row in r.data] == [104, 101, 102, 111, 114]


def test_sort_by_comp_desc(src):
    r = orders(src, view="counted", page_size=500, sort_by="comp_usd", sort_dir="desc")
    assert [row.deal_id for row in r.data][:2] == [101, 102]
    assert r.data[-1].deal_id == 104


def test_sort_not_allowed(src):
    e = _err(lambda: orders(src, sort_by="price; DROP TABLE x"))
    assert e.code == "SORT_NOT_ALLOWED"


def test_view_all_contains_every_classified_in_range_fill(src):
    r = orders(src, view="all", page_size=500)
    ids = {row.deal_id for row in r.data}
    in_range = {101, 102, 104, 105, 106, 107, 109, 110, 111, 112, 113, 114, 116, 117, 118, 119}
    assert in_range <= ids
    assert 103 not in ids and 108 not in ids


def test_order_row_fields(src):
    rows = {r.deal_id: r for r in orders(src, view="all", page_size=500).data}
    r = rows[101]
    assert r.login == A_USD and r.login_sid == f"5-{A_USD}" and r.ccy == "USD"
    assert r.side == "buy" and r.entry == "open" and r.cls == "market"
    assert r.lots == pytest.approx(1.0)
    assert r.ref_price == 100.0 and r.fill_price == 100.10
    assert r.worse_px == pytest.approx(0.1)
    assert r.outcome == "worse"
    assert r.comp_usd == pytest.approx(10.0)
    assert r.delay_ms == 300
    assert r.srv_date == dt.date(2026, 9, 10)
    assert r.fill_time_srv == "2026-09-10 10:00:00.000"
    assert r.fill_time_utc == "2026-09-10T07:00:00.000Z"
    assert r.raw.action == 0 and r.raw.entry == 0 and r.raw.dealer == 776
    assert rows[102].side == "sell" and rows[102].entry == "close"
    assert rows[109].ccy == "CEN"


# ---------------------------------------------------------------------------
# basis / envelope
# ---------------------------------------------------------------------------


def test_basis_on_every_response(src):
    for resp in (summ(src), orders(src)):
        b = resp.basis
        assert b.reference == "request_price"
        assert b.order_scope == "client_market_open_close"
        assert b.netting == "net"
        assert b.exclude_open_positions is True
        assert b.calc_version == CALC_VERSION
        assert resp.as_of == dt.date(2026, 9, 28)
        assert resp.coverage.earliest_srv_date == Q.EARLIEST_SRV_DATE == dt.date(2023, 2, 20)


def test_export_rows_matches_summary_and_all_view(src):
    s, rows = _run(Q.export_rows, q(), source=src)
    assert s.data.comp_net_usd == pytest.approx(14.0)
    assert {r.deal_id for r in rows} == {r.deal_id for r in orders(src, view="all", page_size=500).data}


def test_status(src):
    st = _run(Q.status, source=src)
    assert st.data.earliest_srv_date == dt.date(2023, 2, 20)
    assert st.data.default_as_of == dt.date(2026, 9, 28)
    assert st.data.calc_version == CALC_VERSION
    assert st.basis.reference == "request_price"


# ---------------------------------------------------------------------------
# G10 / G11 semantics
# ---------------------------------------------------------------------------


def test_client_with_zero_mt5_accounts_is_an_empty_result(src):
    r = summ(src, q(client_id=2002))
    assert r.data.subject.client_id == 2002
    assert r.data.subject.logins == []
    assert r.data.deals == 0 and r.data.comp_net_usd == 0
    assert r.data.by_account == [] and r.data.not_counted_by_class == []
    assert src.calls.get("deal_ids", 0) == 0 and src.calls.get("fills", 0) == 0
    o = orders(src, q(client_id=2002))
    assert o.total == 0 and o.data == [] and o.total_pages == 0


def test_date_from_after_the_clipped_date_to_is_range_invalid(src):
    # date_to 09-30 clips to as_of 09-28; date_from 09-29 is after it
    e = _err(lambda: summ(src, q(date_from="2026-09-29", date_to="2026-09-30")))
    assert e.code == "RANGE_INVALID"


def test_precedence_class_beats_anomaly_beats_open(src):
    rows = {r.deal_id: r for r in orders(src, view="all", page_size=500).data}
    # non-market fill inside a still-open position -> class, not excluded_open
    assert rows[118].cls == "stop"
    assert rows[118].not_counted_reason == "not_eligible_class"
    # non-market fill inside an anomalous position -> class, not anomaly
    assert rows[119].cls == "tp"
    assert rows[119].not_counted_reason == "not_eligible_class"
    # market fill inside an anomalous position -> anomaly
    assert rows[109].not_counted_reason == "position_anomaly"
    ex = orders(src, view="excluded_open", page_size=500).data
    assert {r.not_counted_reason for r in ex} == {"position_open_at_as_of"}
    assert 118 not in {r.deal_id for r in ex}


# ---------------------------------------------------------------------------
# thresholds (01 D21, G3)
# ---------------------------------------------------------------------------


def _set_env(monkeypatch, **env):
    from app.core.config import get_settings

    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    get_settings.cache_clear()


def test_deal_cap_is_checked_before_any_fill_is_fetched(monkeypatch):
    _set_env(monkeypatch, EXEC_COMP_MAX_DEALS=3)
    fake = FakeSource(FILLS)
    e = _err(lambda: summ(fake))
    assert e.code == "QUERY_TOO_LARGE" and e.status == 422
    assert fake.calls.get("deal_ids") == 1
    assert fake.calls.get("fills", 0) == 0
    assert fake.calls.get("position_legs", 0) == 0


def test_deal_cap_at_exactly_the_limit_passes(monkeypatch):
    n = len(FakeSource(FILLS).deal_ids([A_USD, B_CEN], T("2026-09-01"), T("2026-09-29")))
    _set_env(monkeypatch, EXEC_COMP_MAX_DEALS=n)
    assert summ(FakeSource(FILLS)).data.deals == 5


def test_time_budget_exhausted_is_query_too_large(monkeypatch):
    _set_env(monkeypatch, EXEC_COMP_QUERY_BUDGET_S=0)
    fake = FakeSource(FILLS)
    e = _err(lambda: summ(fake))
    assert e.code == "QUERY_TOO_LARGE" and e.status == 422
    assert fake.calls.get("fills", 0) == 0


def test_all_slots_busy_is_503_busy(monkeypatch, tmp_path):
    from app.services.exec_comp.limits import QuerySlot

    slot_dir = str(tmp_path / "busy-slots")
    _set_env(monkeypatch, EXEC_COMP_SLOT_DIR=slot_dir, EXEC_COMP_MAX_CONCURRENT=2)
    fake = FakeSource(FILLS)
    with QuerySlot(slot_dir, 2), QuerySlot(slot_dir, 2):
        e = _err(lambda: summ(fake))
    assert e.code == "BUSY" and e.status == 503
    assert fake.calls.get("deal_ids", 0) == 0
    # slots released -> the same query runs
    assert summ(FakeSource(FILLS)).data.deals == 5


def test_one_free_slot_is_enough(monkeypatch, tmp_path):
    from app.services.exec_comp.limits import QuerySlot

    slot_dir = str(tmp_path / "one-free")
    _set_env(monkeypatch, EXEC_COMP_SLOT_DIR=slot_dir, EXEC_COMP_MAX_CONCURRENT=2)
    with QuerySlot(slot_dir, 2):
        assert summ(FakeSource(FILLS)).data.deals == 5


class _TimingOutSource(FakeSource):
    def fills(self, deal_ids):
        raise ExecCompError("UPSTREAM_TIMEOUT", "replica statement killed", status=504)


def test_upstream_timeout_propagates_as_504():
    e = _err(lambda: summ(_TimingOutSource(FILLS)))
    assert e.code == "UPSTREAM_TIMEOUT" and e.status == 504


@pytest.mark.parametrize("errno", [3024, 2013])
def test_replica_source_maps_statement_kill_to_upstream_timeout(errno):
    """source.py turns MAX_EXECUTION_TIME kills / lost connections into 504
    UPSTREAM_TIMEOUT. No connection is opened: the cursor seam is faked."""
    import pymysql

    from app.core.config import get_settings
    from app.services.exec_comp.source import ReplicaFillSource

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, args):
            raise pymysql.err.OperationalError(errno, "Query execution was interrupted")

    rs = ReplicaFillSource(get_settings())
    rs._cursor = lambda: _Cur()  # type: ignore[method-assign]
    with pytest.raises(ExecCompError) as ei:
        rs.fills([1, 2, 3])
    assert ei.value.code == "UPSTREAM_TIMEOUT" and ei.value.status == 504


# ---------------------------------------------------------------------------
# cache (01 D21: keyed by calc_version + subject + dates + as_of)
# ---------------------------------------------------------------------------


def test_cache_hit_is_flagged_and_otherwise_identical():
    cache = FakeRedis()
    first = summ(FakeSource(FILLS), cache=cache)
    assert first.statistics.from_cache is False
    assert cache.store, "a complete result must be cached"
    assert all(t == 3600 for t in cache.ttl.values())
    fake2 = FakeSource(FILLS)
    second = summ(fake2, cache=cache)
    assert second.statistics.from_cache is True
    assert fake2.calls.get("deal_ids", 0) == 0          # served without the replica
    assert second.data.model_dump() == first.data.model_dump()
    assert second.coverage.model_dump() == first.coverage.model_dump()
    assert second.as_of == first.as_of

    o1 = orders(FakeSource(FILLS), view="all", page_size=500)
    o2 = orders(FakeSource(FILLS), cache=cache, view="all", page_size=500)
    assert o2.statistics.from_cache is True
    assert [r.model_dump() for r in o2.data] == [r.model_dump() for r in o1.data]


def test_cache_key_carries_calc_version_subject_dates_and_as_of():
    cache = FakeRedis()
    summ(FakeSource(FILLS), q(as_of="2026-09-27"), cache=cache)
    keys = list(cache.store)
    assert keys and all(f"v{CALC_VERSION}" in k for k in keys)
    assert all(f"client-{CLIENT}" in k and "2026-09-01" in k and "2026-09-27" in k for k in keys)
    # a different as_of misses
    fake = FakeSource(FILLS)
    assert summ(fake, q(as_of="2026-09-26"), cache=cache).statistics.from_cache is False
    assert fake.calls.get("deal_ids") == 1


def test_incomplete_results_are_not_cached():
    cache = FakeRedis()
    lagging = FakeSource(FILLS, head=T("2026-09-28T23:10:00"))
    r = summ(lagging, q(as_of="2026-09-28"), cache=cache)
    assert r.coverage.complete is False
    assert r.coverage.reason
    assert cache.store == {}


def test_errors_are_not_cached(monkeypatch):
    cache = FakeRedis()
    _set_env(monkeypatch, EXEC_COMP_MAX_DEALS=3)
    _err(lambda: summ(FakeSource(FILLS), cache=cache))
    assert cache.store == {}


def test_broken_cache_falls_back_to_compute():
    class Boom:
        def get(self, key):
            raise ConnectionError("redis down")

        def setex(self, *a):
            raise ConnectionError("redis down")

    r = summ(FakeSource(FILLS), cache=Boom())
    assert r.data.deals == 5 and r.statistics.from_cache is False


def test_oversized_rows_are_not_cached_but_result_is_correct(monkeypatch):
    from app.core.config import get_settings

    if not hasattr(get_settings(), "EXEC_COMP_CACHE_MAX_BYTES"):
        pytest.skip("EXEC_COMP_CACHE_MAX_BYTES not implemented yet")
    _set_env(monkeypatch, EXEC_COMP_CACHE_MAX_BYTES=16)
    cache = FakeRedis()
    o = orders(FakeSource(FILLS), cache=cache, view="counted", page_size=500)
    assert {r.deal_id for r in o.data} == set(COUNTED)
    assert not any(k.endswith(":rows") for k in cache.store)
    # a second rows-reader recomputes rather than reading a truncated blob
    fake = FakeSource(FILLS)
    o2 = orders(fake, cache=cache, view="counted", page_size=500)
    assert [r.model_dump() for r in o2.data] == [r.model_dump() for r in o.data]
    assert o2.statistics.from_cache is False
    assert fake.calls.get("fills") == 1


def test_unmatched_fill_is_absent_from_rows_and_totals(src):
    all_rows = orders(src, view="all", page_size=500)
    assert 115 not in {r.deal_id for r in all_rows.data}
    s = summ(src)
    assert s.coverage.unmatched_deals == 1
    class_deals = sum(c.deals for c in s.data.not_counted_by_class)
    # 2 = the market rows of anomalous P5 (109, 110); every row is in exactly one bucket
    assert s.data.deals + s.data.excluded_open.deals + class_deals + 2 == all_rows.total


def test_page_size_above_the_cap_is_rejected(src):
    e = _err(lambda: orders(src, page_size=1001))
    assert e.code == "VALIDATION_ERROR"
    assert _err(lambda: orders(src, page=0)).code == "VALIDATION_ERROR"
    assert _err(lambda: orders(src, view="everything")).code == "VALIDATION_ERROR"


def test_error_statuses_match_the_contract_map(monkeypatch, src):
    """Every ExecCompError raised by the core carries the status the public
    contract (schemas.ERROR_STATUS) promises for its code."""
    from app.schemas.exec_compensation import ERROR_STATUS

    cases = [
        lambda: summ(src, q(client_id=None)),
        lambda: summ(src, q(client_id=None, login_sid="x")),
        lambda: summ(src, q(client_id=None, login_sid="5-99999999")),
        lambda: summ(src, q(date_from="2026-09-20", date_to="2026-09-10")),
        lambda: summ(src, q(date_from="2023-01-01", date_to="2023-03-01")),
        lambda: summ(src, q(as_of="2026-09-29")),
        lambda: orders(src, sort_by="nope"),
        lambda: summ(_TimingOutSource(FILLS)),
    ]
    for case in cases:
        e = _err(case)
        assert e.status == ERROR_STATUS[e.code], (e.code, e.status)
