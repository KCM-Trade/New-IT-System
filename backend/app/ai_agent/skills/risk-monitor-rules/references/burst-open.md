# burst-open · 批量下单 · rule_id 1-50

**Intent**: catch an EA / algorithm firing a burst of large orders at once.

**Fires when** (per server + account + symbol): within `burst_window_sec` seconds the account opened
at least `min_order_count` orders, each of at least `min_lots_per_order` lots. Buy and sell both count
(no same-direction requirement). The window slides over every order, so a burst that straddles a
second boundary is not missed.

**Settings (schema defaults; live values may differ)**: burst_window_sec 3 (range 1-30),
min_order_count 3 (2-50), min_lots_per_order 5.0 (0.01-100). Lots are standard lots; cent-account
lots are divided by 100 before comparing.

**Metric**: `order_count` (orders in the burst, higher = stronger).
Fields the tool returns: order_count, total_lots, first_open, last_open, symbol.

**Scanned**: every 60 seconds.

**Reading it**
- `equity_per_lot` on the page is a display figure (equity ÷ all open lots), not a trigger.
- The same account can fire on many bursts in a day: count alerts AND accounts.
- A burst is a speed/size pattern. It says nothing about whether the orders made money; use
  get_alert_orders or get_trade_activity for P/L.
- Related descriptive feature in get_alert_orders: `same_second_open_groups`.
