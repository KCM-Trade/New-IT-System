"""Tool 3 — ``get_risk_signals`` (docs/ai-agent/02-contracts.md §3.3).

What the risk system has ALREADY said about this client: rule alerts,
watchlist case, CRM risk tags and shared order-IP peers. The tool gives
signals; the conclusion is a person's — ``verdict`` is present and always
``None``, and the definition summary opens with "signal ≠ violation".

Sources:
* alerts   — SQLite ``backend/data/risk_monitor.db`` ``alert_events``, via
             the public ``risk_monitor_db.query_alert_events`` /
             ``count_alert_events_by_rule`` (the Risk Monitor page's own
             query) on ``risk_monitor_db.open_readonly()`` — the agent
             container mounts backend/data read-only, and the default
             connection path sets a WAL pragma and commits;
* cases    — ``risk_cases_service.get_case_detail`` (PG risk_cases);
* CRM tags — PG mirror ``kcm.crm_user_tags`` (J15);
* shared IP— ``login_ip_trade_profit_service.lookup`` (SQLite
             login_ip_orders.db + Redis-cached groups), peers filtered by the
             caller's scope with the masked count reported.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any, Optional

from app.core import login_ip_orders_db as lio_db
from app.core import risk_monitor_db as rmdb
from app.core.config import Settings
from app.core.data_scope import cid_for_crm_user_ids
from app.core.risk_cases_pg import RiskCasesUnavailable, risk_cases_conn
from app.core.sql_helpers import SID_MAP
from app.services import login_ip_trade_profit_service as lip
from app.services.risk_cases_service import get_case_detail

from .risk_bands import RULE_BANDS, rule_band_name  # noqa: F401 — re-export
from .common import (
    MAX_ALERTS,
    CallerCtx,
    ResolvedSubject,
    is_error,
    mask_ip,
    mt_day_bounds_utc,
    ok_envelope,
    parse_date_range,
    parse_subject,
    resolve_subject,
    run_sync_with_timeout,
    to_utc_iso,
    utc_now_iso,
)

TOOL_NAME = "get_risk_signals"

# RULE_BANDS / rule_band_name moved to risk_bands.py (OPT-0066) and are
# re-exported here: slice-3 tools share them, existing imports keep working.
# The shared-IP lookup walks trade_ip_pnl for the whole window and consults
# the Redis-cached group ranking; a 366-day window would be a scan nobody
# waits 25s for. Peers are therefore taken over the LAST 30 days of the
# requested range (order IPs only exist from 2026-09-15 anyway).
SHARED_IP_WINDOW_DAYS = 30
_SERVER_LABEL_BY_SID = {sid: label for label, sid in SID_MAP.items()}


# ── data access (monkeypatch targets) ────────────────────────────────────────


def _fetch_alerts(settings: Settings, login_sids: list[str], since: str, until: str) -> dict[str, Any]:
    """Alerts for every account of the client in [since, until), newest first,
    capped at MAX_ALERTS; ``by_rule`` / ``total`` are counted over ALL matches
    (02 §3.3: the counts must not depend on the page cap).

    One ``query_alert_events`` per SERVER, not per account: the table is keyed
    ``(server, login)`` and a client's accounts on one server go into a single
    ``logins`` IN-list, so a five-account client costs two round trips, not
    ten. Both calls share the page's filter builder, so what "an alert of this
    client in this window" means is defined once, in risk_monitor_db.
    """
    by_server: dict[str, list[int]] = {}
    for login_sid in login_sids:
        sid_s, login_s = login_sid.split("-", 1)
        server = _SERVER_LABEL_BY_SID.get(int(sid_s))
        if server is None:
            continue
        by_server.setdefault(server, []).append(int(login_s))

    entries: list[dict] = []
    by_rule: Counter = Counter()
    total = 0
    conn = rmdb.open_readonly()
    try:
        for server, logins in by_server.items():
            page, _ = rmdb.query_alert_events(
                since, until, server=server, logins=logins,
                limit=MAX_ALERTS, sort_by="scanned_at", sort_order="desc", conn=conn,
            )
            entries.extend(page)
            for rule_id, n in rmdb.count_alert_events_by_rule(
                since, until, server, logins=logins, conn=conn
            ).items():
                by_rule[str(rule_id)] += n
                total += n
    finally:
        conn.close()
    entries.sort(key=lambda a: (a.get("scanned_at") or "", a.get("id") or 0), reverse=True)
    return {"alerts": entries[:MAX_ALERTS], "by_rule": dict(by_rule), "total": total}


def _fetch_case(settings: Settings, client_id: int) -> Optional[dict]:
    try:
        return get_case_detail(settings, user_id=client_id)
    except RiskCasesUnavailable:
        return {"_unavailable": True}


def _fetch_crm_risk_tags(settings: Settings, client_id: int) -> list[str]:
    try:
        with risk_cases_conn(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT t.tag FROM kcm.crm_user_tags ut JOIN kcm.crm_tags t ON t.id = ut.tag_id "
                    "WHERE ut.user_id = %(uid)s ORDER BY t.tag",
                    {"uid": client_id},
                )
                return [r["tag"] for r in cur.fetchall()]
    except RiskCasesUnavailable:
        return []


def _fetch_shared_ip(settings: Settings, client_id: int, day_from: date, day_to: date) -> dict[str, Any]:
    """Peer accounts sharing an order IP with the client. Returns the raw peer
    list keyed by user_id plus the per-IP peer counts; scope filtering happens
    in the tool where the caller context is."""
    # Same read-only-mount rule as the alerts leg: lookup() opens the orders DB
    # through get_connection() (WAL pragma = a write) unless handed a connection.
    conn = lio_db.open_readonly()
    try:
        result = lip.lookup(day_from.isoformat(), day_to.isoformat(), str(client_id), kind="id", conn=conn)
    finally:
        conn.close()
    peers_by_user: dict[int, dict] = {}
    ip_peer_users: dict[str, set[int]] = {}
    for acct in result.get("peer_accounts") or []:
        uid = acct.get("user_id")
        if uid is None or int(uid) == client_id:
            continue
        uid = int(uid)
        peers_by_user.setdefault(uid, {"user_id": uid, "accounts": 0})["accounts"] += 1
        for ip in acct.get("open_ips") or []:
            ip_peer_users.setdefault(ip, set()).add(uid)
    seed_ips = set(result.get("seed_ips") or [])
    ip_counts = {ip: len(users) for ip, users in ip_peer_users.items() if ip in seed_ips}
    return {"peers": peers_by_user, "ip_peer_users": {ip: sorted(u) for ip, u in ip_peer_users.items() if ip in seed_ips},
            "ip_counts": ip_counts, "matched": result.get("matched_as")}


def _fetch_peer_cids(settings: Settings, user_ids: list[int]) -> dict:
    return cid_for_crm_user_ids(settings, user_ids) if user_ids else {}


# ── assembly ─────────────────────────────────────────────────────────────────


def _alert_summary(a: dict) -> str:
    bits = [str(a.get("rule_label") or a.get("rule_id"))]
    if a.get("symbol"):
        bits.append(str(a["symbol"]))
    if a.get("order_count") is not None:
        bits.append(f"{a['order_count']} orders")
    if a.get("total_lots") is not None:
        bits.append(f"{a['total_lots']} lots")
    return " · ".join(bits)


def _shape_alert(a: dict) -> dict:
    sid = SID_MAP.get(a.get("server") or "")
    return {
        "rule_id": a.get("rule_id"),
        "rule_name": rule_band_name(a.get("rule_id")),
        "rule_label": a.get("rule_label"),
        "fired_at": to_utc_iso(a.get("scanned_at")),
        "severity": None,  # alert_events carries no severity; see caveats
        "summary": _alert_summary(a),
        "login_sid": f"{sid}-{a.get('login')}" if sid is not None else None,
        "symbol": a.get("symbol"),
        "evidence_ref": f"risk-monitor#{rule_band_name(a.get('rule_id'))}/{a.get('id')}",
    }


def _shape_case(case: Optional[dict]) -> list[dict]:
    if not case or case.get("_unavailable"):
        return []
    return [
        {
            "case_id": f"case:{case.get('user_id')}",
            "status": case.get("state"),
            "opened_at": case.get("first_signal_at"),
            "conclusion_tags": list(case.get("tags") or []),
            "last_action_at": case.get("action_at") or case.get("last_signal_at"),
            "signal_count": case.get("signal_count"),
            "review_after": case.get("review_after"),
        }
    ]


async def get_risk_signals(ctx: CallerCtx, subject: Any, date_range: Any) -> dict:
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
    login_sids = [subj.value] if subj.kind == "login_sid" else resolved.login_sids

    since, until = mt_day_bounds_utc(rng.day_from, rng.day_to)
    alerts = await run_sync_with_timeout(_fetch_alerts, ctx.settings, login_sids, since, until, ctx=ctx)
    if is_error(alerts):
        return alerts
    case = await run_sync_with_timeout(_fetch_case, ctx.settings, cid, ctx=ctx)
    if is_error(case):
        return case
    tags = await run_sync_with_timeout(_fetch_crm_risk_tags, ctx.settings, cid, ctx=ctx)
    if is_error(tags):
        return tags

    ip_from = max(rng.day_from, rng.day_to - timedelta(days=SHARED_IP_WINDOW_DAYS - 1))
    shared = await run_sync_with_timeout(_fetch_shared_ip, ctx.settings, cid, ip_from, rng.day_to, ctx=ctx)

    # The shared-IP leg DEGRADES rather than failing the tool: alerts, case and
    # tags are the primary answer and each comes from a different store. When
    # this leg fails (orders DB unavailable, lookup timeout) `shared_ip` is
    # null and the caveat names the error code, mirroring how the case leg
    # reports PG being down — the model can still answer the question asked.
    shared_ip_error: Optional[str] = None
    if is_error(shared):
        shared_ip_error = str(shared["error"]["code"])
        shared = None

    # Fan-out side of the scope rule (§2.1): the peers are OTHER clients, so a
    # restricted caller only sees the ones inside their scope — and is told how
    # many were hidden, otherwise "0 peers" reads as "no link".
    peer_ids = sorted(shared["peers"]) if shared else []
    masked = 0
    visible_peers = set(peer_ids)
    if ctx.scope is not None and peer_ids:
        cids = await run_sync_with_timeout(_fetch_peer_cids, ctx.settings, peer_ids, ctx=ctx)
        if is_error(cids):
            # Cannot tell whose the peers are → show none, say why (fail closed).
            shared_ip_error = str(cids["error"]["code"])
            shared = None
            visible_peers = set()
        else:
            visible_peers = {uid for uid in peer_ids if cids.get(uid) is not None and cids.get(uid) in ctx.scope}
            masked = len(peer_ids) - len(visible_peers)

    strongest: Optional[dict] = None
    for ip, users in (shared["ip_peer_users"].items() if shared else ()):
        n = len([u for u in users if u in visible_peers])
        if n and (strongest is None or n > strongest["peer_accounts_on_ip"]):
            strongest = {"ip": mask_ip(ip), "days_cooccur": None, "peer_clients_on_ip": n, "peer_accounts_on_ip": n}

    data = {
        "client_id": cid,
        "login_sids": login_sids,
        "date_range": rng.as_dict(),
        "alerts": [_shape_alert(a) for a in alerts["alerts"]],
        "alerts_total": alerts["total"],
        "alerts_by_rule": alerts["by_rule"],
        "cases": _shape_case(case),
        "crm_risk_tags": tags,
        "shared_ip": None if shared is None else {
            "window": {"from": ip_from.isoformat(), "to": rng.day_to.isoformat()},
            "peer_clients": len(visible_peers),
            "strongest_link": strongest,
            "peers_masked_by_scope": masked,
        },
        "verdict": None,
    }
    caveats = [
        "signal ≠ violation: every item here is a detector output or a human's earlier note, never a finding.",
        "Alerts are selected by scan time inside the MT-day window (converted to UTC, DST-aware); "
        "alert_events is purged after 30 days, so older ranges rely on the case's signal timeline instead.",
        "`alerts_by_rule` and `alerts_total` count EVERY match; `alerts` is capped at "
        f"{MAX_ALERTS} newest rows (see `truncated`).",
        "alert_events stores no severity, so `severity` is null; `rule_name` is the rule band "
        "(burst_open / quick_open_close / quick_profit / gap_trade_* / hedge_open / leverage_abuse / martingale / "
        "rebate_arbitrage / intraday_return).",
        "`cases[]` is the V2 watchlist case (state watching/disposed/whitelisted/archived); `conclusion_tags` are "
        "its tags.",
        f"`shared_ip` uses ORDER (open) IPs from the last {SHARED_IP_WINDOW_DAYS} days of the range only "
        "(data exists from 2026-09-15); peers are other CRM clients that placed orders from a seed IP. "
        "`days_cooccur` is not tracked by the source, so `strongest_link` ranks IPs by peer clients instead. "
        "IPs are masked to /24.",
        "`peers_masked_by_scope` > 0 means peers exist that the caller is not allowed to see.",
    ]
    if shared_ip_error is not None:
        caveats.append(
            f"shared-IP leg unavailable: {shared_ip_error} — `shared_ip` is null for that reason, "
            "not because no peers exist."
        )
    if case and case.get("_unavailable"):
        caveats.append("The case database (PG risk_cases) was unavailable; `cases` is empty for that reason, not because none exists.")
    definition = {
        "summary": "signal ≠ violation. Rule alerts (Risk Monitor), the V2 watchlist case, CRM risk tags and "
        "shared order-IP peers for the client's compliant accounts over the MT-day window.",
        "caveats": caveats,
        "doc": "docs/features/risk-monitor.md; docs/features/risk-watchlist.md; docs/features/login-ip.md §3.7",
    }
    source = {
        "service": "app.core.risk_monitor_db + app.services.risk_cases_service + app.services.login_ip_trade_profit_service",
        "function": "query_alert_events / count_alert_events_by_rule / get_case_detail / lookup",
        "as_of": utc_now_iso(),
    }
    truncated = alerts["total"] > len(alerts["alerts"])
    return ok_envelope(data, definition=definition, source=source, ctx=ctx, truncated=truncated)
