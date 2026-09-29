"""Tool — ``rank_open_positions``: who holds what right now, for one symbol.

Group-level like ``rank_accounts``: no subject, the output fans out to
arbitrary clients, so rows outside the caller's scope are removed BEFORE
``top_n`` and counted in ``rows_masked_by_scope``, and ``totals`` is computed
over the visible rows only. All 口径 lives in
``app.services.open_positions_rank_service``.
"""

from __future__ import annotations

from typing import Any, Optional

from app.services import open_positions_rank_service as ops

from .common import (
    CallerCtx,
    error_envelope,
    is_error,
    mt_local_to_utc_iso,
    ok_envelope,
    run_sync_with_timeout,
    utc_now_iso,
)

TOOL_NAME = "rank_open_positions"


async def rank_open_positions(
    ctx: CallerCtx,
    symbol: Any,
    symbol_match: Any = "family",
    group_by: Any = "client",
    sort: Any = "net_lots",
    top_n: Any = 20,
    sids: Any = None,
) -> dict:
    symbol = str(symbol or "").strip()
    if not ops.SYMBOL_RE.match(symbol):
        return error_envelope("invalid_argument", "symbol must be one trading symbol, e.g. 'XAUUSD'", {"symbol": symbol})
    symbol_match = str(symbol_match or "family").strip().lower()
    if symbol_match not in ops.SYMBOL_MATCH_VALUES:
        return error_envelope("invalid_argument", f"symbol_match must be one of {list(ops.SYMBOL_MATCH_VALUES)}")
    group_by = str(group_by or "client").strip().lower()
    if group_by not in ops.GROUP_BY_VALUES:
        return error_envelope("invalid_argument", f"group_by must be one of {list(ops.GROUP_BY_VALUES)}")
    sort = str(sort or "net_lots").strip().lower()
    if sort not in ops.SORT_VALUES:
        return error_envelope("invalid_argument", f"sort must be one of {list(ops.SORT_VALUES)}")
    try:
        top_n_v = int(top_n)
    except (TypeError, ValueError):
        return error_envelope("invalid_argument", "top_n must be an integer")
    if not 1 <= top_n_v <= ops.MAX_TOP_N:
        return error_envelope("invalid_argument", f"top_n must be between 1 and {ops.MAX_TOP_N}", {"top_n": top_n_v})

    sid_list: Optional[list[int]] = None
    if sids is not None:
        if not isinstance(sids, (list, tuple)) or not sids:
            return error_envelope("invalid_argument", "sids must be a non-empty list of server ids or null")
        try:
            sid_list = sorted({int(s) for s in sids})
        except (TypeError, ValueError):
            return error_envelope("invalid_argument", "sids must be integers")
        bad = [s for s in sid_list if s not in ops.LIVE_SIDS]
        if bad:
            return error_envelope("invalid_argument", f"sids {bad} are not live servers; allowed: {list(ops.LIVE_SIDS)}")

    result = await run_sync_with_timeout(
        ops.fetch_open, ctx.settings, ctx=ctx, symbol=symbol, symbol_match=symbol_match, sids=sid_list
    )
    if is_error(result):
        return result

    grouped = ops.rollup(result["rows"], group_by)

    # ── output scope filter (same rule as rank_accounts) ─────────────────────
    # `None` = unrestricted. A frozenset (even empty) = restricted: a row whose
    # cid is not in it — including an unresolvable cid — is masked.
    masked = 0
    visible: list[dict] = []
    for row in grouped:
        if ctx.scope is not None and (row["cid"] is None or row["cid"] not in ctx.scope):
            masked += 1
            continue
        visible.append(row)

    totals = ops.book_totals(visible)
    ranked = ops.sort_rows(visible, sort)[:top_n_v]
    rows = []
    for i, row in enumerate(ranked, start=1):
        out = {k: v for k, v in row.items() if k != "oldest_open"}
        out["rank"] = i
        out["oldest_open_at"] = mt_local_to_utc_iso(row["oldest_open"])
        rows.append(out)

    matched_symbols = sorted({r["symbol"] for r in result["rows"]})
    data = {
        "symbol": symbol,
        "symbol_match": symbol_match,
        "symbols_matched": matched_symbols,
        "group_by": group_by,
        "sort": sort,
        "top_n": top_n_v,
        "sids": sid_list or list(ops.LIVE_SIDS),
        "totals": totals,
        "rows": rows,
        "rows_returned": len(rows),
        "groups_total": len(visible),
        "rows_masked_by_scope": masked,
    }
    caveats = [
        "Snapshot at call time of orders still open (closeDate = '1970-01-01'); there is no date range.",
        "net_lots = buy_lots - sell_lots (+ = client net long, so the house is net short if it B-books it); "
        "gross_lots = buy + sell. A client with equal buy and sell is locked: large gross, zero net.",
        "Cent products (.cent / .kcmc) have lots AND money divided by 100; CEN accounts have money divided by 100. "
        "XAUUSD.c is NOT a cent product.",
        "floating_pl = PROFIT + SWAPS + COMMISSION of the open orders in USD, as last synced by the back office.",
        "Demo/test accounts and employee clients are excluded; live servers sid 1/5/6 only; CMD 0/1 only "
        "(pending orders are not positions).",
        "totals covers every in-scope position for the matched symbols, not only the rows returned.",
    ]
    if symbol_match == "family":
        caveats.append(
            f"symbol_match 'family' matched every symbol starting with {symbol!r}: {', '.join(matched_symbols) or 'none'}."
        )
    if masked:
        caveats.append(
            f"{masked} {group_by}(s) are outside the caller's data scope and were removed BEFORE taking the top_n "
            "and from totals; rows_masked_by_scope counts them. Do not infer their identity or position."
        )
    if result["truncated"]:
        caveats.append(
            f"More than {ops.MAX_FETCH_ROWS} (account, symbol) rows matched; only the first {ops.MAX_FETCH_ROWS} "
            "were aggregated. Use symbol_match 'exact' or restrict sids."
        )
    definition = {
        "summary": f"Open positions in {symbol} ({symbol_match}) right now, per {group_by}, ranked by {sort}; "
        "SQL aggregation over the open-order sentinel with the certified account universe and cent rules.",
        "caveats": caveats,
        "doc": "app/services/open_positions_rank_service.py; .cursor/skills/kcm-risk-pipeline/SKILL.md (open-position 口径)",
    }
    source = {
        "service": "app.services.open_positions_rank_service",
        "function": "fetch_open",
        "as_of": utc_now_iso(),
    }
    return ok_envelope(data, definition=definition, source=source, ctx=ctx, truncated=bool(result["truncated"]))
