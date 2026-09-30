# quick-open-close · 快开快平 · rule_id 51-60

**Intent**: catch very short holding / scalping that wins often. Each trade is small risk, but a
high hit rate hurts a B-book.

**Fires when** (per server + account + symbol, closed trades in the scan's fetch window): at least
`min_closed_orders` trades were held ≤ `max_hold_seconds`, AND the summed P/L of those short trades is
≥ `min_total_profit_usd` (an optional floor, so trades held 30 s that made a few cents are not
reported).

**Settings (schema defaults; live values may differ)**: max_hold_seconds 60 (1-3600),
min_closed_orders 3 (1-200), min_total_profit_usd optional.

**Metric**: shortest `hold_duration_sec` (lower = stronger).
Fields: order_count, total_lots, hold_duration_sec, total_profit_usd, symbol.

**Scanned**: slow cycle (the page's scan interval, 5-60 min). There is no separate "window"
setting — the time scope is the scan's own fetch window.

**Reading it**
- Descriptive features in get_alert_orders: `median_hold_sec`, `pct_hold_lt_60s`, `win_rate`.
- For a client-level view over days, get_trade_activity with group_by="hold_bucket"
  (<30min / 30min-2h / >2h).
