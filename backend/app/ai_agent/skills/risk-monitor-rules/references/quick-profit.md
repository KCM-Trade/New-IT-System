# quick-profit · 快速获利 · rule_id 61-70

**Intent**: large profit in a short time — direction was very right, a direct B-book loss.

**Fires when** (per server + account + symbol): the sum of closed-trade P/L within the rule's own
`lookback_min` sliding window, plus (if `include_floating`) the current floating P/L, is ≥
`min_profit_usd`. The lookback is independent of the scan interval.

**Settings (schema defaults; live values may differ)**: lookback_min 30 (10-60),
min_profit_usd 5,000 (100-10,000,000), include_floating true.

**Metric**: `total_profit_usd` (higher = stronger).
Fields: total_profit_usd, order_count, total_lots, realized_profit, position_status, symbol.

**position_status**: `closed` (only realized), `open` (only floating), `mixed` (both). For open/mixed
the stored profit is the snapshot at the moment it fired; floating P/L moves afterwards. The page has a
"刷新浮动盈亏" button that refreshes floating P/L on screen only (it does not change the stored alert).

**Deduplication**: the same account+symbol+rule is not re-reported until its lookback has passed.

**Scanned**: slow cycle.
