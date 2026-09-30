# SOURCES — fxbackoffice-schema (not shown to the model)

## Port notes (OPT-0069, 2026-09-30)
This file is NEVER shown to the model (harness resource_filter; pinned by tests/test_ai_agent_skills.py).
File:line references below point at the pre-port tree and may have drifted.
- Cold review 2026-09-30: received the IB run_sql recipes from ib-and-rebate; added P7 (IB wallet balance) — guard ok, EXPLAIN ref on `userId` (2 rows).
- Margin section rewritten: concepts moved to `margin-and-stopout`; the SQL lives only here (P1 absorbed margin-and-stopout §D, which was an MT4-only, fewer-filter copy of P1). Added P2b (margin lookup by login_sid), P4b (rebate per IB for one client), P6 (isIb / partnerId).
- SQL verification 2026-09-30 (run_sql `validate_sql` + `prepare_sql` + EXPLAIN on the replica via connect_readonly): all patterns pass the guard (HAVING on aliases, backticked `GROUP`, `SUM(CMD = 1)`, GROUP_CONCAT all accepted). P1: `INDEX_CLOSEDATE` ref ~42k rows, ran in 0.27 s. P2: full scan of mt4_users (~216k rows), ran in 0.35 s. P2b: LOGIN_SID range. P3: loginSid/(loginSid, closeDate) range. P4: IDX_REF range. P4b: PRIMARY date range (~203k rows for 28 days) — kept with a "about a month" note. P5: user_tags index. P6: PRIMARY. The dropped §D ran in 0.23 s (same plan as P1).
- MARGIN_LEVEL: 1,244 live accounts > 0, 790 of them MT5 (so MT5 is populated); the rest are 0 (no positions).
- stats_ib_commissions lots double count: verified on THIS table (2026-09-22..26: 4,974 client-days, 4,647 with >1 IB row, 4,479 of those with identical lots; 3.83 rows per client-day).
- IB wallet ownership: 13,656 `IB-WALLET%` rows, 13,516 owned by an isIb user, 36 with no user row.
- mt4_trades OPEN_TIME/CLOSE_TIME are `datetime` (whole seconds); closeDate/openDate `date`.
- Gold-symbol TODO removed: the skill says "tell the user which symbols matched".


Harness note: this skill is only useful when `run_sql` is registered (`harness.run_sql_enabled`,
never for data-scope-restricted callers — run_sql.py:43-45). The harness should filter it out otherwise.

`prompt.py` = `backend/app/ai_agent/prompt.py`; `TD/` = `.cursor/skills/database-context/fxbackoffice/tables/`;
`QN` = `.claude/skills/kcm-risk-pipeline/references/query-norms.md`.

| # | Claim | Source |
|---|---|---|
| 1 | Whitelisted tables (7) | backend/app/ai_agent/tools/run_sql.py:120-122; prompt.py:113-115 |
| 2 | Only join path; no client id on mt4_trades; users.cid is 0 CN / 1 Global | prompt.py:141-144 |
| 3 | Client id equalities (mt4_users.userId, transactions.fromUserId, user_tags.userId) | prompt.py:144; TD/stats_ib_commissions.md:11 (ibId/refId → users.id) |
| 4 | Column lists in references/ = schema card columns only | prompt.py:147-169 |
| 5 | mt4_trades ~48M rows; indexes | TD/mt4_trades.md:1, :12-13 |
| 6 | Open sentinel closeDate='1970-01-01', ~50k rows sub-second; CLOSE_TIME refused; no openDate range | prompt.py:152-156; run_sql.py:494-517 |
| 7 | No OR on dates; no self-joins | TD/mt4_trades.md:21; prompt.py:171-174 |
| 8 | *_TIME = MT wall clock, not indexed | prompt.py:149, :121; QN:108-109 |
| 9 | transactions indexes | TD/transactions.md:17-23 |
| 10 | stats_ib_commissions PK and indexes | TD/stats_ib_commissions.md:1, :8 |
| 11 | mt4_users ~192K, users ~68K rows; mt4_users indexes | TD/mt4_users.md:1, :18-20; TD/users.md:1 |
| 12 | Timeout → narrow and retry once | prompt.py:49; run_sql.py:603-611 |
| 13 | Universe filter lines (sid IN (1,5,6), CMD, isDeleted, sid 1 '7%' demo, .demo symbol, GROUP/NAME demo/test, employee join) | QN:12-34, :46-53; backend/app/services/open_positions_rank_service.py:57-75; backend/app/services/login_ip_trade_profit_service.py:169-172 |
| 14 | sid 4 retired with leftover open rows | QN:46-48 |
| 15 | NAME is PII → refused; tools filter NAME | run_sql.py:257-283 ("name" in SENSITIVE_COLUMNS); login_ip_trade_profit_service.py:171 |
| 16 | Guard refuses any SQL comment | run_sql.py:17-23; prompt.py:126 |
| 17 | Cent money/lots divisor expressions | open_positions_rank_service.py:53-55 |
| 18 | Open rows carry position side; closed sid 5 inverted | open_positions_rank_service.py:15-17; TD/mt4_trades.md:37-40 |
| 19 | MARGIN_LEVEL = equity/margin×100 %, 0 when no open positions, ratio currency-immune | backend/app/services/rule_leverage_abuse_service.py:44-46; rule_leverage_abuse_service.py:196 ("margin_level is currency-immune"); backend/app/schemas/risk_monitor.py:103-107 |
| 20 | mt4_users synced within ~seconds to 1 min; BALANCE/EQUITY not strictly real-time | rule_leverage_abuse_service.py:27-31; TD/mt4_users.md:26 |
| 21 | Floating = EQUITY − BALANCE − CREDIT | .cursor/skills/rebate-arbitrage/SKILL.md:89-92 |
| 22 | totalProfit on open order = floating P/L | prompt.py:151; open_positions_rank_service.py:23-24 |
| 23 | MT5 terminal Position ID ≠ mt4_trades.TICKET for sid 5 | TD/mt4_trades.md:26-28 |
| 24 | Past wrong guesses: mt4_trades.CID, users.cid join | prompt.py:132-137 |
| 25 | IB-WALLET group, sid 2 | docs/features/ib-data.md (Metrics table, "IB Wallet Balance" row: GROUP LIKE 'IB-WALLET%'); client-return-rate.md:77 |
| 26 | AGENT_ACCOUNT = IB upline account; excludeFromReports | TD/mt4_users.md:24 |
| 27 | transactions types, status approved, isFee, CEN ÷100, 'transfer in' not deposit | TD/transactions.md:28-34; docs/analysis/deposit-tier-lots-analysis.md:42-58 |
| 28 | Full-chain rebate = sum over every IB row for the client; CRM single-IB report smaller | rebate-arbitrage SKILL.md:69-73, :104 |
| 29 | stats_ib_commissions currency CEN ÷100 | TD/stats_ib_commissions.md:11 |
| 30 | tags / user_tags columns and indexes | TD/tags.md:5-9; TD/user_tags.md:5-10 |
| 31 | CRM tags ≠ behaviour tags | docs/ai-context/CONTEXT.md:70-76 |
| 32 | rank_open_positions rows include is_cent, buy/sell lots; top_n ≤ 50; sort net_lots = |buy−sell| | prompt.py:285-290; backend/app/services/open_positions_rank_service.py:161-181; tools/open_positions.py:89-95 |
| 33 | users columns meaning (isIb, isVerified, isLead, partnerId) | prompt.py:160-162; TD/users.md:42 |
| 34 | Wording rules for run_sql answers | prompt.py:117-122, :127-128 |

