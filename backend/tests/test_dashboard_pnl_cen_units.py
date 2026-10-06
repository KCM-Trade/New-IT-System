"""The dashboard PnL legs read stats_trading, which is ALREADY in dollars for CEN
accounts (app/services/stats_trading_units.py). Until 2026-10-06 all three
divided CEN rows by 100 again, showing the cent accounts' share of company PnL
100x too small. Pin the SQL text so the division does not come back.
"""

import pytest

from app.services import dashboard_pnl_group_service, dashboard_pnl_history_service, dashboard_pnl_service


def _sql_constants(module):
    return [v for k, v in vars(module).items() if k.isupper() and isinstance(v, str) and "stats_trading st" in v]


@pytest.mark.parametrize("module", [dashboard_pnl_service, dashboard_pnl_group_service, dashboard_pnl_history_service])
def test_pnl_leg_does_not_divide_cent_accounts_again(module):
    sqls = _sql_constants(module)
    assert sqls, "no statement reads stats_trading any more — update this guard"
    for sql in sqls:
        assert "__STATS_MONEY_DIV__" not in sql
        assert "st.currency = 'CEN'" not in sql, "stats_trading is already in dollars for CEN accounts"
        # one division remains: the group the CRM leaves in cents, from the cut-over date
        assert "SUM(st.totalPlClosed / IF(st.date >= '2026-04-24' AND mu.`GROUP` IN ('KCMC" in sql
        # the fragment must not introduce a bare percent into a parameterised statement
        assert "%" not in sql.split("SUM(st.totalPlClosed / ")[1].split(") AS pl_usd")[0]
