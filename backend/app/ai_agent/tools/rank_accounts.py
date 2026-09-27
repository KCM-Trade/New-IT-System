"""Tool 4 — ``rank_accounts`` (docs/ai-agent/02-contracts.md §11).

The first GROUP-level certified tool: no ``subject``, the input is a metric and
a window and the output is a ranking that fans out to arbitrary clients. That
fan-out is why this tool filters its OUTPUT by the caller's scope (rows whose
cid is outside the scope are dropped and counted in ``rows_masked_by_scope``)
and takes ``top_n`` only AFTER that filter — a restricted caller must never
learn "there are 3 CN accounts above your best one" from a shorter list.

All 口径 lives in ``app.services.rank_accounts_service``; this module
validates, enforces the group-scan limits (≤ 92 days, top_n ≤ 50, min_orders
gate), runs the service under the tool timeout and wraps the envelope.
"""

from __future__ import annotations

from typing import Any, Optional

from app.services import rank_accounts_service as ras

from .common import (
    CallerCtx,
    error_envelope,
    is_error,
    ok_envelope,
    parse_date_range,
    run_sync_with_timeout,
    utc_now_iso,
)

TOOL_NAME = "rank_accounts"


def _int_arg(value: Any, name: str, *, lo: int, hi: Optional[int] = None) -> int | dict:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return error_envelope("invalid_argument", f"{name} must be an integer")
    if n < lo or (hi is not None and n > hi):
        bound = f"between {lo} and {hi}" if hi is not None else f">= {lo}"
        return error_envelope("invalid_argument", f"{name} must be {bound}", {name: n})
    return n


