# News-event AB scan (IT script `backend/scripts/event_ab_scan.py`)

Not an agent tool. You cannot run it or read its output. Doc: `docs/analysis/news-event-ab-detection.md`.

## What "AB position / 对敲" means here
The same controller (or a coordinated group) uses several accounts to hold deliberately opposite
positions at nearly the same time on the same product (motives named in the company doc: collecting
rebates, washing losses, getting around rules). In the release windows scanned so far the typical
shape was one side stopped out while the opposite side closed in profit or stayed open. The typical
evidence is at the PERSON level
(same client, or the same IP placing orders). A pure server stop-out is margin enforcement, not a
choice by the client, and is not by itself evidence of intent.

## Definition used by the scan (v2)
- Window: from 15 min BEFORE to 60 min AFTER the release, on order OPEN time (asymmetric, because
  some pairs are opened minutes before the release and winners may be held long after).
- Pair shape: exact same symbol (XAUUSD does not pair with XAUUSD.cent), opposite direction,
  lot ratio min/max > 0.8, open times ≤ 5 s apart. Close time is NOT a condition.
- Who can pair:
  - same account (both sides in one account) — kept for the record only, shown in a small table;
  - same client, two different accounts — reported as a case;
  - two different clients — only if they share a LOGIN IP on the same day within an 8-day look-back;
    an IP used by ≤ 5 accounts that day is marked "strong", otherwise "weak".
- Pairs are grouped into cases (client + symbol + account pair) with each ticket counted once.
- Demo/test excluded; servers sid 1 / 5 / 6. Cent amounts /100.

## Schedule
- Since 2026-09-29 the scan runs automatically each morning (09:00 HK) for the PREVIOUS MT day's
  enabled events in a schedule file, and emails the risk team (the login-IP files it needs are only
  ready the next day). Events are enabled month by month by decision; which months are enabled
  is IT's schedule — for a specific event, tell the user to ask IT.
- Ad-hoc scans for another event: ask IT with the release's MT time.

## Findings that explain its design (safe to quote, no client details)
- Without an identity link, the raw four conditions at one NFP release gave ~1.4 million order pairs
  — that is why same-client or IP-link is required.
- Tightening the lot ratio (0.8 → 0.95) barely changes results; the sensitive setting is the open gap.

## Known limits
- Two different CRM clients controlled by one person are missed unless they share a login IP within
  the look-back.
- Account balance used is the CURRENT balance, not the balance at the event.
