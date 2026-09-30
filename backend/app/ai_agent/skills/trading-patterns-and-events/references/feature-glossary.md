# Feature glossary — exact meaning of each quotable feature

## get_trade_activity (one client or one account, closed orders by MT close day)
- Totals: orders, standard lots, gross/net profit, win rate, avg/median hold minutes, symbols.
  net_profit = PROFIT + COMMISSION + SWAPS. Cent products (.cent/.kcmc) have lots and money /100;
  CEN accounts money /100. XAUUSD.c is NOT a cent product.
- `group_by`: "symbol" (what), "day" (when), "hold_bucket" (how long).
- Hold buckets: <30min = [0, 1800 s), 30min-2h = [1800, 7200 s), >2h = [7200 s, ∞).
- `open_positions`: a snapshot at call time; ignores the date range.
- Fact flags (thresholds are fixed in code; quote them when you use a flag):
  - `single_symbol_concentration`: top symbol holds ≥ 90 % of closed lots and orders ≥ 5.
  - `night_window_scalping`: ≥ 30 % of orders opened 00:00-02:00 MT and median hold < 30 min and
    orders ≥ 10.
  - `short_hold_dominant`: ≥ 50 % of closed orders held < 30 min and orders ≥ 10.
- Orders still open are not in the totals (they are in open_positions).

## rank_open_positions (snapshot now, one symbol family)
- buy_lots, sell_lots, net_lots (buy − sell, + = net long), gross_lots, floating_pl.
- A client with equal buy and sell lots is fully locked: no net exposure. Lead with net lots.

## get_risk_signals
- Detector alerts for one client, the watchlist case (if any), CRM risk tags, and other clients that
  shared ORDER IPs with this client (IPs masked; peers outside the user's data scope are hidden and
  only counted). A shared IP is a link to investigate, not proof of common control (VPN exit IPs are
  shared by many accounts, and mobile IPs change daily).
