"""Pydantic models for the trade-IP profit attribution API (OPT-0063 Phase 2).

All endpoints sit under `/api/v1/login-ip/trade-profit/*` and are gated to the
`risk` module (the /login-ips page itself is `cs`; this one tab is risk-only).
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# /groups
# ---------------------------------------------------------------------------


class TradeProfitDailyPoint(BaseModel):
    date: str  # YYYY-MM-DD (MT close day)
    profit_usd: float


class TradeProfitGroupRow(BaseModel):
    """One account group in the list view (one row per union-find component)."""

    group_id: str
    profit_usd: float
    trades: int
    lots: float
    accounts: int
    clients: int
    ibs: int
    same_client: bool  # True = 一人多户 (1 client, >= 2 accounts)
    shared_ips: int
    active_days: int
    profitable_days: int
    dominant_symbol: str
    dominant_symbol_share: float
    avg_hold_min: float
    daily: List[TradeProfitDailyPoint]


class TradeProfitStatistics(BaseModel):
    from_cache: bool = False
    query_time_ms: int = 0
    # Window context so the reader can reconcile groups against coverage:
    # with-IP trades in the window vs. trades inside the returned groups
    # (the difference is solo accounts on private IPs, which form no group).
    window_with_ip_trades: int = 0
    groups_trades: int = 0


class TradeProfitGroupsResponse(BaseModel):
    data: List[TradeProfitGroupRow]
    total: int
    page: int
    page_size: int
    total_pages: int
    statistics: TradeProfitStatistics


# ---------------------------------------------------------------------------
# /groups/{group_id}
# ---------------------------------------------------------------------------


class TradeProfitGroupAccount(BaseModel):
    account_key: str  # '{server}-{login}', e.g. 'MT5-67043240'
    server: str
    account_id: int
    user_id: Optional[int] = None
    ib_id: Optional[int] = None
    trades: int
    profit_usd: float
    lots: float
    active_days: int
    avg_hold_min: float
    dominant_symbol: str


class TradeProfitGroupIp(BaseModel):
    ip: str
    accounts: int  # member accounts seen on this IP in the window
    bridge: bool  # True = this IP created at least one edge inside the group
    country: Optional[str] = None  # cache-only geo; never billed per page view
    window_clients: Optional[int] = None  # distinct CRM clients on this IP (whole window)
    window_active_days: Optional[int] = None


class TradeProfitGroupDetailResponse(BaseModel):
    group: dict[str, Any]  # the full group row (incl. accounts_detail + daily)
    member_ips: List[TradeProfitGroupIp]
    params: dict[str, Any]  # the threshold set the group_id is valid under
    statistics: dict[str, Any]


# ---------------------------------------------------------------------------
# /ips
# ---------------------------------------------------------------------------


class TradeProfitIpRow(BaseModel):
    ip: str
    country: Optional[str] = None
    profit_usd: float
    trades: int
    lots: float
    accounts: int
    clients: int
    ibs: int
    active_days: int
    dominant_symbol: str
    dominant_symbol_share: float
    avg_hold_min: float
    shared_exit: bool  # clients >= public_ip_clients (carrier NAT / VPN)


class TradeProfitIpsResponse(BaseModel):
    data: List[TradeProfitIpRow]
    total: int
    page: int
    page_size: int
    total_pages: int
    statistics: TradeProfitStatistics


# ---------------------------------------------------------------------------
# /coverage
# ---------------------------------------------------------------------------


class TradeProfitNoIpCause(BaseModel):
    cause: str
    trades: int
    profit_usd: float


class TradeProfitIncompleteLog(BaseModel):
    date: str  # YYYY-MM-DD
    server: str


class TradeProfitCoverageResponse(BaseModel):
    date_from: str
    date_to: str
    data_from: str  # first day with order_ip data (Phase 1 go-live backfill)
    total_trades: int
    total_profit_usd: float
    with_ip_trades: int
    with_ip_profit_usd: float
    no_ip_trades: int
    no_ip_profit_usd: float
    no_ip_by_cause: List[TradeProfitNoIpCause]
    # (date, server) whose journal was never parsed — the day would otherwise
    # present as "suspiciously low profit" weeks later with no explanation.
    incomplete_logs: List[TradeProfitIncompleteLog]
    # Window days with zero reconciled rows (reconcile never ran for them).
    unreconciled_dates: List[str]


# ---------------------------------------------------------------------------
# /lookup (OPT-0063 Option A — point lookup by client / account / IP)
# ---------------------------------------------------------------------------


class TradeProfitLookupAccount(BaseModel):
    """One account aggregate in a lookup result (mirrors group detail fields)."""

    account_key: str
    server: str
    account_id: int
    user_id: Optional[int] = None
    ib_id: Optional[int] = None
    trades: int
    profit_usd: float
    lots: float
    active_days: int
    avg_hold_min: float
    dominant_symbol: str
    open_ips: List[str]  # distinct open IPs this account used in the window
    is_seed: bool = False  # True = direct query hit (ID or IP mode)


class TradeProfitLookupResponse(BaseModel):
    query: str
    query_kind: str  # "id" | "ip"
    matched_as: Optional[List[str]] = None  # id mode: ["account_id"] / ["user_id"] / both
    window: dict[str, str]
    group_ids: List[str]
    seed_accounts: List[TradeProfitLookupAccount]
    # Full peer set on the seed IP(s), including seed rows (is_seed marks them).
    peer_accounts: List[TradeProfitLookupAccount]
    seed_ips: List[str]
    below_cluster_threshold: bool
