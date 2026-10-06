---
name: fxbackoffice-schema
description: Writing run_sql against fxbackoffice (MySQL) — tables mt4_trades, mt4_users, users, transactions, stats_ib_commissions, user_tags, tags; join path, indexes, 30s cost rules, open-position sentinel, cent/demo/employee filters, ready SQL for 單邊多單/單邊空單 gold holders, margin level (保證金水平/快被SO), per-account closed trades, rebate by IB. Use before any run_sql on fxbackoffice.
---

# fxbackoffice schema for run_sql

## When to use
Only when `run_sql` is in your tool list AND no certified tool can answer. Load this skill
before writing the SQL, and read `references/<table>.md` for the table you are about to touch.
Certified tools first:
| Question | Use instead of SQL |
|---|---|
| Who holds the most of symbol X now (net / gross / floating) | `rank_open_positions` |
| A client's net deposit, net gain, rebate, balances | `get_client_overview` |
| Account ranking by win rate / profit / lots / orders / profit factor over closed orders (up to 92 days) | `rank_accounts` |
| How one client/account trades | `get_trade_activity` |
SQL is justified for a column or filter those tools do not have — most often **margin level**,
**"only one side" filters combined with margin level**, or **IB commission rows**.

## Tables you can query (whitelist) and the join path
`mt4_trades` (orders) · `mt4_users` (MT accounts) · `users` (CRM clients) · `transactions`
(payments) · `stats_ib_commissions` (daily rebate per IB per client) · `user_tags` · `tags`.
Nothing else in fxbackoffice (no `ib_tree`, no `stats_*` other than the one above, no `mt5_live`).

**The only join path:** `mt4_trades.loginSid = mt4_users.loginSid`, then `mt4_users.userId = users.id`.
- There is no client-id column on `mt4_trades`.
- `users.cid` is the company flag (0 CN / 1 Global), **never** a client id — joining on it
  silently multiplies rows. The client id is `users.id` = `mt4_users.userId` =
  `transactions.fromUserId` = `user_tags.userId` = `stats_ib_commissions.refId`/`ibId`.
- `GROUP` is a reserved word: always write it as `` mu.`GROUP` ``.

Column lists: `references/mt4_trades.md`, `references/mt4_users.md`, `references/users.md`,
`references/transactions.md`, `references/stats_ib_commissions.md`, `references/tags.md`.
Use only the columns listed there (they match the system-prompt schema card).

## Cost rules (30 s per statement, shared replica)
- `mt4_trades` is ~48M rows. Every query on it MUST filter on an indexed column:
  `closeDate`, `openDate` or `loginSid` (indexes: `closeDate`; `openDate`; `loginSid`;
  `(loginSid, closeDate)`; `(SYMBOL, sid, closeDate)`).
- **Open positions = `closeDate = '1970-01-01'`** (indexed, ~50k rows, sub-second). Never use
  `CLOSE_TIME` / `OPEN_TIME` for this (not indexed; the guard refuses it). Never add an `openDate`
  range to an open-positions question (it drops everything opened earlier).
- **Whole-universe aggregates: aggregate first, join after.** Put the `mt4_trades` scan and its
  `GROUP BY loginSid` in a subquery with NO joins, then join `mt4_users` / `users` to that result
  (a few thousand rows) for the demo/employee/CEN filters. Joining per order is twice as slow.
  When money is summed, also group the subquery by the cent-symbol flag so the /100 can be applied
  outside. About one month of `closeDate` fits in the budget; longer windows: split by month or say
  it does not fit. Prefer `rank_accounts` when its metrics are enough.
- No `OR` across date conditions (breaks the index). No self-joins of `mt4_trades` (pairing orders
  across accounts will not finish) — cross-account trading-style detection is a Risk Monitor question.
- `*_TIME` columns are MT server wall clock and not indexed: filter by `closeDate`/`openDate`
  first, then narrow by time inside that.
- `transactions`: filter by `fromUserId` (index with `type`), or `type + status + processedAt`, or `createdAt`.
- `stats_ib_commissions`: always give a `date` range; filter by `ibId` (indexed with date) or by
  `refId` inside a date range.
- `mt4_users` (~192K rows) and `users` (~68K) are small enough to scan, but join them by key.
- If a statement times out: narrow the indexed filter and retry once; do not retry the same SQL.

