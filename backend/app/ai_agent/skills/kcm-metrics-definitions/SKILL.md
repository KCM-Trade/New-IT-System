---
name: kcm-metrics-definitions
description: House definitions (口径) behind every KCM number — net deposit (净入金/淨入金) two legs vs IB withdrawal, net gain (净赚/淨賺/賺咗幾多/蝕咗), full-chain rebate, cent (CEN/.cent/.kcmc ÷100, 美分戶), demo/employee exclusion, MT server day and DST, sid / loginSid, MT5 direction, hold buckets, win rate, return rate, max drawdown (回撤/MDD), 扛單, cumulative vs window figures.
---

# KCM metric definitions (口径)

## When to use
Load this before you state, compare or filter on any money, lots, date-range or return figure,
and whenever the user asks "what does X mean / why does this differ from the CRM / from page Y".
Typical triggers: 自開戶以來淨盈利為正 / 淨入金為負 / 這個客戶賺錢嗎 / cent 戶要除 100 嗎 /
MT 時間還是香港時間 / 收益率 / 最大回撤 / 扛單.

The system prompt already carries the short version of these rules. This skill adds the
reasoning, the edge cases and the list of "look-alike" numbers that are NOT the house metric.
If anything here seems to differ from the system prompt, the system prompt wins.

## 1. Subjects: client vs account
- **Client (客户)** = CRM `users.id` (also called client id / userId). One client can hold many accounts.
- **Account (账户)** = one MT trading account, written `loginSid` = `{SID}-{LOGIN}`, e.g. `1-8522845`.
  A bare LOGIN is ambiguous (the same number can exist on two servers) — always ask for or quote the SID.
  Users often type `SID-67040168` or `5-67040168`; only the second is a valid login_sid.
- **sid (server)**: 1 = MT4 Live, 5 = MT5, 6 = MT4 Live 2. These three are the live trading
  servers every tool covers. sid 2 = IB wallet (commission wallet, not a trading account);
  sid 4 = a retired server with leftover rows (never part of any live figure).
- `cid` on a client is the company/region flag (0 = CN, 1 = Global), **not** a client id.
- Counting: "positions / accounts / clients" are three different numbers. One account can hold
  hundreds of open orders. Always say which one you are counting.

## 2. Who is in the figures (universe)
- Demo/test accounts and employee clients are excluded from every certified tool figure.
  A subject that is an employee or only demo returns `subject_excluded` — there are no figures.
- Only buy/sell orders count as trades (pending orders and balance operations are not trades).

## 3. Cent (美分) conversion
- **CEN account** (account currency `CEN`): all money is stored in cents → ÷100 to get USD.
- **Cent symbols** — names ending in `.cent` or `.kcmc`: money AND lots are stored ×100 → ÷100.
- Lots are ×100 because of the **symbol**, not the account: only cent symbols have their lots divided.
  In the live data CEN accounts trade only cent symbols, so in practice the two coincide.
- `XAUUSD.c` is **not** a cent symbol. Do not divide it.
- A CEN account trading a cent symbol is divided once, not twice.
- Rebate (commission) amounts are already USD — never ÷100.
- Every certified tool has already converted. Never convert a tool figure again.

## 4. Time and day boundary
- A "day" is an **MT server day**. The MT server runs UTC+3 in summer and UTC+2 in winter on the
  US DST calendar (2nd Sunday of March → 1st Sunday of November).
- Closed-order figures are grouped by the MT server **close day**; an order opened inside the range
  but still open is not in the closed totals (it shows as an open position).
- Tools apply the day boundary themselves — never re-convert tool times. Quote date ranges as
  "YYYY-MM-DD to YYYY-MM-DD (MT server days)". Hong Kong time is what the web pages display.
- A user who gives an MT timestamp (e.g. "2026.09.28 03:31:05 MT time") means MT server wall clock.

## 5. Direction (buy/sell)
- MT5 (sid 5) **closed** orders store the exit side; tools have normalised direction to the
  position side. Open positions are stored with the position side on every server.
- Net lots = buy lots − sell lots (+ = client net long). A client with equal buy and sell is
  **locked (锁仓/對鎖)**: large gross, zero net exposure.

## 6. Net deposit (净入金 / 淨入金) — always two legs
| Leg | Tool field | Contains |
|---|---|---|
| Trading net deposit (交易净入金) | `net_deposit_trading` | approved deposits + withdrawals of trading money (withdrawals are negative) |
| IB withdrawal (IB 佣金提现) | `ib_withdrawal` | the client's IB-commission cash-outs (type `ib withdrawal`) |

- "净入金 / net deposit" on its own = `net_deposit_trading`. Filter and rank on that leg.
- The **legacy single number** = the two legs added. Only give it when asked, labelled
  "legacy net deposit (incl. IB withdrawal)". For an IB who also trades it can read deeply negative
  while they actually lose as a trader.
- Not in trading net deposit: internal transfers (`transfer in/out`, IB rebate moved into a
  trading account), bonus / credit. So a client who funds via a wallet transfer can show a small
  trading net deposit.
- Negative net deposit only says more money came out than went in. **It is not profit.**
  Positive net deposit does not mean the client is losing either.
- Some pages use a different definition. The **IB Data / IB deposits page's "Net Deposit" includes
  IB withdrawal**. If the user quotes a page number that differs, say which definition each uses
  rather than calling either wrong.

## 7. Net gain (净赚 / 淨賺) — "is the client making money"
- **Strict definition:** `net_gain = profit_all + floating_pl + rebate_all`
  - `profit_all` = lifetime closed P/L (profit + swaps + commission),
  - `floating_pl` = current unrealised P/L on open positions (= equity − balance − credit per account),
  - `rebate_all` = **full-chain rebate**: everything paid to every IB level on this client's trading.
