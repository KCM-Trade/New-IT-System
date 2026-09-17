#!/usr/bin/env python3
"""News-event AB position scan (news-event line, criteria settled 2026-09-17).

Detects AB positions around a scheduled news release (NFP / CPI / FOMC ...):

  Part A — same-client pairs (identity is the linkage, no IP needed):
    same CRM clientid (mt4_users.userid) + exact SYMBOL (no .cent folding)
    + opposite CMD + lot ratio min/max > 0.8 + |open gap| <= 5 s.
    Cross-account cases are the priority; same-account locks are kept for
    the record only (user decision 2026-09-17: deprioritised, not dropped).

  Part B — cross-client pairs (2026-09-17 user decision):
    same shape filter across DIFFERENT clientids, but the pair only counts
    when the two accounts share a login IP:
      - same-day co-occurrence on >= 1 IP within --ip-lookback-days
        (default 8; mobile IPs rotate daily, event-day-only misses half)
      - IP degree (accounts on that IP that day) <= --ip-degree-max
        (default 5) grades the evidence "strong"; above = "weak" (kept,
        labelled — carrier NAT / VPN exits like Cloudflare WARP).
    Without the IP layer, shape alone matched 1,394 account pairs in the
    2026-09-16 FOMC window (flat gap distribution = pure coincidence);
    with it, 6. SSOT: docs/analysis/news-event-ab-detection.md §5.2.1.

Outputs two CSVs and (by default) sends the approved email template
(Chinese body, bilingual section titles, CRM deep links, no emojis).

Standard invocations (run from backend/ with .venv activated):

  # test read -> kieran only (default recipient)
  python scripts/event_ab_scan.py --event-mt "2026-09-16 21:00" --label "FOMC 议息"

  # official -> risk team, CC kieran
  python scripts/event_ab_scan.py --event-mt "2026-09-16 21:00" --label "FOMC 议息" \
      --mail-to risk@kcmtrade.com --mail-cc kieran.xiang@kohleservices.com

Times are MT server time (UTC+3), same as blowup_audit_window.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pymysql
import pymysql.cursors

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

CENT_SUFFIXES = (".cent", ".kcmc")
SID_TO_SERVER = {1: "MT4", 5: "MT5", 6: "MT4_Live2"}
CRM_LINK = "https://mt4.kohleglobal.com/crm/users/{uid}"

# ── styling constants for the email (approved template 2026-09-17) ──
LINK = "color:#3498db;text-decoration:underline;"
TH = "background:#34495e;color:#fff;padding:6px 8px;text-align:left;font-size:12px"
TD = "padding:6px 8px;border-bottom:1px solid #e0e0e0"


def parse_dt(s: str) -> dt.datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"Invalid datetime '{s}', expected 'YYYY-MM-DD HH:MM[:SS]'")


def build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="News-event AB position scan")
    p.add_argument("--event-mt", type=parse_dt, required=True,
                   help="Release moment in MT time, e.g. '2026-09-16 21:00'")
    p.add_argument("--label", type=str, default="",
                   help="Event label for subject/body, e.g. 'FOMC 议息' / 'US NFP'")
    p.add_argument("--before-min", type=int, default=15,
                   help="Window minutes before the release (default 15)")
    p.add_argument("--after-min", type=int, default=60,
                   help="Window minutes after the release (default 60)")
    p.add_argument("--max-gap-sec", type=int, default=5)
    p.add_argument("--min-lot-ratio", type=float, default=0.8)
    p.add_argument("--ip-lookback-days", type=int, default=8,
                   help="Login-IP lookback days incl. event day (default 8)")
    p.add_argument("--ip-degree-max", type=int, default=5,
                   help="Max accounts/IP/day for 'strong' evidence (default 5)")
    p.add_argument("--send-email", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--mail-to", type=str,
                   default=os.getenv("AB_EVENT_MAIL_TO", "kieran.xiang@kohleservices.com"),
                   help="Default = kieran (test read). Official run: risk@kcmtrade.com")
    p.add_argument("--mail-cc", type=str, default=os.getenv("AB_EVENT_MAIL_CC", ""))
    p.add_argument("--note", type=str, default="",
                   help="Optional analyst paragraph (plain text/HTML) inserted "
                        "under the cross-account table, e.g. repeat-actor notes")
    p.add_argument("--out-dir", type=Path, default=BACKEND_ROOT / "scripts" / "output")
    p.add_argument("--hk-offset-hours", type=int, default=5,
                   help="HK display offset vs MT (5 in summer, 6 in winter)")
    return p.parse_args()


def get_conn():
    from app.core.config import get_settings

    s = get_settings()
    return pymysql.connect(
        host=s.DB_HOST, user=s.DB_USER, password=s.DB_PASSWORD,
        database=s.MYSQL_DATABASE_FXBACKOFFICE, port=int(s.DB_PORT),
        charset=s.DB_CHARSET, cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10, read_timeout=300,
    )


def is_cent(symbol: str) -> bool:
    return str(symbol).lower().endswith(CENT_SUFFIXES)


def pull_orders(start_mt: dt.datetime, end_mt: dt.datetime) -> pd.DataFrame:
    """Window candidates. openDate equality hits IDX_OPEN_DATE — keep it."""
    days = sorted({start_mt.date().isoformat(), end_mt.date().isoformat()})
    date_sql = ",".join(f"'{d}'" for d in days)
    sql = f"""
    SELECT /*+ MAX_EXECUTION_TIME(120000) */
      T.TICKET, T.sid, T.loginSid, T.SYMBOL, T.CMD, T.lots,
      T.OPEN_TIME, T.CLOSE_TIME, T.totalProfit, T.COMMENT,
      U.userid, U.NAME, U.groupsid, U.BALANCE
    FROM fxbackoffice.mt4_trades T
    JOIN fxbackoffice.mt4_users U ON U.loginsid = T.loginSid
    WHERE T.openDate IN ({date_sql})
      AND T.OPEN_TIME >= %s AND T.OPEN_TIME < %s
      AND T.sid IN (1,5,6)
      AND T.CMD IN (0,1) AND (T.isDeleted = 0 OR T.isDeleted IS NULL)
      AND LOWER(U.groupsid) NOT LIKE '%%demo%%' AND LOWER(U.groupsid) NOT LIKE '%%test%%'
      AND LOWER(U.NAME)     NOT LIKE '%%demo%%' AND LOWER(U.NAME)     NOT LIKE '%%test%%'
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (start_mt, end_mt))
        df = pd.DataFrame(cur.fetchall())
    if not df.empty:
        df["lots"] = df["lots"].astype(float)
        df["totalProfit"] = df["totalProfit"].astype(float)
        df["BALANCE"] = df["BALANCE"].astype(float)
        # MT5 CMD inversion (known fxbo trap): sid=5 CLOSED rows store the EXIT
        # direction (opposite of the position); open rows and MT4 (sid 1/6)
        # store the true direction. Normalise to true position direction so
        # the scan result does not depend on WHEN it runs (a leg closing
        # between two runs would otherwise create/destroy fake pairs — one
        # such artifact was caught in the 2026-09-16 window, acct 5-60005147).
        closed = df["CLOSE_TIME"].notna() & (df["CLOSE_TIME"] > dt.datetime(1971, 1, 1))
        flip = (df["sid"] == 5) & closed
        df["CMD"] = df["CMD"].where(~flip, 1 - df["CMD"])
    return df


