# SOURCES — kcm-metrics-definitions (not shown to the model)

## Port notes (OPT-0069, 2026-09-30)
This file is NEVER shown to the model (harness resource_filter; pinned by tests/test_ai_agent_skills.py).
File:line references below point at the pre-port tree and may have drifted.
- Profit-factor TODO(business) removed from SKILL.md: the skill now says no house definition is documented.
- §3 cent: added "lots ×100 only on cent symbols; CEN accounts trade only cent symbols" — data check 2026-09-30 (closed 2025-12-01..03, 2026-06-01..03, 2026-09-21..28 and the open book, sids 1/5/6, CMD 0/1): zero orders of a CEN account on a non-cent symbol; 2 open orders of a non-CEN account on a cent symbol.


Paths are relative to the repo root unless absolute. `prompt.py` = `backend/app/ai_agent/prompt.py`.

| # | Claim in SKILL.md | Source |
|---|---|---|
| 1 | Client = `users.id`; account = `loginSid` `{SID}-{LOGIN}`; bare login ambiguous across servers | docs/ai-context/CONTEXT.md:10-17; CLAUDE.md:53; prompt.py:41-43 |
| 2 | sid 1 = MT4 Live, 5 = MT5, 6 = MT4 Live 2 | prompt.py:145; .cursor/skills/database-context/fxbackoffice/tables/mt4_trades.md:18 |
| 3 | sid 2 = IB wallet, not a trading account | docs/features/client-return-rate.md:77 ("sid IN (1,5,6) 排除 IB Wallet"), :404; ~/.claude/skills/app-deposit-analysis/SKILL.md:25 |
| 4 | sid 4 = retired server with leftover rows | .claude/skills/kcm-risk-pipeline/references/query-norms.md:46-48 |
| 5 | `cid` 0 = CN / 1 = Global, not a client id | prompt.py:142-144; CLAUDE.md (Data Scope bullet: "users.cid 只有 0=CN / 1=Global") |
| 6 | rows ≠ accounts ≠ clients | query-norms.md:56-57 |
| 7 | Demo/test + employees excluded by every certified tool; subject_excluded | prompt.py:46, :65; tools/client_overview.py:293-294; tools/rank_accounts.py:164 |
| 8 | CMD 0/1 only are trades | tools/open_positions.py:120-121; tables/mt4_trades.md:16 |
| 9 | CEN account money ÷100; .cent/.kcmc money and lots ÷100; XAUUSD.c not cent; divided once | prompt.py:64, :77; backend/app/services/trade_activity_service.py:137-146; backend/app/services/open_positions_rank_service.py:20-22 |
| 10 | Rebate amounts already USD, never ÷100 | .cursor/skills/rebate-arbitrage/SKILL.md:66-68; /opt/myproject/KCM_Risk_Control_System/docs/architecture.md:861 (T6 rebate_usd "绝不 ÷100") |
| 11 | MT server day, UTC+3 summer / UTC+2 winter on US DST calendar | prompt.py:62-63; CLAUDE.md:54 |
| 12 | Closed figures by close day; open-in-range orders not in closed totals | tools/trade_activity.py:87-88 |
| 13 | Web pages display Hong Kong time | CLAUDE.md:54 ("frontend renders in Asia/Hong_Kong") |
| 14 | MT5 closed CMD = exit side; normalised by tools; open rows carry position side | prompt.py:76; tables/mt4_trades.md:37-40; open_positions_rank_service.py:15-17 |
| 15 | net lots = buy − sell; equal buy/sell = locked | tools/open_positions.py:114-115; prompt.py:98-99 |
| 16 | Net deposit two legs, definitions, default = trading leg, legacy sum label | prompt.py:66-71; tools/client_overview.py:299-300; rebate-arbitrage SKILL.md:60-62 |
| 17 | Withdrawals stored negative | docs/features/client-return-rate.md:73 |
| 18 | Internal transfers / bonus / credit not in trading net deposit | KCM_Risk_Control_System/docs/architecture.md:878-884 (T7 columns: internal_transfer / bonus_credit are separate from trading_net_deposit) |
| 19 | Negative net deposit is not profit; positive does not mean losing | prompt.py:72-73; rebate-arbitrage SKILL.md:63-64 |
| 20 | IB Data page "Net Deposit" includes IB withdrawal | docs/features/ib-data.md:55-59 |
| 21 | Net gain strict formula, full-chain rebate, strict null | prompt.py:74-75; rebate-arbitrage SKILL.md:75-88; tools/client_overview.py:297-298 |
| 22 | floating = equity − balance − credit | rebate-arbitrage SKILL.md:89-92; backend/app/services/account_enrichment.py:381-393 |
| 23 | Equivalence with equity − trading net deposit + rebate; withdrawal doesn't change it | rebate-arbitrage SKILL.md:80-86, :101-102 |
| 24 | Known over-statement when IB commission moved into trading account | rebate-arbitrage SKILL.md:126-127 |
| 25 | Coverage start dates 2020-08-24 / 2021-08-02 / 2021-07-28 | rebate-arbitrage SKILL.md:113-114 |
| 26 | `money` cumulative to as_of | prompt.py:56; tools/client_overview.py:295-296 |
| 27 | Look-alike numbers list | rebate-arbitrage SKILL.md:115-125 |
| 28 | net_profit = PROFIT+COMMISSION+SWAPS; gross_profit = SUM(PROFIT) | tools/rank_accounts.py:156-157; backend/app/services/rank_accounts_service.py:92 |
| 29 | Win rate = PROFIT > 0 / closed orders | tools/rank_accounts.py:156 |
| 30 | Hold bucket edges | prompt.py:78; tools/trade_activity.py:95; docs/features/hold-bucket-report.md:106 |
| 31 | Open orders fall into >2h when looked at historically | docs/features/window-scan.md:302 |
| 32 | Return-rate formulas (正数入金收益率, ROACE, 含浮动收益率, 扛单率) | docs/features/client-return-rate.md:80-90, :203-213 |
| 33 | MDD definition, windows, 2021-07-13 start, MAX over accounts, "—" never 0%, EOD lower bound, needs risk module | docs/features/client-return-mdd.md:10-31, :89-92, :188-191; client-return-rate.md:91 |
| 34 | Deposit tier: gross deposit not net; median withdraw/deposit 0.50–0.59; one-sided trim lifts mean; stock vs flow | docs/analysis/deposit-tier-lots-analysis.md:80-86, :126-133, :138-145 |
| 35 | rank_accounts ranks accounts not clients; return_pct refused | prompt.py:90-95 |
| 36 | Batch get_client_overview ≤ 50, filter yourself, list failed | prompt.py:81-83 |

