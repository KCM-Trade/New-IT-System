# SOURCES — margin-and-stopout (not shown to the model)

## Port notes (OPT-0069, 2026-09-30)
This file is NEVER shown to the model (harness resource_filter; pinned by tests/test_ai_agent_skills.py).
File:line references below point at the pre-port tree and may have drifted.
- MC/SO-level TODO(business) removed: the skill says the levels are not documented and must not be stated.
- §A.2 and §D no longer carry their own SQL; they point to `fxbackoffice-schema` P2b / P1 (dedupe per OPT-0069).
- Reference to the excluded `mt-manager-howto` skill removed.
- Cold review 2026-09-30: the execution-mechanism line (stop-out vs the execution-delay component) and the leverage-abuse rule details (band, levels, trigger timing, 15-minute freshness, get_risk_alerts recipe) removed — confidential / risk-audience only.


| Claim | Source |
|---|---|
| EQUITY = BALANCE + CREDIT + FLOATING; floating = EQUITY − BALANCE − CREDIT; credit = 赠金 (bonus) | docs/features/risk-watchlist.md:120–123; .cursor/skills/rebate-arbitrage/SKILL.md:90 |
| MARGIN_LEVEL = Equity / Margin × 100; margin_ratio = 100 / margin level | docs/lessons/lesson-opt-0030-leverage-abuse-snapshot-scan.md:67–68, :140–141; docs/features/risk-monitor.md:321 |
| Empty account: Margin = 0 and MT reports MARGIN_LEVEL = 0 → must require > 0 | lesson-opt-0030…md:71; docs/features/risk-monitor.md:325; backend/app/services/rule_leverage_abuse_service.py:45 |
| mt4_users has MARGIN, MARGIN_LEVEL, MARGIN_FREE, LEVERAGE, BALANCE, EQUITY, CREDIT | .cursor/skills/database-context/fxbackoffice/tables/mt4_users.md:8–10 |
| run_sql schema card exposes LEVERAGE, BALANCE, EQUITY, CREDIT, MARGIN_LEVEL, GROUP, CURRENCY on mt4_users | backend/app/ai_agent/prompt.py:157–159 |
| Stored account values are replica-synced, not strictly real-time | .cursor/skills/database-context/fxbackoffice/tables/mt4_users.md:26 |
| Leverage rule trusts only rows with MODIFY_TIME in last 15 min | docs/features/risk-monitor.md:325 |
| CEN: ratio unaffected, money ÷100 | .cursor/skills/risk-monitor/references/implementation-status.md:24 ("CEN:ratio 免疫、equity/margin ÷100") |
| Leverage-abuse band 101–110, event-gated (margin level at the moment of opening), ignores loss-drift accounts | docs/features/risk-monitor.md:317–319; .cursor/skills/risk-monitor/references/rules-catalog.md:137–141 |
| Rule levels 200 / 150 / 125 configured on the page; MARGIN > 0; min_equity_usd default 100 | rules-catalog.md:141–142; docs/features/risk-monitor.md:317, :325 |
| get_risk_alerts leverage rows: leverage, equity, margin_level, margin_used, free_margin; metric = margin_level ascending | backend/app/ai_agent/tools/risk_bands.py:115–118; risk_alerts.py:84; prompt.py:202, :209–211 |
| rank_open_positions args (symbol, symbol_match, group_by, sort incl. floating_loss, top_n ≤ 50, sids) and rows (login_sids, client_id, is_cent, buy/sell/net lots, floating_pl) | backend/app/ai_agent/prompt.py:281–291; backend/app/services/open_positions_rank_service.py:40, :108–121, :166–180 |
| rank_open_positions `is_cent` = CEN account OR .cent/.kcmc symbol | open_positions_rank_service.py:57–60 |
| Filters are applied after the top_n cut (tool sorts then slices) | backend/app/ai_agent/tools/open_positions.py:88–89 |
| rank_open_positions returns no margin / margin level / leverage | open_positions_rank_service.py:57–72 (SELECT list) |
| get_client_overview accounts: balance / equity / credit / group / is_cent, no margin fields; 1–50 subjects per call | backend/app/ai_agent/tools/common.py:447–484; prompt.py:308–316 |
| get_trade_activity open snapshot = count, lots, floating_pl, oldest_open_at | backend/app/services/trade_activity_service.py:252–257 |
| sid 1 MT4 live, 5 MT5, 6 MT4 live 2 | prompt.py:145 |
| Open orders: closeDate = '1970-01-01'; never CLOSE_TIME; never add openDate range; totalProfit on open order = floating | prompt.py:151–156 |
| MT5 CMD inversion affects CLOSED rows only (open rows are position side) | prompt.py:76, :120; docs/analysis/mt-night-window-abook-simulation.md:45 ("未平仓行正常") |
| Cent symbol lots ÷100 | open_positions_rank_service.py:54–55; prompt.py:77 |
| Demo filter GROUP NOT LIKE '%demo%'; employees COALESCE(isEmployee,0)=0 | prompt.py:159–161 |
| 15 s statement budget | prompt.py:171 |

## Unsourced / needs business confirmation
- TODO(business): MC and SO levels per MT4 / MT5 group. Searched docs/, .cursor/skills/, backend/app — nothing found. The skill tells the model never to state one.
- "Margin call = warning, stop-out = forced close" is general MT platform knowledge, not written in the repo. Low risk, but flag for the dealing team to confirm wording.
- Whether mt4_users.MARGIN_LEVEL is populated for MT5 (sid 5) accounts the same way as MT4: the leverage rule is described as cross-server (docs/ai-context/PROJECT_CONTEXT.md:196) and reads mt4_users, which implies yes; not explicitly verified in a doc.
- Replica sync lag of MARGIN_LEVEL: only "not strictly real-time" is documented; no figure.

## Notes for implementer
- The biggest real-use gap (6 of the 13 dealer questions in real_questions.txt) is "one-sided gold + exclude cent + margin level threshold + floating loss". Suggest extending `rank_open_positions` with: `margin_level` (and equity/credit) per account row, `exclude_cent: bool` and `side: 'long_only'|'short_only'|'any'` applied BEFORE top_n, and `margin_level_lt/gt`. Without it the model must post-filter the top 50 and use run_sql.
- The run_sql schema card lists MARGIN_LEVEL but not MARGIN / MARGIN_FREE. The skill uses `MARGIN_LEVEL > 0` as the empty-account guard (equivalent given the MT convention). Consider adding MARGIN to the card.
- Pattern D uses `mu.\`GROUP\`` (backticks, reserved word) and `SUM(t.CMD = 1)`; please confirm the run_sql guard's parser accepts both before shipping this skill.
- prompt.py says open-exposure questions should use rank_open_positions "instead of SQL"; pattern D is a run_sql exposure query justified only by the margin-level filter. If you'd rather keep the prompt rule absolute, delete section D.
