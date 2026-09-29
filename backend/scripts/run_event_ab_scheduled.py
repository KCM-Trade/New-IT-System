#!/usr/bin/env python3
"""Daily cron entry for news-event AB scans.

Reads scripts/event_ab_schedule.csv and, for every ENABLED event whose MT date
is yesterday (HK), runs event_ab_scan.py. Running T+1 is deliberate: the
login-IP file for the event day is only generated at 05:10 the next day.

Rows with enabled=no are candidates only. On the run day of the last enabled
event, the runner mails a reminder (kieran) listing the next month's
candidates, so a human decides whether to enable them — nothing past the
enabled range is scanned or mailed automatically (user decision 2026-09-29).

  # cron (HKT): 0 9 * * *  .../backend/.venv/bin/python .../backend/scripts/run_event_ab_scheduled.py \
  #                 --mail-to risk@kcmtrade.com --mail-cc kieran...,lawrence...
  # manual check without scanning or mailing:
  python scripts/run_event_ab_scheduled.py --run-date 2026-10-30 --dry-run
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPTS_DIR.parent
SCHEDULE = SCRIPTS_DIR / "event_ab_schedule.csv"
REMINDER_TO = "kieran.xiang@kohleservices.com"

# MT moves with US DST, HK does not -> HK display offset is 5 in summer, 6 in winter.
US_DST_START = {2026: dt.date(2026, 3, 8), 2027: dt.date(2027, 3, 14)}
US_DST_END = {2026: dt.date(2026, 11, 1), 2027: dt.date(2027, 11, 7)}

TH = "background:#34495e;color:#fff;padding:6px 8px;text-align:left;font-size:12px"
TD = "padding:6px 8px;border-bottom:1px solid #e0e0e0;font-size:13px"
WEEKDAY = "一二三四五六日"


def hk_offset_hours(day: dt.date) -> int:
    start, end = US_DST_START.get(day.year), US_DST_END.get(day.year)
    if start is None or end is None:
        raise SystemExit(f"No US DST dates configured for {day.year} — extend US_DST_* first")
    return 5 if start <= day < end else 6


def load_schedule() -> list[tuple[dt.datetime, bool, str]]:
    rows = []
    with SCHEDULE.open(encoding="utf-8") as f:
        lines = (ln for ln in f if ln.strip() and not ln.startswith("#"))
        for rec in csv.DictReader(lines):
            rows.append((dt.datetime.strptime(rec["event_mt"].strip(), "%Y-%m-%d %H:%M"),
                         rec["enabled"].strip().lower() == "yes",
                         rec["label"].strip()))
    return rows


def fmt_day(d: dt.date) -> str:
    return f"{d:%m-%d}（{WEEKDAY[d.weekday()]}）"


def build_reminder(last_enabled: dt.datetime, candidates) -> tuple[str, str]:
    month = candidates[0][0].strftime("%Y-%m") if candidates else ""
    body_rows = ""
    for t, _, label in candidates:
        hk = t + dt.timedelta(hours=hk_offset_hours(t.date()))
        hk_txt = f"{hk:%H:%M}" if hk.date() == t.date() else f"{hk:%m-%d %H:%M}"
        run_day = t.date() + dt.timedelta(days=1)
        body_rows += "<tr>" + "".join(f'<td style="{TD}">{c}</td>' for c in (
            fmt_day(t.date()), label, f"{t:%H:%M}", hk_txt, f"{fmt_day(run_day)} 09:00")) + "</tr>"
    head = "".join(f'<th style="{TH}">{h}</th>' for h in
                   ("事件日", "事件", "公布时刻 MT", "公布时刻 HK", "扫描执行（HK）"))
    table = (f'<table style="border-collapse:collapse;margin:6px 0 14px"><tr>{head}</tr>{body_rows}</table>'
             if candidates else "<p><b>日程表里没有下一批候选日期</b>，需要先查官方日程补进 event_ab_schedule.csv。</p>")
    html = f"""<div style="font-family:Arial,'Microsoft YaHei',sans-serif;font-size:14px;color:#222;line-height:1.6">
