"""On-demand reads from the mt5_live replica (01 D21) — the real ``FillSource``.

Only the access paths measured safe in 02 §5 are used:

- ``Timestamp`` index to turn an MT server instant into a Deal number;
- ``Login = ? AND Deal in [lo, hi)`` on the covering ``IDX_POSITION(Login,
  PositionID)`` index — Deal numbers only, no table rows read;
- primary-key ``IN`` batches (<= 10,000) of deals LEFT JOIN orders_history by
  its primary key (deals first; orders -> deals has no index and times out);
- ``Login = ? AND PositionID IN (...)`` point lookups for position lifecycles.

Never: a scan by Login alone for table rows, a scan by a time column, or a
join driven from orders_history. Every statement runs under
``MAX_EXECUTION_TIME`` via ``connect_readonly``; the query-wide time budget is
checked between batches.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Iterable, Optional, Sequence

import pymysql

from app.core.config import Settings
from app.core.mysql_readonly import connect_readonly

from .calc import filetime_to_utc, srv_to_utc_calendar, utc_to_filetime
from .errors import ExecCompError
from .limits import Deadline
from .models import Account, PositionLeg, RawFill, ReplicaHead

logger = logging.getLogger(__name__)

FILL_BATCH = 10_000
POSITION_BATCH = 2_000
# pymysql error numbers: server-side MAX_EXECUTION_TIME kill; client read
# timeout / lost connection (the socket gave up before the server did).
_ER_QUERY_TIMEOUT = 3024
_CR_SERVER_LOST = (2013, 2006)

_FILL_SQL = """
SELECT d.Deal, d.`Order` AS d_order, d.PositionID, d.Login, d.Action, d.Entry,
       d.Price, d.Volume, d.ContractSize, d.RateProfit, d.Symbol, d.Dealer,
       d.TimeMsc, d.Timestamp, d.Profit, d.PricePosition,
       o.`Order` AS o_order, o.Type, o.Reason, o.Comment,
       o.PriceCurrent, o.PriceOrder, o.TimeSetupMsc
