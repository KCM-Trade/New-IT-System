# Blow-up audit (IT script `backend/scripts/blowup_audit_window.py`)

Not an agent tool; on demand, run by IT for an MT time window. Doc: `docs/features/blowup-audit.md`.

## What it outputs (Excel, optionally emailed)
1. **Blown-up accounts**: accounts with at least one losing close in the window whose CURRENT balance
   is below 0 — and/or accounts with a stop-out order comment (`[so`, `so:`, `cso:`), depending on the
   audit mode. Hourly breakdown, total loss, worst balance.
2. **AB counterpart candidates**: for each losing order, an order on the same symbol, opposite
   direction, opened within ±60 s (default), lot ratio 0.5-2× (default), that made money. By default
   the counterpart must belong to the SAME CRM client (another account of the same person).
- Servers selectable (default MT5 only); demo/test excluded; cent /100.
- Times are MT server time.

## Limits
- Anchored on the BLOWN-UP account, so it cannot find news-release pairs where the winner is still
  open — that is the event AB scan.
- Balance is a current snapshot: an account that went negative and was later reset to 0 is missed.
- No IP information (trades do not store per-order IP).

## How it differs from the Risk Monitor gap-trade tab
- The gap-trade tab (rule 71) runs daily on its own for the MT 00:00-02:00 gap window and keeps
  results 30 days; the agent can read it. The blow-up audit is a one-off report for any window.
