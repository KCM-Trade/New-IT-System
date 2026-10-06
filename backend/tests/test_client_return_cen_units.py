"""Anti-drift guards for CEN (US-cent) unit conversion in Client Return Rate SQL.

Two kinds of source feed the money columns, and they need opposite handling:

* RAW-CENTS tables — ``mt4_users`` (EQUITY), ``stats_transactions``,
  ``stats_balances``, ``mt4_trades``: a CEN account's amounts are cents and every
  aggregate must be ``SUM(IF(currency = 'CEN', col / 100.0, col))``.
* The CRM's trading pre-aggregates — ``stats_trading`` and
  ``stats_trading_running_totals``: ALREADY in dollars for CEN accounts. They
  must NOT be divided again.

History, because this file used to assert the opposite for ``profit_hist``:
the leg summed the running totals raw until 2026-08-28, when a /100 was added
on the belief that the table was in account currency (sample: client 128535,
CEN legs summing to -758.57, read as cents). On 2026-10-06 that client's CEN
accounts were reconciled against ``mt4_trades`` order by order: the raw trades
sum to -75,857 cents, so -758.57 is the DOLLAR figure and the division made
cent legs 100x too small. The same check on 300 sampled CEN accounts and on
the whole September universe is recorded in app/services/stats_trading_units.py.

These tests assert on generated SQL text rather than hitting MySQL — SQL text is
where this class of drift happens.
"""

import re

from app.services.client_return_service import _build_phase2_sql

_RAW_SQL = _build_phase2_sql("1,2,3", "2026-07-01", "2026-07-15")

# Strip `--` comments: the fix ships explanatory prose that mentions CEN and /100,
# and that prose must not be able to satisfy these assertions. Pin executable SQL.
_SQL = re.sub(r"--[^\n]*", "", _RAW_SQL)

# Every money aggregate in Phase 2 that reads a RAW-CENTS fxbackoffice table,
# mapped to the raw column it must normalize. profit_hist_trades is deliberately
# absent: its source is already in dollars (see the class below).
_MONEY_LEGS = {
    "equity": "EQUITY",
    "deposits_hist": "st.amount",
    "withdrawals_hist": "st.amount",
    "ib_withdrawal_hist": "st.amount",
    "deposits_month": "st.amount",
    "withdrawals_month": "st.amount",
    "ib_withdrawal_month": "st.amount",
    "deposits_90d": "st.amount",
}


def _leg(alias: str) -> str:
    """Extract the SUM(...) aggregate expression assigned to a given SQL alias.

    Mirrors the helper in test_client_return_trading_net_deposit.py: an alias can
    appear both on the outer SELECT and as the aggregate inside a LEFT JOIN
    subquery, and we want the aggregate.
    """
    for m in re.finditer(rf"\) AS {re.escape(alias)}\b", _SQL):
        end = m.start()
        start = _SQL.rfind("SUM(", 0, end)
        if start == -1:
            continue
        span = _SQL[start + len("SUM(") : end]
        if " AS " not in span:
            return span
    raise AssertionError(f"no SUM(...) aggregate found for alias {alias!r}")


class TestStatsTradingLegsAreNotDividedAgain:
    """stats_trading and stats_trading_running_totals are ALREADY in dollars for
    CEN accounts (app/services/stats_trading_units.py has the measurements), so
    the two legs that read them must not carry the CEN /100 every raw-cents leg
    needs. History: profit_hist summed the running totals raw until 2026-08-28,
    when a /100 was added on the belief that the table was in account currency;
    2026-10-06 trade-level reconciliation showed that belief was wrong and the
    division understated cent legs 100x."""

    def test_profit_hist_leg_has_no_cen_division(self):
        leg = _leg("profit_hist_trades")
        assert "CEN" not in leg and "/ 100" not in leg and "/100" not in leg, leg
        assert "plClosedHavingActivityRunningTotal" in leg

    def test_profit_hist_corrects_the_group_the_crm_leaves_in_cents(self):
        leg = _leg("profit_hist_trades")
        assert "- 0.99 * COALESCE(ud.undivided_pl, 0)" in leg
        assert "sd.date >= '2026-04-24'" in _SQL
        assert "sd.loginSid IN (SELECT ug.loginSid FROM fxbackoffice.mt4_users ug WHERE ug.`GROUP` IN (" in _SQL

    def test_period_profit_fast_path_has_no_cen_division(self):
        from app.services import client_return_service as crs

        for sql in (crs.SQL_PHASE1_STATS, crs.SQL_PHASE1_STATS_SEARCH):
            assert "__STATS_MONEY_DIV__" not in sql
            assert "'CEN'" not in sql, "stats_trading is already in dollars for CEN accounts"
            assert "SUM(st.totalPlClosed / IF(st.date >= '2026-04-24' AND st.loginSid IN (SELECT" in sql

    def test_period_profit_fallback_still_divides_raw_cents(self):
        """mt4_trades IS raw cents — that leg keeps its single division."""
        from app.services import client_return_service as crs

        for sql in (crs.SQL_PHASE1_TRADES, crs.SQL_PHASE1_TRADES_SEARCH):
            assert "IF(mu.CURRENCY = 'CEN', t.totalProfit / 100.0, t.totalProfit)" in sql


class TestEveryMoneyLegNormalizesCen:
    """Blanket guard so the next money column added here can't skip the rule."""

    def test_all_money_aggregates_branch_on_cen(self):
        missing = [alias for alias in _MONEY_LEGS if "CEN" not in _leg(alias)]
        assert not missing, (
            f"money aggregates with no CEN branch: {missing}. Every SUM over a "
            f"currency-tagged fxbackoffice table must be "
            f"SUM(IF(currency = 'CEN', col / 100.0, col)) — see CLAUDE.md."
        )

    def test_all_money_aggregates_divide_by_100(self):
        missing = [
            alias
            for alias in _MONEY_LEGS
            if "/ 100.0" not in _leg(alias) and "/100.0" not in _leg(alias)
        ]
        assert not missing, f"money aggregates with a CEN branch but no /100: {missing}"

    def test_each_money_leg_divides_its_own_raw_column(self):
        wrong = []
        for alias, column in _MONEY_LEGS.items():
            leg = _leg(alias)
            if not re.search(rf"{re.escape(column)}\s*/\s*100\.0", leg):
                wrong.append((alias, column))
        assert not wrong, f"the /100 is applied to the wrong operand for: {wrong}"
