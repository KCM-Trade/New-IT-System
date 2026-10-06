"""rank_accounts_service (OPT-0065 item 4) — SQL text and row normalisation,
no database. What is pinned:

  * the statement carries the certified account universe verbatim: demo/test
    GROUP+NAME exclusion, isEmployee, sid list, CMD 0/1, isDeleted, closeDate
    BETWEEN — the same filters trade_activity_service uses per client;
  * the cent rule is in SQL: money ÷100 for CEN accounts OR cent symbols,
    lots ÷100 for cent SYMBOLS only, and `XAUUSD.c` does not match;
  * min_orders is a HAVING, the metric is an ORDER BY alias, LIMIT is bound;
  * return_pct is never ranked and is None on every row.
"""

from __future__ import annotations

import pytest

from app.services import rank_accounts_service as ras


class _Cur:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def execute(self, sql, params):
        self.executed.append((sql, list(params)))

    def fetchall(self):
        return list(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, rows):
        self.cur = _Cur(rows)
        self.closed = False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


def test_sql_carries_the_certified_universe_filters():
    sql = ras.build_rank_sql("win_rate", "desc", (1, 5, 6))
    for needle in (
        "LOWER(mu.`GROUP`) NOT LIKE '%%demo%%'",
        "LOWER(mu.NAME)    NOT LIKE '%%test%%'",
        "COALESCE(u.isEmployee, 0) = 0",
        "t.sid IN (%s, %s, %s)",
        "t.CMD IN (0, 1)",
        "(t.isDeleted = 0 OR t.isDeleted IS NULL)",
        "t.closeDate BETWEEN %s AND %s",
        "HAVING orders >= %s",
        "LIMIT %s",
    ):
        assert needle in sql, needle


def test_trades_are_aggregated_before_the_account_join():
    """Joining mt4_users/users per ORDER doubled the run time (2026-10-06,
    14 days 19.6s vs 10.6s). The joins must sit outside the mt4_trades scan."""
    sql = ras.build_rank_sql("net_profit", "desc", (1, 5, 6))
    inner_start = sql.index("FROM fxbackoffice.mt4_trades t")
    inner_end = sql.index(") a")
    first_join = sql.index("JOIN fxbackoffice.mt4_users mu")
    assert inner_start < inner_end < first_join
    assert "JOIN" not in sql[inner_start:inner_end]
    # every universe filter on mt4_trades is inside the scan, not after it
    for needle in ("t.closeDate BETWEEN %s AND %s", "t.sid IN (%s, %s, %s)", "t.CMD IN (0, 1)"):
        assert inner_start < sql.index(needle) < inner_end, needle


def test_cent_rule_is_applied_in_sql_to_money_and_lots_separately():
    sql = ras.build_rank_sql("net_profit", "desc", (1,))
    # money: CEN account OR cent symbol
    assert "SUM(a.sum_total_profit / IF(UPPER(mu.CURRENCY) = 'CEN' OR a.cent_sym, 100, 1)) AS net_profit" in sql
    assert "SUM(a.sum_profit / IF(UPPER(mu.CURRENCY) = 'CEN' OR a.cent_sym, 100, 1)) AS gross_profit" in sql
    # lots: cent SYMBOL only
    assert "SUM(a.sum_lots / IF(a.cent_sym, 100, 1)) AS lots" in sql
    # the flag both divisors use is the cent-SYMBOL rule, and it is part of the
    # inner GROUP BY so cent and non-cent orders are never summed together
    assert "(LOWER(t.SYMBOL) LIKE '%%.cent' OR LOWER(t.SYMBOL) LIKE '%%.kcmc') AS cent_sym" in sql
    assert "GROUP BY t.loginSid, t.sid, cent_sym" in sql
    # '.c' alone must never match — the pattern requires the full suffix
    assert "LIKE '%%.c'" not in sql


@pytest.mark.parametrize("metric,expr", [("win_rate", "win_rate"), ("net_profit", "net_profit"), ("lots", "lots"), ("orders", "orders")])
def test_order_by_uses_the_metric_alias_then_orders_then_login(metric, expr):
    sql = ras.build_rank_sql(metric, "asc", (1, 5, 6))
    assert f"ORDER BY {expr} ASC, orders DESC, login_sid" in sql


def test_return_pct_is_not_rankable():
    with pytest.raises(AssertionError):
        ras.build_rank_sql("return_pct", "desc", (1,))
    with pytest.raises(ValueError):
        ras.rank(None, metric="return_pct", order="desc", day_from="2026-09-01", day_to="2026-09-07",
                 min_orders=20, sids=None, limit=10, connect=lambda s: _Conn([]))


def test_rank_binds_params_in_order_and_closes_the_connection():
    conn = _Conn([])
    ras.rank(None, metric="win_rate", order="desc", day_from="2026-09-21", day_to="2026-09-27",
             min_orders=20, sids=None, limit=30, connect=lambda s: conn)
    sql, params = conn.cur.executed[0]
    assert params == ["2026-09-21", "2026-09-27", 1, 5, 6, 20, 30]
    assert conn.closed is True


def test_rank_caps_the_fetch_limit():
    conn = _Conn([])
    ras.rank(None, metric="orders", order="desc", day_from="2026-09-21", day_to="2026-09-27",
             min_orders=20, sids=[5], limit=99_999, connect=lambda s: conn)
    _, params = conn.cur.executed[0]
    assert params[-1] == ras.MAX_FETCH_ROWS
    assert params[2:3] == [5]


def test_normalise_row_types_rounds_and_picks_metric_value():
    raw = {"login_sid": "1-100", "sid": 1, "client_id": "555", "cid": "0", "cent_account": 1, "cent_symbol": 0,
           "orders": 40, "wins": 25, "lots": "12.3456", "net_profit": "1234.567", "gross_profit": "1300.001"}
    row = ras.normalise_row(raw, "win_rate")
    assert row["client_id"] == 555 and row["cid"] == 0 and row["is_cent"] is True
    assert row["win_rate"] == 0.625 and row["metric_value"] == 0.625
    assert row["lots"] == 12.346 and row["net_profit"] == 1234.57
    assert row["return_pct"] is None


def test_normalise_row_handles_missing_cid_and_zero_orders():
    row = ras.normalise_row({"login_sid": "5-1", "sid": 5, "client_id": None, "cid": None, "orders": 0, "wins": 0}, "orders")
    assert row["cid"] is None and row["client_id"] is None
    assert row["win_rate"] == 0.0 and row["metric_value"] == 0
