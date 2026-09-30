# Trading-style features from the Risk control tools

Companion to the `trading-patterns-and-events` skill (which covers the tools every caller has).
Same rule: quote features, never label the person.

| Style (user words) | Features from the Risk control tools | Tool |
|---|---|---|
| EA / robot / 同秒下单 | `same_second_open_groups`, `orders_in_same_second_groups`; burst-open alerts (many large orders in seconds) | get_alert_orders; get_risk_alerts(tab="burst-open") |
| Scalping / 短线 / 剥头皮 | `median_hold_sec`, `pct_hold_lt_60s`; quick-open-close alerts | get_alert_orders; get_risk_alerts(tab="quick-open-close") |
| Martingale / 加仓 | martingale-tab alerts (`add_count`, `lot_ratio_mg`, `floating_pnl`); `lot_escalation_steps`, `max_consecutive_lot_ratio` | get_risk_alerts(tab="martingale"); get_alert_orders |
| Hedging / locking / 锁仓 | hedge-open alerts; `opposite_side_overlap_pct`; intraday-return `lock_pct` | get_alert_orders; get_risk_alerts |
| AB / opposite accounts / 对敲 | gap-trade rule 71 pairs (L + C legs, `open_diff_sec`, `lot_ratio`, `shared_ip_count`) — the only stored cross-account pairing | get_risk_alerts(tab="gap-trade", rule_ids 71-80) → get_alert_orders |
| Gap trading / 缺口 | gap-trade tab: rule 71 SO+AB pairs, rule 81 profit held through the daily gap | get_risk_alerts(tab="gap-trade") |
| News-release trading | who opened/closed profitably within ±N min of a release moment | get_economic_calendar → get_window_scan (`references/window-scan-howto.md`) |

Cross-account pairing exists only in gap-trade rule 71 and in the IT scripts
(`references/event-ab-scan.md`, `references/blowup-audit.md`). Pairing raw orders across clients at a
news release is meaningless without an identity/IP link: one NFP window gave ~1.4 million candidate
order pairs.

## get_alert_orders (orders behind 1-3 Risk Monitor alerts) — `features` per alert
- `median_hold_sec` — median hold of CLOSED orders (open orders excluded; their hold is still growing).
- `pct_hold_lt_60s` — % of closed orders held under 60 seconds.
- `same_second_open_groups` — number of (symbol, second) groups with ≥ 2 orders opened in the same
  second; `orders_in_same_second_groups` — how many orders sit in those groups.
- `lot_escalation_steps` — count of consecutive same-symbol, same-direction opens where lots ≥ 1.5×
  the previous one (1-2-4-8 = 3 steps). `max_consecutive_lot_ratio` — the largest such ratio.
  Covers the returned orders, open or closed, winning or losing — it is not the martingale rule.
- `opposite_side_overlap_pct` — time with BOTH a buy and a sell open on the same symbol ÷ time with
  anything open, over all symbols (%).
- `win_rate`, `net_profit_usd` (closed; profit + swap + commission), `floating_profit_usd` (open).
- Money is USD (cent already /100). Times are UTC in the tool output.

## Risk Monitor alert fields that describe style
- martingale: add_count, anchor_lots, new_lots, lot_ratio_mg, floating_pnl, direction.
- hedge-open: buy_count, sell_count, buy_lots, sell_lots.
- intraday-return: trades_today, lots_today, median_hold_sec, lock_pct (share of active time holding
  both directions with the smaller side ≥ half the larger), top_symbol.
- gap-trade rule 71: open_diff_sec, lot_ratio, shared_ip_count between loser (L) and counterpart (C).
