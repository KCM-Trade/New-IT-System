# martingale · 马丁策略 · rule_id 111-120

**Intent**: "adding to a losing position" — same-direction adds while the existing position is under
water. If the market comes back, the company's loss is amplified.

**Fires when** — at the moment an account opens a new order, for that symbol + direction's CURRENT
open positions, all three hold:
1. summed floating P/L < −`floating_loss_floor_usd` (default 0 = any floating loss);
2. number of adds (open positions − 1) ≥ `min_add_count` (default 1);
3. the LARGEST add's lots ≥ the anchor's lots × `lot_multiplier` (1.0 = 1:1, 2.0 = 1:2).
The anchor is the oldest position still open. If the client closes the original first leg, the next
oldest becomes the anchor — this is intended (the rule is about what is held now).

**Metric**: `lot_ratio_mg` = largest add lots ÷ anchor lots (higher = stronger).
Fields: order_count, total_lots, direction, anchor_lots, new_lots, lot_ratio_mg, add_count,
floating_pnl, symbol.

**Scanned**: every 60 s, reading only opens at least 30 s old. Same blind spot as leverage-abuse
(under 30 s never, 30-90 s maybe, 90 s+ always); a ladder fully closed inside that time disappears.

**Not the same as** get_alert_orders' `lot_escalation_steps` / `max_consecutive_lot_ratio`, which
count consecutive opens at ≥ 1.5× the previous one over the returned orders (closed or open, any
P/L). Quote the one you actually have and name it.

Wording: "consistent with martingale-style adding: 4 same-direction adds, ratio 2.0×, floating
−350.00 USD" — not "this is a martingale trader".
