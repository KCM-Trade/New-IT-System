# intraday-return · 即日高收益 · rule_id 131-140

**Intent**: small accounts that make a multiple of their starting equity within ONE MT trading day
(the request came from the risk team after small-deposit accounts made several times their deposit in
a day using locking and step-up adds).

**Formula (v3)**, per account, for the current MT trading day:
- initial_equity = previous day's end-of-day equity + today's real deposits + credit − withdrawals /
  credit-out made BEFORE the first trade of the day (a withdrawal after trading = taking profit, not
  deducted).
- intraday_profit = P/L of everything opened today (closed + still open) + carried_gain, where
  carried_gain = max(overnight positions' P/L now, 0) − max(their floating at yesterday's close, 0).
  So recovering an overnight LOSS counts as 0, and an overnight winner is not re-counted every day.
- return_pct = 100 × intraday_profit ÷ initial_equity.
- Balance adjustments whose comment starts with "Balance Adjustment", "Adjustment" or "Initial" are
  not counted as deposits. Credit/bonus IS counted in the base. If withdrawals today exceed 50 % of
  deposits, the alert carries a flag.

**Fires when** (all of): initial_equity ≥ 50 USD, intraday_profit ≥ 30 USD, return_pct ≥ the tier,
net P/L over the last 7 days (closed + all current floating) ≥ 0, plus two optional conditions that
are off by default (minimum lock %, maximum median hold).
Seeded tiers: rule 131 = ≥ 100 %, rule 132 = ≥ 300 %. Only the highest tier reached is reported per
account per day; falling back from 300 % to 150 % does NOT open a new 100 % alert.
(These are the seeded values; the live tiers are page settings the tools do not return.)

**Metric**: `peak_return_pct` = the highest intraday return seen that trading day — the value that
fired. `return_pct` = latest tick, can be lower. The row keeps updating during the day.
Fields: trading_day, return_pct, peak_return_pct, initial_equity, intraday_profit, trades_today,
lots_today, median_hold_sec, lock_pct, top_symbol.
- `lock_pct` = share of the active time the account held both directions with the smaller side ≥
  half the larger (default ratio 0.5).

**Time column**: `trading_day` (MT calendar day, inclusive both ends) — not scan time.
**Scanned**: every 5 minutes. Alerts are emailed to the risk team on detection.
**Money**: USD, cent accounts /100 before the 50 / 30 USD checks.