- Same quantity as `equity − trading net deposit + full-chain rebate`.
- > 0 = the client (plus IB chain) is ahead of the company; < 0 = behind.
- **Strict null:** if any leg is unknown, net_gain is null → report "unknown", never 0.
- A withdrawal does not change net gain (it lowers equity and net deposit equally).
- Known small bias: if a client moves IB commission from the wallet into a trading account, net
  gain is slightly **over**-stated (that money is counted in equity and in rebate).
- "Lifetime" in the data base starts at: closed P/L 2020-08-24, rebate 2021-08-02,
  cash flow 2021-07-28. Older history is missing, not zero.
- `money` in get_client_overview is cumulative up to `source.as_of`, whatever date_range you pass.

**Look-alike numbers that are NOT net gain** (name them if the user quotes one):
- Risk Monitor "淨賺" column = equity − legacy net deposit (includes IB withdrawal, no rebate leg).
- Risk Watchlist "净值−(PL+Rebate)" = not a profit metric at all (how much money is still in the account, mixed with rebate).
- Client Return Rate numerator = equity − net deposit (no rebate leg).
- Client PnL Monitor "净盈亏(含佣金)" = closed profit + IB commission income; no net deposit, no floating.

## 8. Trade statistics
- **Lots** = standard lots after cent conversion.
- **net_profit** = profit + commission + swaps. **gross_profit** = profit only (before swap and
  commission) — it is NOT "sum of winning trades".
- **Win rate** = closed orders with profit > 0 ÷ closed orders; swap/commission are not part of the test.
- **Profit factor** (sum of wins ÷ sum of losses) is not returned by any tool and cannot be derived
  from tool totals, and no house definition of it is documented — do not supply one.
- **Hold buckets:** `<30min` = [0, 30 min), `30min-2h` = [30 min, 2 h), `>2h` = [2 h, ∞).
- Hold time of an open position keeps growing; historic "hold" questions about open orders all fall in `>2h`.

## 9. Return rate and drawdown (Client Return Rate page, /client-return-rate)
None of these are returned by a tool today. You may explain them; you cannot compute them.
- **正数入金收益率** (positive net deposit): (equity − net deposit) ÷ net deposit × 100.
- **调整后收益率 / 负净入金回报率**: fallback formulas for clients whose net deposit ≤ 0 (page-specific buckets).
- **ROACE (长期收益率)** = lifetime closed profit ÷ average daily equity over active days. Excludes floating P/L.
- **含浮动收益率** = (lifetime closed profit + change in floating P/L) ÷ average daily equity. Moves daily with price.
- **扛单率 (floating burden)** = average daily floating P/L ÷ average daily balance. More negative = more money sitting in losing open trades.
- **Max drawdown (最大回撤, MDD)** = worst peak-to-trough fall of a time-weighted unit value, built per
  account from end-of-day balances; client value = the worst of their accounts; windows 30d/90d/180d/365d/all
  (all = from 2021-07-13), always anchored to today. Blank ("—") means not enough data — never 0%.
  End-of-day data, so intraday drawdown is invisible (the figure is a lower bound). Shown only to
  users with the Risk control permission.
- If the user asks you to use "the Client Return Rate list/data": say you cannot read that page's
  table; ask them to paste the ids and you can pull certified figures for them (get_client_overview).

## 10. Cumulative (stock) vs window (flow)
- Lifetime/cumulative figures (money legs, equity) and window figures (trades in last 30 days) are
  different units. Do not subtract or compare them directly.
- To compare windows of different length, normalise (per day / per month) and say so.
- A "deposit tier" built from gross deposits without subtracting withdrawals overstates how much
  money a client really had (in a measured MT5 sample the median withdrawal ÷ deposit ratio was 0.50–0.59).
- Trimming "the lowest 20%" is one-sided and lifts the average; always show the untrimmed value beside it.

## How to answer with the existing tools
| Question | Tool call |
|---|---|
| "Of these N clients, whose net profit since opening is positive / net deposit negative" | ONE `get_client_overview(subjects=[…≤50], date_range=…)`; filter `money.net_gain` > 0 or `money.net_deposit_trading` < 0 yourself; list `failed` subjects |
| "Is client X making money" | `get_client_overview` → `net_gain` (and its three legs) |
| "Top N winners last week" | `rank_accounts(metric="net_profit", date_range=last week, top_n=N)` — accounts, not clients; closed P/L only; say so. Follow with `get_client_overview` on the resulting client_ids if the user then asks about lifetime money |
| "How does X trade / scalping" | `get_trade_activity(subject, date_range, group_by="hold_bucket" or "symbol")` |
| "Who is holding most XAUUSD now" | `rank_open_positions(symbol="XAUUSD")` — lead with net lots |

## What you cannot do today
- No certified return rate, ROACE, floating-inclusive return, 扛单率, MDD or profit factor.
- No historic floating P/L or historic equity for a past date (floating is a point-in-time value).
- No name/email lookup; ids only.
- No reading of web-page tables (Client Return Rate list, Risk Watchlist) — ask for ids.

## Wording rules
- Always say which definition you used ("trading net deposit, excl. IB withdrawal";
  "net gain = closed + floating + full-chain rebate").
- Say "accounts" or "clients" explicitly; rank_accounts ranks accounts.
- Say whether a figure is lifetime (cumulative to as_of) or for the date range.
- Unknown is "unknown / 未知", never 0.
- A metric describes; it is not a verdict. Never call a client a cheater, abuser, 套利者 etc.
