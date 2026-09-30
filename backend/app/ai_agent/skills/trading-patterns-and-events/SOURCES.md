# SOURCES — trading-patterns-and-events (not shown to the model)

## Port notes (OPT-0069, 2026-09-30)
This file is NEVER shown to the model (harness resource_filter; pinned by tests/test_ai_agent_skills.py).
File:line references below point at the pre-port tree and may have drifted.
- Cold review 2026-09-30: all Risk-control-tool content moved to risk-monitor-rules (references/style-features.md, window-scan-howto.md, event-ab-scan.md, blowup-audit.md); this skill keeps only what every caller's tools can answer; IT-script detection settings no longer described here.
- Event-AB month-enable TODO replaced by "ask IT".
- Rows naming Risk control tools are marked conditional on those tools being registered.


"NE" = `docs/analysis/news-event-ab-detection.md`, "BA" = `docs/features/blowup-audit.md`,
"BAS" = `.cursor/skills/blowup-audit/SKILL.md`.

## SKILL.md

| Claim | Source |
|---|---|
| Features describe, not labels; wording "consistent with" | `backend/app/ai_agent/prompt.py:227-230`; `tools/alert_orders.py:571-573` |
| Forbidden words | prompt.py:51-54, 181-183 |
| same_second_open_groups / orders_in_same_second_groups | `backend/app/ai_agent/tools/alert_orders.py:216-217,239-240` |
| burst-open = many large orders in seconds | `docs/features/risk-monitor.md:115-119` |
| hold buckets and flags | `backend/app/services/trade_activity_service.py:52-63`; `tools/trade_activity.py:86-97`; prompt.py:78 |
| median_hold_sec, pct_hold_lt_60s | `tools/alert_orders.py:64,235-236` |
| martingale fields, lot_escalation_steps | `tools/risk_bands.py:120-127`; `tools/alert_orders.py:65,218-220,241-256` |
| opposite_side_overlap_pct, lock_pct, net vs gross lots | `tools/alert_orders.py:221-222,258-267`; `tools/risk_bands.py:133-143`; prompt.py:96-102 |
| Only rule 71 stores counterpart; L and C legs | prompt.py:231-232,260 |
| Shared order-IP peers in get_risk_signals, masked | prompt.py:326-329; `docs/features/ai-assistant.md` §1 table (get_risk_signals row, "IP 掩 /24") |
| 08:30 ET = 15:30 MT all year, FOMC 21:00 MT all year | NE:244-246; BAS §"The other line" (`--event-mt` bullet) |
| HK = MT+5 summer / +6 winter; US DST, 1st Sunday Nov | `docs/features/window-scan.md:73`; prompt.py:62-63 |
| get_economic_calendar upcoming only, days_ahead 1-60, importance | `tools/economic_calendar.py:27-28,89-91`; prompt.py:292-298 |
| time_mt first, source_url, fred_api_key_missing | prompt.py:103-105; `tools/economic_calendar.py:136-139` |
| PCE not always last Friday; don't infer dates | NE:247-248 |
| get_window_scan args | prompt.py:262-269; `tools/window_scan.py:98-144` |
| event_ab_scan window −15/+60, pairs | NE:25-45 |
| results emailed to risk team with CSV | NE:232-243; BAS §"The other line" |
| blowup audit definition, Excel/email | BA:9-17,62-72; BAS "Business Context" |
| agent cannot run scripts; point to docs | prompt.py:233-235; `docs/ai-agent/11-slice3-risk-control.md:62-63` ("脚本不 agent 化") |
| order source/comment/magic not in tools | inferred from tool outputs: `tools/trade_activity.py`, `tools/alert_orders.py` (no such fields); `docs/ai-agent/11-slice3-risk-control.md:174` (column list) |
| ~1.4 million pairs at one NFP window | NE:62-67 |
| Stop-out is not evidence of intent | `docs/features/login-ip.md:120-137` |

## references/
| Claim | Source |
|---|---|
| Flag thresholds (90 %/5; 30 % 00-02 MT, median <30 min, ≥10; 50 % <30 min, ≥10) | `backend/app/services/trade_activity_service.py:59-63` |
| XAUUSD.c not cent; cent lots+money /100 | `tools/trade_activity.py:89-92`; prompt.py:77 |
| open_positions snapshot, open orders not in totals | `tools/trade_activity.py:87-88,94` |
| alert_orders feature definitions | `tools/alert_orders.py:212-274` |
| Times in tool output UTC | prompt.py:256 ("UTC times") |
| rank_open_positions fields, locked client | prompt.py:96-102,281-291 |
| intraday lock_pct meaning | `.cursor/skills/risk-monitor/references/rules-catalog.md:232`; `.cursor/skills/risk-monitor/references/data-model.md:166` |
| VPN exit shared by 26 accounts; mobile IP rotates daily | NE:351-352 |
| window-scan single moment vs hold-bucket trend | `docs/features/window-scan.md:17-60` |
| window-scan rules (client-level closed sum > 0, floating excluded, close-tab empty cols) | `docs/features/window-scan.md:62-110`; `tools/window_scan.py:217-235` |
| symbol is a prefix, include_trades ≤5, sorts, truncated | prompt.py:262-269; `tools/window_scan.py:229-243` |
| FOMC 21:00 MT = 02:00 HK next day (summer) | BAS (`FOMC 02:00 HK（次日）→ 前一日 21:00 MT`); NE:185-188 (3C header) |
| AB definition + motives + person-level evidence | `docs/features/login-ip.md:122` |
| Observed shape: one side SO, other profit/open | NE:81-92 (§3.1 A1-A3), NE:37-51 (§2.1 rationale 1) |
| v2: same-account down-weighted; same-client case; cross-client needs login-IP link, 8 days, ≤5 strong | NE:25-31; BAS "口径 v2" bullet |
| exact symbol, opposite, lot ratio > 0.8, gap ≤ 5 s, close time not a condition | NE:33-51 |
| Aggregate into cases, dedupe tickets | NE:295-298 (§4 Step 3); BAS five-things #3 |
| Schedule 09:00 HK, previous MT day, IP files next day, month-by-month, October enabled | NE:232-243 |
| Lot ratio not the knob; gap is | NE:120-121 |
| Blind spot different clients one person; current balance | NE:306-316,364 |
| blowup: BALANCE<0 and/or SO comment, ±60 s, 0.5-2×, same client default, default sid 5, Excel | BA:9-17,42-60,62-72; BAS "Business Context" |
| blowup limits (anchored on blown account; reset balance; no IP) | BA:18-26,143,33 |

## Unsourced / needs business confirmation
- TODO(business): which months after October 2026 are enabled in the event AB schedule.
- The Hong Kong "09:00" run time and "previous MT day" are from NE §4.0 only; not re-verified against
  the crontab (host crontab is outside the repo).

## Notes for implementer
1. `docs/features/blowup-audit.md:18-22` and NE header (line 5-6) still say the news-event line is
   "代码尚未落地"; the script landed 2026-09-17 (NE:25, BAS). Docs are stale, skill follows the script.
2. NE:369-371 says the MT day boundary anchors on `Europe/Athens`; CLAUDE.md and prompt.py:62-63 say
   US DST calendar (not Athens). Skill uses prompt.py's US-DST wording.
3. `BA:35-40` says times are MT UTC+3 and "HKT = MT + 5h" (fixed). Correct only in summer; skill uses
   the DST-aware conversion.
4. Consider adding a `past_events` mode or a static list to get_economic_calendar: users ask about past
   releases ("who traded at last NFP") and the tool only looks forward.
