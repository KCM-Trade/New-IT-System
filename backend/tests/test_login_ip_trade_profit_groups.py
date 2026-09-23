"""OPT-0063 Phase 2 — grouping (union-find over shared private IPs).

Rows are written straight into a tmp trade_ip_pnl; no MySQL anywhere.

What is pinned:
- the edge rule: >= 2 shared IP-days OR >= 2 distinct shared private IPs —
  a single co-occurrence must NOT connect (over a 90-day window one reassigned
  home broadband would merge unrelated households);
- shared exits (>= public_ip_clients distinct clients) never connect;
- union-find transitivity merges A-B and B-C into one component without
  leaking into an unrelated pair;
- the client-count filter and the 一人多户 (same-client) switch;
- group_id is stable under the same parameters and changes with the window.
"""

from __future__ import annotations

import pytest

from app.services import login_ip_trade_profit_service as svc
from app.services.login_ip_trade_profit_service import GroupParams


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from app.core import login_ip_orders_db

    monkeypatch.setattr(login_ip_orders_db, "_DB_PATH", tmp_path / "login_ip_orders.db")
    login_ip_orders_db.init_login_ip_orders_db()
    # The cache would leak groups between tests; keep every test uncached.
    monkeypatch.setattr(svc, "_get_redis", lambda: None)
    return login_ip_orders_db


def _pnl(server, account, ip, close_date, user_id, ib_id=None, profit=10.0,
         deal_ref=None, symbol="XAUUSD"):
    return (
        server,
        deal_ref or f"{server}-{account}-{close_date}-{ip}",
        close_date,
        account,
        str(account),          # position_ref
        ip,                    # open_ip
        None,                  # close_ip
        user_id,
        ib_id if ib_id is not None else user_id + 9000,
        symbol,
        1.0,                   # lots
        profit,
        3600,                  # hold_sec
        close_date,            # open_date (same day; irrelevant to grouping)
        0,                     # reason
        None,                  # no_ip_cause
    )


def _params(**kw):
    kw.setdefault("date_from", "2026-09-15")
    kw.setdefault("date_to", "2026-09-21")
    return GroupParams(**kw)


def _groups(db):
    return svc.compute_groups(_params())


# ---------------------------------------------------------------------------
# the edge rule
# ---------------------------------------------------------------------------


def test_single_shared_ip_day_does_not_connect(db):
    db.replace_trade_ip_pnl_for_date("2026-09-15", [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-15", user_id=101),
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-15", user_id=102),
    ])
    assert _groups(db) == []


def test_two_shared_ip_days_connect(db):
    db.replace_trade_ip_pnl_for_date("2026-09-15", [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-15", user_id=101),
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-15", user_id=102),
    ])
    db.replace_trade_ip_pnl_for_date("2026-09-16", [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-16", user_id=101),
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-16", user_id=102),
    ])
    groups = _groups(db)
    assert len(groups) == 1
    g = groups[0]
    assert g["accounts"] == 2
    assert g["clients"] == 2
    assert g["shared_ips"] == 1
    assert g["active_days"] == 2
    assert g["profit_usd"] == 40.0
    assert g["member_ips"][0]["ip"] == "1.1.1.1"
    assert g["member_ips"][0]["bridge"] is True


def test_two_distinct_ips_cross_day_connect(db):
    """A uses ip1 Mon, B uses ip1 Tue (never same day); both also touch ip2
    on different days. Zero shared IP-days, two shared private IPs -> edge."""
    db.replace_trade_ip_pnl_for_date("2026-09-15", [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-15", user_id=101),
        _pnl("MT5", 1001, "2.2.2.2", "2026-09-15", user_id=101, deal_ref="a2"),
    ])
    db.replace_trade_ip_pnl_for_date("2026-09-16", [
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-16", user_id=102),
        _pnl("MT5", 1002, "2.2.2.2", "2026-09-16", user_id=102, deal_ref="b2"),
    ])
    groups = _groups(db)
    assert len(groups) == 1
    assert groups[0]["shared_ips"] == 2