def shape_pairs(df: pd.DataFrame, max_gap_sec: int, min_lot_ratio: float) -> pd.DataFrame:
    """All different-account opposite pairs per exact symbol (sorted sweep)."""
    rows = []
    for sym, g in df.groupby("SYMBOL"):
        buys = g[g.CMD == 0].sort_values("OPEN_TIME")
        sells = g[g.CMD == 1].sort_values("OPEN_TIME")
        if buys.empty or sells.empty:
            continue
        s_rows = list(sells.itertuples())
        j0 = 0
        for x in buys.itertuples():
            while j0 < len(s_rows) and (x.OPEN_TIME - s_rows[j0].OPEN_TIME).total_seconds() > max_gap_sec:
                j0 += 1
            j = j0
            while j < len(s_rows) and (s_rows[j].OPEN_TIME - x.OPEN_TIME).total_seconds() <= max_gap_sec:
                y = s_rows[j]
                j += 1
                lo, hi = min(x.lots, y.lots), max(x.lots, y.lots)
                if hi <= 0 or lo / hi <= min_lot_ratio:
                    continue
                rows.append({
                    "symbol": sym,
                    "b_login": x.loginSid, "s_login": y.loginSid,
                    "b_uid": x.userid, "s_uid": y.userid,
                    "b_ticket": x.TICKET, "s_ticket": y.TICKET,
                    "gap": abs((y.OPEN_TIME - x.OPEN_TIME).total_seconds()),
                })
    return pd.DataFrame(rows)


