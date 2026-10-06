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
    sql = ras.build_rank_sql("win_rate", "desc", (1, 5, 6))
    inner_start = sql.index("FROM fxbackoffice.mt4_trades t")
    inner_end = sql.index(") a")
    first_join = sql.index("JOIN fxbackoffice.mt4_users mu")
    assert inner_start < inner_end < first_join
    assert "JOIN" not in sql[inner_start:inner_end]
    # every universe filter on mt4_trades is inside the scan, not after it
    for needle in ("t.closeDate BETWEEN %s AND %s", "t.sid IN (%s, %s, %s)", "t.CMD IN (0, 1)"):
        assert inner_start < sql.index(needle) < inner_end, needle


def test_cent_rule_is_applied_in_sql_to_money_and_lots_separately():
    sql = ras.build_rank_sql("win_rate", "desc", (1,))
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


# ── stats_trading path (2026-10-06) ──────────────────────────────────────────

@pytest.mark.parametrize("metric,table", [("win_rate", "mt4_trades"), ("net_profit", "stats_trading"),
                                          ("lots", "stats_trading"), ("orders", "stats_trading"),
                                          ("profit_factor", "stats_trading")])
def test_only_win_rate_reads_the_orders_table(metric, table):
    assert ras.source_table(metric) == table
    sql = ras.build_rank_sql(metric, "desc", (1, 5, 6))
    assert ("FROM fxbackoffice.mt4_trades t" in sql) is (table == "mt4_trades")
    assert ("FROM fxbackoffice.stats_trading st" in sql) is (table == "stats_trading")


def test_stats_sql_carries_the_universe_filters_and_param_order():
    sql = ras.build_rank_sql("net_profit", "desc", (1, 5, 6))
    for needle in (
        "st.date BETWEEN %s AND %s",
        "st.tradeCnt > 0",
        "mu.sid IN (%s, %s, %s)",
        "LOWER(mu.`GROUP`) NOT LIKE '%%demo%%'",
        "LOWER(mu.NAME)    NOT LIKE '%%test%%'",
        "COALESCE(u.isEmployee, 0) = 0",
        "HAVING orders >= %s",
        "LIMIT %s",
    ):
        assert needle in sql, needle
    # Same bind order as the mt4_trades statement: from, to, sids…, min_orders, limit.
    assert sql.index("st.date BETWEEN") < sql.index("mu.sid IN") < sql.index("HAVING orders") < sql.index("LIMIT %s")
    # aggregated before the account join, like the other statement
    assert "JOIN" not in sql[sql.index("FROM fxbackoffice.stats_trading st"):sql.index(") a")]


def test_stats_sql_does_not_divide_cent_accounts_again():
    """stats_trading is ALREADY in dollars for cent accounts (stats_trading_units).
    A `CURRENCY = 'CEN'` divisor here would understate them 100x."""
    sql = ras.build_rank_sql("net_profit", "desc", (1, 5, 6))
    assert "'CEN', 100" not in sql and "'CEN' OR" not in sql
    assert "SUM(a.sum_lots) AS lots" in sql
    # …except the group the CRM leaves in cents, from the cut-over date on.
    assert "IF(a.late = 1 AND mu.`GROUP` IN ('KCMC\\\\5Cent_C40L10'), 100, 1)" in sql
    assert "(st.date >= '2026-04-24') AS late" in sql
    assert "GROUP BY st.loginSid, late" in sql


def test_stats_sql_reports_wins_as_null_not_zero():
    sql = ras.build_rank_sql("orders", "desc", (1,))
    assert "NULL AS wins" in sql and "NULL AS win_rate" in sql
    row = ras.normalise_row({"login_sid": "1-1", "sid": 1, "client_id": 9, "cid": 1, "cent_account": 0,
                             "cent_symbol": 0, "orders": 40, "wins": None, "win_rate": None, "lots": "2",
                             "net_profit": "10", "gross_profit": "12", "profit_factor": "1.23456"}, "net_profit")
    assert row["wins"] is None and row["win_rate"] is None
    assert row["profit_factor"] == 1.2346 and row["metric_value"] == 10.0


def test_profit_factor_ranking_leaves_out_accounts_without_a_loss():
    assert "HAVING orders >= %s AND profit_factor IS NOT NULL" in ras.build_rank_sql("profit_factor", "desc", (1,))
    assert "profit_factor IS NOT NULL" not in ras.build_rank_sql("net_profit", "desc", (1,))
    row = ras.normalise_row({"login_sid": "1-1", "orders": 30, "wins": None, "profit_factor": None}, "profit_factor")
    assert row["profit_factor"] is None and row["metric_value"] is None


def test_profit_factor_is_on_both_statements_with_the_same_definition():
    trades = ras.build_rank_sql("win_rate", "desc", (1,))
    assert "SUM(IF(t.PROFIT > 0, t.PROFIT, 0)) AS sum_pos" in trades
    assert "SUM(IF(t.PROFIT < 0, t.PROFIT, 0)) AS sum_neg" in trades
    for sql in (trades, ras.build_rank_sql("net_profit", "desc", (1,))):
        assert "NULLIF(ABS(SUM(a.sum_neg / " in sql and "AS profit_factor" in sql