## Filters the certified tools apply — reproduce them or list them as not handled
```sql
WHERE t.closeDate = '1970-01-01'
  AND t.sid IN (1, 5, 6)
  AND t.CMD IN (0, 1)
  AND COALESCE(t.isDeleted, 0) = 0
  AND (t.sid <> 1 OR t.LOGIN NOT LIKE '7%')
  AND t.SYMBOL NOT LIKE '%.demo'
  AND LOWER(mu.`GROUP`) NOT LIKE '%demo%' AND LOWER(mu.`GROUP`) NOT LIKE '%test%'
```
plus `JOIN users u ON u.id = mu.userId AND COALESCE(u.isEmployee, 0) = 0` to drop employees.
Why each line: `closeDate = '1970-01-01'` = still open (for closed orders use `closeDate BETWEEN …`);
sid 4 is a retired server with leftover open rows; CMD 2-5 are pending orders and 6 is a balance
operation; sid 1 logins starting with 7 are demo; `.demo` symbols are MT5 demo.

**Never put comments (`--`, `#`, `/* */`) in the SQL you send — the guard refuses any comment.**
The `[…]` markers below are explained in the bullets, not inside the SQL.
- The certified tools also drop accounts whose NAME contains demo/test. `NAME` is personal data and
  refused by the guard, so run_sql **cannot** apply that filter: say "demo accounts identified only
  by account name may remain".
- Cent: money ÷ `IF(UPPER(mu.CURRENCY) = 'CEN' OR LOWER(t.SYMBOL) LIKE '%.cent' OR LOWER(t.SYMBOL) LIKE '%.kcmc', 100, 1)`;
  lots ÷ `IF(LOWER(t.SYMBOL) LIKE '%.cent' OR LOWER(t.SYMBOL) LIKE '%.kcmc', 100, 1)`. `XAUUSD.c` is not cent.
  To **exclude** cent accounts (剔除 cent 戶): `UPPER(mu.CURRENCY) <> 'CEN'` and, if the user means
  cent symbols too, `LOWER(t.SYMBOL) NOT LIKE '%.cent' AND LOWER(t.SYMBOL) NOT LIKE '%.kcmc'`.
- Direction: open rows carry the position side on all servers. **Closed** sid 5 rows carry the
  exit side — for closed-order direction use `IF(t.sid = 5, 1 - t.CMD, t.CMD)` or state it is not normalised.
- Day boundary: `closeDate`/`openDate` are already MT server days. Do not convert.

## Margin level (保證金水平 / margin level %) — the SQL side
The concepts (equity, credit, margin call, stop-out, what the leverage-abuse tab is and is not) are
in the `margin-and-stopout` skill. The column facts you need for SQL:
- `mt4_users.MARGIN_LEVEL` = equity ÷ used margin × 100, a percent, computed by the MT server for the
  **whole account** (all symbols, not only the one you filtered). Populated on MT4 and MT5 accounts.
- It is **0 when the account has no open positions** (the large majority of accounts) — always add
  `mu.MARGIN_LEVEL > 0`, or a "margin level < X" filter returns every empty account.
- It is a ratio, so cent accounts need no conversion. `EQUITY`/`BALANCE`/`CREDIT` do need ÷100 for CEN.
- It is a synced copy (normally within about a minute of the MT server), not tick-real-time, and has
  no history.
- Stop-out / margin-call levels are not in these tables and are not documented in this system: use
  the user's own threshold ("margin level below X%"); never say "about to be stopped out".

## Ready SQL patterns (copy, then adjust only the parts named below the pattern)
Each pattern was run through the run_sql guard and EXPLAINed on the replica (2026-09-30): none scans
`mt4_trades` without an index. Each returns ≤ 200 rows; always keep an ORDER BY and a LIMIT.

