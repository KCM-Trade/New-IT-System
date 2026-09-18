"""Intraday Return mail source (OPT-0062) — registry pieces for MAIL_SOURCES.

Field getters, template context, rules loader, digest template builder and
the frozen test-send fallback sample for the ``intraday_return`` module
(rule band 131-140). Registered in ``registry.MAIL_SOURCES``; the dispatcher
stays untouched (iron rule: the mail center is a pure consumer of
alert_events — every money figure below is read as stored, never rescaled).

Email format follows the alert-email-style skill: English body, bilingual
section titles, NO emojis, 2-column label/value tables (bold labels only),
dual MT(UTC+3)/HK(UTC+8) times, max-width 600px.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .subject import build_subject
from ...core.risk_monitor_db import (
    fetch_intraday_return_alerts_after,
    fetch_intraday_return_alerts_by_ids,
    fetch_intraday_return_alerts_for_day,
    fetch_recent_intraday_return_alerts,
    load_intraday_return_config,
)
from ..rule_intraday_return_service import (
    INTRADAY_RETURN_RULE_ID_BASE,
    INTRADAY_RETURN_RULE_ID_MAX,
)

logger = logging.getLogger(__name__)

_RISK_MONITOR_PAGE_URL = "https://analysis.kohleservices.com/risk-monitor?tab=intraday-return"
# CRM account page (same deep-link shape the frontend LoginCell uses).
_CRM_ACCOUNT_URL = "https://mt4.kohleglobal.com/admin/accounts/{login}"


# ── Field getters (subscription condition evaluation) ───────────────────────

def _opt_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


FIELD_GETTERS: Dict[str, Any] = {
    "return_pct": lambda a: _opt_float(a.get("return_pct")),
    "peak_return_pct": lambda a: _opt_float(a.get("peak_return_pct")),
    "intraday_profit": lambda a: _opt_float(a.get("intraday_profit")),
    "initial_equity": lambda a: _opt_float(a.get("initial_equity")),
    "net_7d": lambda a: _opt_float(a.get("net_7d")),
    "trades_today": lambda a: _opt_int(a.get("trades_today")),
    "lock_pct": lambda a: _opt_float(a.get("lock_pct")),
    "equity": lambda a: _opt_float(a.get("equity")),
}

FILTERABLE_FIELDS: Dict[str, Tuple[str, str]] = {
    "return_pct": ("float", "即日收益率(%)"),
    "peak_return_pct": ("float", "當日峰值收益率(%)"),
    "intraday_profit": ("float", "當日盈利(USD)"),
    "initial_equity": ("float", "初始權益(USD)"),
    "net_7d": ("float", "近7日淨利(USD)"),
    "trades_today": ("int", "當日開倉筆數"),
    "lock_pct": ("float", "鎖倉時間占比(%)"),
    "equity": ("float", "當前淨值(USD)"),
}


def match_context(alert: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Renderability gate: the digest needs the return and its denominator.
    None (detail row missing) = never matches, same as the other sources."""
    ret = _opt_float(alert.get("return_pct"))
    init_eq = _opt_float(alert.get("initial_equity"))
    if ret is None or init_eq is None:
        return None
    return {"return_pct": ret, "initial_equity": init_eq}


EMPTY_CONTEXT: Dict[str, Any] = {"return_pct": 0.0, "initial_equity": 0.0}


def rules_loader() -> List[Dict[str, Any]]:
    """Configured tiers with their EFFECTIVE alert_events.rule_id (131 + index)."""
    cfg = load_intraday_return_config()
    out: List[Dict[str, Any]] = []
    for idx, r in enumerate(cfg.get("rules") or []):
        out.append({
            "id": INTRADAY_RETURN_RULE_ID_BASE + idx,
            "name": str(r.get("name") or f"Rule {idx + 1}"),
            "enabled": bool(r.get("enabled", True)),
            "params": {
                "min_return_pct": r.get("min_return_pct"),
                "min_initial_equity_usd": r.get("min_initial_equity_usd"),
                "min_profit_usd": r.get("min_profit_usd"),
                "min_net_7d_usd": r.get("min_net_7d_usd"),
                "net_window_days": r.get("net_window_days"),
            },
        })
    return out


# ── Digest template ──────────────────────────────────────────────────────────

_LABEL_TD = (
    "padding:2px 12px 2px 0;font-weight:bold;white-space:nowrap;"
    "vertical-align:top;font-size:13px;"
)
_VALUE_TD = "padding:2px 0;word-break:break-word;font-size:13px;"
_NEG_STYLE = "color:#c0392b;"
_SUB_TD = (
    "padding:2px 12px 2px 16px;white-space:nowrap;vertical-align:top;"
    "font-size:12px;color:#555;"
)


def _row(label: str, value_html: str) -> str:
    return (
        f"<tr><td style=\"{_LABEL_TD}\">{html.escape(label)}:</td>"
        f"<td style=\"{_VALUE_TD}\">{value_html}</td></tr>"
    )


def _sub_row(label: str, value_html: str) -> str:
    return (
        f"<tr><td style=\"{_SUB_TD}\">{html.escape(label)}:</td>"
        f"<td style=\"{_VALUE_TD}\">{value_html}</td></tr>"
    )


