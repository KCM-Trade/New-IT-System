# leverage-abuse · 滥用杠杆 · rule_id 101-110

**Intent**: a client using almost all free margin AT THE MOMENT OF OPENING a position.

**Fires when**: an account opened a market order recently, and at the first scan at least 30 seconds
after that open its margin level (MT's own equity ÷ margin, %) is below the rule's
`max_margin_level`, with margin > 0 and equity ≥ the rule's minimum (default 100 USD, filters cent
dust).
- Evaluated only right after an open, on purpose: a margin level that sinks later because of losses
  is NOT flagged (those accounts are losing, which is not the risk this tab targets).
- Account-level: margin level covers all positions, so `symbol` is empty on these alerts.

**Settings**: three rules at margin level 200 %, 150 %, 125 % (documented configuration).
They are page settings and may have changed; the tools do not return the live levels.

**Metric**: `margin_level` % — sorted ASCENDING (lower = less free margin = stronger).
Fields: leverage, equity, margin_level, margin_used, free_margin, streak_count (always empty now),
symbol. Values are frozen at the moment of detection.

**Page filter**: a leverage multi-select (1:1000 / 1:400 / 1:200 / 1:100), default 1:1000 only —
the page may show fewer rows than the tool unless the user widened it.

**Scanned**: every 60 s, reading only opens at least 30 s old.
**Blind spot (be exact)**: a position held under 30 s is never seen; 30-90 s may or may not be seen;
90 s or longer is always seen. Do not say "anything over 60 s is caught".

**Not the same as "near stop-out now"**: this tab does not list accounts whose margin level is low
right now. See the margin-and-stopout skill if present.
