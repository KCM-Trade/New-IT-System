"""Tool 2 — ``get_trade_activity`` (docs/ai-agent/02-contracts.md §3.2).

How the client trades: closed-order totals over the MT-day window grouped by
symbol / day / hold bucket, plus a this-instant snapshot of open positions and
fact-only flags. The query and every 口径 decision live in
``app.services.trade_activity_service``; this module validates, resolves the
subject, runs the service under the tool timeout and wraps the envelope.
"""

from __future__ import annotations

from typing import Any

from app.services import trade_activity_service as tas

from .common import (
    CallerCtx,
    ResolvedSubject,
    connect_mysql,
    error_envelope,
    is_error,
    mt_local_to_utc_iso,
    ok_envelope,
    parse_date_range,
    parse_subject,
    resolve_subject,
    run_sync_with_timeout,
    utc_now_iso,
)

TOOL_NAME = "get_trade_activity"


async def get_trade_activity(ctx: CallerCtx, subject: Any, date_range: Any, group_by: Any = "symbol") -> dict:
    subj = parse_subject(subject)
    if isinstance(subj, dict):
        return subj
    rng = parse_date_range(date_range)
    if isinstance(rng, dict):
        return rng
    group_by = str(group_by or "symbol").strip().lower()
    if group_by not in tas.GROUP_BY_VALUES:
        return error_envelope("invalid_argument", f"group_by must be one of {list(tas.GROUP_BY_VALUES)}")

    resolved = await resolve_subject(ctx, subj, tool=TOOL_NAME)
    if isinstance(resolved, dict):
        return resolved
    assert isinstance(resolved, ResolvedSubject)

    # A login_sid subject narrows the activity to that ONE account; a client_id
    # subject covers every compliant account the client holds.
    login_sids = [subj.value] if subj.kind == "login_sid" else resolved.login_sids
    if subj.kind == "login_sid" and subj.value not in resolved.login_sids:
        return error_envelope(
            "subject_excluded",
            f"{subj.label} is a demo/test account or not a live trading account (sid 1/5/6).",
            {"client_id": resolved.client_id},
        )

    result = await run_sync_with_timeout(
        tas.by_subject,
        ctx.settings,
        ctx=ctx,
        login_sids=login_sids,
        date_from=rng.day_from.isoformat(),
        date_to=rng.day_to.isoformat(),
        group_by=group_by,
        connect=connect_mysql,
    )
    if is_error(result):
        return result

    open_pos = dict(result["open_positions"])
    open_pos["oldest_open_at"] = mt_local_to_utc_iso(open_pos.get("oldest_open_at"))

    data = {
        "client_id": resolved.client_id,
        "login_sids": login_sids,
        "date_range": rng.as_dict(),
        "group_by": group_by,
        "totals": result["totals"],
        "rows": result["rows"],
        "open_positions": open_pos,
        "flags": result["flags"],
    }
    caveats = [
        "Closed orders are selected by their MT server close DAY (closeDate BETWEEN from AND to); "
        "an order opened inside the range but still open is NOT in totals — it is in open_positions.",
        "sid=5 (MT5) closed rows store the exit side in CMD; direction has been normalised to the position side.",
        "Cent products (.cent / .kcmc) have lots AND money divided by 100; CEN accounts have money divided by 100. "
        "XAUUSD.c is NOT a cent product.",
        "Lots are standard lots after that conversion. net_profit = PROFIT + COMMISSION + SWAPS (mt4_trades.totalProfit).",
        "Demo/test accounts and employee clients are excluded.",
        "`open_positions` is a snapshot at call time and ignores date_range.",
        "Hold buckets: <30min = [0,1800s), 30min-2h = [1800,7200s), >2h = [7200s,∞).",
        "flags are facts, not conclusions: " + "; ".join(f"{k} = {v}" for k, v in tas.FLAG_RULES.items()) + ".",
    ]
    if result.get("rows_truncated"):
        caveats.append(
            f"More than {tas.MAX_TRADE_ROWS} closed orders matched; totals cover only the first "
            f"{tas.MAX_TRADE_ROWS} by close time. Narrow the range for exact figures."
        )
    definition = {
        "summary": "Closed-order activity over the MT-day window (orders, lots, gross/net profit, win rate, hold "
        "times) grouped by " + group_by + ", plus the current open-position snapshot and fact-only flags.",
        "caveats": caveats,
        "doc": ".cursor/skills/kcm-risk-pipeline/SKILL.md (open-position / demo 口径); docs/features/window-scan.md (buckets, direction)",
    }
    source = {
        "service": "app.services.trade_activity_service",
        "function": "by_subject",
        "as_of": utc_now_iso(),
    }
    return ok_envelope(data, definition=definition, source=source, ctx=ctx, truncated=bool(result["truncated"]))
