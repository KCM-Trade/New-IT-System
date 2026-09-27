"""Tool 1 — ``get_client_overview`` (docs/ai-agent/02-contracts.md §3.1).

Who the client is, which accounts they hold, and how the money stands. Every
figure comes from an existing service-layer definition; this module only
assembles them:

* accounts            — live ``fxbackoffice.mt4_users`` (balance / equity /
                        credit, CEN ÷100), demo/test dropped, employees refused
* net deposit legs    — ``account_enrichment.query_net_deposit_split``
                        (trading leg vs 'ib withdrawal' leg, DECOMPOSED — no
                        include_ib_withdrawal switch, §2.2)
* profit / rebate /
  floating / net_gain — ``net_gain_sql.net_gain_by_ids`` on the risk_cases PG
                        (STRICT: any leg NULL → net_gain NULL)
* activity_status     — the risk_cases ``activity_status_case()`` waterfall,
                        evaluated as-of ``date_range.to``
* crm_tags            — PG mirror ``kcm.crm_user_tags``

The data-access functions are module-level so tests can monkeypatch them and
exercise the assembly logic without a database.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from app.core.config import Settings
from app.core.risk_cases_pg import risk_cases_conn
from app.services.account_enrichment import query_net_deposit_split
from app.services.net_gain_sql import net_gain_by_ids
from app.services.risk_cases_service import activity_status_case

from .common import (
    CallerCtx,
    DateRange,
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
    today_mt,
    utc_now_iso,
)

TOOL_NAME = "get_client_overview"

# The same waterfall risk_cases uses for the watchlist, evaluated for ONE user
# and as-of a caller-chosen day instead of current_date. ``holding`` reads the
# live positions snapshot, which only makes sense when the as-of day is today;
# for a historical as-of the branch is switched off (see _fetch_activity).
_ACTIVITY_SQL = f"""
    WITH pos AS (
        SELECT DISTINCT user_id FROM kcm.active_positions_snapshot
        WHERE %(holding_live)s
    )
    SELECT {activity_status_case("%(asof)s::date")} AS activity_status,
           t.last_trade_date
    FROM kcm.user_profile p
    LEFT JOIN kcm.user_activity_summary t ON t.user_id = p.user_id
    LEFT JOIN pos ON pos.user_id = p.user_id
    WHERE p.user_id = %(uid)s
"""

_CRM_TAGS_SQL = """
    SELECT t.tag
    FROM kcm.crm_user_tags ut
    JOIN kcm.crm_tags t ON t.id = ut.tag_id
    WHERE ut.user_id = %(uid)s
    ORDER BY t.tag
"""

_LAST_TRADE_SQL = """
    SELECT t.loginSid AS login_sid, MAX(t.OPEN_TIME) AS last_open
    FROM fxbackoffice.mt4_trades t
    WHERE t.loginSid IN ({placeholders})
      AND t.openDate BETWEEN %s AND %s
      AND t.CMD IN (0, 1)
      AND COALESCE(t.isDeleted, 0) = 0
    GROUP BY t.loginSid
