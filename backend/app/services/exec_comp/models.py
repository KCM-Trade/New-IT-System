"""Data shapes shared by the source, the pure functions and the query core.

``FillSource`` is the seam between the query core and the MT5 replica: the
real implementation is ``source.ReplicaFillSource``; tests pass a fake.
Everything here is plain data so the pure functions never see a DB cursor.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional, Protocol, Sequence


@dataclass(frozen=True)
class Account:
    login: int
    ccy: str        # mt4_users.CURRENCY; only CEN / USD are valid (02 §4)
    group: str      # mt4_users.GROUP, e.g. "KCMC\\5c_L10"


@dataclass(frozen=True)
class RawFill:
    """One mt5_deals row (Action in {0,1}) LEFT JOIN mt5_orders_history."""

    deal_id: int
    order_id: int
    position_id: int
    login: int
    action: int                  # 0 buy / 1 sell — the direction (02 §4)
    entry: int                   # 0 in / 1 out / 2 inout / 3 out_by
    price: float
    volume: int                  # lots * 10000
    contract_size: float
    rate_profit: float
    symbol: str
    dealer: int
    time_msc: dt.datetime        # MT server wall clock (naive)
    timestamp_ft: int            # Windows FILETIME, true UTC (02 §2)
    profit: float
    price_position: float
    # orders_history side; order_found=False => all below are None (pending)
    order_found: bool
    order_type: Optional[int] = None
    order_reason: Optional[int] = None
    comment: Optional[str] = None
    price_current: Optional[float] = None
    price_order: Optional[float] = None
    time_setup_msc: Optional[dt.datetime] = None


@dataclass(frozen=True)
class PositionLeg:
    """One lifecycle deal of a position, for the fully-closed test (02 §6.1)."""

    login: int
    position_id: int
    deal_id: int
    entry: int
    volume: int
    time_msc: dt.datetime


@dataclass(frozen=True)
class ReplicaHead:
    deal_id: int
    time_srv: dt.datetime
    time_utc: dt.datetime


class FillSource(Protocol):
    """On-demand reads from mt5_live (01 D21). Every method must respect the
    replica rules of 02 §5: PK range / PK IN / (Login, PositionID) index only,
    batches of at most 10,000 rows."""

    def accounts_for_client(self, client_id: int) -> list[Account]: ...

    def account(self, login: int) -> Optional[Account]: ...

    def replica_head(self) -> Optional[ReplicaHead]: ...

    def deal_ids(
        self, logins: Sequence[int], srv_from: dt.datetime, srv_to: dt.datetime
    ) -> list[int]:
        """Deal numbers of these logins whose fill time falls in the MT server
        interval [srv_from, srv_to) — via the Timestamp locate + covering
        (Login, PositionID) index; no table rows are read."""
        ...

    def fills(self, deal_ids: Sequence[int]) -> list[RawFill]:
        """Fetch by primary key (batches <= 10,000), LEFT JOIN orders_history."""
        ...

    def position_legs(
        self, positions: Sequence[tuple[int, int]]
    ) -> list[PositionLeg]:
        """All deals of these (login, position_id) pairs — full lifecycle,
        Login + PositionID IN point lookups (batches <= 2,000 positions)."""
        ...
