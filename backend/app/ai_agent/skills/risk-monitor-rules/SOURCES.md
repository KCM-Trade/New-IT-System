# SOURCES — risk-monitor-rules (not shown to the model)

## Port notes (OPT-0069, 2026-09-30)
This file is NEVER shown to the model (harness resource_filter; pinned by tests/test_ai_agent_skills.py).
File:line references below point at the pre-port tree and may have drifted.
- Cold review 2026-09-30: received style-features.md (split from trading-patterns feature-glossary), window-scan-howto.md, event-ab-scan.md, blowup-audit.md.
- Scan-interval, live-tier and leverage-level TODOs replaced by "page settings the tools do not return".
- gap-trade scan time: every concrete time (05:20 / 07:20 HKT, the early-pass HK window) removed; the skill tells the model to quote the get_risk_alerts caveat. That fact is owned by OPT-0072 (DST fix), not this skill.
- Tag-review TODO replaced by "refer to the risk team".


Paths are repo-relative. "RC" = `.cursor/skills/risk-monitor/references/rules-catalog.md`,
"DM" = `.cursor/skills/risk-monitor/references/data-model.md`, "RM" = `docs/features/risk-monitor.md`.

## SKILL.md

| Claim | Source |
|---|---|
| Needs Risk control tools; otherwise say 需要 Risk control 模块权限 | `backend/app/ai_agent/prompt.py:20-24` |
| Purpose = B-book risk, not protecting clients | RM:17-19; `.cursor/skills/risk-monitor/SKILL.md:18-22` |
| Tabs read stored alerts, nothing recomputed | `docs/ai-agent/11-slice3-risk-control.md` §1.1 (line 27); prompt.py:191 |
| Retention 30 days | `backend/app/core/risk_monitor_db.py:183-186`; prompt.py:191; `tools/risk_alerts.py:542` |
| Servers sid 1/5/6 | RM:23-29 |
| Cent converted at detection | `tools/risk_alerts.py:551`; `.cursor/skills/risk-monitor/SKILL.md:157-168` |
| No severity | RC:13; RM:374; RC:168 |
| Thresholds per rule, ≤10 rules, config drawer | DM:60-166 |
| Hedge prod window 30s vs default 3 | DM:110,122 |
| Band table rule ranges / tab keys / 中文 | `backend/app/ai_agent/tools/risk_bands.py:26-60`; prompt.py:195-205; `frontend/src/pages/RiskMonitor.tsx:1413-1437` |
| Main metrics | `tools/risk_bands.py:75-145` |
| burst every 60s; leverage+martingale fast tier 60s | `.cursor/skills/risk-monitor/SKILL.md:44-51,121` (prod `BURST_FAST_TIER_ENABLED` true) |
| QOC/QP/hedge slow tier, scan_interval 5-60 default 10 | `.cursor/skills/risk-monitor/SKILL.md:39-42`; DM:64 |
| intraday every 5 min | `.cursor/skills/risk-monitor/SKILL.md:55-60,131` |
| gap once a day + intraday pass | `backend/app/core/burst_open_scheduler.py:1408-1457` |
| intraday seed tiers 100/300 | RM:295; RC:231 |
| 121-130 retired | `tools/risk_bands.py:128-132`; prompt.py:205 |
| Tool usage recipes (group_by, sort, two calls for gap) | prompt.py:207-225 |
| "N alerts (M accounts)", accounts_in_rows | prompt.py:236-238; `tools/risk_alerts.py:543-544` |
| intraday filtered by trading_day, others by scan time | `tools/risk_bands.py:62-65`; `tools/risk_alerts.py:545-547` |
| Names / IPs / remarks withheld from tools | `tools/risk_bands.py:71-74` |
| No 已阅/已处置 control exists | grep of `frontend/src` for 已阅 → no hits (2026-09-30); `docs/features/risk-watchlist.md:33-37,64` |
| Only rule 71 stores counterpart; get_alert_orders returns L and C | prompt.py:231-232; `tools/prompt.py` TOOL_DOCSTRINGS get_alert_orders |
| CRM tag 禁止出金(風控) / Withdrawal Notice | RM:187; RC:311 |
| Forbidden words | prompt.py:181-183, 51-54 |
| peak_return_pct vs return_pct | `tools/risk_bands.py:133-143`; prompt.py:204 |
| Sidebar title "Risk Rule Alerts" | `frontend/src/i18n/locales/en-US.ts:63`, `zh-CN.ts:65` |