def _fmt_money(value: Any) -> str:
    v = _opt_float(value)
    return f"{v:,.2f}" if v is not None else "-"


def _money_html(value: Any) -> str:
    txt = _fmt_money(value)
    v = _opt_float(value)
    if v is not None and v < 0:
        return f"<span style=\"{_NEG_STYLE}\">{html.escape(txt)}</span>"
    return html.escape(txt)


def _fmt_pct(value: Any) -> str:
    v = _opt_float(value)
    return f"{v:,.0f}%" if v is not None else "-"


def _fmt_hold(value: Any) -> str:
    v = _opt_float(value)
    if v is None:
        return "-"
    s = int(round(v))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {(s % 3600) // 60}m"


def _fmt_shift(iso: Any, hours: int) -> str:
    if not iso:
        return "-"
    s = str(iso).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt.astimezone(timezone.utc) + timedelta(hours=hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _account_section(index: int, alert: Dict[str, Any], match: Dict[str, Any]) -> str:
    esc = lambda v: html.escape(str(v if v not in (None, "") else "-"))
    server = str(alert.get("server") or "-")
    login = alert.get("login")
    login_html = (
        f"<a href=\"{_CRM_ACCOUNT_URL.format(login=login)}\" style=\"color:#2563eb;\">"
        f"{esc(login)}</a>"
        if login else "-"
    )
    currency = str(alert.get("currency") or "USD")

    ret_html = html.escape(_fmt_pct(alert.get("return_pct")))
    peak = _opt_float(alert.get("peak_return_pct"))
    ret_now = _opt_float(alert.get("return_pct"))
    if peak is not None and ret_now is not None and peak > ret_now:
        ret_html += f" (peak today {html.escape(_fmt_pct(peak))})"

    rows = [
        _row("Account", f"{esc(server)} {login_html}"),
        _row("Group / Currency", esc(f"{alert.get('group') or '-'} / {currency}")),
        _row("Zipcode", esc(alert.get("zipcode"))),
        _row("Initial equity", _money_html(alert.get("initial_equity")) + " USD"),
        _sub_row("prev-day EOD equity", _money_html(alert.get("prev_day_equity"))),
        _sub_row("deposits today", _money_html(alert.get("deposits_in"))),
        _sub_row("credit in today", _money_html(alert.get("credit_in"))),
        _row("Intraday profit", _money_html(alert.get("intraday_profit")) + " USD"),
        _sub_row("same-day positions P&L", _money_html(alert.get("same_day_pnl"))),
        _sub_row(
            "overnight gain",
            _money_html(alert.get("carried_gain"))
            + html.escape(
                f" (now {_fmt_money(alert.get('carried_now'))} vs "
                f"yesterday EOD {_fmt_money(alert.get('carried_float0'))})"
            ),
        ),
        _row("Return", ret_html),
        _row("Net 7d (closed + floating)", _money_html(alert.get("net_7d")) + " USD"),
        _row("Equity now", _money_html(alert.get("equity_now")) + " USD"),
        _row("Trades today", esc(
            f"{alert.get('trades_today') if alert.get('trades_today') is not None else '-'} orders / "
            f"{_fmt_money(alert.get('lots_today'))} lots / top {alert.get('top_symbol') or '-'}"
        )),
        _row("Median hold", esc(_fmt_hold(alert.get("median_hold_sec")))),
        _row("Locked time", esc(
            f"{_opt_float(alert.get('lock_pct')):.1f}%" if _opt_float(alert.get("lock_pct")) is not None else "-"
        )),
    ]
    if int(alert.get("flag_withdraw_gt_half_deposit") or 0):
        rows.append(_row(
            "Withdrawal flag",
            f"<span style=\"{_NEG_STYLE}\">"
            + html.escape(
                f"withdrawals today {_fmt_money(alert.get('withdrawals_out'))} USD "
                f"exceed half of today's deposits"
            )
            + "</span>",
        ))
    rows += [
        _row("Matched condition", esc("; ".join(match.get("labels") or ["-"]))),
        _row("Trading day (MT)", esc(alert.get("trading_day"))),
        _row("Scanned MT Time", esc(_fmt_shift(alert.get("scanned_at"), 3))),
        _row("Scanned HK Time", esc(_fmt_shift(alert.get("scanned_at"), 8))),
        _row("Alert ID", esc(alert.get("id"))),
        _row("Rule", esc(alert.get("rule_label"))),
    ]
    title = f"{index}. Intraday Return · 即日高收益 — account {server} {login}"
    return (
        f"<div style=\"margin:18px 0 0;\">"
        f"<div style=\"font-size:16px;font-weight:bold;margin-bottom:6px;\">"
        f"{html.escape(title)}</div>"
        f"<table style=\"border-collapse:collapse;\">{''.join(rows)}</table>"
        f"</div>"
    )


def build_intraday_return_digest_email(
    hits: List[Tuple[Dict[str, Any], Dict[str, Any]]],
    *,
    subscription: Dict[str, Any],
    sibling_map: Dict[int, List[str]],
    test: bool = False,
) -> Tuple[str, str]:
    """Render (subject, body_html) for one digest of intraday-return hits.

    ``sibling_map`` is part of the registry contract; same-day sibling
    accounts are listed per section when present.
    """
    n = len(hits)
    subject = build_subject("即日高收益 Intraday Return", f"{n} 个账户", test)

    sections: List[str] = []
    for i, (alert, match) in enumerate(hits):
        section = _account_section(i + 1, alert, match)
        siblings = sibling_map.get(int(alert.get("id") or 0), [])
        if siblings:
            section = section.replace(
                "</table>",
                _row("Same-day siblings", html.escape(", ".join(siblings))) + "</table>",
                1,
            )
        sections.append(section)
    updated_at = subscription.get("updated_at") or "-"
    body = f"""<meta name="viewport" content="width=device-width,initial-scale=1">
<div style="max-width:600px;font-family:Arial,Helvetica,sans-serif;color:#1a1a1a;font-size:13px;">
<p>Dear Risk Team,</p>
<p>{n} account(s) crossed the intraday-return threshold on the current MT
trading day (same-day return on initial equity = prev-day EOD equity +
deposits + credit; overnight positions count only the part newly gained in
the profit zone today). Subscription: {html.escape(str(subscription.get('name') or ''))}.</p>
{''.join(sections)}
<p style="margin-top:20px;">Review on the Risk Monitor page:
<a href="{_RISK_MONITOR_PAGE_URL}" style="color:#2563eb;">{_RISK_MONITOR_PAGE_URL}</a></p>
<hr style="border:none;border-top:1px solid #d0d0d0;margin:16px 0 8px;">
<p style="color:#666;font-size:12px;">This is an auto email sent by the Trade Real-time Monitor
(intraday-return mail alert). Subscription config last updated: {html.escape(str(updated_at))}.
If you have any problem, please contact kieran.xiang@kohleservices.com</p>
</div>"""
    return subject, body


# ── Test-send fallback sample ────────────────────────────────────────────────
# SAMPLE DATA ONLY — frozen snapshot modeled on the risk desk's original case
# 5-67044208 on 2026-09-16: registered 06:58, deposited 50 at 07:03, 88
# XAUUSD trades, +478.27 by day end → 957% on a 50 USD base. Keys mirror the
# aliased row shape the intraday-return fetchers return. Never persisted,
# never dispatched — pure test-send preview rendering.
TEST_SEND_SAMPLE_ALERT: Dict[str, Any] = {
    "id": 999902,
    "scanned_at": "2026-09-16T09:30:00Z",
    "rule_id": INTRADAY_RETURN_RULE_ID_BASE + 1,
    "rule_label": "Rule 2 — 即日收益 ≥300%",
    "server": "MT5",
    "login": 67044208,
    "symbol": "XAUUSD",
    "order_count": 88,
    "total_lots": 4.4,
    "first_open": "2026-09-16T04:05:00Z",
    "last_open": "2026-09-16T09:20:00Z",
    "equity": 528.27,
    "balance": 528.27,
    "group": "KCM_VN_L4",
    "currency": "USD",
    "zipcode": "700000",
    "net_deposit_hist": 50.0,
    "user_id": 168990,
    "trading_day": "2026-09-16",
    "prev_day_equity": 0.0,
    "deposits_in": 50.0,
    "credit_in": 0.0,
    "withdrawals_out": 0.0,
    "adj_excluded": 0.0,
    "initial_equity": 50.0,
    "equity_now": 528.27,
    "same_day_pnl": 478.27,
    "carried_float0": 0.0,
    "carried_now": 0.0,
    "carried_gain": 0.0,
    "intraday_profit": 478.27,
    "return_pct": 957.0,
    "peak_return_pct": 957.0,
    "net_7d": 478.27,
    "realized_7d": 478.27,
    "floating_all_now": 0.0,
    "flag_withdraw_gt_half_deposit": 0,
    "trades_today": 88,
    "lots_today": 4.4,
    "median_hold_sec": 630,
    "lock_pct": 88.0,
    "top_symbol": "XAUUSD",
    "detail_updated_at": "2026-09-16T09:30:00Z",
}


# Registry entry — imported and installed by registry.MAIL_SOURCES.
SOURCE: Dict[str, Any] = {
    "label": "即日高收益 Intraday Return",
    "rule_id_range": (INTRADAY_RETURN_RULE_ID_BASE, INTRADAY_RETURN_RULE_ID_MAX),
    "rules_loader": rules_loader,
    "filterable_fields": FILTERABLE_FIELDS,
    "field_getters": FIELD_GETTERS,
    "match_context": match_context,
    "empty_context": EMPTY_CONTEXT,
    "fetch_after": fetch_intraday_return_alerts_after,
    "fetch_by_ids": fetch_intraday_return_alerts_by_ids,
    "fetch_for_day": fetch_intraday_return_alerts_for_day,
    "fetch_recent": fetch_recent_intraday_return_alerts,
    "template_builder": build_intraday_return_digest_email,
    "fallback_sample": TEST_SEND_SAMPLE_ALERT,
}