**P1 — accounts holding ONLY one side of gold, with margin level** (單邊多單/單邊空單黃金, 剔除 cent 戶, 快被SO)
```sql
SELECT t.loginSid, mu.userId AS client_id, t.sid,
       MAX(mu.MARGIN_LEVEL) AS margin_level_pct,
       MAX(mu.EQUITY) AS equity, MAX(mu.BALANCE) AS balance, MAX(mu.CREDIT) AS credit,
       SUM(CASE WHEN t.CMD = 0 THEN t.lots ELSE 0 END) AS buy_lots,
       SUM(CASE WHEN t.CMD = 1 THEN t.lots ELSE 0 END) AS sell_lots,
       SUM(t.totalProfit) AS floating_pl,
       COUNT(*) AS orders, GROUP_CONCAT(DISTINCT t.SYMBOL) AS symbols
FROM mt4_trades t
JOIN mt4_users mu ON mu.loginSid = t.loginSid
JOIN users u ON u.id = mu.userId AND COALESCE(u.isEmployee, 0) = 0
WHERE t.closeDate = '1970-01-01'
  AND t.sid IN (1, 5, 6) AND t.CMD IN (0, 1) AND COALESCE(t.isDeleted, 0) = 0
  AND (t.sid <> 1 OR t.LOGIN NOT LIKE '7%') AND t.SYMBOL NOT LIKE '%.demo'
  AND LOWER(mu.`GROUP`) NOT LIKE '%demo%' AND LOWER(mu.`GROUP`) NOT LIKE '%test%'
  AND t.SYMBOL LIKE 'XAUUSD%'
  AND UPPER(mu.CURRENCY) <> 'CEN'
  AND LOWER(t.SYMBOL) NOT LIKE '%.cent' AND LOWER(t.SYMBOL) NOT LIKE '%.kcmc'
GROUP BY t.loginSid, mu.userId, t.sid
HAVING sell_lots = 0 AND buy_lots > 0
   AND margin_level_pct > 0 AND margin_level_pct < 300
ORDER BY buy_lots DESC
LIMIT 50
```
- Measured: driven by the `closeDate` index (open rows only), ~0.3 s.
- Adjustable parts: the symbol family (`'XAUUSD%'`); the servers (`t.sid IN (1, 6)` = MT4 only,
  `(5)` = MT5 only — open MT5 rows carry the position side, so CMD needs no flip here); the two
  cent-exclusion lines (drop them only if the user wants cent accounts kept — then divide money and
  cent-symbol lots by 100 and say so); the one-side test (`sell_lots = 0 AND buy_lots > 0` = long only;
  `buy_lots = 0 AND sell_lots > 0` = short only); the margin-level bounds (use the user's number, e.g.
  `< 300`, `< 500`, or `> 3000`); floating loss (同時需要浮動虧損) → add `AND floating_pl < 0` to HAVING;
  the ORDER BY (`buy_lots DESC` / `sell_lots DESC` for "most", `margin_level_pct ASC` for "lowest margin level").
- Money columns are USD here only because CEN accounts are excluded.
- "Only one side" is per account **for this symbol family**; the account may hold other symbols, and
  the margin level covers them too. Say so.
- Gold = the `XAUUSD…` family (as in rank_open_positions); tell the user which symbols matched (`symbols`).
- `floating_pl` here is the gold rows only (open-row `totalProfit`), not the whole account.
- A client can be one-sided in one account and hedged in another; this pattern is per account.
- If margin level is NOT part of the question, use `rank_open_positions(symbol, group_by="account")`
  instead (certified) — see the `margin-and-stopout` skill for how to post-filter it.

**P2 — accounts with low margin level (any symbol)**
```sql
SELECT mu.loginSid, mu.userId AS client_id, mu.sid, mu.CURRENCY, mu.MARGIN_LEVEL,
       mu.EQUITY, mu.BALANCE, mu.CREDIT, mu.LEVERAGE
FROM mt4_users mu
JOIN users u ON u.id = mu.userId AND COALESCE(u.isEmployee, 0) = 0
WHERE mu.sid IN (1, 5, 6) AND COALESCE(mu.isDeleted, 0) = 0
  AND LOWER(mu.`GROUP`) NOT LIKE '%demo%' AND LOWER(mu.`GROUP`) NOT LIKE '%test%'
  AND mu.MARGIN_LEVEL > 0 AND mu.MARGIN_LEVEL < 150
ORDER BY mu.MARGIN_LEVEL ASC
LIMIT 100
```
- Measured: reads the whole `mt4_users` table (no index on MARGIN_LEVEL; ~216k rows, ~0.4 s). Fine for
  `mt4_users`; never copy this shape onto `mt4_trades`.
- EQUITY/BALANCE/CREDIT of rows with CURRENCY = 'CEN' are in cents — divide or say so.

**P2b — margin level of accounts you already have** (e.g. the `login_sids` from rank_open_positions)
```sql
SELECT loginSid, `GROUP`, CURRENCY, LEVERAGE, BALANCE, EQUITY, CREDIT, MARGIN_LEVEL
FROM mt4_users
WHERE loginSid IN ('1-8522845', '5-67040168') AND MARGIN_LEVEL > 0
```
Replace the ids with the exact login_sids. Apply the user's threshold yourself and state it.

