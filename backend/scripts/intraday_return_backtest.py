#!/usr/bin/env python3
"""
Intraday Return (即日高收益, OPT-0062) backtest / daily list — formula v3.

Re-runs the risk-monitor rule 131-140 formula over past MT trading days and
writes one CSV row per (server, login, trading_day) with every column of
`alert_intraday_return_detail`, so the risk desk can ask "who would have
been flagged on day X at threshold Y" without touching the live scanner.

⚠ END-OF-DAY 口徑 = LOWER BOUND. The live job ticks every 5 minutes and keeps
the intraday PEAK (`peak_return_pct`); this script only sees each day's
closing snapshot (`mt5_daily` / `mt4_daily`), so an account that spiked to
400% at noon and gave half back by the close shows ~200% here. Counts from
this script are therefore a floor on what the live rule fires.

⚠ EOD floating split is approximate. `intraday_profit` needs the day-end
floating P&L split into "positions opened today" (same_day_pnl) and
"overnight positions" (carried_now). The daily tables only store the
ACCOUNT-level floating (ProfitEquity − Balance − Credit / EQUITY − BALANCE −
CREDIT); per-position day-end prices do not exist. When every position still
open at day end was opened today the whole floating is same-day; when every
one is overnight it is all carried; when both kinds are open the floating is
split proportionally by lots. Day-traders (the target population) close
everything intraday, so their rows are exact.

Formula v3 (SSOT: docs/optimization/items/OPT-0062-intraday-return-rule.md,
implemented once in app.services.rule_intraday_return_service.compute_account_metrics
and reused here):

    initial_equity  = prev_day_equity + deposits_in + credit_in
    carried_float0  = prev_eq − prev_bal − prev_credit
    same_day_pnl    = positions opened today: realized + EOD floating
    carried_now     = overnight positions: realized today + EOD floating
    carried_gain    = max(carried_now, 0) − max(carried_float0, 0)
    intraday_profit = same_day_pnl + carried_gain
    return_pct      = 100 × intraday_profit / initial_equity
    net_7d          = realized P&L over the window (incl. today) + EOD floating

Usage (from backend/):
    .venv/bin/python scripts/intraday_return_backtest.py \
        --from 2026-09-14 --to 2026-09-17 --threshold 300 --servers mt5
    .venv/bin/python scripts/intraday_return_backtest.py \
        --from 2026-09-17 --to 2026-09-17 --threshold 100 \
        --send-email --mail-to risk@kcmtrade.com

Data-source rules baked in (do not "optimise" them away):
  - MT5 deals are ranged on `Timestamp` (Windows FILETIME, indexed), never on
    `Time` (19s vs 0.25s measured).
  - `mt4_daily` is only ever hit by (LOGIN, TIME) primary-key point lookups;
    a TIME range scan over the table takes > 300s and gets killed.
  - `mt5_daily` is only ever hit with `Datetime IN (...)` (PK first column);
    a bare `Login` lookup times out.
  - Deposit denylist = NON_DEPOSIT_COMMENT_PREFIXES (Balance Adjustment /
    Adjustment / Initial) — ops adjustments never enter the deposit base.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import pymysql
import pymysql.cursors

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.services.rule_intraday_return_service import (  # noqa: E402
    NON_DEPOSIT_COMMENT_PREFIXES,
    compute_account_metrics,
    compute_behavior_features,
    is_real_flow,
)

DEFAULT_WATCH = "67044208,60006521,60011522"
SERVERS: Dict[str, Dict[str, Any]] = {
    "mt5": {"type": "mt5", "db": "mt5_live", "label": "MT5", "sid": 5},
    "mt4_live": {"type": "mt4", "db": "mt4_live", "label": "MT4_Live", "sid": 1},
    "mt4_live2": {"type": "mt4", "db": "mt4_live2", "label": "MT4_Live2", "sid": 6},
}
PREV_DAY_LOOKBACK = 4  # weekend + one holiday

CSV_COLUMNS = [
    "server", "login", "group", "currency", "trading_day",
    "prev_day_equity", "deposits_in", "credit_in", "withdrawals_out", "adj_excluded",
    "initial_equity", "equity_now", "same_day_pnl", "carried_float0", "carried_now",
    "carried_gain", "intraday_profit", "return_pct", "net_7d", "realized_7d",
    "floating_all_now", "flag_withdraw_gt_half_deposit",
    "trades_today", "lots_today", "median_hold_min", "lock_pct", "top_symbol",
    "pass_net_7d", "hit",
]

# ── SQL helpers ─────────────────────────────────────────────────────────────

FT_EXPR = "((UNIX_TIMESTAMP(%s) + 11644473600) * 10000000)"


def mt5_daily_stamp(day: dt.date) -> int:
    """mt5_daily.Datetime = server-local 23:59:59 stored as if UTC."""
    return int(dt.datetime.combine(day, dt.time(23, 59, 59), dt.timezone.utc).timestamp())


def is_demo(group: Any, name: Any = None) -> bool:
    g = str(group or "").lower()
    n = str(name or "").lower()
    return "demo" in g or "test" in g or "demo" in n or "test" in n


def parse_day(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def in_chunks(seq: List[int], size: int = 800) -> Iterable[List[int]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class Db:
    def __init__(self) -> None:
        s = get_settings()
        self.conn = pymysql.connect(
            host=s.DB_HOST, user=s.DB_USER, password=s.DB_PASSWORD,
            port=int(s.DB_PORT), charset=s.DB_CHARSET,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10, read_timeout=600, autocommit=True,
        )
        self.q("SET SESSION MAX_EXECUTION_TIME=240000")

    def q(self, sql: str, args: Any = None) -> List[Dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            return list(cur.fetchall())

    def currency_map(self, sid: int, logins: Iterable[int]) -> Dict[int, str]:
        out: Dict[int, str] = {}
        ls = list(logins)
        for chunk in in_chunks(ls):
            keys = [f"{sid}-{l}" for l in chunk]
            rows = self.q(
                "SELECT loginSid, UPPER(CURRENCY) AS cur FROM fxbackoffice.mt4_users "
                f"WHERE loginSid IN ({','.join(['%s'] * len(keys))})", keys,
            )
            for r in rows:
                out[int(str(r["loginSid"]).split("-", 1)[1])] = r["cur"] or "USD"
        return out


# ── MT5 ─────────────────────────────────────────────────────────────────────

def mt5_daily_rows(db: Db, days: List[dt.date], logins: Optional[List[int]] = None
                   ) -> Dict[Tuple[dt.date, int], Dict[str, Any]]:
    stamps = [mt5_daily_stamp(d) for d in days]
    out: Dict[Tuple[dt.date, int], Dict[str, Any]] = {}
    login_chunks: List[Optional[List[int]]] = list(in_chunks(logins)) if logins else [None]
    for chunk in login_chunks:
        sql = (
            "SELECT Datetime AS d, Login AS login, `Group` AS g, Name AS n, "
            "ProfitEquity AS eq, Balance AS bal, Credit AS credit "
            f"FROM mt5_live.mt5_daily WHERE Datetime IN ({','.join(['%s'] * len(stamps))})"
        )
        args: List[Any] = list(stamps)
        if chunk:
            sql += f" AND Login IN ({','.join(['%s'] * len(chunk))})"
            args += chunk
        for r in db.q(sql, args):
            day = dt.datetime.fromtimestamp(int(r["d"]), dt.timezone.utc).date()
            out[(day, int(r["login"]))] = r
    return out


def mt5_day(db: Db, day: dt.date, *, window: int, positions_now: Dict[int, List[Dict[str, Any]]]
            ) -> Dict[int, Dict[str, Any]]:
    """Per-login raw inputs + behaviour positions for one MT5 trading day."""
    d0 = f"{day} 00:00:00"
    d1 = f"{day + dt.timedelta(days=1)} 00:00:00"
    d2 = f"{day + dt.timedelta(days=2)} 00:00:00"
    w0 = f"{day - dt.timedelta(days=window - 1)} 00:00:00"
    day_start = dt.datetime.combine(day, dt.time())
    day_end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time())

    # Trades over [window start, D+2): realized_7d, EOD-open derivation, D's positions.
    deals = db.q(
        "SELECT d.Login AS login, d.PositionID AS pid, d.Action AS action, d.Entry AS entry, "
        "d.Symbol AS symbol, d.Volume/10000 AS lots, d.Profit AS profit, d.Storage AS storage, "
        "d.Commission AS commission, d.Time AS t "
        "FROM mt5_live.mt5_deals d INNER JOIN mt5_live.mt5_users u ON u.Login = d.Login "
        f"WHERE d.Timestamp >= {FT_EXPR} AND d.Timestamp < {FT_EXPR} AND d.Action IN (0,1) "
        "AND u.`Group` NOT LIKE '%%demo%%' AND u.`Group` NOT LIKE '%%test%%' "
        "AND COALESCE(u.Name,'') NOT LIKE '%%demo%%' AND COALESCE(u.Name,'') NOT LIKE '%%test%%'",
        (w0, d2),
    )
    flows = db.q(
        "SELECT d.Login AS login, d.Action AS action, d.Profit AS profit, d.Comment AS comment "
        "FROM mt5_live.mt5_deals d INNER JOIN mt5_live.mt5_users u ON u.Login = d.Login "
        f"WHERE d.Timestamp >= {FT_EXPR} AND d.Timestamp < {FT_EXPR} AND d.Action IN (2,3) "
        "AND u.`Group` NOT LIKE '%%demo%%' AND u.`Group` NOT LIKE '%%test%%'",
        (d0, d1),
    )

    acc: Dict[int, Dict[str, Any]] = {}

    def slot(login: int) -> Dict[str, Any]:
        s = acc.get(login)
        if s is None:
            s = {"raw": defaultdict(float), "positions": [], "open_meta": {}, "closes": {},
                 "eod_open_same": 0.0, "eod_open_carried": 0.0}
            acc[login] = s
        return s

    opens: Dict[Tuple[int, int], Dict[str, Any]] = {}
    closes: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for r in deals:
        key = (int(r["login"]), int(r["pid"]))
        if int(r["entry"]) in (0, 2):
            opens[key] = r
        if int(r["entry"]) in (1, 2, 3):
            c = closes.setdefault(key, {"t": r["t"], "lots": 0.0, "pnl": 0.0, "symbol": r["symbol"],
                                        "dir": "S" if int(r["action"]) == 0 else "B"})
            c["lots"] += float(r["lots"] or 0)
            c["pnl"] += float(r["profit"] or 0) + float(r["storage"] or 0) + float(r["commission"] or 0)
            if r["t"] > c["t"]:
                c["t"] = r["t"]

    # realized over the window (incl. today) + today's split
    for key, c in closes.items():
        login, pid = key
        if c["t"] >= day_end:
            continue  # closed after D: not realized in D's window
        s = slot(login)
        s["raw"]["realized_7d"] += c["pnl"]
        if day_start <= c["t"] < day_end:
            o = opens.get(key)
            same_day = o is not None and day_start <= o["t"] < day_end
            if same_day:
                s["raw"]["same_day_pnl"] += c["pnl"]
                s["positions"].append({"symbol": o["symbol"], "direction": "B" if int(o["action"]) == 0 else "S",
                                       "lots": float(o["lots"] or 0), "open_time": o["t"], "close_time": c["t"]})
            else:
                s["raw"]["carried_now"] += c["pnl"]
                s["positions"].append({"symbol": c["symbol"], "direction": c["dir"], "lots": c["lots"],
                                       "open_time": day_start - dt.timedelta(seconds=1), "close_time": c["t"]})

    # EOD-open positions: opened ≤ D end, no close ≤ D end.
    for key, o in opens.items():
        if o["t"] >= day_end:
            continue
        c = closes.get(key)
        if c is not None and c["t"] < day_end:
            continue
        login = key[0]
        s = slot(login)
        same_day = o["t"] >= day_start
        lots = float(o["lots"] or 0)
        if same_day:
            s["eod_open_same"] += lots
        else:
            s["eod_open_carried"] += lots
        s["positions"].append({"symbol": o["symbol"], "direction": "B" if int(o["action"]) == 0 else "S",
                               "lots": lots, "open_time": o["t"], "close_time": None})
    # Opened before the window, closed after D end → open at D end (carried).
    for key, c in closes.items():
        if key in opens or c["t"] < day_end:
            continue
        s = slot(key[0])
        s["eod_open_carried"] += c["lots"]
        s["positions"].append({"symbol": c["symbol"], "direction": c["dir"], "lots": c["lots"],
                               "open_time": day_start - dt.timedelta(seconds=1), "close_time": None})
    # Still open now with TimeCreate ≤ D end and not seen above → carried.
    seen_keys = set(opens) | set(closes)
    for login, plist in positions_now.items():
        for p in plist:
            if p["t"] >= day_end or (login, p["pid"]) in seen_keys:
                continue
            s = slot(login)
            s["eod_open_carried"] += p["lots"]
            s["positions"].append({"symbol": p["symbol"], "direction": p["dir"], "lots": p["lots"],
                                   "open_time": day_start - dt.timedelta(seconds=1), "close_time": None})

    for r in flows:
        s = slot(int(r["login"]))
        p = float(r["profit"] or 0)
        if int(r["action"]) == 2:
            if is_real_flow(r["comment"]):
                if p > 0:
                    s["raw"]["dep_in"] += p
                else:
                    s["raw"]["withdrawals_out"] += -p
            else:
                s["raw"]["adj_excluded"] += p
        elif p > 0:
            s["raw"]["cred_in"] += p
    return acc


def mt5_positions_now(db: Db) -> Dict[int, List[Dict[str, Any]]]:
    rows = db.q(
        "SELECT p.Login AS login, p.Position AS pid, p.Symbol AS symbol, p.Action AS action, "
        "p.Volume/10000 AS lots, p.TimeCreate AS t FROM mt5_live.mt5_positions p"
    )
    out: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[int(r["login"])].append({"pid": int(r["pid"]), "symbol": r["symbol"],
                                     "dir": "B" if int(r["action"]) == 0 else "S",
                                     "lots": float(r["lots"] or 0), "t": r["t"]})
    return out


# ── MT4 ─────────────────────────────────────────────────────────────────────

def mt4_day(db: Db, dbname: str, day: dt.date, *, window: int) -> Dict[int, Dict[str, Any]]:
    d0 = f"{day} 00:00:00"
    d1 = f"{day + dt.timedelta(days=1)} 00:00:00"
    d_end_lit = f"{day} 23:59:59"
    w0 = f"{day - dt.timedelta(days=window - 1)} 00:00:00"
    day_start = dt.datetime.combine(day, dt.time())
    day_end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time())
    demo_sql = (
        "AND u.`GROUP` NOT LIKE '%%demo%%' AND u.`GROUP` NOT LIKE '%%test%%' "
        "AND COALESCE(u.NAME,'') NOT LIKE '%%demo%%' AND COALESCE(u.NAME,'') NOT LIKE '%%test%%' "
        "AND t.LOGIN NOT LIKE '7%%' "
    )
    sel = ("SELECT t.LOGIN AS login, t.TICKET AS ticket, t.CMD AS cmd, t.SYMBOL AS symbol, "
           "t.VOLUME/100 AS lots, t.OPEN_TIME AS ot, t.CLOSE_TIME AS ct, t.PROFIT AS profit, "
           "t.SWAPS AS storage, t.COMMISSION AS commission, t.COMMENT AS comment "
           f"FROM {dbname}.mt4_trades t INNER JOIN {dbname}.mt4_users u ON u.LOGIN = t.LOGIN ")
    closed = db.q(sel + "WHERE t.CLOSE_TIME >= %s AND t.CLOSE_TIME < %s AND t.CMD IN (0,1,6,7) " + demo_sql,
                  (w0, d1))
    # Open at D end: closed after D end, or never closed — both with OPEN_TIME ≤ D end.
    open_eod = db.q(
        sel + "WHERE t.CMD IN (0,1) AND t.OPEN_TIME <= %s "
        "AND (t.CLOSE_TIME = '1970-01-01 00:00:00' OR t.CLOSE_TIME > %s) " + demo_sql,
        (d_end_lit, d_end_lit),
    )

    acc: Dict[int, Dict[str, Any]] = {}

    def slot(login: int) -> Dict[str, Any]:
        s = acc.get(login)
        if s is None:
            s = {"raw": defaultdict(float), "positions": [], "eod_open_same": 0.0, "eod_open_carried": 0.0}
            acc[login] = s
        return s

    for r in closed:
        login = int(r["login"])
        cmd = int(r["cmd"])
        p = float(r["profit"] or 0)
        today = day_start <= r["ct"] < day_end
        if cmd in (6, 7):
            if not today:
                continue
            s = slot(login)
            if cmd == 6:
                if is_real_flow(r["comment"]):
                    if p > 0:
                        s["raw"]["dep_in"] += p
                    else:
                        s["raw"]["withdrawals_out"] += -p
                else:
                    s["raw"]["adj_excluded"] += p
            elif p > 0:
                s["raw"]["cred_in"] += p
            continue
        pnl = p + float(r["storage"] or 0) + float(r["commission"] or 0)
        s = slot(login)
        s["raw"]["realized_7d"] += pnl
        if today:
            if r["ot"] >= day_start:
                s["raw"]["same_day_pnl"] += pnl
            else:
                s["raw"]["carried_now"] += pnl
            s["positions"].append({"symbol": r["symbol"], "direction": "B" if cmd == 0 else "S",
                                   "lots": float(r["lots"] or 0), "open_time": r["ot"], "close_time": r["ct"]})
    for r in open_eod:
        s = slot(int(r["login"]))
        lots = float(r["lots"] or 0)
        if r["ot"] >= day_start:
            s["eod_open_same"] += lots
        else:
            s["eod_open_carried"] += lots
        s["positions"].append({"symbol": r["symbol"], "direction": "B" if int(r["cmd"]) == 0 else "S",
                               "lots": lots, "open_time": r["ot"], "close_time": None})
    return acc


def mt4_daily_rows(db: Db, dbname: str, days: List[dt.date], logins: List[int]
                   ) -> Dict[Tuple[dt.date, int], Dict[str, Any]]:
    out: Dict[Tuple[dt.date, int], Dict[str, Any]] = {}
    times = [f"{d} 23:59:59" for d in days]
    for chunk in in_chunks(logins):
        rows = db.q(
            "SELECT LOGIN AS login, TIME AS t, `GROUP` AS g, EQUITY AS eq, BALANCE AS bal, CREDIT AS credit "
            f"FROM {dbname}.mt4_daily WHERE LOGIN IN ({','.join(['%s'] * len(chunk))}) "
            f"AND TIME IN ({','.join(['%s'] * len(times))})",
            (*chunk, *times),
        )
        for r in rows:
            out[(r["t"].date(), int(r["login"]))] = r
    return out


# ── evaluation ──────────────────────────────────────────────────────────────

def evaluate(server_label: str, day: dt.date, login: int, s: Dict[str, Any], *,
             prev: Optional[Dict[str, Any]], eod: Dict[str, Any], group: str, currency: str,
             args: argparse.Namespace) -> Dict[str, Any]:
    raw = dict(s["raw"])
    prev_eq = float((prev or {}).get("eq") or 0)
    prev_bal = float((prev or {}).get("bal") or 0)
    prev_credit = float((prev or {}).get("credit") or 0)
    eq_eod = float(eod.get("eq") or 0)
    bal_eod = float(eod.get("bal") or 0)
    credit_eod = float(eod.get("credit") or 0)
    floating_eod = eq_eod - bal_eod - credit_eod
    # EOD floating split by what was open at day end (approximate when mixed).
    same_lots, carried_lots = s["eod_open_same"], s["eod_open_carried"]
    tot = same_lots + carried_lots
    if tot > 0:
        share_same = same_lots / tot
    else:
        share_same = 0.0  # nothing open → floating ≈ 0 anyway; anything left is carried
    raw["same_day_pnl"] = raw.get("same_day_pnl", 0.0) + floating_eod * share_same
    raw["carried_now"] = raw.get("carried_now", 0.0) + floating_eod * (1 - share_same)
    raw.update(prev_eq=prev_eq, prev_bal=prev_bal, prev_credit=prev_credit,
               floating_all_now=floating_eod, balance_now=bal_eod, credit_now=credit_eod)
    divisor = 100.0 if currency == "CEN" else 1.0
    m = compute_account_metrics(raw, divisor=divisor,
                                include_deposits_in_base=not args.no_deposits_in_base)
    day_start = dt.datetime.combine(day, dt.time())
    day_end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time())
    feats = compute_behavior_features(s["positions"], day_start=day_start, now_local=day_end,
                                      lock_ratio_min=0.5, divisor=divisor)
    ret = m["return_pct"]
    pass_net = m["net_7d"] >= args.min_net_7d
    hit = (ret is not None and m["initial_equity"] >= args.min_initial_equity
           and m["intraday_profit"] >= args.min_profit and ret >= args.threshold and pass_net)
    return {
        "server": server_label, "login": login, "group": group, "currency": currency,
        "trading_day": str(day),
        **{k: v for k, v in m.items() if k != "balance_now"},
        "trades_today": feats["trades_today"], "lots_today": feats["lots_today"],
        "median_hold_min": (round(feats["median_hold_sec"] / 60, 1)
                            if feats["median_hold_sec"] is not None else None),
        "lock_pct": feats["lock_pct"], "top_symbol": feats["top_symbol"],
        "pass_net_7d": pass_net, "hit": hit,
    }


def run_day(db: Db, day: dt.date, args: argparse.Namespace, servers: List[str],
            positions_now: Dict[int, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    prev_days = [day - dt.timedelta(days=k) for k in range(1, PREV_DAY_LOOKBACK + 1)]
    rows: List[Dict[str, Any]] = []
    for key in servers:
        srv = SERVERS[key]
        if srv["type"] == "mt5":
            acc = mt5_day(db, day, window=args.net_window, positions_now=positions_now)
            logins = sorted(acc)
            if not logins:
                continue
            daily = mt5_daily_rows(db, [day] + prev_days, logins)
        else:
            acc = mt4_day(db, srv["db"], day, window=args.net_window)
            logins = sorted(acc)
            if not logins:
                continue
            daily = mt4_daily_rows(db, srv["db"], [day] + prev_days, logins)
        cur_map = db.currency_map(srv["sid"], logins)
        for login in logins:
            eod = daily.get((day, login))
            if eod is None:
                continue  # no day-end row → account not on the server that day
            if is_demo(eod.get("g"), eod.get("n")):
                continue
            prev = next((daily.get((d, login)) for d in prev_days if daily.get((d, login))), None)
            rows.append(evaluate(srv["label"], day, login, acc[login], prev=prev, eod=eod,
                                 group=str(eod.get("g") or ""), currency=cur_map.get(login, "USD"),
                                 args=args))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="day_from", type=parse_day, required=True, help="first MT trading day (inclusive)")
    ap.add_argument("--to", dest="day_to", type=parse_day, required=True, help="last MT trading day (inclusive)")
    ap.add_argument("--threshold", type=float, default=300.0, help="min_return_pct (default 300)")
    ap.add_argument("--min-initial-equity", type=float, default=50.0, help="门槛 min_initial_equity_usd (default 50)")
    ap.add_argument("--min-profit", type=float, default=30.0, help="门槛 min_profit_usd (default 30)")
    ap.add_argument("--min-net-7d", type=float, default=0.0, help="门槛 min_net_7d_usd (default 0)")
    ap.add_argument("--net-window", type=int, default=7, help="net_window_days (default 7)")
    ap.add_argument("--servers", default="mt5,mt4_live,mt4_live2", help="comma list of mt5,mt4_live,mt4_live2")
    ap.add_argument("--csv", default=None, help="output CSV path (default backend/data/tmp/intraday_return_backtest_<from>_<to>.csv)")
    ap.add_argument("--watch", default=DEFAULT_WATCH, help="comma list of logins always printed")
    ap.add_argument("--no-deposits-in-base", action="store_true", help="initial_equity = prev_day_equity only")
    ap.add_argument("--send-email", action="store_true", help="email the CSV via SMTP")
    ap.add_argument("--mail-to", dest="mail_to", default="kieran.xiang@kohleservices.com", help="recipients for --send-email")
    args = ap.parse_args()

    servers = [s.strip() for s in args.servers.split(",") if s.strip()]
    for s in servers:
        if s not in SERVERS:
            ap.error(f"unknown server {s!r}")
    watch = {int(x) for x in args.watch.split(",") if x.strip()}
    csv_path = Path(args.csv) if args.csv else (
        BACKEND_ROOT / "data" / "tmp" / f"intraday_return_backtest_{args.day_from}_{args.day_to}.csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    db = Db()
    positions_now = mt5_positions_now(db) if "mt5" in servers else {}
    all_rows: List[Dict[str, Any]] = []
    day = args.day_from
    while day <= args.day_to:
        rows = run_day(db, day, args, servers, positions_now)
        all_rows.extend(rows)
        hits = [r for r in rows if r["return_pct"] is not None
                and r["initial_equity"] >= args.min_initial_equity
                and r["intraday_profit"] >= args.min_profit and r["return_pct"] >= args.threshold]
        gated = [r for r in hits if r["pass_net_7d"]]
        print(f"{day}: evaluated={len(rows)} hits>={args.threshold:g}%={len(hits)} after net_{args.net_window}d>={args.min_net_7d:g}: {len(gated)}")
        for r in sorted(gated, key=lambda r: -r["return_pct"]):
            print(f"   HIT {r['server']:9s} {r['login']:9d} init={r['initial_equity']:10.2f} "
                  f"profit={r['intraday_profit']:10.2f} ret={r['return_pct']:8.1f}% net7d={r['net_7d']:10.2f} "
                  f"trades={r['trades_today']} {r['top_symbol']}")
        for r in rows:
            if r["login"] in watch:
                print(f"   WATCH {r['server']:9s} {r['login']:9d} prev_eq={r['prev_day_equity']:.2f} "
                      f"dep={r['deposits_in']:.2f} cred={r['credit_in']:.2f} init={r['initial_equity']:.2f} "
                      f"same_day={r['same_day_pnl']:.2f} carried_now={r['carried_now']:.2f} "
                      f"carried0={r['carried_float0']:.2f} profit={r['intraday_profit']:.2f} "
                      f"ret={r['return_pct']} net7d={r['net_7d']:.2f} hit={r['hit']}")
        day += dt.timedelta(days=1)

    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(all_rows, key=lambda r: (r["trading_day"], -(r["return_pct"] or -1e9))):
            w.writerow(r)
    n_hit = sum(1 for r in all_rows if r["hit"])
    print(f"\nCSV: {csv_path}  rows={len(all_rows)} hits={n_hit}")

    if args.send_email:
        from app.services.email_service import send_email
        subject = (f"[Intraday Return backtest] {args.day_from} ~ {args.day_to} "
                   f"threshold {args.threshold:g}% — {n_hit} hit(s)")
        body = (f"<p>Intraday Return (OPT-0062, formula v3, end-of-day lower bound).</p>"
                f"<p>Days {args.day_from} ~ {args.day_to}; threshold {args.threshold:g}%; "
                f"min initial equity {args.min_initial_equity:g} / min profit {args.min_profit:g} / "
                f"min net {args.net_window}d {args.min_net_7d:g}; servers {', '.join(servers)}.</p>"
                f"<p>{n_hit} hit(s) over {len(all_rows)} evaluated account-days. Full list attached.</p>")
        send_email(subject=subject, body=body, to=args.mail_to, attachments=[str(csv_path)])
        print(f"email sent to {args.mail_to}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