## Unsourced / needs business confirmation
- TODO(business): is there a house definition of **profit factor** (users asked for it in A-book questions)? Nothing in repo defines it; tools cannot compute it because `gross_profit` is pre-swap/commission profit, not sum of winners.
- The "(user types `SID-67040168`)" example is from the real-questions list, not from a doc — it is an illustration, not a fact.
- sid 1 is described in `app-deposit-analysis` as "CN (CNY 账簿, 独立实体)" and in prompt.py as "MT4 live". The skill uses prompt.py wording only; whether sid 1 ≡ CN entity is not asserted. TODO(business) if the agent should ever say sid 1 = CN.

## Notes for implementer
- `.cursor/skills/database-context/fxbackoffice/tables/mt4_trades.md:20` still says OPEN_TIME/CLOSE_TIME are "UTC+3 无 DST"; this contradicts CLAUDE.md:54 and prompt.py:62-63 (DST). The skill follows prompt.py. The table doc should be corrected (query-norms.md:113-118 already says DST).
- `/home/kcm-trade/.claude/skills/kcm-business-concepts/SKILL.md` "Timezone: DB stores UTC+2, HK = DB+6" is also inconsistent with the DST rule; not used.
- prompt.py already duplicates §3–§7 in short form. If skills ship, the long-form reasoning (look-alike list, coverage dates, return/MDD definitions) can live here and prompt.py can keep only the rules it enforces today. No wording in this skill contradicts prompt.py.
- prompt.py rule 5 forbidden words are repeated in the last wording rule.