def test_shared_exit_never_connects(db):
    """An IP with >= public_ip_clients distinct clients is a carrier NAT:
    two accounts sharing it on many days still form no edge."""
    rows = []
    # 10 distinct clients on the same IP -> public
    for i in range(10):
        rows.append(_pnl("MT5", 2000 + i, "9.9.9.9", "2026-09-15", user_id=300 + i))
        rows.append(_pnl("MT5", 2000 + i, "9.9.9.9", "2026-09-16", user_id=300 + i,
                         deal_ref=f"x{i}"))
    db.replace_trade_ip_pnl_for_date("2026-09-15", [r for r in rows if r[2] == "2026-09-15"])
    db.replace_trade_ip_pnl_for_date("2026-09-16", [r for r in rows if r[2] == "2026-09-16"])
    assert _groups(db) == []


def test_transitivity_merges_without_leaking(db):
    """A-B and B-C each share 2 IP-days -> one group of three; the unrelated
    D-E pair forms its own group, not part of the first."""
    rows = [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-15", user_id=101),
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-15", user_id=102, deal_ref="a"),
        _pnl("MT5", 1002, "2.2.2.2", "2026-09-16", user_id=102, deal_ref="b"),
        _pnl("MT5", 1003, "2.2.2.2", "2026-09-16", user_id=103, deal_ref="c"),
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-16", user_id=101, deal_ref="d"),
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-16", user_id=102, deal_ref="e"),
        _pnl("MT5", 1002, "2.2.2.2", "2026-09-17", user_id=102, deal_ref="f"),
        _pnl("MT5", 1003, "2.2.2.2", "2026-09-17", user_id=103, deal_ref="g"),
        # unrelated pair
        _pnl("MT4", 8001, "5.5.5.5", "2026-09-15", user_id=105),
        _pnl("MT4", 8002, "5.5.5.5", "2026-09-15", user_id=106, deal_ref="h"),
        _pnl("MT4", 8001, "5.5.5.5", "2026-09-16", user_id=105, deal_ref="i"),
        _pnl("MT4", 8002, "5.5.5.5", "2026-09-16", user_id=106, deal_ref="j"),
    ]
    by_date = {}
    for r in rows:
        by_date.setdefault(r[2], []).append(r)
    for day, day_rows in by_date.items():
        db.replace_trade_ip_pnl_for_date(day, day_rows)

    groups = _groups(db)
    assert len(groups) == 2
    sizes = sorted(g["accounts"] for g in groups)
    assert sizes == [2, 3]
    big = next(g for g in groups if g["accounts"] == 3)
    assert big["clients"] == 3
    # A shares 2 IP-days with B on 1.1.1.1; B-C share 2.2.2.2 twice.
    assert big["shared_ips"] == 2


# ---------------------------------------------------------------------------
# filters
# ---------------------------------------------------------------------------


def _same_client_rows():
    return [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-15", user_id=101),
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-15", user_id=101, deal_ref="a"),
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-16", user_id=101, deal_ref="b"),
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-16", user_id=101, deal_ref="c"),
    ]


def test_same_client_group_hidden_by_default(db):
    db.replace_trade_ip_pnl_for_date("2026-09-15", _same_client_rows()[:2])
    db.replace_trade_ip_pnl_for_date("2026-09-16", _same_client_rows()[2:])
    assert _groups(db) == []


def test_same_client_group_behind_the_switch(db):
    db.replace_trade_ip_pnl_for_date("2026-09-15", _same_client_rows()[:2])
    db.replace_trade_ip_pnl_for_date("2026-09-16", _same_client_rows()[2:])
    groups = svc.compute_groups(_params(include_same_client=True))
    assert len(groups) == 1
    assert groups[0]["same_client"] is True
    assert groups[0]["clients"] == 1
    assert groups[0]["accounts"] == 2