FROM mt5_live.mt5_deals d
LEFT JOIN mt5_live.mt5_orders_history o ON o.`Order` = d.`Order`
WHERE d.Deal IN ({ids}) AND d.Action IN (0, 1)
"""


def _chunks(seq: Sequence, n: int) -> Iterable[Sequence]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _account(row: dict) -> Account:
    return Account(login=int(row["LOGIN"]), ccy=str(row["CURRENCY"]), group=row["GROUP"] or "")


class ReplicaFillSource:
    """``models.FillSource`` over the replica. One lazily opened connection
    per instance; call ``close()`` (the query core does) when done. Bind a
    budget with ``with_deadline()`` — a fresh instance per query run."""

    def __init__(self, settings: Settings, deadline: Optional[Deadline] = None) -> None:
        self._settings = settings
        self._deadline = deadline
        self._conn = None
        self._stmt_ms = int(settings.EXEC_COMP_STATEMENT_TIMEOUT_MS)

    # --- plumbing -----------------------------------------------------------

    def with_deadline(self, deadline: Deadline) -> "ReplicaFillSource":
        return ReplicaFillSource(self._settings, deadline)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 - closing a dead socket is not an error
                pass
            self._conn = None

    def _check(self, stage: str) -> None:
        if self._deadline is not None:
            self._deadline.check(stage)

    def _cursor(self):
        if self._conn is None:
            # read_timeout strictly above the server kill so the server stops first.
            read_timeout = self._stmt_ms // 1000 + 10
            self._conn = connect_readonly(
                self._settings, max_execution_ms=self._stmt_ms, read_timeout=read_timeout
            )
        return self._conn.cursor()

    def _run(self, sql: str, args: Sequence = ()) -> list[dict]:
        try:
            with self._cursor() as cur:
                cur.execute(sql, args)
                return list(cur.fetchall())
        except pymysql.err.MySQLError as exc:
            code = exc.args[0] if exc.args else None
            if code == _ER_QUERY_TIMEOUT or code in _CR_SERVER_LOST:
                self.close()
                raise ExecCompError(
                    "UPSTREAM_TIMEOUT",
                    "the MT5 replica did not answer in time; narrow the date range or retry later",
                    status=504,
                ) from exc
            raise

    # --- FillSource ---------------------------------------------------------

    def accounts_for_client(self, client_id: int) -> list[Account]:
        rows = self._run(
            "SELECT LOGIN, CURRENCY, `GROUP` FROM fxbackoffice.mt4_users "
            "WHERE userId = %s AND sid = 5",
            (int(client_id),),
        )
        return sorted((_account(r) for r in rows), key=lambda a: a.login)

    def account(self, login: int) -> Optional[Account]:
        # loginSid is the indexed key (LOGIN_SID); LOGIN is an unindexed
        # varchar and `LOGIN = ? AND sid = 5` scans the whole table.
        rows = self._run(
            "SELECT LOGIN, CURRENCY, `GROUP` FROM fxbackoffice.mt4_users "
            "WHERE loginSid = %s AND sid = 5",
            (f"5-{int(login)}",),
        )
        return _account(rows[0]) if rows else None

    def replica_head(self) -> Optional[ReplicaHead]:
        rows = self._run("SELECT MAX(Deal) AS d FROM mt5_live.mt5_deals")
        max_deal = rows[0]["d"] if rows else None
        if max_deal is None:
            return None
        rows = self._run(
            "SELECT Deal, TimeMsc, Timestamp FROM mt5_live.mt5_deals WHERE Deal = %s",
            (int(max_deal),),
        )
        if not rows:
            return None
        r = rows[0]
        return ReplicaHead(
            deal_id=int(r["Deal"]), time_srv=r["TimeMsc"], time_utc=filetime_to_utc(int(r["Timestamp"]))
        )

    def _locate(self, srv: dt.datetime) -> int:
        """First Deal whose true-UTC Timestamp is at/after the MT server
        instant ``srv`` (DST-aware); MAX(Deal)+1 when there is none yet."""
        ft = utc_to_filetime(srv_to_utc_calendar(srv))
        rows = self._run(
            "SELECT Deal FROM mt5_live.mt5_deals WHERE Timestamp >= %s ORDER BY Timestamp LIMIT 1",
            (ft,),
        )
        if rows:
            return int(rows[0]["Deal"])
        rows = self._run("SELECT MAX(Deal) AS d FROM mt5_live.mt5_deals")
        return int(rows[0]["d"] or 0) + 1

    # Deal numbers are located by Timestamp, whose order matches Deal order
    # only up to ties / same-millisecond writes. Widen the window a little;
    # the query core filters every fill by its own server time anyway.
    _LOCATE_MARGIN = dt.timedelta(minutes=10)

    def deal_ids(
        self, logins: Sequence[int], srv_from: dt.datetime, srv_to: dt.datetime
    ) -> list[int]:
        if not logins:
            return []
        lo = self._locate(srv_from - self._LOCATE_MARGIN)
        hi = self._locate(srv_to + self._LOCATE_MARGIN)
        out: list[int] = []
        for login in logins:
            self._check("deal ids")
            rows = self._run(
                "SELECT Deal FROM mt5_live.mt5_deals WHERE Login = %s AND Deal >= %s AND Deal < %s",
                (int(login), lo, hi),
            )
            out.extend(int(r["Deal"]) for r in rows)
        out.sort()
        return out

    def fills(self, deal_ids: Sequence[int]) -> list[RawFill]:
        ids = sorted({int(d) for d in deal_ids})
        out: list[RawFill] = []
        for chunk in _chunks(ids, FILL_BATCH):
            self._check("fills")
            placeholders = ",".join(["%s"] * len(chunk))
            for r in self._run(_FILL_SQL.format(ids=placeholders), chunk):
                found = r["o_order"] is not None
                out.append(
                    RawFill(
                        deal_id=int(r["Deal"]),
                        order_id=int(r["d_order"]),
                        position_id=int(r["PositionID"]),
                        login=int(r["Login"]),
                        action=int(r["Action"]),
                        entry=int(r["Entry"]),
                        price=float(r["Price"]),
                        volume=int(r["Volume"]),
                        contract_size=float(r["ContractSize"]),
                        rate_profit=float(r["RateProfit"]),
                        symbol=str(r["Symbol"]),
                        dealer=int(r["Dealer"]),
                        time_msc=r["TimeMsc"],
                        timestamp_ft=int(r["Timestamp"]),
                        profit=float(r["Profit"]),
                        price_position=float(r["PricePosition"] or 0.0),
                        order_found=found,
                        order_type=int(r["Type"]) if found else None,
                        order_reason=int(r["Reason"]) if found else None,
                        comment=r["Comment"] if found else None,
                        price_current=float(r["PriceCurrent"]) if found else None,
                        price_order=float(r["PriceOrder"]) if found else None,
                        time_setup_msc=r["TimeSetupMsc"] if found else None,
                    )
                )
        return out

    def position_legs(self, positions: Sequence[tuple[int, int]]) -> list[PositionLeg]:
        by_login: dict[int, list[int]] = {}
        for login, pos in positions:
            by_login.setdefault(int(login), []).append(int(pos))
        out: list[PositionLeg] = []
        for login, pos_ids in sorted(by_login.items()):
            for chunk in _chunks(sorted(set(pos_ids)), POSITION_BATCH):
                self._check("position legs")
                placeholders = ",".join(["%s"] * len(chunk))
                rows = self._run(
                    "SELECT Login, PositionID, Deal, Entry, Volume, TimeMsc "
                    "FROM mt5_live.mt5_deals "
                    f"WHERE Login = %s AND PositionID IN ({placeholders}) AND Action IN (0, 1)",
                    (login, *chunk),
                )
                out.extend(
                    PositionLeg(
                        login=int(r["Login"]),
                        position_id=int(r["PositionID"]),
                        deal_id=int(r["Deal"]),
                        entry=int(r["Entry"]),
                        volume=int(r["Volume"]),
                        time_msc=r["TimeMsc"],
                    )
                    for r in rows
                )
        return out