## references/
| File / claim | Source |
|---|---|
| burst-open trigger, both directions, sliding window | RM:115-127; RC:7-17 |
| burst defaults 3s / 3 / 5.0 lots, cent lots /100 | DM:64-70; `.cursor/skills/risk-monitor/SKILL.md:157-163` |
| equity_per_lot display only | RM:125; RC:15 |
| same_second_open_groups | `tools/alert_orders.py:216-217,239` |
| QOC trigger + defaults | RM:129-135; RC:23-30; DM:75-86 |
| QP trigger, lookback, defaults, position_status, floating refresh, dedup | RM:137-153; RC:36-89; DM:88-98 |
| Gap window MT 00-02, daily pause 00-01 + weekends, gold opens 01:00 | RM:155-170; RC:239-249,295 |
| Gap scan Mon-Sat 07:20 HKT, Sunday skipped | `backend/app/core/burst_open_scheduler.py:1408-1431`; RM:159-161 |
| Rule 71 L/C definition, ±300s, 0.5-2×, same-client OR same-group | RM:167; RC:257-262 |
| min_l_loss_usd 100 + IP bypass | RM:174; RC:266-269 |
| Rule 71 fields | `tools/risk_bands.py:91-98` |
| legs_masked_by_scope | `tools/alert_orders.py:590-591` |
| Rule 81 open/close bands, Monday→Friday | RM:170; RC:288-298 |
| Rule 81 thresholds 100 USD, OR of two rules, drawer disabled | `backend/app/services/rule_gap_trade_gap_service.py:68`; RC:276-287 |
| Rule 81 net deposit includes 'ib withdrawal' | DM:24-30,56-59 |
| Intraday CRM tagging pass, 10 per pass, withdrawal→CS review | RM:180-194; RC:306-316 |
| Hedge trigger, EPS 0.01, one account only, splitting, aggregate view | RM:196-236; RC:101-133; DM:102-128 |
| Hedge motive wording (rebate/volume) | RM:198 |
| Leverage event-gated, 30s settle, margin>0, min equity 100 | RC:135-157; RM:315-326 |
| Leverage 200/150/125 | RC:142; RM:317 |
| Leverage page filter default 1:1000 | RC:170 |
| Blind-spot <30 / 30-90 / ≥90 | RC:159-166 |
| Leverage fields, streak_count empty | `tools/risk_bands.py:112-119`; RC:154 |
| Martingale three conditions, anchor re-anchor, largest add | RC:172-190; RM:333-341 |
| lot_escalation_steps ≥1.5× | `tools/alert_orders.py:65,218-220` |
| Intraday formula v3, blacklist, credit, 50% flag | RM:281-301; RC:198-221 |
| Intraday thresholds 50/30/7d≥0, optional lock/hold | RC:215-216; DM:150-166 |
| Tier suppression incl. fall-back | RC:231 |
| lock_pct definition | RC:232; DM:166 |
| Intraday emailed to risk team | RM:297-299 |
| Glossary terms + states | `docs/ai-context/CONTEXT.md:21-85` |
| /risk-watchlist single all-clients view, 60s refresh | `docs/features/risk-watchlist.md:52-63` |
| 風控中/已風控/優質代理 planned | `docs/features/risk-watchlist.md:31-37` |
| Sidebar "Client Activity Monitor" | `frontend/src/i18n/locales/en-US.ts:64` |

## Unsourced / needs business confirmation
- TODO(business): production value of the slow-cycle scan interval (`scan_interval_min`, default 10).
- TODO(business): are leverage-abuse levels still 200/150/125 in production?
- TODO(business): are intraday-return live tiers still 100 % / 300 %?
- TODO(business): who reviews/removes the rule-81 CRM withdrawal tags and on what criteria?
- Live thresholds for burst / QOC / QP / hedge / martingale are not documented beyond schema defaults
  (hedge window 30 s is the only documented production value).

## Notes for implementer
1. **Gap-trade scan time conflict.** `prompt.py:216-217` and `tools/risk_alerts.py:545-547` say
   "scanned the NEXT day (05:20 HKT) for the previous MT day … scanned_at is one day after
   window_date". The scheduler (`backend/app/core/burst_open_scheduler.py:1408-1431`) runs
   Mon–Sat **07:20 HKT** and scans the **current** MT day's 00:00-02:00 window
   (`window_day = now_mt` at :1133). Stale "05:20 / Tue-Sat" comments remain in
   `backend/app/api/v1/routes/risk_monitor.py:1529,1567,1657`. Also RM:159 and RC:239 say 07:20.
   The skill tells the model to quote the tool caveat and not compute offsets; please fix prompt.py /
   the caveat (and check whether the Monday "extend back" advice still makes sense).
2. **Winter offset of the 07:20 HKT cron (possible bug, not in skill).** The code comment equates
   07:20 HKT with MT 02:20 — true only in summer (MT = UTC+3). In winter MT = UTC+2, so 07:20 HKT =
   MT 01:20, i.e. BEFORE the 00:00-02:00 window closes. Worth checking before 2026-11-01.
3. **Rule 81 gate conflict in docs.** RM:168 says `$1,000`; code `HARDCODED_PROFIT_GATE_USD = 100.0`
   and RC:283-284 (changed 2026-07-08) say 100. Skill uses 100. RM should be updated.
4. **Stale martingale note.** RC:194 says the martingale tab is hidden (`MARTINGALE_TAB_VISIBLE=false`);
   `RiskMonitor.tsx:1413-1422` now lists `martingale` as a visible tab. Skill treats it as visible.
5. **Page time display.** `RiskMonitor.tsx:828-833` renders all times as fixed `Etc/GMT-3`
   ("MT 时间 · UTC+3"). After the US DST switch (1st Sunday of November) the real MT clock is UTC+2,
   so page times will read 1 h later than the MT clock and than DST-aware tool output. Not put in
   the skill (unverified in winter); consider a caveat.
6. RM:186 says the gap intraday tier does not write `alert_events`; RC:310 says it writes once per
   client per trading day. Skill avoids the question.