async def rank_accounts(
    ctx: CallerCtx,
    metric: Any,
    date_range: Any,
    top_n: Any = 10,
    min_orders: Any = ras.DEFAULT_MIN_ORDERS,
    order: Any = "desc",
    sids: Any = None,
    allow_low_min_orders: Any = False,
) -> dict:
    metric = str(metric or "").strip().lower()
    if metric not in ras.METRICS:
        return error_envelope("invalid_argument", f"metric must be one of {list(ras.METRICS)}")
    if metric not in ras.RANKABLE_METRICS:
        # In the contract's list, but without a certified source — say why
        # instead of ranking by a number that is not what it claims to be.
        return error_envelope("invalid_argument", ras.RETURN_PCT_CAVEAT, {"metric": metric})

    order = str(order or "desc").strip().lower()
    if order not in ras.ORDERS:
        return error_envelope("invalid_argument", f"order must be one of {list(ras.ORDERS)}")

    rng = parse_date_range(date_range)
    if isinstance(rng, dict):
        return rng
    if rng.days > ras.MAX_RANGE_DAYS:
        # Group scans are refused above 92 days even though a single client
        # may look at 366: the universe is every live account.
        return error_envelope(
            "range_too_wide",
            f"date_range spans {rng.days} days; group rankings are limited to {ras.MAX_RANGE_DAYS}. Narrow the range.",
            {"days": rng.days, "max_days": ras.MAX_RANGE_DAYS},
        )

    top_n_v = _int_arg(top_n, "top_n", lo=1, hi=ras.MAX_TOP_N)
    if isinstance(top_n_v, dict):
        return top_n_v
    min_orders_v = _int_arg(min_orders, "min_orders", lo=1)
    if isinstance(min_orders_v, dict):
        return min_orders_v
    if min_orders_v < ras.MIN_ORDERS_SOFT_FLOOR and not bool(allow_low_min_orders):
        return error_envelope(
            "invalid_argument",
            f"min_orders={min_orders_v} is below {ras.MIN_ORDERS_SOFT_FLOOR}: one lucky order would be a 100% win rate. "
            "Keep the default (20) unless the user EXPLICITLY asked for a lower floor; if they did, "
            "pass allow_low_min_orders=true.",
            {"min_orders": min_orders_v, "soft_floor": ras.MIN_ORDERS_SOFT_FLOOR},
        )

    sid_list: Optional[list[int]] = None
    if sids is not None:
        if not isinstance(sids, (list, tuple)) or not sids:
            return error_envelope("invalid_argument", "sids must be a non-empty list of server ids or null")
        try:
            sid_list = sorted({int(s) for s in sids})
        except (TypeError, ValueError):
            return error_envelope("invalid_argument", "sids must be integers")
        bad = [s for s in sid_list if s not in ras.LIVE_SIDS]
        if bad:
            return error_envelope(
                "invalid_argument", f"sids {bad} are not live servers; allowed: {list(ras.LIVE_SIDS)}"
            )

    # Fetch more than top_n only when a scope filter can eat rows.
    fetch_limit = top_n_v if ctx.scope is None else min(top_n_v * 3, ras.MAX_FETCH_ROWS)

    rows = await run_sync_with_timeout(
        ras.rank,
        ctx.settings,
        ctx=ctx,
        metric=metric,
        order=order,
        day_from=rng.day_from.isoformat(),
        day_to=rng.day_to.isoformat(),
        min_orders=min_orders_v,
        sids=sid_list,
        limit=fetch_limit,
    )
    if is_error(rows):
        return rows

    # ── output scope filter (02 §11) ─────────────────────────────────────────
    # `None` = unrestricted. A frozenset (even empty) = restricted: a row whose
    # cid is not in it — INCLUDING an unresolvable cid — is masked. Fail
    # closed, never "show it because we could not tell whose it is".
    masked = 0
    visible: list[dict] = []
    for row in rows:
        if ctx.scope is not None and (row["cid"] is None or row["cid"] not in ctx.scope):
            masked += 1
            continue
        visible.append(row)
    fetched = len(rows)
    visible = visible[:top_n_v]
    for i, row in enumerate(visible, start=1):
        row["rank"] = i

    data = {
        "metric": metric,
        "order": order,
        "date_range": rng.as_dict(),
        "top_n": top_n_v,
        "min_orders": min_orders_v,
        "sids": sid_list or list(ras.LIVE_SIDS),
        "rows": visible,
        "rows_returned": len(visible),
        "rows_fetched": fetched,
        "rows_masked_by_scope": masked,
    }
    caveats = [
        f"Only accounts with at least {min_orders_v} closed orders in the window are ranked (min_orders).",
        "win_rate = closed orders with PROFIT > 0 / closed orders; swap and commission are NOT part of the win test.",
        "net_profit = sum of totalProfit (PROFIT + COMMISSION + SWAPS) in USD; cent accounts and cent products "
        "(.cent / .kcmc) are already divided by 100 — XAUUSD.c is NOT a cent product.",
        "Lots are standard lots after the same cent conversion (lots /100 only for cent SYMBOLS).",
        "Demo/test accounts and employee clients are excluded; live servers sid 1/5/6 only; CMD 0/1 only.",
        "Orders are selected by their MT server close DAY (closeDate BETWEEN from AND to).",
        f"Ties are broken by more orders first, then by login_sid. Group rankings are limited to {ras.MAX_RANGE_DAYS} days.",
        ras.RETURN_PCT_CAVEAT,
    ]
    if masked:
        caveats.append(
            f"{masked} account(s) in the fetched ranking are outside the caller's data scope and were removed BEFORE "
            "taking the top_n; rows_masked_by_scope counts them. Do not infer their identity or position."
        )
    if fetched >= fetch_limit and ctx.scope is not None and len(visible) < top_n_v:
        caveats.append(
            "Fewer than top_n rows remained after the scope filter; more in-scope accounts may exist below the "
            "fetch window. Ask with a smaller top_n or a narrower range."
        )
    definition = {
        "summary": f"Live trading accounts ranked by {metric} ({order}) over the MT-day window, "
        f"{min_orders_v}+ closed orders each; SQL aggregation over mt4_trades with the certified account universe "
        "and cent rules.",
        "caveats": caveats,
        "doc": "docs/ai-agent/02-contracts.md §11; app/services/trade_activity_service.py (shared universe / cent rule)",
    }
    source = {
        "service": "app.services.rank_accounts_service",
        "function": "rank",
        "as_of": utc_now_iso(),
    }
    return ok_envelope(data, definition=definition, source=source, ctx=ctx, truncated=False)