<p>Kieran，</p>
<p>数据公布窗口 AB 仓检测已启用的最后一期（{last_enabled:%Y-%m-%d} {last_enabled:%H:%M} MT）今天已扫描完毕。
<b>之后的数据日目前没有启用，不会自动扫描或发邮件。</b></p>
<p style="font-size:15px;margin:14px 0 4px"><b>{month} 候选数据日 / Next Month Candidates</b></p>
{table}
<p style="font-size:15px;margin:14px 0 4px"><b>如需继续 / To Continue</b></p>
<ul style="margin:4px 0 12px">
<li>回复 Claude「启用 {month} 的数据日」，或把 <code>backend/scripts/event_ab_schedule.csv</code> 中对应行的 <code>enabled</code> 改为 <code>yes</code>。</li>
<li>收件人沿用 10 月设置：risk@kcmtrade.com，CC kieran / lawrence。</li>
<li>启用前建议再核对一次官方日程（bls.gov / bea.gov / federalreserve.gov），日期可能顺延。</li>
</ul>
<p style="color:#888;font-size:12px;margin-top:18px">来自 run_event_ab_scheduled.py 的自动提醒，请勿回复。</p>
</div>"""
    subject = f"[AB 仓检测] 提醒：是否继续设置 {month} 数据日 / Reminder: enable {month} event scans?"
    return subject, html


def main() -> int:
    p = argparse.ArgumentParser(description="Run scheduled news-event AB scans (T+1)")
    p.add_argument("--run-date", type=dt.date.fromisoformat, default=dt.date.today(),
                   help="Pretend today is this HK date (default: today)")
    p.add_argument("--dry-run", action="store_true", help="Print commands / reminder only")
    p.add_argument("--mail-to", default="")
    p.add_argument("--mail-cc", default="")
    args = p.parse_args()

    schedule = load_schedule()
    target = args.run_date - dt.timedelta(days=1)
    due = [(t, label) for t, on, label in schedule if on and t.date() == target]
    print(f"[{dt.datetime.now():%F %T}] run_date={args.run_date} target_mt_date={target} due={len(due)}")

    failures = 0
    for event_mt, label in due:
        cmd = [sys.executable, str(SCRIPTS_DIR / "event_ab_scan.py"),
               "--event-mt", event_mt.strftime("%Y-%m-%d %H:%M"),
               "--label", label,
               "--hk-offset-hours", str(hk_offset_hours(event_mt.date()))]
        if args.mail_to:
            cmd += ["--mail-to", args.mail_to]
        if args.mail_cc:
            cmd += ["--mail-cc", args.mail_cc]
        print("  $", " ".join(cmd))
        if args.dry_run:
            continue
        rc = subprocess.run(cmd, cwd=BACKEND_ROOT).returncode
        print(f"  -> exit {rc}")
        failures += rc != 0

    # Reminder: on the run day of the last enabled event, list next month's candidates.
    enabled = [t for t, on, _ in schedule if on]
    if enabled and args.run_date == max(enabled).date() + dt.timedelta(days=1):
        last = max(enabled)
        later = sorted((r for r in schedule if not r[1] and r[0] > last), key=lambda r: r[0])
        month = later[0][0].strftime("%Y-%m") if later else None
        candidates = [r for r in later if r[0].strftime("%Y-%m") == month]
        subject, html = build_reminder(last, candidates)
        print(f"  [REMIND] to={REMINDER_TO} candidates={len(candidates)} subject={subject}")
        if not args.dry_run:
            sys.path.insert(0, str(BACKEND_ROOT))
            from app.services.email_service import send_email
            try:
                send_email(subject=subject, body=html, to=REMINDER_TO)
            except Exception as exc:  # scans already ran; surface the failure in the cron log
                print(f"  [REMIND] FAILED: {exc}")
                failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
