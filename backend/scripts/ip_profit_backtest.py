#!/usr/bin/env python3
"""IP profit attribution backtest (login-IP proxy, exploratory — 2026-09-21).

Boss request: "attribute every closed trade's profit to the IP the order was
placed from; when one IP earns unusually much, check whether the accounts
behind it belong to different clients / IBs (mule accounts running one
method)."

Per-order IPs only exist in the MT journal logs, which we keep 7 days. This
script is the CHEAP APPROXIMATION that answers "is there a signal at all"
over history, before building the real per-order extractor:

  trade side  fxbackoffice.mt4_trades on the slave, pulled ONE closeDate per
              query (INDEX_CLOSEDATE; a 5-month range scan times out at 60 s)
              and aggregated server-side to (sid, LOGIN, openDate, userId).
  IP side     backend/data/login_ip/YYYYMMDD/*.json — the daily login-IP
              snapshots (kept since 2026-04-08). One IP per (server, login,
              MT day), chosen as:
                1. analysis_last_trade_ip.json  — real close IP that day
                   (available from 2026-07-13), else
                2. the login IP with the most logins that day
                   (analysis_account_logins.json), with ip_cnt recorded so
                   the reader can see how ambiguous the pick was.
  join key     (server, LOGIN, openDate) — profit is attributed to the IP of
               the OPEN day, because the boss asked for the IP "at order
               placement", not at close.

Outputs (backend/scripts/output/):
  ip_profit_<start>_<end>_summary.csv     one row per IP
  ip_profit_<start>_<end>_drilldown.csv   accounts under the top IPs
and (by default) an email to kieran with the headline tables.

Usage (from backend/ with .venv):
  python scripts/ip_profit_backtest.py --start 2026-08-21 --end 2026-09-20
  python scripts/ip_profit_backtest.py --start ... --end ... --no-send-email
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import pymysql
import pymysql.cursors

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

SID_TO_SERVER = {1: "MT4", 5: "MT5", 6: "MT4_Live2"}
CRM_LINK = "https://mt4.kohleglobal.com/crm/users/{uid}"
NO_IP = "<no-ip>"

TH = "background:#34495e;color:#fff;padding:6px 8px;text-align:left;font-size:12px;white-space:nowrap"
TD = "padding:5px 8px;border-bottom:1px solid #e0e0e0;white-space:nowrap"
TDR = TD + ";text-align:right"
LINK = "color:#3498db;text-decoration:underline;"


# ─────────────────────────────── args ───────────────────────────────
def build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="IP profit attribution backtest (login-IP proxy)")
    p.add_argument("--start", required=True, help="first closeDate, YYYY-MM-DD (MT day)")
    p.add_argument("--end", required=True, help="last closeDate inclusive, YYYY-MM-DD (MT day)")
    p.add_argument("--ip-dir", type=Path,
                   default=Path(os.getenv("LOGIN_IP_DATA_DIR", str(BACKEND_ROOT / "data" / "login_ip"))))
    p.add_argument("--ip-lookback-days", type=int, default=120,
                   help="how far before --start to load IP snapshots (open days can precede the close window)")
    p.add_argument("--top", type=int, default=20, help="rows in the email ranking tables")
    p.add_argument("--drill-top", type=int, default=8, help="multi-client IPs to drill down in the email")
    p.add_argument("--public-ip-clients", type=int, default=10,
                   help="IPs with >= this many distinct clients are labelled as likely shared exits "
                        "(one client with many accounts on one IP is a finding, not a NAT)")
    p.add_argument("--out-dir", type=Path, default=BACKEND_ROOT / "scripts" / "output")
    p.add_argument("--mail-to", default=os.getenv("IP_PROFIT_MAIL_TO", "kieran.xiang@kohleservices.com"))
    p.add_argument("--mail-cc", default=os.getenv("IP_PROFIT_MAIL_CC", ""))
    p.add_argument("--no-send-email", dest="send_email", action="store_false")
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


# ───────────────────────── step 1: trades per day ─────────────────────────
DAY_SQL = """
SELECT /*+ MAX_EXECUTION_TIME(90000) */
       t.sid, t.LOGIN AS login, t.openDate AS open_date, t.closeDate AS close_date,
       mu.userId AS user_id, mu.CURRENCY AS currency, mu.`GROUP` AS grp,
       COUNT(*)                                                     AS trades,
       SUM(t.lots)                                                  AS lots,
       SUM(t.totalProfit / IF(mu.CURRENCY = 'CEN', 100, 1))         AS profit_usd,
       SUM(t.SYMBOL LIKE 'XAUUSD%%')                                AS xau_trades,
       SUM(TIMESTAMPDIFF(SECOND, t.OPEN_TIME, t.CLOSE_TIME))        AS hold_sec_sum