## SQL patterns — proof status
| Pattern | Status |
|---|---|
| Universe filter block | Proven: same predicates as `open_positions_rank_service._OPEN_SQL` + `_ACCOUNT_FILTER_SQL` (prod, 2026-09-29), minus the NAME predicate; plus QN's sid-1 '7%' and `.demo` symbol lines (QN measured 2026-07-20). |
| P1 one-sided gold + margin level | **Verify with EXPLAIN before trusting.** Built from the proven open-positions SQL (same driving index `closeDate`), adds `mu.MARGIN_LEVEL` and a HAVING. Not run in repo. HAVING on SELECT aliases is valid MySQL; confirm the run_sql guard (sqlglot) accepts it. |
| P2 low margin level | **Verify with EXPLAIN before trusting.** No index on MARGIN_LEVEL → full scan of mt4_users (~192K rows); expected well under 15 s but unmeasured. |
| P3 one account closed orders | Proven shape: `trade_activity_service._CLOSED_SQL` (loginSid + closeDate BETWEEN). Uses index `(loginSid, closeDate)`. |
| P4 IB rebate per referred client | **Verify with EXPLAIN before trusting.** Uses `IDX_REF(ibId,date,currency)`; not run in repo. |
| P5 CRM tags | **Verify with EXPLAIN before trusting.** Trivial indexed lookup; not run in repo. |

## Unsourced / needs business confirmation
- TODO(business): stop-out and margin-call levels per group (users ask "快被SO"). Not in any whitelisted table or doc.
- TODO(business): gold symbol naming — the skill assumes the family `XAUUSD%` (as rank_open_positions does). Are there gold symbols not starting with `XAUUSD` on live servers (e.g. `GOLD…`)? query-norms mentions `Gold.demo` on a demo account only.
- "Do not sum `lots` in stats_ib_commissions (counted once per IB level)" is **inferred**: the measured double-count is for `stats_ib_commissions_by_login_sid.lots` (deposit-tier-lots-analysis.md:77-78) and KCM T6 (architecture.md:867). Same per-level row structure applies to `(ibId, refId)`, but not measured on this table. TODO(business/data): verify.
- `isLead` gloss "still a lead (not a converted client)" is an interpretation of the column name + activity-status-column skill's "lead" universe; not a documented definition.
- sid 1 '7%' demo rule and `.demo` symbol rule come from QN (KCM project norms), not from the certified tools' SQL (which use GROUP/NAME only). Adding them is stricter than the tools; harmless but means SQL counts can be slightly lower than tool counts.

## Notes for implementer
- The system-prompt schema card lacks `mt4_users.MARGIN` and `MARGIN_FREE` (both exist, TD/mt4_users.md:10). The skill works around it with `MARGIN_LEVEL > 0`; consider adding them to the card.
- The card also lacks `mt4_users.isDeleted` usage guidance — it is listed; P2 uses it. `users.masterPartnerId` exists but is not in the card; the IB skill does not use it.
- `run_sql.FIXED_CAVEATS[0]` says CEN accounts store "money (and lots) ×100". Per open_positions_rank_service.py:20-22 and trade_activity_service.py:137-146, lots are ×100 only for cent SYMBOLS, not for CEN accounts. The caveat text slightly overstates; the skill follows the service code.
- TD/mt4_trades.md:19-20 says "未平仓：CLOSE_TIME='1970-01-01'" and "UTC+3 无 DST" — both superseded (closeDate sentinel; DST). The skill follows prompt.py.
- Consider putting the "never write SQL comments" rule next to any SQL example you ship in prompts — the model copies examples verbatim.
