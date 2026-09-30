---
name: trading-patterns-and-events
description: Describe a client's trading style from data without verdicts — EA/机械人/程式单/同秒下单, scalping 剥头皮/短线/持仓时间, 马丁/加仓/越亏越加, 锁仓/对冲/hedging, AB 仓/对敲/对手账户, gap/缺口, news windows NFP 非农/CPI/FOMC 议息/数据公布 and their MT/HK times, get_economic_calendar, who runs the AB scan and blow-up audit.
---

# Trading patterns and news events

## When to use
- "Is he an EA / 係咪机械人 / 程式交易", "is this scalping / 剥头皮", "is he doing martingale /
  马丁", "is he locking / 锁仓", "is this an AB pair / 对敲", "who traded around NFP / CPI / FOMC",
  "when is the next NFP", "did anyone trade both sides at the release".
- Also when the user asks for an "AB scan" or "爆仓审计 / blow-up audit" — those are IT scripts.

## Key rule: describe features, never label the person
The tools return FEATURES (counts, ratios, hold times, overlaps). A style name is the analyst's
conclusion, not yours. Pattern for every answer:
> "<feature> = <value> (<tool>) — this is consistent with <style>-type trading; it does not by itself
> show <style>. What would confirm it: <what the analyst could check>."
Never: "he is an EA trader / scalper / martingale player / runs an AB pair". Never use fraudster,
abuser, violator, cheater, scammer, 作弊, 欺诈, 违规者, 套利者.

## Features you can quote, by style
Details and definitions: `references/feature-glossary.md`. Summary (tools every caller has):

| Style (user words) | Observable features | Tool |
|---|---|---|
| EA / robot / 同秒下单 | very high order counts, many orders per day | get_trade_activity totals, group_by="day" |
| Scalping / 短线 / 剥头皮 | hold buckets <30min / 30min-2h / >2h; flags `short_hold_dominant`, `night_window_scalping` | get_trade_activity(group_by="hold_bucket") |
| Hedging / locking / 锁仓 | current buy_lots vs sell_lots (net ≈ 0 = fully locked) | rank_open_positions; get_trade_activity `open_positions` |
| AB / opposite accounts / 对敲 | shared-order-IP peers (a link to investigate, not proof) | get_risk_signals |
| Single-symbol focus | flag `single_symbol_concentration`; group_by="symbol" | get_trade_activity |
| Detector alerts already recorded for ONE client | alert counts by rule | get_risk_signals |

Alert-level features (same-second groups, lot escalation, overlap %, stored AB pairs, who traded
around a release) come from the Risk control tools. If `get_risk_alerts` / `get_alert_orders` /
`get_window_scan` are in your tool list, load the `risk-monitor-rules` skill
(`references/style-features.md`). If they are not, those questions need the Risk control module
permission (需要 Risk control 模块权限) — say so; do not describe how a detector decides.

## News events: times and tools
- MT server time follows the US DST calendar, so US 08:30 ET releases (NFP, CPI, PCE, GDP …) are
  **15:30 MT all year**, and the FOMC decision is **21:00 MT all year**. Hong Kong = MT + 5 h in
  summer, MT + 6 h in winter (US DST ends the 1st Sunday of November).
- `get_economic_calendar(days_ahead 1-60, importance "high"|"all")` lists UPCOMING US releases only
  (from today forward). Quote `time_mt` first, then `time_hk`, and give `source_url`. If
  `definition.caveats` has `fred_api_key_missing`, say only FOMC dates are available now.
- It cannot give PAST release dates. For a past event, ask the user for the date (or the MT/HK time)
  — do not guess dates by rule (e.g. PCE is not always the last Friday).
- "Who traded around the release" is the Trade Window Scan (Risk control module): see above.

## The two IT scripts (not agent tools)
- A **news-event AB scan** and a **blow-up audit** exist as scripts IT runs; results go to the risk
  team. You cannot run either or read their results. For a scan of a specific event, tell the user to
  ask IT / the risk team and give the event's MT time. Do not describe their detection settings.

## What you cannot do today (say so plainly)
- Confirm an order came from an EA: order comments, magic numbers and the order's source
  (mobile / desktop / EA) are not in any tool.
- Pair orders ACROSS accounts or clients yourself: tools work on one subject or on stored alerts.
- Look up past release dates, or non-US releases.
- See per-order IPs; get_risk_signals gives shared-order-IP peers with IPs masked.

## Wording rules
- "Consistent with", "the orders show", "the feature is" — not "is", "proves", "caught".
- Always give the number and the tool, e.g. "62 % of 140 closed orders held under 30 min
  (get_trade_activity)".
- A pattern that lost money is still a pattern; a pattern that made money is still not a verdict.
- Stop-outs are server actions, not the client's choice: a stop-out on one side is not evidence of
  intent to pair.
