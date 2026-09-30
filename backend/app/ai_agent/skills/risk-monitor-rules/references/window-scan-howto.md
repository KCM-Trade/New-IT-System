# get_window_scan — the /window-scan page ("Trade Window Scan")

Question it answers: "at that moment, who got in (or got out) and made money?" — a single moment,
not "every day at this time" (that is the Hold Duration Analysis page, a T-1 trend report).

## Arguments
- `anchor_hk`: "YYYY-MM-DD HH:MM", Hong Kong wall clock, must be in the past. The tool converts to MT
  time with DST (summer HK − 5 h, winter HK − 6 h).
- `window_min`: 1, 3, 5, 10 or 15 → the window is ± that many minutes (symmetric).
- `scan_by`: "open" (default, orders OPENED in the window — "who got in") or "close" (orders CLOSED
  in the window — "who took money out at that moment").
- `hold_bucket`: "total" | "lt30m" | "m30_2h" | "gt2h" — filters single orders BEFORE summing per client.
- `sids` subset of [1,5,6]; `symbol` is a PREFIX (e.g. "XAUUSD" also matches XAUUSD.c / .cent).
- `top_n` 1-50; `sort` "closed_profit" | "net_gain" | "lots".
- `include_trades=true` adds per-order rows, only when top_n ≤ 5.

## Rules of the result (quote them)
- A client is listed only if the SUM of their CLOSED orders in the window is > 0 (client level, not
  per order). Floating profit never makes a client "profitable"; it is shown separately.
- With scan_by="close", open orders / floating profit are structurally 0 / empty — not missing data.
- `closed_profit` / `floating_profit` cover the window's orders only. `net_deposit` (trading, excludes
  IB withdrawals), `total_rebate`, `net_gain` are the client's LIFETIME figures.
- Demo/test and employees excluded. If `stats.truncated` is true the list is incomplete — narrow the
  window or add a symbol.

## Using it for a data release
1. Get the release time: `get_economic_calendar` for upcoming releases; for a past one, ask the user.
   US 08:30 ET data = 15:30 MT; FOMC = 21:00 MT (all year).
2. Convert to Hong Kong time for `anchor_hk` (MT + 5 h summer / + 6 h winter). Example:
   FOMC 21:00 MT in September = 02:00 HK the next calendar day.
3. Typical calls: `scan_by="open", window_min=15` (positioned just before/after the release) and
   `scan_by="close", window_min=15` (took profit right after).
4. Say the limits: ± 15 min at most, symmetric; the IT event AB scan uses −15 / +60 min and pairs
   accounts, which this tool does not do.
