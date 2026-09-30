# Watchlist, cases and states (已阅 ≠ 已处置)

## Vocabulary (company glossary)
- **Signal 信号** — one detector hit: what happened, no human judgement. ("alert" is fine in speech.)
- **Case 案卷** — one client's risk file: which signals fired, behaviour tags, current state, how
  often acted on. At most one case per client.
- **Action 处置动作** — an intervention done outside the system and recorded once inside it
  (e.g. ZIP slippage, rebate change, move to dealing, warning).
- **Verdict 结论** — a human's judgement on a case (real risk handled / real risk not acted on /
  false positive / exempt). "Looked at it" is not a verdict.
- States: **观察中** watching (default) · **已阅** read — someone looked and snoozed it, the case is NOT
  closed · **已处置** disposed — a verdict + action + date were recorded; the case keeps being watched
  against a new baseline, it does not leave the list · **豁免** exempt — confirmed long-term false
  positive, with a reason (the risk team's own word is 「優質代理」).
- Behaviour tag (from the detection side) ≠ CRM Tags (typed by people in the CRM) ≠ account notes.
  Never call them all "tags" in one sentence.

## What exists today
- The page `/risk-watchlist` (sidebar "Client Activity Monitor") currently shows ONE view: all
  clients, one row per client, grouped into 交易状态 (trading-status) buckets, refreshed every 60 s.
- There is no control to mark 已阅 / 已处置 / 豁免, and no "case closed" state. The planned
  disposition labels (風控中 / 已風控 / 優質代理) are design, not a live feature.
- get_risk_signals returns a watchlist case for a client when one exists; `verdict` is always null.

## Wording
- Never say a case is "closed", "resolved", "cleared" or "handled" because it was looked at.
- Say "no verdict is recorded in the system" when asked whether something was dealt with.
