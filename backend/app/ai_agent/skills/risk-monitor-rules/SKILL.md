---
name: risk-monitor-rules
description: Risk Monitor (Risk Rule Alerts) tabs and rule bands — 批量下单 burst-open, 快开快平, 快速获利, Gap Trade 缺口/SO+AB/爆仓对手, 对冲刷单 hedge, 滥用杠杆 leverage, 马丁 martingale, 即日高收益 intraday return. Use for "点解会触发/why did it fire", "呢个 tab 睇咩", rule_id 71/81/131, thresholds, scan timing, 30-day retention, 已阅/已处置 wording.
---

# Risk Monitor rules (Risk Rule Alerts / 交易实时监控)

## When to use
- The user names a Risk Monitor tab, a rule id, or pastes a `…/risk-monitor?tab=…` link.
- "Why did account X get flagged / 点解会有告警 / 觸發咗乜", "what does 快开快平 mean",
  "how often is it scanned", "how long are alerts kept", "is this case closed".
- Only meaningful when get_risk_alerts / get_alert_orders are in your tool list. If they are not,
  say the question needs the Risk Control module permission (需要 Risk control 模块权限) and stop —
  do not rebuild a tab with other tools (same rule as the system prompt).

## Key facts
- Purpose: the page flags clients whose trading is a risk to the company's B-book P&L. It is not
  about protecting clients from blowing up. Every hit is a signal (信号/告警/命中), not a verdict.
- Data: every tab reads alerts the detectors ALREADY wrote. Nothing is recomputed when you ask.
  Alerts are kept 30 days, then deleted. Older alerts no longer exist anywhere the tools can see.
- Servers: MT4 Live (sid 1), MT5 (sid 5), MT4 Live2 (sid 6). Demo/test accounts are excluded.
  CEN (cent) money and lots are already converted to USD / standard lots at detection time.
- No severity levels. All hits are equal; the only ordering is each band's main metric.
- Thresholds are per-rule settings the risk team edits on the page (rule-config drawer), up to 10
  rules per tab. The tools do NOT return the live settings. The defaults in `references/` are the
  schema defaults and can differ from what is live (hedge-open is known to run 30 s instead of the
  3 s default). For the live value, point the user to the tab's rule settings.

## The bands (one line each — details in references/)

| tab (`?tab=`) | 中文 | rule_id | detects | main metric | scanned |
|---|---|---|---|---|---|
| burst-open | 批量下单 | 1-50 | many large orders on one account+symbol within a few seconds (buy and sell both count) | order_count | every 60 s |
| quick-open-close | 快开快平 | 51-60 | several closed trades held ≤ N seconds whose summed P/L passes a floor | shortest hold (lower = stronger) | slow cycle |
| quick-profit | 快速获利 | 61-70 | profit (closed, optionally + floating) in a sliding lookback window ≥ threshold | total_profit_usd | slow cycle |
| gap-trade | Gap Trade | 71-80 | stop-out loser + opposite counterpart account (SO+AB pair) in the MT 00:00-02:00 gap window | net_usd of the pair | once a day |
| gap-trade | Gap Trade | 81-90 | client profit from positions held through the daily gap, vs net deposit | total_profit_usd | once a day (+ early-morning pass) |
| hedge-open | 对冲刷单 | 91-100 | same account + symbol opens buy AND sell of exactly equal lots within a short window | total_lots | slow cycle |
| leverage-abuse | 滥用杠杆 | 101-110 | margin level right after a new open is below the rule's level | margin_level (lower = stronger) | every 60 s |
| martingale | 马丁策略 | 111-120 | a new same-direction add on a symbol whose open positions are in floating loss, add ≥ anchor lots × multiplier | lot_ratio_mg | every 60 s |
| intraday-return | 即日高收益 | 131-140 | intraday return % on the MT trading day ≥ tier (seed tiers 100 % / 300 %) | peak_return_pct | every 5 min |

- 121-130 (返佣套利 rebate arbitrage) is retired: no data, no tab. Do not query it.
- "slow cycle" = the page's scan interval setting (5-60 min, default 10). The tools do not return
  the interval set in production — for the live value point the user to the page.
- gap-trade is ONE tab with TWO bands whose metrics differ. See references/gap-trade.md.

Per-band reference files (read only the one you need):
`references/burst-open.md` · `references/quick-open-close.md` · `references/quick-profit.md` ·
`references/gap-trade.md` · `references/hedge-open.md` · `references/leverage-abuse.md` ·
`references/martingale.md` · `references/intraday-return.md` · `references/watchlist-and-states.md`
Trading-style features, window scan and the IT scripts: `references/style-features.md` ·
`references/window-scan-howto.md` · `references/event-ab-scan.md` · `references/blowup-audit.md`

## How to answer with the tools
- "Who did tab X fire on (today / this week)":
  `get_risk_alerts(tab="<tab>", date_range={from,to}, group_by="client")`. Range is MT server days,
  at most 31, and nothing older than 30 days exists.
- "Why did it fire / show me the numbers": `get_risk_alerts(..., group_by="alert")` and quote that
  band's figures (the per-band files list which fields exist). Then, if needed,
  `get_alert_orders(alert_ids=[…≤3])` for the orders and descriptive features.
- "Biggest N accounts on tab X": `group_by="account", top_n=N, sort="metric"`; say what the metric
  is (`data.metric.label`). For gap-trade without a named band call twice
  (rule_ids 71-80 and 81-90), as the system prompt says.
- "Anything on client Y": `get_risk_alerts(client_ids=[…])`, or `get_risk_signals` for one client.
- Counting: `alerts` counts FIRINGS. The same account fires again on later scans (burst-open and
  leverage-abuse most of all). Write "N alerts (M accounts)" with M from `data.accounts_in_rows`.
- Time column: intraday-return rows belong to an MT trading day (`trading_day`). Every other tab is
  filtered by scan time. Quote the tool's own time caveat (get_risk_alerts `definition.caveats`)
  for when a tab is scanned; do not compute your own scan time or offset.

## What you cannot do today (say so plainly)
- Read or change a rule's live thresholds, enable/disable rules, press "立即扫描" (scan now), or
  re-run a scan for an older window.
- See alerts older than 30 days, or anything for band 121-130.
- See the name, IP list or CRM remarks stored with an alert (they are withheld from the tools).
- Mark a case 已阅 or 已处置: there is no such control in the system today (see
  references/watchlist-and-states.md).
- Prove an AB (opposite-account) pair from one account's orders. Only gap-trade rule 71 stores the
  counterpart leg (`get_alert_orders` returns the L and C legs).
- Tell whether a rule 81 client was auto-tagged in the CRM (禁止出金(風控) / Withdrawal Notice): the
  tools do not read the tagging log. Point to CRM or IT.

## Wording rules
- Signals, not verdicts. Say "rule 81 fired: gap-window profit 1,234.00 USD, 1.4× net deposit"
  — never "this client is a gap trader / wash trader / martingale player". Never use fraudster,
  abuser, violator, cheater, scammer, 作弊, 欺诈, 违规者, 套利者.
- Name the band's metric and its direction (lower margin level = stronger; higher lot ratio =
  stronger).
- intraday-return: the tier that fired is `peak_return_pct`. `return_pct` is the latest tick and can
  now be below the tier. Say both when they differ.
- A hit does not mean the client made money (quick-open-close and gap SO+AB can include losing legs;
  martingale fires on floating LOSS).
- 已阅 (read) is not 已处置 (disposed). There is no "case closed" state.
- Use the tab's Chinese name the user used; the page title in the sidebar is "Risk Rule Alerts".
