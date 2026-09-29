"""Offline xlsx export for exec-compensation ranges above the online deal cap.

The online API refuses a query touching more than ``EXEC_COMP_MAX_DEALS``
fills (QUERY_TOO_LARGE, "请联系 IT 手动处理"). This is that manual path: it
runs the same query core (``query.compute_uncached``) one date chunk at a
time with the cap and the time budget lifted, concatenates the rows, builds
ONE summary with ``query.build_summary`` over all of them (open-position
status depends only on as_of, never on the range, so chunks are additive;
delay percentiles are recomputed over every row, never averaged) and writes
the same two-sheet xlsx as the API, three notices in row 1 included.

Run it OFF-PEAK: it reads the mt5_live replica (read-only, every statement
still under MAX_EXECUTION_TIME via connect_readonly), chunk after chunk, for
as long as the range needs. Each chunk is one replica run; keep --chunk-days
small enough that a chunk stays in the tens of thousands of fills.

Usage (from backend/):
    .venv/bin/python scripts/exec_comp_offline_export.py \\
        --client-id 153034 --date-from 2026-08-29 --date-to 2026-09-28 \\
        [--as-of 2026-09-28] [--chunk-days 7] --out /path/out.xlsx
    (or --login-sid 5-<login> instead of --client-id)
Exit code 0 = written; 2 = rejected parameters (the error code is printed).
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings  # noqa: E402
from app.services.exec_comp import export, query  # noqa: E402
from app.services.exec_comp.errors import ExecCompError  # noqa: E402
from app.services.exec_comp.source import ReplicaFillSource  # noqa: E402

# Offline only: no deal cap, a generous per-chunk budget.
OFFLINE_MAX_DEALS = 10**9
OFFLINE_BUDGET_S = 3600


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def offline_settings():
    s = copy.copy(get_settings())
    s.EXEC_COMP_MAX_DEALS = OFFLINE_MAX_DEALS
    s.EXEC_COMP_QUERY_BUDGET_S = OFFLINE_BUDGET_S
    return s


def chunks(date_from: dt.date, date_to: dt.date, days: int):
    cur = date_from
    while cur <= date_to:
        end = min(date_to, cur + dt.timedelta(days=days - 1))
        yield cur, end
        cur = end + dt.timedelta(days=1)


def run(
    *, client_id, login_sid, date_from: dt.date, date_to: dt.date, as_of: dt.date,
    chunk_days: int, settings, source_factory=None, clock=None, log=print,
):
    """Returns (SummaryResponse, rows sorted by fill time, unit_checked)."""
    if chunk_days < 1:
        raise ExecCompError("VALIDATION_ERROR", "--chunk-days must be >= 1")
    source_factory = source_factory or (lambda: ReplicaFillSource(settings))
    eff_to = min(date_to, as_of)
    if date_from > eff_to:
        raise ExecCompError("RANGE_INVALID", f"date_from is after {eff_to.isoformat()}")
    rows: list[dict] = []
    logins = None
    unmatched = 0
    complete = True
    reason = None
    coverage = None
    unit_checked = 0
    for c0, c1 in chunks(date_from, eff_to, chunk_days):
        t0 = time.perf_counter()
        res = query.compute_uncached(
            query.Query(client_id=client_id, login_sid=login_sid, date_from=c0, date_to=c1, as_of=as_of),
            source=source_factory(),
            clock=clock,
            settings=settings,
        )
        if logins is None:
            logins = [int(g["key"].split("-", 1)[1]) for g in res.summary["by_account"]]
        rows.extend(res.rows)
        unmatched += res.coverage["unmatched_deals"]
        complete = complete and res.coverage["complete"]
        reason = reason or res.coverage["reason"]
        coverage = res.coverage
        unit_checked += res.unit_checked
        log(f"  {c0}..{c1}: {res.ids:,} deal ids, {len(res.rows):,} rows "
            f"({time.perf_counter() - t0:.1f}s)")
        del res

    summary_dict = query.build_summary(
        rows,
        logins=logins or [],
        client_id=client_id,
        login_sid=login_sid,
        date_from=date_from,
        date_to=eff_to,
        date_to_requested=date_to,
        as_of=as_of,
    )
    coverage = {**coverage, "unmatched_deals": unmatched, "complete": complete, "reason": reason}
    resp = query.summary_response(summary_dict, as_of, coverage)
    rows.sort(key=lambda r: (r["fill_time_srv"], r["deal_id"]))
    return resp, rows, unit_checked


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--client-id", type=int)
    who.add_argument("--login-sid", help="5-<login>")
    ap.add_argument("--date-from", type=_date, required=True)
    ap.add_argument("--date-to", type=_date, required=True)
    ap.add_argument("--as-of", type=_date, help="default = yesterday (MT server day)")
    ap.add_argument("--chunk-days", type=int, default=7)
    ap.add_argument("--out", required=True, help="path.xlsx")
    args = ap.parse_args(argv)

    as_of = args.as_of or (query._today_srv(None) - dt.timedelta(days=1))
    settings = offline_settings()
    print(f"exec-comp offline export: {args.client_id or args.login_sid} "
          f"{args.date_from}..{args.date_to} as_of {as_of}, chunks of {args.chunk_days} days")
    t0 = time.perf_counter()
    try:
        resp, rows, _ = run(
            client_id=args.client_id, login_sid=args.login_sid, date_from=args.date_from,
            date_to=args.date_to, as_of=as_of, chunk_days=args.chunk_days, settings=settings,
        )
    except ExecCompError as exc:
        print(f"rejected: {exc.code}: {exc.message}", file=sys.stderr)
        return 2
    data = export.build_xlsx(resp, rows)
    with open(args.out, "wb") as fh:
        fh.write(data)
    d = resp.data
    print(
        f"wrote {args.out} ({len(data) / 1e6:.1f} MB, {len(rows):,} rows, "
        f"{time.perf_counter() - t0:.0f}s)\n"
        f"net {d.comp_net_usd} / positive {d.comp_positive_usd} USD, {d.deals:,} deals, "
        f"{d.lots} lots; excluded open {d.excluded_open.positions}/{d.excluded_open.deals}/"
        f"{d.excluded_open.lots}; delay median/p95/max/min {d.delay.median_ms}/{d.delay.p95_ms}/"
        f"{d.delay.max_ms}/{d.delay.min_ms} ms; complete={resp.coverage.complete}, "
        f"unmatched={resp.coverage.unmatched_deals}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
