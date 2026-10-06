"""Money units of the CRM's trading pre-aggregates for cent (CEN) accounts.

``fxbackoffice.stats_trading`` (one row per day per account) and
``stats_trading_running_totals`` (one lifetime row per account) are maintained
by the CRM. Unlike every other currency-tagged table we read — ``mt4_trades``,
``mt4_users``, ``stats_balances``, ``stats_transactions`` all hold CEN accounts
in raw cents — these two are ALREADY in dollars: the CRM divides money (and, in
``stats_trading``, lots) by 100 when it builds them. Dividing again understates
a cent account 100x.

Measured on the replica, 2026-10-06:

* September 2026, whole live universe (3,527 accounts with a closed order):
  ``SUM(stats_trading.totalPlClosed)`` equals the trade-level USD figure
  (``mt4_trades.totalProfit`` / 100 for cent) for 3,523 accounts; order counts
  and lots equal for all 3,527.
* 300 randomly sampled CEN accounts: the running total equals lifetime
  ``mt4_trades`` / 100 for 298, the other two are within rounding.
* That raw trades are cents is anchored on an internal transfer: USD account
  5-60013606 was debited 5,958.60 where CEN account 5-67033815 was credited
  595,860.00.

The exception is the group below. Its rows are NOT divided for money (lots
still are) from 2026-04-24 on — the same accounts are divided before that date,
and an account that left the group is divided again from the next day. It is
eight accounts; it needs the division the CRM skipped. A cent group the CRM
mishandles in future would show up 100x too large — add it here.
"""

from __future__ import annotations

UNDIVIDED_CEN_GROUPS = ("KCMC\\5Cent_C40L10",)
UNDIVIDED_SINCE = "2026-04-24"


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


# No percent signs in any fragment, so they are safe both in statements run
# with driver parameters and in ones run without.
UNDIVIDED_GROUPS_SQL = ", ".join(_literal(g) for g in UNDIVIDED_CEN_GROUPS)
UNDIVIDED_LOGINS_SQL = (
    f"(SELECT ug.loginSid FROM fxbackoffice.mt4_users ug WHERE ug.`GROUP` IN ({UNDIVIDED_GROUPS_SQL}))"
)


def daily_money_divisor_sql(login_expr: str, date_expr: str) -> str:
    """Divisor for one ``stats_trading`` row's money columns: 1, or 100 for the
    rows the CRM left in cents."""
    return f"IF({date_expr} >= '{UNDIVIDED_SINCE}' AND {login_expr} IN {UNDIVIDED_LOGINS_SQL}, 100, 1)"


def daily_money_divisor_by_group_sql(group_expr: str, date_expr: str) -> str:
    """Same divisor for statements that already join ``mt4_users``: test the
    account's group directly instead of a login subquery."""
    return f"IF({date_expr} >= '{UNDIVIDED_SINCE}' AND {group_expr} IN ({UNDIVIDED_GROUPS_SQL}), 100, 1)"