FROM fxbackoffice.mt4_trades t
JOIN fxbackoffice.mt4_users mu ON mu.loginSid = t.loginSid
JOIN fxbackoffice.users u      ON u.id = mu.userId AND COALESCE(u.isEmployee, 0) = 0
WHERE t.closeDate = %s
  AND t.sid IN (1, 5, 6)
  AND t.CMD IN (0, 1)
  AND (t.isDeleted = 0 OR t.isDeleted IS NULL)
  AND LOWER(mu.`GROUP`) NOT LIKE '%%demo%%' AND LOWER(mu.`GROUP`) NOT LIKE '%%test%%'
  AND LOWER(mu.NAME)    NOT LIKE '%%demo%%' AND LOWER(mu.NAME)    NOT LIKE '%%test%%'
GROUP BY t.sid, t.LOGIN, t.openDate, t.closeDate, mu.userId, mu.CURRENCY, mu.`GROUP`
"""


def pull_trades(start: dt.date, end: dt.date) -> pd.DataFrame:
    frames = []
    day = start
    with get_conn() as conn, conn.cursor() as cur:
        while day <= end:
            t0 = time.time()
            cur.execute(DAY_SQL, (day.isoformat(),))
            rows = cur.fetchall()
            print(f"  {day} rows={len(rows):6d} {time.time() - t0:5.1f}s", flush=True)
            if rows:
                frames.append(pd.DataFrame(rows))
            day += dt.timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    for c in ("trades", "lots", "profit_usd", "xau_trades", "hold_sec_sum"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["login"] = df["login"].astype(str)
    df["open_date"] = pd.to_datetime(df["open_date"]).dt.strftime("%Y%m%d")
    df["server"] = df["sid"].map(SID_TO_SERVER)
    df["login_sid"] = df["sid"].astype(str) + "-" + df["login"]
    return df


# ───────────────────────── step 2: (server, login, day) → IP ─────────────────────────
def load_ip_map(ip_dir: Path, first_day: dt.date, last_day: dt.date) -> pd.DataFrame:
    """One IP per (server, login, MT day). last-close IP wins, else dominant login IP."""
    recs: dict[tuple[str, str, str], tuple[str, str, int]] = {}
    days_loaded = 0
    for d in sorted(p.name for p in ip_dir.iterdir() if p.is_dir() and p.name.isdigit()):
        if not (first_day.strftime("%Y%m%d") <= d <= last_day.strftime("%Y%m%d")):
            continue
        logins_path = ip_dir / d / "analysis_account_logins.json"
        if not logins_path.exists():
            continue
        days_loaded += 1
        with open(logins_path, encoding="utf-8") as f:
            logins = json.load(f)
        for server, accs in logins.items():
            for login, ips in accs.items():
                if not ips:
                    continue
                best = max(ips.items(), key=lambda kv: kv[1])[0]
                recs[(server, str(login), d)] = (best, "login_dominant", len(ips))
        close_path = ip_dir / d / "analysis_last_trade_ip.json"
        if close_path.exists():
            with open(close_path, encoding="utf-8") as f:
                closes = json.load(f)
            for server, accs in closes.items():
                for login, info in accs.items():
                    ip = (info or {}).get("ip")
                    if not ip:
                        continue
                    prev = recs.get((server, str(login), d))
                    recs[(server, str(login), d)] = (ip, "last_close", prev[2] if prev else 1)
    print(f"  IP snapshots loaded: {days_loaded} days, {len(recs):,} (server,login,day) keys", flush=True)
    rows = [(k[0], k[1], k[2], v[0], v[1], v[2]) for k, v in recs.items()]
    return pd.DataFrame(rows, columns=["server", "login", "open_date", "ip", "ip_source", "ip_cnt"])


# ───────────────────────── step 3: CRM lookups ─────────────────────────
def chunked_in(cur, sql_tmpl: str, ids: list, chunk: int = 1000) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        ph = ",".join(["%s"] * len(part))
        cur.execute(sql_tmpl.format(ph=ph), part)
        out.extend(cur.fetchall())
    return out


def fetch_ib_and_names(user_ids: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """direct IB per client (ib_tree level=1, verified 1 row per referralId) + names."""
    with get_conn() as conn, conn.cursor() as cur:
        ib_rows = chunked_in(
            cur,
            "SELECT /*+ MAX_EXECUTION_TIME(30000) */ referralId AS user_id, ibId AS ib_id "
            "FROM fxbackoffice.ib_tree WHERE level = 1 AND referralId IN ({ph})",
            user_ids,
        )
        ib_ids = sorted({r["ib_id"] for r in ib_rows})
        name_rows = chunked_in(
            cur,
            "SELECT /*+ MAX_EXECUTION_TIME(30000) */ id, "
            "TRIM(CONCAT(COALESCE(firstName,''),' ',COALESCE(lastName,''))) AS name, "
            "country, cid FROM fxbackoffice.users WHERE id IN ({ph})",
            sorted(set(user_ids) | set(ib_ids)),
        )
    return pd.DataFrame(ib_rows, columns=["user_id", "ib_id"]), pd.DataFrame(
        name_rows, columns=["id", "name", "country", "cid"])


def cached_countries(ips: list[str]) -> dict[str, str]:
    """Cache-only geo (no MaxMind billing). Missing IPs simply have no country."""
    try:
        from app.core import login_ip_db
        return login_ip_db.get_cached_countries(ips)
    except Exception as exc:  # noqa: BLE001 — geo is decoration here
        print(f"  [geo] cache unavailable: {exc}", flush=True)
        return {}


# ───────────────────────── step 4: aggregate ─────────────────────────
def aggregate(df: pd.DataFrame, public_ip_clients: int) -> pd.DataFrame:
    df = df.copy()
    df["ip"] = df["ip"].fillna(NO_IP)
    df["attr_good"] = ((df["ip_source"] == "last_close") | (df["ip_cnt"] == 1)).fillna(False)
    g = df.groupby("ip")
    s = pd.DataFrame({
        "profit_usd": g["profit_usd"].sum(),
        "trades": g["trades"].sum(),
        "lots": g["lots"].sum(),
        "accounts": g["login_sid"].nunique(),
        "clients": g["user_id"].nunique(),
        "ibs": g["ib_id"].nunique(),
        "active_days": g["open_date"].nunique(),
        "xau_trades": g["xau_trades"].sum(),
        "hold_sec_sum": g["hold_sec_sum"].sum(),
        "good_trades": df.assign(gt=df["trades"].where(df["attr_good"], 0)).groupby("ip")["gt"].sum(),
    })
    s["xau_share"] = (s["xau_trades"] / s["trades"]).round(3)
    s["avg_hold_min"] = (s["hold_sec_sum"] / s["trades"] / 60).round(1)
    s["profit_per_lot"] = (s["profit_usd"] / s["lots"].replace(0, float("nan"))).round(2)
    s["attr_quality"] = (s["good_trades"] / s["trades"]).round(2)
    s["shared_exit"] = s["clients"] >= public_ip_clients
    s = s.drop(columns=["xau_trades", "hold_sec_sum", "good_trades"]).reset_index()
    return s.sort_values("profit_usd", ascending=False)


def drilldown(df: pd.DataFrame, ips: list[str], names: pd.DataFrame) -> pd.DataFrame:
    d = df[df["ip"].isin(ips)].copy()
    g = d.groupby(["ip", "login_sid", "user_id", "ib_id"], dropna=False)
    out = pd.DataFrame({
        "profit_usd": g["profit_usd"].sum(),
        "trades": g["trades"].sum(),
        "lots": g["lots"].sum(),
        "active_days": g["open_date"].nunique(),
        "xau_trades": g["xau_trades"].sum(),
        "hold_sec_sum": g["hold_sec_sum"].sum(),
        "last_close_days": d[d["ip_source"] == "last_close"].groupby(
            ["ip", "login_sid", "user_id", "ib_id"], dropna=False)["open_date"].nunique(),
    }).reset_index()
    out["last_close_days"] = out["last_close_days"].fillna(0).astype(int)
    out["xau_share"] = (out["xau_trades"] / out["trades"]).round(2)
    out["avg_hold_min"] = (out["hold_sec_sum"] / out["trades"] / 60).round(1)
    nm = names.set_index("id")
    out["client_name"] = out["user_id"].map(nm["name"])
    out["client_country"] = out["user_id"].map(nm["country"])
    out["ib_name"] = out["ib_id"].map(nm["name"])
    out = out.drop(columns=["xau_trades", "hold_sec_sum"])
    rank = {ip: i for i, ip in enumerate(ips)}
    out["_r"] = out["ip"].map(rank)
    return out.sort_values(["_r", "profit_usd"], ascending=[True, False]).drop(columns="_r")


# ───────────────────────── email ─────────────────────────
def _money(v: float) -> str:
    color = "#c0392b" if v < 0 else "#27ae60"
    return f"<span style='color:{color}'>{v:+,.0f}</span>"


def _summary_table(s: pd.DataFrame, countries: dict[str, str]) -> str:
    rows = []
    for _, r in s.iterrows():
        flag = " <span style='color:#7f8c8d'>(shared exit?)</span>" if r["shared_exit"] else ""
        rows.append(
            f"<tr><td style='{TD}'><code>{r['ip']}</code>{flag}</td>"
            f"<td style='{TD}'>{countries.get(r['ip'], '')}</td>"
            f"<td style='{TDR}'>{_money(r['profit_usd'])}</td>"
            f"<td style='{TDR}'>{int(r['trades']):,}</td>"
            f"<td style='{TDR}'>{r['lots']:,.1f}</td>"
            f"<td style='{TDR}'>{int(r['accounts'])}</td>"
            f"<td style='{TDR}'><b>{int(r['clients'])}</b></td>"
            f"<td style='{TDR}'>{int(r['ibs'])}</td>"
            f"<td style='{TDR}'>{int(r['active_days'])}</td>"
            f"<td style='{TDR}'>{r['xau_share']:.0%}</td>"
            f"<td style='{TDR}'>{r['avg_hold_min']:,.0f}</td>"
            f"<td style='{TDR}'>{r['attr_quality']:.0%}</td></tr>"
        )
    head = "".join(f"<th style='{TH}'>{h}</th>" for h in (
        "IP", "国家", "盈利 USD", "单数", "手数", "账户", "客户", "IB", "活跃日", "XAU 占比", "均持仓 min", "归因可信"))
    return f"<table style='border-collapse:collapse;font-size:12px;margin-bottom:14px'><tr>{head}</tr>{''.join(rows)}</table>"


def _drill_table(d: pd.DataFrame) -> str:
    rows = []
    for _, r in d.iterrows():
        uid = int(r["user_id"]) if pd.notna(r["user_id"]) else None
        client = (f"<a href='{CRM_LINK.format(uid=uid)}' style='{LINK}'>{uid}</a> {r['client_name'] or ''}"
                  if uid else "-")
        ib = (f"{int(r['ib_id'])} {r['ib_name'] or ''}" if pd.notna(r["ib_id"]) else "-")
        rows.append(
            f"<tr><td style='{TD}'>{r['login_sid']}</td>"
            f"<td style='{TD}'>{client}</td>"
            f"<td style='{TD}'>{r['client_country'] or ''}</td>"
            f"<td style='{TD}'>{ib}</td>"
            f"<td style='{TDR}'>{_money(r['profit_usd'])}</td>"
            f"<td style='{TDR}'>{int(r['trades']):,}</td>"
            f"<td style='{TDR}'>{r['lots']:,.1f}</td>"
            f"<td style='{TDR}'>{int(r['active_days'])}</td>"
            f"<td style='{TDR}'>{r['xau_share']:.0%}</td>"
            f"<td style='{TDR}'>{r['avg_hold_min']:,.0f}</td>"
            f"<td style='{TDR}'>{int(r['last_close_days'])}</td></tr>"
        )
    head = "".join(f"<th style='{TH}'>{h}</th>" for h in (
        "账户", "客户 (CRM)", "国", "直属 IB", "盈利 USD", "单数", "手数", "活跃日", "XAU", "均持仓 min", "平仓IP实证日"))
    return f"<table style='border-collapse:collapse;font-size:12px;margin-bottom:10px'><tr>{head}</tr>{''.join(rows)}</table>"


def build_email(args, df: pd.DataFrame, summary: pd.DataFrame, drill: pd.DataFrame,
                drill_ips: list[str], countries: dict[str, str]) -> tuple[str, str]:
    total_trades = int(df["trades"].sum())
    no_ip = summary[summary["ip"] == NO_IP]
    no_ip_trades = int(no_ip["trades"].sum()) if not no_ip.empty else 0
    no_ip_profit = float(no_ip["profit_usd"].sum()) if not no_ip.empty else 0.0
    attributed = df[df["ip"] != NO_IP]
    good = attributed[(attributed["ip_source"] == "last_close") | (attributed["ip_cnt"] == 1)]
    lc = attributed[attributed["ip_source"] == "last_close"]
    with_ip = summary[summary["ip"] != NO_IP]
    top_all = with_ip.head(args.top)
    multi = with_ip[(with_ip["clients"] >= 2) & (~with_ip["shared_exit"])].head(args.top)
    shared = with_ip[with_ip["shared_exit"]].head(10)

    subject = (f"交易 IP 盈利统计（登录 IP 近似 · 测试）IP Profit Attribution Backtest "
               f"({args.start} – {args.end})")

    drill_html = ""
    for ip in drill_ips:
        part = drill[drill["ip"] == ip]
        srow = summary[summary["ip"] == ip].iloc[0]
        drill_html += (
            f"<h4 style='color:#2c3e50;font-size:13px;margin:14px 0 4px'><code>{ip}</code> "
            f"{countries.get(ip, '')} — 盈利 {_money(srow['profit_usd'])} USD · "
            f"{int(srow['accounts'])} 账户 / {int(srow['clients'])} 客户 / {int(srow['ibs'])} IB</h4>"
            + _drill_table(part)
        )

    html = f"""