def test_min_clients_filter(db):
    db.replace_trade_ip_pnl_for_date("2026-09-15", [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-15", user_id=101),
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-15", user_id=102, deal_ref="a"),
    ])
    db.replace_trade_ip_pnl_for_date("2026-09-16", [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-16", user_id=101, deal_ref="b"),
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-16", user_id=102, deal_ref="c"),
    ])
    assert svc.compute_groups(_params(min_clients=3)) == []
    assert len(svc.compute_groups(_params(min_clients=2))) == 1


def _five_clients_one_day(ip="1.1.1.1", n=5, user_base=101, account_base=1001):
    """n distinct clients, one IP, one close day — a single co-occurrence."""
    return [
        _pnl("MT5", account_base + i, ip, "2026-09-15", user_id=user_base + i,
             deal_ref=f"{ip}-{i}")
        for i in range(n)
    ]


def test_five_clients_on_one_ip_one_day_connects_under_page_rule(db):
    """The page rule: one IP used by >= 5 distinct clients is a group even
    when they only shared it on a single day. public_ip_clients stays at the
    cap so the IP is not dropped as a shared exit first."""
    db.replace_trade_ip_pnl_for_date("2026-09-15", _five_clients_one_day())
    # Legacy edges ignore a single co-occurrence.
    assert svc.compute_groups(_params()) == []
    groups = svc.compute_groups(_params(
        min_clients=5, public_ip_clients=1000, ip_min_clients=5,
    ))
    assert len(groups) == 1
    assert groups[0]["clients"] == 5
    assert groups[0]["accounts"] == 5
    assert groups[0]["same_client"] is False
    assert groups[0]["member_ips"][0]["bridge"] is True


def test_four_clients_on_one_ip_does_not_qualify(db):
    db.replace_trade_ip_pnl_for_date("2026-09-15", _five_clients_one_day(n=4))
    assert svc.compute_groups(_params(
        min_clients=2, public_ip_clients=1000, ip_min_clients=5,
    )) == []


def test_one_client_many_accounts_stays_hidden_under_page_rule(db):
    """Five accounts of one CRM client are not five people."""
    rows = [
        _pnl("MT5", 1001 + i, "1.1.1.1", "2026-09-15", user_id=101,
             deal_ref=f"s{i}")
        for i in range(5)
    ]
    db.replace_trade_ip_pnl_for_date("2026-09-15", rows)
    assert svc.compute_groups(_params(
        public_ip_clients=1000, ip_min_clients=5, include_same_client=True,
    )) == []


def test_page_rule_ignores_legacy_two_person_edges(db):
    """Two clients sharing an IP on two days still form a group under the
    legacy rule, but not when ip_min_clients=5 — that IP never had 5 people."""
    db.replace_trade_ip_pnl_for_date("2026-09-15", [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-15", user_id=101),
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-15", user_id=102, deal_ref="a"),
    ])
    db.replace_trade_ip_pnl_for_date("2026-09-16", [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-16", user_id=101, deal_ref="b"),
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-16", user_id=102, deal_ref="c"),
    ])
    assert len(svc.compute_groups(_params())) == 1
    assert svc.compute_groups(_params(
        public_ip_clients=1000, ip_min_clients=5,
    )) == []


def test_busy_ip_is_kept_when_public_cap_is_raised(db):
    """15 clients on one IP, one day. Default public_ip_clients=10 drops it;
    the page sends 1000 so it counts."""
    db.replace_trade_ip_pnl_for_date(
        "2026-09-15", _five_clients_one_day(n=15),
    )
    assert svc.compute_groups(_params(ip_min_clients=5, public_ip_clients=10)) == []
    groups = svc.compute_groups(_params(
        min_clients=5, public_ip_clients=1000, ip_min_clients=5,
    ))
    assert len(groups) == 1
    assert groups[0]["clients"] == 15


# ---------------------------------------------------------------------------
# group_id + detail
# ---------------------------------------------------------------------------


