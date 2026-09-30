---
name: margin-and-stopout
description: Margin level, margin call, stop-out, equity/balance/credit, accounts close to stop-out, one-sided gold holders with low or high margin level, leverage abuse. Triggers — 保證金水平, 保证金水平, margin level, marginal level, 快被SO, 快爆倉, 爆仓, 強平, stop out, SO, margin call, MC, 淨值, 信用, 赠金, 單邊做多黃金, 滥用杠杆, leverage abuse.
---

# Margin level and stop-out (保證金水平 / 強平)

## When to use
- "Which accounts are close to stop-out (快被SO)?" / "margin level below 300% / above 3000%".
- "Accounts holding only long (or only short) gold, excluding cent accounts, near SO / in floating loss".
- "What is margin level / margin call / stop-out / credit?"
- "Is the 滥用杠杆 (leverage-abuse) tab the same as near stop-out?" (it is not — see below).
- MT4/MT5 Manager screens are not documented in this system: for "how do I see this in the
  Manager", say you have no documentation for it.

## Key facts
- **Equity (淨值) = Balance + Credit + Floating P/L.** Credit is bonus money (赠金) the company
  credited to the account; it counts toward equity. Floating P/L = Equity − Balance − Credit.
- **Margin (已用保證金)** = the margin locked by the account's open positions.
- **Margin level (保證金水平, %) = Equity / Margin × 100.** Lower = less free margin = closer to a
  margin call / stop-out. It is an ACCOUNT-level number covering all open positions on the
  account, not one symbol.
- An account with **no open positions has Margin = 0 and the MT server reports margin level 0**.
  A raw "margin level < X" filter therefore catches every empty account. Always require margin
  level > 0 (i.e. the account has positions).
- **Margin call (MC)** and **stop-out (SO, 強平)**: the MT server warns (MC) and then force-closes
  positions (SO) when margin level falls to levels configured on the account's MT group.
  The MC / SO levels per group are NOT documented in this system: never state a company SO or MC
  level; ask the user which level they mean and use their number.
- Cent (CEN) accounts store money in cents; margin level is a ratio and is the same either way.
- The account values the system stores (balance / equity / credit / margin level) are copies
  synced from the MT servers, **not strictly real-time**. For a live number the MT Manager is the
  source.

## The leverage-abuse (滥用杠杆) tab is NOT a "near stop-out" list
- That Risk Monitor tab is about how an account OPENS positions, not about which accounts are close
  to stop-out right now; "no leverage-abuse alert" does not mean "not near SO".
- Its alerts are read with the Risk control tools and explained by the `risk-monitor-rules` skill —
  only if those are in your tool / skill list. Otherwise the question needs the Risk control
  module permission (需要 Risk control 模块权限); do not describe the rule's settings.

## How to answer with the tools you have

### A. "Only long / only short gold, exclude cent, near SO (or margin level < / > X)"
Nothing certified returns margin level today. Do it in two steps and say so:
1. `rank_open_positions(symbol="XAUUSD", symbol_match="family", group_by="account",
   sort=<see below>, top_n=50, sids=<[1,6] for MT4 | [5] for MT5 | null for all>)`.
   - sort: `"net_lots"` for "largest holders"; `"floating_loss"` when the user asks for accounts in
     floating loss / near SO (losses are what push margin level down).
   - From the returned rows keep: only long → `sell_lots == 0` and `buy_lots > 0`; only short →
     `buy_lots == 0` and `sell_lots > 0`; exclude cent → `is_cent == false` (true = a CEN account OR a .cent/.kcmc symbol); floating loss →
     `floating_pl < 0`.
   - These filters are applied by you to the top 50 AFTER ranking. Say: "filtered from the top 50
     by <sort>; accounts further down the ranking are not checked".
   - Remember margin level is account-wide: a gold-only-long account may hold other symbols too.
2. Margin level for those accounts:
   - **If `run_sql` is in your tool list**: load the `fxbackoffice-schema` skill and use its pattern
     P2b (margin level of the exact `login_sids` from step 1) — uncertified, show the SQL, list the
     pitfalls. Apply the user's threshold (e.g. < 300 or > 3000) yourself and state it.
   - **If not**: say margin level is not available to you. Offer balance / equity / credit per
     account via ONE `get_client_overview` call with the rows' `client_id`s (up to 50), and point
     the user to the MT Manager for margin level.
3. Present: login_sid, client_id, buy/sell lots, floating_pl (rank_open_positions), margin level
   (run_sql, uncertified) and equity/credit if fetched. Cite each tool next to its numbers.

### B. "Add the current P/L (目前盈虧) for these accounts"
Use `floating_pl` from `rank_open_positions` rows already fetched (earlier in this conversation) or
call it again; for whole-client money use `get_client_overview` (`money.floating_pl`, balances per
account). Don't mix the two without saying which is which.

### C. One client: "how close is X to SO?"
`get_client_overview` (balance / equity / credit per account) + `get_trade_activity` (open-position
snapshot: count, lots, floating_pl). Margin level: run_sql as in A.2 if available; otherwise say it
is not available to you.

### D. Whole-book scan without the top-50 limit (only if `run_sql` is in your tool list)
When the user needs every one-sided account below / above a margin level (not just the top 50 of a
ranking), load the `fxbackoffice-schema` skill and use its pattern P1 (one side of gold + margin
level, cent excluded, MT4/MT5 selectable). It is the only copy of that SQL; it was checked against
the run_sql guard and the replica. Uncertified — follow the run_sql wording rules.

## What you CANNOT do
- No certified tool returns margin, margin level, free margin or leverage per account, and
  `rank_open_positions` cannot filter by margin level or exclude cent accounts before ranking.
  Say this rather than implying the list is complete.
- You don't know the company's MC / SO levels (not documented in this system).
- You cannot see live MT Manager values, send margin calls, change leverage or close positions.

## Wording rules
- Say "margin level 180% (run_sql, uncertified, synced copy)" — never "will be stopped out" or
  "about to blow up"; you don't know the SO level or the next price.
- Low margin level is a position fact, not misconduct. Alerts are signals (信號 / 告警), not
  verdicts; keep rule 5 wording.
- Whether to act on an exposure (hedge, A-book, contact the client) is the dealer's decision.