<div style="font-family:'Segoe UI',Roboto,'Microsoft YaHei',Arial,sans-serif;color:#333;max-width:1100px;font-size:13px;line-height:1.6">
<h2 style="color:#2c3e50;border-bottom:3px solid #34495e;padding-bottom:8px">交易 IP 盈利统计（测试稿）— IP Profit Attribution Backtest</h2>

<h3 style="color:#2c3e50;font-size:14px">第一部分 — 口径 · Method</h3>
<p style="margin:4px 0 10px;color:#555">
窗口：平仓日 {args.start} – {args.end}（MT 日），sid 1 / 5 / 6，剔除 demo / test / 员工，CEN 折算 USD，盈亏 = PROFIT + swap + commission。
每笔盈亏记到<b>开仓当天</b>该账户的 IP 上。逐单 IP 只在 MT 日志里（本机只留 7 天），所以本稿用<b>登录 IP 近似</b>：
① 当天有「最后平仓 IP」快照的用它（07-13 起有）；② 否则取当天登录次数最多的 IP。
「归因可信」= 该 IP 下由 ① 或当天只登录过一个 IP 的单占比；开仓早于 04-08 的单没有 IP 数据，落入 no-ip 桶。
</p>

<h3 style="color:#2c3e50;font-size:14px">第二部分 — 总览 · Coverage</h3>
<table style="border-collapse:collapse;font-size:12.5px;margin-bottom:14px">
  <tr><th style="text-align:left;padding:4px 14px 4px 0">已平仓单</th><td>{total_trades:,}（{df['user_id'].nunique():,} 客户 / {df['login_sid'].nunique():,} 账户）</td></tr>
  <tr><th style="text-align:left;padding:4px 14px 4px 0">能归因到 IP</th><td>{int(attributed['trades'].sum()):,}（{attributed['trades'].sum() / max(total_trades, 1):.1%}），其中平仓 IP 实证 {int(lc['trades'].sum()):,}、单 IP 日 {int(good['trades'].sum()) - int(lc['trades'].sum()):,}</td></tr>
  <tr><th style="text-align:left;padding:4px 14px 4px 0">no-ip 桶</th><td>{no_ip_trades:,} 单，盈亏 {_money(no_ip_profit)} USD</td></tr>
  <tr><th style="text-align:left;padding:4px 14px 4px 0">出现的 IP 数</th><td>{len(with_ip):,}，其中 ≥ {args.public_ip_clients} 客户的共享出口 {int(with_ip['shared_exit'].sum()):,} 个</td></tr>
  <tr><th style="text-align:left;padding:4px 14px 4px 0">客户盈亏合计</th><td><b>{_money(float(df['profit_usd'].sum()))} USD</b></td></tr>