def test_group_id_stable_and_window_sensitive(db):
    db.replace_trade_ip_pnl_for_date("2026-09-15", [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-15", user_id=101),
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-15", user_id=102, deal_ref="a"),
    ])
    db.replace_trade_ip_pnl_for_date("2026-09-16", [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-16", user_id=101, deal_ref="b"),
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-16", user_id=102, deal_ref="c"),
    ])
    g1 = svc.compute_groups(_params())[0]["group_id"]
    g2 = svc.compute_groups(_params())[0]["group_id"]
    g3 = svc.compute_groups(_params(date_to="2026-09-20"))[0]["group_id"]
    assert g1 == g2
    assert g1 != g3


def test_group_detail_enriches_ips(db, monkeypatch):
    db.replace_trade_ip_pnl_for_date("2026-09-15", [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-15", user_id=101),
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-15", user_id=102, deal_ref="a"),
    ])
    db.replace_trade_ip_pnl_for_date("2026-09-16", [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-16", user_id=101, deal_ref="b"),
        _pnl("MT5", 1002, "1.1.1.1", "2026-09-16", user_id=102, deal_ref="c"),
    ])
    from app.core import login_ip_db

    monkeypatch.setattr(
        login_ip_db, "get_cached_countries", lambda ips: {"1.1.1.1": "Hong Kong"}
    )
    gid = svc.compute_groups(_params())[0]["group_id"]
    detail = svc.get_group_detail(gid, _params())
    assert detail is not None
    assert detail["member_ips"][0]["country"] == "Hong Kong"
    assert detail["member_ips"][0]["window_clients"] == 2
    assert detail["group"]["accounts_detail"][0]["account_key"].startswith("MT5-")
    assert svc.get_group_detail("000000000000", _params()) is None


# ---------------------------------------------------------------------------
# ip ranking + coverage
# ---------------------------------------------------------------------------


def test_ip_ranking_sorted_and_flagged(db):
    db.replace_trade_ip_pnl_for_date("2026-09-15", [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-15", user_id=101, profit=50.0),
        _pnl("MT5", 1002, "2.2.2.2", "2026-09-15", user_id=102, profit=-20.0),
        _pnl("MT5", 1003, "2.2.2.2", "2026-09-15", user_id=103, profit=5.0, deal_ref="z"),
    ])
    rows = svc.compute_ip_ranking("2026-09-15", "2026-09-21")
    assert [r["ip"] for r in rows] == ["1.1.1.1", "2.2.2.2"]
    assert rows[1]["profit_usd"] == -15.0
    assert rows[1]["clients"] == 2
    assert rows[1]["shared_exit"] is False


def test_coverage_splits_no_ip_by_cause(db):
    db.replace_trade_ip_pnl_for_date("2026-09-15", [
        _pnl("MT5", 1001, "1.1.1.1", "2026-09-15", user_id=101, profit=50.0),
        # no-IP rows: open_ip NULL + a cause
        ("MT5", "n1", "2026-09-15", 1004, "1004", None, None, 104, 9104, "XAUUSD",
         1.0, -7.5, 60, "2026-09-10", 0, "pre_golive"),
        ("MT5", "n2", "2026-09-15", 1005, "1005", None, None, 105, 9105, "XAUUSD",
         1.0, 2.5, 60, "2026-09-15", 2, "server_initiated"),
    ])
    cov = svc.get_coverage("2026-09-15", "2026-09-21")
    assert cov["total_trades"] == 3
    assert cov["with_ip_trades"] == 1
    assert cov["no_ip_trades"] == 2
    causes = {c["cause"]: c["trades"] for c in cov["no_ip_by_cause"]}
    assert causes == {"pre_golive": 1, "server_initiated": 1}
    # No parse runs exist in this fixture DB -> every expected (day, server)
    # inside the window is listed as incomplete.
    assert {"date": "2026-09-15", "server": "MT5"} in [
        {"date": i["date"], "server": i["server"]} for i in cov["incomplete_logs"]
    ]
    # 2026-09-16 has no reconciled rows at all.
    assert "2026-09-16" in cov["unreconciled_dates"]
