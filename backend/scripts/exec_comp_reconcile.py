"""Reconcile the exec-compensation query core against the 04 §1 baseline.

Runs ``query.compute_uncached`` against the live MT5 replica (read-only,
through ``connect_readonly``) for client 153034, 2026-08-29..2026-09-28,
as_of 2026-09-28, and checks every figure of
docs/exec-compensation/04-acceptance.md §1.1 / §1.2 / §1.4 exactly. With
``--csv`` it also diffs deal by deal against the prototype CSV (04 §1.3).

Usage (from backend/):
    .venv/bin/python scripts/exec_comp_reconcile.py \
        [--csv ~/exec_comp_proto/comp_153034_mt5_20260829-0929.csv]
Exit code 0 = everything matches.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
from collections import Counter
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings  # noqa: E402
from app.services.exec_comp import query  # noqa: E402
from app.services.exec_comp.source import ReplicaFillSource  # noqa: E402

QUERY = query.Query(
    client_id=153034, login_sid=None,
    date_from=dt.date(2026, 8, 29), date_to=dt.date(2026, 9, 28), as_of=dt.date(2026, 9, 28),
)
# "Now" = the day the baseline was taken, so as_of 09-28 is the default.
CLOCK = dt.datetime(2026, 9, 29, 12, 0, 0)

EXPECTED = {
    "deals": 99916, "lots": 6354.84, "comp_net_usd": 208.2398, "comp_positive_usd": 208.6051,
    "open": (49961, 3178.47, 92.2154), "close": (49955, 3176.37, 116.0244),
    "outcomes": (49697, 50093, 126), "delay": (326, 368, 602, 1),
    "day_0924": 22.1980, "day_0928": 33.7918, "max_single": 0.145,
    "excluded_open": (502, 502, 5.02, 0.0855),
    "sl": (4, 2.00, 0.0600), "close_by": (2, 0.10, 0.0320),
    "unit_checked": 49959,
}


def _eq(a, b, dp=4) -> bool:
    return round(float(a), dp) == round(float(b), dp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="prototype CSV for the per-deal diff (04 §1.3)")
    args = ap.parse_args()

    settings = get_settings()
    res = query.compute_uncached(QUERY, source=ReplicaFillSource(settings), clock=CLOCK, settings=settings)
    s = res.summary
    checks: list[tuple[str, object, object, bool]] = []

    def check(name, expected, actual, ok=None):
        checks.append((name, expected, actual, _eq(expected, actual) if ok is None else ok))

    check("as_of", "2026-09-28", str(res.as_of), str(res.as_of) == "2026-09-28")
    check("coverage.complete", True, res.coverage["complete"], res.coverage["complete"] is True)
    check("deals", EXPECTED["deals"], s["deals"])
    check("lots", EXPECTED["lots"], s["lots"])
    check("comp_net_usd", EXPECTED["comp_net_usd"], s["comp_net_usd"])
    check("comp_positive_usd", EXPECTED["comp_positive_usd"], s["comp_positive_usd"])
    by_entry = {g["key"]: g for g in s["by_entry"]}
    for key in ("open", "close"):
        n, lots, net = EXPECTED[key]
        g = by_entry.get(key, {"deals": 0, "lots": 0, "comp_net_usd": 0})
        check(f"{key}.deals", n, g["deals"]); check(f"{key}.lots", lots, g["lots"])
        check(f"{key}.net", net, g["comp_net_usd"])
    o = s["outcomes"]
    for name, exp in zip(("worse", "same", "better"), EXPECTED["outcomes"]):
        check(f"outcome.{name}", exp, o[name])
    d = s["delay"]
    for name, exp in zip(("median_ms", "p95_ms", "max_ms", "min_ms"), EXPECTED["delay"]):
        check(f"delay.{name}", exp, d[name])
    by_day = {g["key"]: g for g in s["by_day"]}
    check("day 2026-09-24", EXPECTED["day_0924"], by_day["2026-09-24"]["comp_net_usd"])
    check("day 2026-09-28", EXPECTED["day_0928"], by_day["2026-09-28"]["comp_net_usd"])
    check("max_single_comp_usd", EXPECTED["max_single"], s["max_single_comp_usd"])
    ex = s["excluded_open"]
    for name, exp in zip(("positions", "deals", "lots", "comp_net_usd_if_counted"), EXPECTED["excluded_open"]):
        check(f"excluded_open.{name}", exp, ex[name])
    by_cls = {c["cls"]: c for c in s["not_counted_by_class"]}
    for cls in ("sl", "close_by"):
        n, lots, net = EXPECTED[cls]
        c = by_cls.get(cls, {"deals": 0, "lots": 0, "comp_net_usd": 0})
        check(f"{cls}.deals", n, c["deals"]); check(f"{cls}.lots", lots, c["lots"])
        check(f"{cls}.net", net, c["comp_net_usd"])
    check("unit_checked (04 §1.4)", EXPECTED["unit_checked"], res.unit_checked)
    check("unit_mismatches", 0, sum(res.unit_mismatches.values()))

    if args.csv:
        mine = {r["deal_id"]: r for r in res.rows}
        diffs: Counter = Counter()
        with open(os.path.expanduser(args.csv), newline="") as fh:
            proto = {int(r["deal"]): r for r in csv.DictReader(fh)}
        for deal, p in proto.items():
            m = mine.get(deal)
            if m is None:
                diffs["missing_in_system"] += 1
                continue
            if not m["counted"]:
                diffs[f"not_counted:{m['not_counted_reason']}:{m['cls']}"] += 1
                continue
            same = (
                Decimal(p["ref_price"]) == Decimal(str(m["ref_price"]))
                and Decimal(p["fill_price"]) == Decimal(str(m["fill_price"]))
                and int(p["delay_ms"]) == m["delay_ms"]
                and abs(float(p["comp_usd"]) - m["comp_usd"]) <= 0.0001
            )
            diffs["equal" if same else "value_diff"] += 1
        diffs["system_only"] = len(set(mine) - set(proto))
        # 04 §1.3: the only allowed differences are the 502 open-position rows
        # and the 6 rows of another class (4 sl + 2 close_by).
        allowed = {
            "not_counted:position_open_at_as_of:market": 502,
            "not_counted:not_eligible_class:sl": 4,
            "not_counted:not_eligible_class:close_by": 2,
        }
        for k, v in allowed.items():
            check(f"csv {k}", v, diffs.get(k, 0), diffs.get(k, 0) == v)
        check("csv equal rows", EXPECTED["deals"], diffs.get("equal", 0), diffs.get("equal", 0) == EXPECTED["deals"])
        for k in ("value_diff", "missing_in_system", "system_only"):
            check(f"csv {k}", 0, diffs.get(k, 0), diffs.get(k, 0) == 0)
        other = {k: v for k, v in diffs.items() if k not in allowed and k not in ("equal", "value_diff", "missing_in_system", "system_only")}
        check("csv other differences", {}, other, not other)

    width = max(len(c[0]) for c in checks)
    for name, exp, act, ok in checks:
        print(f"{'OK  ' if ok else 'FAIL'} {name:<{width}}  expected={exp}  actual={act}")
    bad = sum(1 for c in checks if not c[3])
    print(f"\n{len(checks) - bad}/{len(checks)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
