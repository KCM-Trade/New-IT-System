# gap-trade · Gap Trade · rule_id 71-80 and 81-90

One tab, two bands with DIFFERENT metrics. Always say which band a figure belongs to.

## Background: the daily gap window
- The MT server pauses trading 00:00-01:00 MT every day and over the weekend. The tab looks at the
  MT 00:00-02:00 window around the re-open (gold re-opens at MT 01:00).
- The tab is scanned once a day, after that window. For WHEN the scan runs and which MT day it
  covers, quote the get_risk_alerts time caveat — do not state or compute a scan time yourself.
- The page shows no "scan now" or "refresh" button for this tab: data changes once a day.

## Rule 71 · SO+AB pair · 爆仓 AB 仓配对 (L = 爆仓方 loser, C = 对手方 counterpart)
**Fires when**
- L leg: a stop-out order (order comment starts with `[so`, `so:` or `cso:`) closed in the window;
- C leg: same symbol, OPPOSITE direction, opened within ±300 seconds of L, lot ratio 0.5-2×, and
  EITHER the same client on a different account OR a different client in the same account group;
- loser loss of at least `min_l_loss_usd` (default 100 USD) — BUT a pair where L and C shared a
  login IP while L was open is always kept, whatever the loss.
**Metric**: `net_usd` of the pair (higher = stronger).
**Fields**: l_login_sid, l_lots, l_profit_usd, c_login_sid, c_lots, c_profit_usd, open_diff_sec,
lot_ratio, net_usd, shared_ip_count, window_date, symbol. The IP addresses themselves and the client
names are NOT returned.
**Orders**: get_alert_orders on a rule-71 alert returns both the L and the C leg. If the C leg belongs
to a client outside the user's data scope, it is removed (`legs_masked_by_scope`) — do not guess it.

## Rule 81 · excess gap profit · Gap Trade 超额获利客户
**Counts only positions held THROUGH the gap**: opened 23:00-24:00 MT on the previous trading day
AND closed 01:00-02:00 MT on the scan day (Monday looks back to Friday 23:00-24:00). Profit is summed
per CLIENT (all accounts), cent already /100.
**Fires when** (either rule; fixed in code, the page's settings drawer is disabled):
- `neg_deposit`: net deposit ≤ 0 AND gap profit ≥ 100 USD; or
- `ratio_x1`: net deposit > 0 AND gap profit ÷ net deposit ≥ 1× AND gap profit ≥ 100 USD.
- A client whose net deposit cannot be found is not reported.
**Net deposit used here INCLUDES IB commission withdrawals** (`'ib withdrawal'`, the legacy
client-level figure). It is NOT the same as `net_deposit_trading` from get_client_overview — say so
if you put them side by side.
**Metric**: `total_profit_usd` (higher = stronger). Fields: contributing_login_sids,
contributing_account_count, symbols, symbol_count, total_profit_usd, profit_ratio, triggered_by,
window_date.

## Rule 81 follow-up outside the tab
- An early pass repeats every few minutes during the gap window, re-evaluates rule 81 early and,
  for new hits, writes a CRM tag so the client's withdrawals go to CS manual review:
  `禁止出金(風控)` for CN clients, `Withdrawal Notice` for Global clients. At most 10 tags per pass.
- The tools cannot see whether a tag was written. For "was X tagged", point to the CRM tags
  (get_client_overview returns CRM tags) or IT.
- Who reviews and removes these tags is not documented here — refer the user to the risk team.

## What this tab is NOT
- It is not the news-event AB scan (NFP/CPI/FOMC windows) and not the blow-up audit — those are
  scripts IT runs; see `references/event-ab-scan.md` and `references/blowup-audit.md`.