</table>

<h3 style="color:#2c3e50;font-size:14px">第三部分 — 盈利最高的 IP（全部）· Top IPs by Profit</h3>
{_summary_table(top_all, countries)}

<h3 style="color:#2c3e50;font-size:14px">第四部分 — 多客户共用且盈利最高的 IP · Multi-Client IPs</h3>
<p style="margin:4px 0 8px;color:#555">老板问的那一类：同一 IP 下 ≥ 2 个不同 CRM 客户，且排除了 ≥ {args.public_ip_clients} 客户的共享出口（运营商 NAT / VPN / 机房）。</p>
{_summary_table(multi, countries) if not multi.empty else "<p style='color:#7f8c8d'>无</p>"}

<h3 style="color:#2c3e50;font-size:14px">第五部分 — 下钻 · Drill-down（前 {len(drill_ips)} 个多客户 IP）</h3>
<p style="margin:4px 0 4px;color:#555">「平仓IP实证日」= 该账户有多少个交易日的 IP 来自真实平仓日志而非登录近似；同一 IP 下各账户的 XAU 占比、均持仓、活跃日高度一致 = 「同一个方法」的形态证据。</p>
{drill_html or "<p style='color:#7f8c8d'>无</p>"}

<h3 style="color:#2c3e50;font-size:14px">第六部分 — 共享出口（仅供参考）· Shared Exits</h3>
{_summary_table(shared, countries) if not shared.empty else "<p style='color:#7f8c8d'>无</p>"}