"""


# ── data access (monkeypatch targets) ────────────────────────────────────────


def _fetch_money_pg(settings: Settings, client_id: int) -> dict[str, Optional[float]]:
    with risk_cases_conn(settings) as conn:
        with conn.cursor() as cur:
            legs = net_gain_by_ids(cur, [client_id]).get(client_id) or {}
    return {
        "profit_all": legs.get("profit_all"),
        "rebate_all": legs.get("rebate_all"),
        "floating_pl": legs.get("floating_pl"),
        "net_gain": legs.get("net_gain"),
    }


def _fetch_net_deposit_split(settings: Settings, client_id: int) -> dict[str, float]:
    conn = connect_mysql(settings)
    try:
        split = query_net_deposit_split(conn, [client_id]).get(client_id) or {}
    finally:
        conn.close()
    return {
        "net_deposit_trading": round(float(split.get("trading_net_deposit") or 0.0), 2),
        "ib_withdrawal": round(float(split.get("ib_withdrawal") or 0.0), 2),
    }


def _fetch_last_trades(settings: Settings, login_sids: list[str], rng: DateRange) -> dict[str, Optional[str]]:
    if not login_sids:
        return {}
    conn = connect_mysql(settings)
    try:
        with conn.cursor() as cur:
            cur.execute(
                _LAST_TRADE_SQL.format(placeholders=", ".join(["%s"] * len(login_sids))),
                (*login_sids, rng.day_from.isoformat(), rng.day_to.isoformat()),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return {r["login_sid"]: mt_local_to_utc_iso(r.get("last_open")) for r in rows}


def _fetch_activity(settings: Settings, client_id: int, asof: date) -> dict[str, Any]:
    holding_live = asof >= today_mt()
    with risk_cases_conn(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(_ACTIVITY_SQL, {"uid": client_id, "asof": asof.isoformat(), "holding_live": holding_live})
            row = cur.fetchone()
            cur.execute(_CRM_TAGS_SQL, {"uid": client_id})
            tags = [r["tag"] for r in cur.fetchall()]
    if row is None:
        return {"activity_status": None, "crm_tags": tags, "holding_live": holding_live}
    return {
        "activity_status": row["activity_status"],
        "crm_tags": tags,
        "holding_live": holding_live,
    }


# ── the tool ─────────────────────────────────────────────────────────────────


async def get_client_overview(ctx: CallerCtx, subject: Any, date_range: Any) -> dict:
    subj = parse_subject(subject)
    if isinstance(subj, dict):
        return subj
    rng = parse_date_range(date_range)
    if isinstance(rng, dict):
        return rng

    resolved = await resolve_subject(ctx, subj, tool=TOOL_NAME)
    if isinstance(resolved, dict):
        return resolved
    assert isinstance(resolved, ResolvedSubject)
    cid = resolved.client_id

    money_pg = await run_sync_with_timeout(_fetch_money_pg, ctx.settings, cid, ctx=ctx)
    if is_error(money_pg):
        return money_pg
    split = await run_sync_with_timeout(_fetch_net_deposit_split, ctx.settings, cid, ctx=ctx)
    if is_error(split):
        return split
    last = await run_sync_with_timeout(_fetch_last_trades, ctx.settings, resolved.login_sids, rng, ctx=ctx)
    if is_error(last):
        return last
    activity = await run_sync_with_timeout(_fetch_activity, ctx.settings, cid, rng.day_to, ctx=ctx)
    if is_error(activity):
        return activity

    accounts = []
    for a in resolved.accounts:
        accounts.append({**a, "last_trade_at": last.get(a["login_sid"])})

    data = {
        "client": {
            "client_id": cid,
            "cid": resolved.cid,
            "country": resolved.country,
            "registered_at": resolved.registered_at,
            "crm_tags": activity["crm_tags"],
        },
        "accounts": accounts,
        "money": {
            "net_deposit_trading": split["net_deposit_trading"],
            "ib_withdrawal": split["ib_withdrawal"],
            "profit_all": money_pg["profit_all"],
            "rebate_all": money_pg["rebate_all"],
            "floating_pl": money_pg["floating_pl"],
            "net_gain": money_pg["net_gain"],
            "net_gain_definition": "profit_all + floating_pl + rebate_all (STRICT: null when any leg is null)",
        },
        "activity_status": activity["activity_status"],
        "date_range": rng.as_dict(),
        "accounts_excluded_as_demo": resolved.excluded_accounts,
    }
    caveats = [
        "CEN (cent) accounts are already divided by 100; all money is USD.",
        "Demo/test accounts are excluded from `accounts`; employee clients are refused (subject_excluded).",
        "`money` is CUMULATIVE up to `source.as_of` — it is NOT restricted to `date_range`. "
        "`date_range` only affects `activity_status` (as-of its last day) and `accounts[].last_trade_at`.",
        "`net_gain` is the STRICT definition: profit_all + floating_pl + rebate_all, null if any leg is unknown; "
        "`rebate_all` is the FULL-CHAIN rebate (every IB level), from kcm.daily_user_rebate.",
        "`net_deposit_trading` (deposits + withdrawals) and `ib_withdrawal` (IB commission cash-outs) are two legs on "
        "purpose; the legacy single net-deposit number was their sum and misleads for IB-cum-traders.",
        "`activity_status` follows the risk-watchlist waterfall (holding > active_1d/7d/30d/90d > dormant > "
        "funded_no_trade > new_no_fund > no_fund) with calendar-day windows; `holding` is only evaluated when "
        "date_range.to is today.",
        "`accounts[].balance/equity/credit` are the live broker values at call time, not a date_range snapshot.",
    ]
    definition = {
        "summary": "Client identity, compliant trading accounts with live balances, cumulative money legs "
        "(trading net deposit, IB withdrawal, closed profit, full-chain rebate, floating P&L, STRICT net gain) "
        "and the risk-watchlist activity status.",
        "caveats": caveats,
        "doc": "CLAUDE.md#净入金公司盈亏口径; .cursor/skills/rebate-arbitrage/SKILL.md §2.2; docs/features/risk-watchlist.md",
    }
    source = {
        "service": "app.services.net_gain_sql + app.services.account_enrichment + app.services.risk_cases_service",
        "function": "net_gain_by_ids / query_net_deposit_split / activity_status_case",
        "as_of": utc_now_iso(),
    }
    return ok_envelope(data, definition=definition, source=source, ctx=ctx)
