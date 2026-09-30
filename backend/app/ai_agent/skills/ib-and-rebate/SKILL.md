---
name: ib-and-rebate
description: IB (introducing broker / 代理) questions — IB tree and upline chain (上級代理/代理鏈), MIB 一級代理, 二級/三級代理, staff/sales code (銷售), direct IB, rebate / commission (返佣/反佣/佣金), full-chain rebate, IB withdrawal (IB 佣金提現), IB wallet, IB who also trades, rebate farming signals (刷佣), "which IB does client X belong to", "how much rebate did X generate".
---

# IBs and rebate

## When to use
Any question about who introduced a client, an IB's clients or earnings, rebate/commission
amounts, IB withdrawals, or "rebate" inside a profit question. Load `kcm-metrics-definitions`
too if the question also involves net deposit or net gain.

## 1. Roles (who is who)
- **Client (客户)**: a CRM user (`users.id`) who trades. Leaf of the IB tree.
- **IB (代理 / introducing broker)**: a client flagged as an IB in the CRM. An IB earns
  **rebate (返佣, a.k.a. IB commission)** on the trading of the clients below them.
- **Levels:** the first external agent level is called **MIB (一级代理)**; agents under an MIB are
  二级 / 三级 / 四级代理. Which CRM tag ids mark the levels is not documented here — do not
  identify a level from a tag id.
- **Staff / sales (销售)**: internal sales accounts sit at the top of a chain. They carry a CRM tag
  in the "Staff Code" category (category 1). In a chain they are shown by their sales code, not by name.
- **Venue / team (场地)**: sales teams are CRM tags in category 6.
- **Employee** (`users.isEmployee = 1`) is broader than staff; employees are excluded from all client figures.
- A client sits in one IB chain. The CRM records the client's introducing IB (one level only). For
  the full chain use the IB Tree page (below) rather than following that link upward.

## 2. Rebate (返佣 / 反佣 / IB commission)
- Recorded daily per (IB, referred client): who earned it, whose trading generated it, and the
  amount actually paid.
- **Full-chain rebate (全链返佣)** = everything paid to **every** IB level on one client's trading.
  This is the company's real rebate cost for that client, and it is the `rebate_all` leg of net gain.
- A CRM commission report for one IB shows only that IB's own level, so it is **smaller** than the
  full-chain figure. The difference is expected, not an error — say so when a user compares them.
- Rebate amounts are already USD; never ÷100.
- The lots recorded next to rebate rows repeat per IB level — never use them as trading volume;
  volume comes from the trades themselves (get_trade_activity / rank_accounts).
- The certified rebate figure is a lifetime sum (cumulative to as_of); the data starts 2021-08-02.

## 3. IB withdrawal and the IB wallet
- IB commission is paid into an **IB wallet** (a separate account on sid 2, not a trading account).
- **IB withdrawal (IB 佣金提现)** = the IB cashing commission out of that wallet
  (payment type `ib withdrawal`). It is NOT trading money.
- That is why net deposit is reported as two legs: `net_deposit_trading` and `ib_withdrawal`.
  For an **IB who also trades**, the legacy single net-deposit number (legs added) can look deeply
  negative ("took out millions") while, as a trader, they actually lost. Always show both legs.
- An IB may also move commission from the wallet into their own trading account. That transfer is
  not a trading deposit, and it slightly over-states that client's net gain (the money is counted in
  equity and in rebate).

## 4. Rebate inside "is the company winning or losing on this client"
- House net gain = closed P/L + floating P/L + **full-chain rebate**. A client who loses a little on
  trading but generates a lot of rebate can still be a net cost to the company.
- The old rebate-arbitrage detector is **retired and has no data**.
  Any "rebate farming / 刷佣" question has to be answered from the figures themselves
  (rebate_all vs profit_all over the same client), described as figures, not as a conclusion.

## How to answer with the existing tools
| Question | What to call | Read from |
|---|---|---|
| "How much rebate did client X's trading generate (all levels)?" | `get_client_overview(subjects=[X], date_range=any)` | `money.rebate_all` (lifetime, full chain) |
| "Has client X (an IB) withdrawn commission?" | `get_client_overview` | `money.ib_withdrawal` (lifetime) |
| "Is X a net cost once rebate is included?" | `get_client_overview` | `money.net_gain` and its three legs |
| "Does X carry an IB / staff / venue CRM tag?" | `get_client_overview` | `client.crm_tags` |
| "Which clients did IB Y earn from, in a period" / "rebate paid on client X, per IB" / "who is X's direct IB, is X an IB" / "IB Y's wallet balance now" | No certified tool. Only if `run_sql` is in your tool list: load the `fxbackoffice-schema` skill, section "IB questions". Otherwise say you cannot answer it and point to the pages below. | — |

## What you cannot do today (say it plainly)
- **No IB tree / upline chain / full downline.** The tree tables are not available to you. You can
  see one level (the direct IB) only via run_sql, and only for users who have it. For the full chain
  (sales code > IB > sub-IB > client) point the user to the **IB Tree Query page
  (`/cs/ib-tree`)**. Do not reconstruct a chain by repeated guessing.
- **No per-IB deposit/withdrawal report.** Point to the **IB deposits page (`/cs/ib-deposits`)** or
  **IB Data page (`/warehouse/ib-data`)**. Note their "Net Deposit" INCLUDES IB withdrawal.
- **No list of an IB's whole downline** (clients who generated no rebate never appear in rebate rows).
- **No "which staff/sales owns this client"** beyond CRM tags you can read.
- **No company P&L per IB** (no certified tool aggregates net gain over an IB's clients). If the user
  gives you the client ids, you can call `get_client_overview` with up to 50 of them and sum
  the returned figures — say you derived the sum.
- No rebate-arbitrage alerts: that detector is retired.

## Wording rules
- Say "full-chain rebate (all IB levels)" vs "this IB's own commission" explicitly; they differ.
- Say "IB withdrawal (commission cash-out)" and keep it apart from trading net deposit.
- Use ids and sales codes, not names. Never infer an IB's identity from a masked or missing row.
- High rebate relative to trading P/L is a **figure**, not a verdict. Never call anyone a rebate
  abuser, 套利者, 刷单者 or similar — describe the numbers and leave the conclusion to the analyst.