<p style="font-size:12px;color:#7f8c8d;margin-top:12px">
附件 1：全部 IP 汇总 CSV。附件 2：前 {len(drill_ips)} 个多客户 IP 的账户明细 CSV。国家列来自本地 geo 缓存，未命中留空。
</p>
<p style="font-size:11px;color:#95a5a6;border-top:1px solid #eee;padding-top:8px">
数据来源：fxbackoffice.mt4_trades（从库）+ backend/data/login_ip 每日快照 + ib_tree level=1。登录 IP 近似下单 IP，仅用于验证假设；正式版需从 journal 提取逐单 IP。自动生成，请勿回复。
</p>
</div>
"""
    return subject, html


# ───────────────────────── main ─────────────────────────
def main() -> int:
    args = build_args()
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{start:%Y%m%d}_{end:%Y%m%d}"

    print("[1/4] trades per closeDate", flush=True)
    df = pull_trades(start, end)
    if df.empty:
        print("no trades in window"); return 1
    print(f"  {len(df):,} (account, open_day) rows, {int(df['trades'].sum()):,} trades", flush=True)

    print("[2/4] IP snapshots", flush=True)
    ipmap = load_ip_map(args.ip_dir, start - dt.timedelta(days=args.ip_lookback_days), end)
    df = df.merge(ipmap, on=["server", "login", "open_date"], how="left")

    print("[3/4] CRM: direct IB + names", flush=True)
    user_ids = sorted({int(u) for u in df["user_id"].dropna().unique()})
    ib_map, names = fetch_ib_and_names(user_ids)
    df = df.merge(ib_map, on="user_id", how="left")

    print("[4/4] aggregate", flush=True)
    summary = aggregate(df, args.public_ip_clients)
    with_ip = summary[summary["ip"] != NO_IP]
    drill_ips = with_ip[(with_ip["clients"] >= 2) & (~with_ip["shared_exit"])].head(args.drill_top)["ip"].tolist()
    drill = drilldown(df, drill_ips, names) if drill_ips else pd.DataFrame()
    countries = cached_countries(with_ip.head(200)["ip"].tolist() + drill_ips)
    summary["country"] = summary["ip"].map(countries)

    csv_summary = args.out_dir / f"ip_profit_{tag}_summary.csv"
    csv_drill = args.out_dir / f"ip_profit_{tag}_drilldown.csv"
    summary.to_csv(csv_summary, index=False)
    drill.to_csv(csv_drill, index=False)
    print(f"  summary → {csv_summary}\n  drill   → {csv_drill}", flush=True)
    print(summary.head(15).to_string(index=False), flush=True)

    subject, html = build_email(args, df, summary, drill, drill_ips, countries)
    html_path = args.out_dir / f"ip_profit_{tag}.html"
    html_path.write_text(html, encoding="utf-8")
    if args.send_email:
        from app.services.email_service import send_email
        send_email(subject=subject, body=html, to=args.mail_to, cc=args.mail_cc or None,
                   attachments=[str(csv_summary), str(csv_drill)])
        print(f"  email sent → {args.mail_to}" + (f" cc {args.mail_cc}" if args.mail_cc else ""), flush=True)
    else:
        print(f"  email NOT sent (preview {html_path})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
