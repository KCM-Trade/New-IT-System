# hedge-open · 对冲刷单 · rule_id 91-100

**Intent**: catch lock-position churning — the same account opening buy AND sell on the same symbol
at the same time. Typical motives named in the design: generating rebate / volume. That is the
design's motive, not a finding about any client.

**Fires when** (per server + account + symbol, a sliding window on open time):
- at least `min_orders_per_side` buys AND at least that many sells in the window;
- buy lots and sell lots differ by less than 0.01 lot ("perfect 1:1", fixed in code);
- the matched size min(buy lots, sell lots) ≥ `min_total_lots`.
Small lots are caught on purpose (unlike burst-open, there is no per-order lot minimum).

**Settings (schema defaults; live values may differ)**: window_sec 3 (1-60) — production is set to
30 seconds; min_orders_per_side 1; min_total_lots 0.01. Each rule has a user-given name shown as
"Rule N — <name>".

**Metric**: `total_lots` = buy lots + sell lots (= 2× the matched hedge size).
Fields: order_count, total_lots, total_open_lots, buy_count, sell_count, buy_lots, sell_lots, symbol.

**Scanned**: slow cycle. No "scan now" button.

**Known limits (say them when relevant)**
- Only hedges INSIDE one account. Hedges across two accounts of the same client, or across clients,
  are not detected by this rule.
- A sequence longer than the window is split into several alerts, and some of its orders can drop
  out of every alert. One cluster = one alert only while it fits in the window.
- The page has an "聚合" (aggregate) view that folds one account's alerts into one row.

**Descriptive feature** in get_alert_orders: `opposite_side_overlap_pct` (share of time both a buy and
a sell were open on the same symbol).