def aggregate_cases(df: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    """Same-client pairs -> one case per (uid, symbol, unordered account set).

    Sum over DISTINCT tickets, never over pair rows (cartesian double-count).
    """
    if pairs.empty:
        return pd.DataFrame()
    by_ticket = df.set_index("TICKET")
    pairs = pairs.copy()
    pairs["acct_key"] = pairs.apply(lambda r: frozenset({r.b_login, r.s_login}), axis=1)
    out = []
    for (uid, sym, accts), g in pairs.groupby(["b_uid", "symbol", "acct_key"]):
        tickets = sorted(set(g.b_ticket) | set(g.s_ticket))
        t = by_ticket.loc[tickets]
        fx = 0.01 if is_cent(sym) else 1.0
        buys_t, sells_t = t[t.CMD == 0], t[t.CMD == 1]
        out.append({
            "clientid": uid,
            "client_name": t["NAME"].iloc[0],
            "symbol": sym,
            "form": "cross-account" if len(accts) > 1 else "same-account",
            "accounts": " / ".join(sorted(accts)),
            "buy_orders": len(buys_t), "sell_orders": len(sells_t),
            "buy_lots": round(buys_t["lots"].sum(), 2),
            "sell_lots": round(sells_t["lots"].sum(), 2),
            "min_gap_sec": int(g["gap"].min()),
            "first_open_mt": t["OPEN_TIME"].min().strftime("%Y-%m-%d %H:%M:%S"),
            "last_open_mt": t["OPEN_TIME"].max().strftime("%Y-%m-%d %H:%M:%S"),
            "net_profit_usd": round(float(t["totalProfit"].sum()) * fx, 2),
            "so_hit": bool(t["COMMENT"].astype(str).str.lower().str.startswith(("[so", "so:", "cso:")).any()),
            "order_pairs": len(g),
            "tickets": len(tickets),
        })
    return pd.DataFrame(out).sort_values("net_profit_usd").reset_index(drop=True)


def load_ip_index(event_day: dt.date, lookback_days: int):
    """(server, login) -> {(day, ip)} plus (day, server, ip) -> degree."""
    ip_dir = os.getenv("LOGIN_IP_DATA_DIR", str(BACKEND_ROOT / "data" / "login_ip"))
    days = [(event_day - dt.timedelta(days=i)).strftime("%Y%m%d") for i in range(lookback_days)]
    acct_day_ips: dict[tuple, set] = defaultdict(set)
    ip_degree: dict[tuple, int] = {}
    missing = []
    for day in days:
        path = os.path.join(ip_dir, day, "analysis_ip_to_accounts.json")
        if not os.path.exists(path):
            missing.append(day)
            continue
        data = json.load(open(path))
        for server, ips in data.items():
            for ip, accounts in ips.items():
                ip_degree[(day, server, ip)] = len(accounts)
                for login in accounts:
                    acct_day_ips[(server, str(login))].add((day, ip))
    if missing:
        print(f"[IP ] WARNING missing IP files for {missing} — "
              "event-day file is generated at 05:10 NEXT day; rerun T+1 for full coverage")
    return acct_day_ips, ip_degree


def cross_client_ip_pairs(df: pd.DataFrame, pairs: pd.DataFrame,
                          acct_day_ips, ip_degree, degree_max: int) -> pd.DataFrame:
    """Cross-client account pairs that share >= 1 same-day IP; graded by degree."""
    cc = pairs[pairs.b_uid != pairs.s_uid]
    if cc.empty:
        return pd.DataFrame()

    def acct_key(login_sid: str):
        sid_s, _, login = str(login_sid).partition("-")
        return (SID_TO_SERVER.get(int(sid_s), "?"), login)

    names = df.drop_duplicates("userid").set_index("userid")["NAME"]
    seen: set[frozenset] = set()
    out = []
    for r in cc.groupby(["b_login", "s_login", "b_uid", "s_uid"]).size().reset_index(name="n").itertuples():
        pair_key = frozenset({r.b_login, r.s_login})
        if pair_key in seen:
            continue
        a, b = acct_day_ips.get(acct_key(r.b_login), set()), acct_day_ips.get(acct_key(r.s_login), set())
        hits = []
        for day, ip in (a & b):
            deg = min(ip_degree.get((day, acct_key(r.b_login)[0], ip), 10 ** 9),
                      ip_degree.get((day, acct_key(r.s_login)[0], ip), 10 ** 9))
            hits.append((day, ip, deg))
        if not hits:
            continue
        seen.add(pair_key)
        min_deg = min(h[2] for h in hits)
        # combined window net across both accounts (all their window trades)
        t = df[df.loginSid.isin([r.b_login, r.s_login])]
        net = float((t["totalProfit"] * t["SYMBOL"].map(lambda s: 0.01 if is_cent(s) else 1.0)).sum())
        out.append({
            "a_login": r.b_login, "a_uid": r.b_uid, "a_name": names.get(r.b_uid, ""),
            "b_login": r.s_login, "b_uid": r.s_uid, "b_name": names.get(r.s_uid, ""),
            "order_pairs": int(r.n),
            "shared_days": len({h[0] for h in hits}),
            "shared_ips": len({h[1] for h in hits}),
            "min_ip_degree": min_deg,
            "grade": "strong" if min_deg <= degree_max else "weak",
            "combined_net_usd": round(net, 2),
            "evidence": "; ".join(f"{d}:{ip}(deg{deg})" for d, ip, deg in sorted(hits)[:8]),
        })
    if not out:
        return pd.DataFrame()
    return pd.DataFrame(out).sort_values(["grade", "combined_net_usd"]).reset_index(drop=True)


# ── email (approved template: Chinese body, bilingual titles, no emojis) ──

def _crm(uid, name=""):
    label = f"{uid} {name}".strip()
    return f"<a href='{CRM_LINK.format(uid=uid)}' style='{LINK}'>{label}</a>"


def _case_table(sub: pd.DataFrame) -> str:
    headers = ["客户 ID", "姓名", "品种", "账户", "买入手数", "卖出手数",
               "开仓差(秒)", "首笔开仓 (MT)", "客户净额 (USD)"]
    cols = ["clientid", "client_name", "symbol", "accounts", "buy_lots", "sell_lots",
            "min_gap_sec", "first_open_mt", "net_profit_usd"]
    head = "".join(f"<th style='{TH}'>{h}</th>" for h in headers)
    rows = []
    for _, r in sub.iterrows():
        tds = []
        for c in cols:
            v = r[c]
            if c == "clientid":
                v = _crm(int(v))
            elif c == "net_profit_usd":
                color = "#c0392b" if r[c] > 0 else "#27ae60"
                v = f"<span style='color:{color};font-weight:600'>{r[c]:+,.2f}</span>"
            tds.append(f"<td style='{TD}'>{v}</td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return ("<table style='border-collapse:collapse;font-size:12px;width:100%;margin-bottom:14px'>"
            f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>")


def _ip_table(linked: pd.DataFrame) -> str:
    headers = ["#", "客户", "账户对", "证据", "IP 等级", "合计净额 (USD)"]
    head = "".join(f"<th style='{TH}'>{h}</th>" for h in headers)
    rows = []
    for i, (_, r) in enumerate(linked.iterrows(), 1):
        grade = "强" if r["grade"] == "strong" else "<span style='color:#b45309'>弱（VPN/共享出口，仅供参考）</span>"
        ev = (f"{r['shared_days']} 天内用过 {r['shared_ips']} 个相同 IP"
              f"（该 IP 当天最少 {r['min_ip_degree']} 个账户在用）")
        clients = _crm(int(r["a_uid"]), r["a_name"]) + " × " + _crm(int(r["b_uid"]), r["b_name"])
        rows.append(
            f"<tr><td style='{TD}'>{i}</td><td style='{TD}'>{clients}</td>"
            f"<td style='{TD};font-family:Consolas,monospace'>{r['a_login']} / {r['b_login']}</td>"
            f"<td style='{TD}'>{ev}</td><td style='{TD}'>{grade}</td>"
            f"<td style='{TD};text-align:right'>{r['combined_net_usd']:+,.2f}</td></tr>"
        )
    return ("<table style='border-collapse:collapse;font-size:12px;width:100%;margin-bottom:8px'>"
            f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>")


def build_email(args, start_mt, end_mt, df, cases, linked, n_shape_cc) -> tuple[str, str]:
    label = args.label or "News Event"
    hk_start = start_mt + dt.timedelta(hours=args.hk_offset_hours)
    cross = cases[cases["form"] == "cross-account"] if not cases.empty else pd.DataFrame()
    same = cases[cases["form"] == "same-account"] if not cases.empty else pd.DataFrame()
    same_big = same[same["net_profit_usd"].abs() >= 20].sort_values("net_profit_usd") if not same.empty else pd.DataFrame()
    n_linked = len(linked)
    note_html = f"<p style='margin:6px 0 14px;color:#555'>{args.note}</p>" if args.note else ""

    subject = (f"AB 仓检测与跨客户 IP 关联 AB Position Scan & IP-Linkage "
               f"({start_mt:%Y-%m-%d} {label})")

    html = f"""
<div style="font-family:'Segoe UI',Roboto,'Microsoft YaHei',Arial,sans-serif;color:#333;max-width:1000px;font-size:13px;line-height:1.6">
<h2 style="color:#2c3e50;border-bottom:3px solid #c0392b;padding-bottom:8px">AB 仓检测 — {start_mt:%Y-%m-%d} {label} AB Position Scan</h2>

<h3 style="color:#2c3e50;font-size:14px">第一部分 — 判断条件 · Criteria</h3>
<p style="margin:4px 0 12px;color:#555">
扫描窗口 {start_mt:%Y-%m-%d %H:%M} – {end_mt:%H:%M} MT（HK {hk_start:%H:%M} 起），sid 1 / 5 / 6。
配对条件：同一 CRM clientid · 品种精确匹配（.cent 不归一化）· 方向相反 · 手数比 min/max &gt; {args.min_lot_ratio} ·
开仓差 ≤ {args.max_gap_sec} 秒 · 同账户 / 跨账户都算。案例按（clientid, 品种, 账户组合）聚合，盈亏按 ticket 去重求和。
剔除 demo/test；CEN 账户金额已折算 USD。
</p>

<h3 style="color:#2c3e50;font-size:14px">第二部分 — 检测结果 · Scan Results</h3>
<table style="border-collapse:collapse;font-size:12.5px;margin-bottom:14px">
  <tr><th style="text-align:left;padding:4px 14px 4px 0">候选订单</th><td>{len(df):,}（{df['userid'].nunique()} 客户 / {df['loginSid'].nunique()} 账户）</td></tr>
  <tr><th style="text-align:left;padding:4px 14px 4px 0">检出案例</th><td><b>{len(cases)}</b>（跨账户 <b>{len(cross)}</b> · 同账户对锁 {len(same)}）</td></tr>
  <tr><th style="text-align:left;padding:4px 14px 4px 0">客户净额合计</th><td><b>{(cases['net_profit_usd'].sum() if not cases.empty else 0):+,.2f} USD</b></td></tr>
</table>

<h4 style="color:#2c3e50;font-size:13px;margin-bottom:6px">同客户跨账户案例（{len(cross)} 个）</h4>
{_case_table(cross) if not cross.empty else "<p style='color:#7f8c8d;font-size:12px'>本窗口无同客户跨账户案例</p>"}
{note_html}

<h4 style="color:#2c3e50;font-size:13px;margin-bottom:6px">同账户对锁 · |净额| ≥ 20 USD（{len(same_big)} / {len(same)} 个）</h4>
{_case_table(same_big) if not same_big.empty else "<p style='color:#7f8c8d;font-size:12px'>无 ≥ 20 USD 的同账户案例</p>"}
<p style="color:#555;margin:6px 0 14px">其余同账户案例见附件。同账户对锁优先级较低，仅作留档。</p>

<hr style="border:none;border-top:2px solid #e0e0e0;margin:20px 0">

<h3 style="color:#2c3e50;font-size:14px">第三部分 — 跨客户 IP 关联检测 · Cross-Client IP-Linkage Detection</h3>
<p style="margin:4px 0 10px;color:#555">
上面第二部分只能抓「同一个客户」名下的对敲。两个<b>不同客户</b>之间的对敲，光看交易形态抓不出来——
本窗口满足形态条件（反向、同品种、{args.max_gap_sec} 秒内、手数接近）的跨客户组合有 <b>{n_shape_cc:,} 对</b>，
绝大多数只是新闻时刻大家都在抢单的巧合。所以加了一层 <b>IP 检测</b>：两个账户还必须<b>用过同一个 IP 上网</b>，
才算疑似关联。加上这层后剩 <b>{n_linked} 对</b>，全部列在下表。
</p>

<div style="background:#f7f9fa;border-left:4px solid #34495e;padding:10px 14px;margin:10px 0 14px;font-size:12.5px;color:#444">
  <p style="margin:3px 0"><b>IP 怎么算「同一个」——两条规则（大白话）：</b></p>
  <p style="margin:6px 0 3px"><b>1. 这个 IP 得是「私人的」。</b>我们统计每个 IP 当天有多少个账户在用：
  家里宽带、个人手机热点，一般只有 1–2 个账户；而 VPN、运营商公共出口，一个 IP 背后是几十上百个互不相识的人。
  <b>只有当天使用者不超过 {args.ip_degree_max} 个账户的 IP，才算数</b>；超过的标「弱」，仅供参考。</p>
  <p style="margin:6px 0 3px"><b>2. 不只看事件当天，往前看 {args.ip_lookback_days} 天。</b>手机上网的 IP 每天都会变，
  两个账户可能前几天在同一个 WiFi 出现过、事件日却各用各的流量。所以只要<b>过去 {args.ip_lookback_days} 天内任何一天</b>
  两个账户用过同一个 IP，就算关联。</p>
</div>

<h4 style="color:#2c3e50;font-size:13px;margin-bottom:6px">IP 关联案例（{n_linked} 个）</h4>
{_ip_table(linked) if n_linked else "<p style='color:#7f8c8d;font-size:12px'>本窗口无跨客户 IP 关联案例</p>"}

<p style="font-size:12px;color:#7f8c8d;margin-top:12px">
附件 1：案例明细 CSV（全部同客户案例）。附件 2：跨客户 IP 关联对 CSV（含逐日 IP 证据）。
</p>
<p style="font-size:11px;color:#95a5a6;border-top:1px solid #eee;padding-top:8px">
数据来源：fxbackoffice.mt4_trades（从库）+ 每日登录 IP 快照。未注明处时间均为 MT（UTC+3）。自动生成，请勿回复。
</p>
</div>
"""
    return subject, html


def main() -> int:
    args = build_args()
    start_mt = args.event_mt - dt.timedelta(minutes=args.before_min)
    end_mt = args.event_mt + dt.timedelta(minutes=args.after_min)
    print(f"[INIT] event(MT)={args.event_mt}  window(MT)=[{start_mt}, {end_mt})  label={args.label!r}")

    df = pull_orders(start_mt, end_mt)
    print(f"[PULL] orders={len(df)} clients={df['userid'].nunique() if len(df) else 0}")
    if df.empty:
        print("[DONE] no candidate orders — nothing to scan")
        return 0

    pairs = shape_pairs(df, args.max_gap_sec, args.min_lot_ratio)
    sc_pairs = pairs[pairs.b_uid == pairs.s_uid] if not pairs.empty else pd.DataFrame()
    n_shape_cc = 0
    if not pairs.empty:
        cc = pairs[pairs.b_uid != pairs.s_uid]
        n_shape_cc = cc.groupby(["b_login", "s_login"]).ngroups if not cc.empty else 0
    print(f"[PAIR] shape pairs={len(pairs)} (same-client rows={len(sc_pairs)}, cross-client account-pairs={n_shape_cc})")

    cases = aggregate_cases(df, sc_pairs)
    n_cross = int((cases["form"] == "cross-account").sum()) if not cases.empty else 0
    print(f"[CASE] same-client cases={len(cases)} (cross-account={n_cross})")

    acct_day_ips, ip_degree = load_ip_index(args.event_mt.date(), args.ip_lookback_days)
    linked = cross_client_ip_pairs(df, pairs, acct_day_ips, ip_degree, args.ip_degree_max)
    print(f"[IP  ] cross-client IP-linked pairs={len(linked)}"
          + (f" (strong={int((linked['grade'] == 'strong').sum())})" if len(linked) else ""))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{args.event_mt:%Y%m%d_%H%M}"
    csv_cases = args.out_dir / f"ab_event_{tag}_cases.csv"
    csv_linked = args.out_dir / f"ab_event_{tag}_ip_linked.csv"
    (cases if not cases.empty else pd.DataFrame()).to_csv(csv_cases, index=False)
    (linked if len(linked) else pd.DataFrame()).to_csv(csv_linked, index=False)
    print(f"[OUT ] {csv_cases}\n[OUT ] {csv_linked}")

    if args.send_email:
        from app.services.email_service import send_email

        subject, html = build_email(args, start_mt, end_mt, df, cases, linked, n_shape_cc)
        send_email(subject=subject, body=html, to=args.mail_to,
                   cc=args.mail_cc or None, attachments=[str(csv_cases), str(csv_linked)])
        print(f"[MAIL] sent to={args.mail_to} cc={args.mail_cc or '-'}")
    else:
        print("[MAIL] skipped (--no-send-email)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