**P3 — one account's closed orders in a window**
```sql
SELECT t.ticketSid, t.SYMBOL, t.CMD, t.lots, t.OPEN_TIME, t.CLOSE_TIME,
       t.OPEN_PRICE, t.CLOSE_PRICE, t.PROFIT, t.SWAPS, t.COMMISSION, t.totalProfit
FROM mt4_trades t
WHERE t.loginSid = '1-8522845'
  AND t.closeDate BETWEEN '2026-09-01' AND '2026-09-28'
  AND t.CMD IN (0, 1) AND COALESCE(t.isDeleted, 0) = 0
ORDER BY t.CLOSE_TIME
LIMIT 200
```
Prefer `get_trade_activity` for totals; use this only for order-level rows. sid 5 CMD is the exit side
here. To select by OPEN day instead (e.g. fills on one day), use `t.openDate = 'YYYY-MM-DD'` — also indexed.
Times are MT server wall clock in whole seconds; there is no request-price column.

**P4 — rebate earned by one IB, per referred client**
```sql
SELECT c.refId AS client_id, c.currency, SUM(c.commission) AS commission
FROM stats_ib_commissions c
WHERE c.ibId = 123456
  AND c.date BETWEEN '2026-09-01' AND '2026-09-28'
GROUP BY c.refId, c.currency
ORDER BY commission DESC
LIMIT 50
```
**P4b — rebate paid on one client's trading, per IB** (same table, `refId` side)
```sql
SELECT c.ibId, c.currency, SUM(c.commission) AS commission
FROM stats_ib_commissions c
WHERE c.refId = 123456
  AND c.date BETWEEN '2026-09-01' AND '2026-09-28'
GROUP BY c.ibId, c.currency
ORDER BY commission DESC
LIMIT 50
```
`refId` has no leading index: this reads the date range of the primary key, so keep the range to about
a month. Do not SUM `lots` from this table (see references/stats_ib_commissions.md).

**P5 — CRM tags of given clients**
```sql
SELECT ut.userId AS client_id, tg.tag, tg.categoryId, ut.createdAt
FROM user_tags ut JOIN tags tg ON tg.id = ut.tagId
WHERE ut.userId IN (111111, 222222)
LIMIT 200
```
Prefer `get_client_overview` (returns `crm_tags`) unless you need the category or tagging date.

**P6 — is a client an IB, and who introduced them**
```sql
SELECT id, isIb, partnerId FROM users WHERE id IN (111111, 222222)
```

## IB questions (moved here from `ib-and-rebate`: they need run_sql)
| Question | Pattern / table |
|---|---|
| Which clients did IB Y earn from, and how much, in a period | P4 (`stats_ib_commissions`, `ibId = Y` + date range, grouped by `refId`) |
| Rebate paid on client X in a period, per IB | P4b (`refId = X` + a date range of about a month at most, grouped by `ibId`) |
| Who is X's direct IB? Is X an IB? | P6 (`users.partnerId`, `users.isIb`) — one level only; the full chain is the IB Tree page `/cs/ib-tree` |
| IB Y's wallet balance now | P7 below |

**P7 — an IB's commission wallet balance** (wallet accounts are sid 2, group `IB-WALLET…`; the row's `userId` is the IB's own client id)
```sql
SELECT mu.loginSid, mu.CURRENCY, mu.BALANCE
FROM mt4_users mu
WHERE mu.userId = 123456 AND mu.`GROUP` LIKE 'IB-WALLET%'
LIMIT 20
```
BALANCE is in cents when CURRENCY = 'CEN'.

## Pitfalls that produced wrong answers before
- Guessing column names from the MT Manager API (e.g. `mt4_trades.CID`) — use only listed columns.
- Joining or grouping on `users.cid` — it is a country/company flag.
- Finding open orders with `CLOSE_TIME = '1970-01-01'` — refused; use `closeDate`.
- Forgetting `sid IN (1,5,6)` on open positions — sid 4 adds thousands of dead rows.
- MT5 order numbers from the MT5 terminal (Position ID) do **not** match `mt4_trades.TICKET` for sid 5.
  Look up MT5 orders by account + time instead, and say the ticket numbers differ.
- `MARGIN_LEVEL = 0` read as "0% margin level, about to be stopped out" — it means no open positions.

## What you cannot do with run_sql
- No IB tree / downline hierarchy (`ib_tree` is not whitelisted).
- No MT5 raw tables (millisecond deal times, Position IDs, EA magic numbers).
- No personal data: names, emails, phones, addresses, IPs are refused.
- No historic balances/equity/margin (only the current snapshot in `mt4_users`).
- No writes of any kind.

## Wording rules (from the system prompt, repeated because they matter here)
- Say the figures are 未认证 / uncertified, show the exact SQL (or `data.sql_executed` when it differs),
  cite "(run_sql, uncertified)", and list the pitfalls your SQL did not handle (cent, demo-by-name,
  employees, sid 5 direction, MT day boundary).
- Say what one row is (account vs client) and the time the snapshot was read.
