"""Public contract of the CS fund risk-control ledger API (出入金风控台账).

SSOT: docs/features/fund-risk-ledger.md.

One ledger row = one CRM transaction id. Order-derived fields are never typed
by staff: the server reads them from the MySQL replica and stores a snapshot.
Rules:

- Enums are stable lowercase strings; the UI owns the Chinese labels.
- Times are UTC ISO8601 ``...Z`` (CRM stores naive UTC+3, converted on read).
- Money is CNY face value (deposit: requestedAmount, withdrawal: vendorAmount).
- Client name is NOT stored in the ledger; it is merged live from the replica
  and is ``None`` when the replica is unavailable.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

# --- enums -----------------------------------------------------------------

Direction = Literal["deposit", "withdrawal"]

# 被封银行卡资料
FrozenScope = Literal["none", "one_card", "all_cards", "non_counter", "unknown"]

# 封卡单位
Authority = Literal["bank", "police_antifraud", "alipay", "unknown"]

# 客户来源
ClientSource = Literal["online", "offline"]

# 是否成功转账
TransferSucceeded = Literal["yes", "no", "unknown"]

RowSource = Literal["manual", "import"]

# Non-blocking notes attached to an order preview.
OrderWarning = Literal[
    "psp_unmapped",      # PSP has no channel mapping -> channel = "其他: <psp name>"
    "non_cn_psp",        # psps.cid != 0
    "test_account",      # employee / demo / test account
    "not_approved",      # status is not "approved" (fresh / declined / ...)
    "amount_not_cny",    # no CNY face value on this order
]

# The 34 province-level names accepted in *_province fields (short form).
PROVINCES: tuple[str, ...] = (
    "北京", "天津", "上海", "重庆",
    "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建",
    "江西", "山东", "河南", "湖北", "湖南", "广东", "海南", "四川", "贵州",
    "云南", "陕西", "甘肃", "青海", "台湾",
    "内蒙古", "广西", "西藏", "宁夏", "新疆",
    "香港", "澳门",
)

TEXT_MAX = 4000
SHORT_MAX = 64


# --- order (read from the replica) ------------------------------------------

class OrderSnapshot(BaseModel):
    """Order facts derived from fxbackoffice; never typed by staff."""

    transaction_id: int
    client_id: Optional[int] = None
    client_name: Optional[str] = None       # live only, never persisted
    login_sid: Optional[str] = None
    venue: Optional[str] = None             # CRM tag (categoryId=6), upper-cased
    direction: Direction
    txn_type: str                           # deposit / withdrawal / ib withdrawal
    psp_id: Optional[int] = None
    psp_name: Optional[str] = None
    channel: str                            # mapped label, or "其他: <psp name>"
    amount_cny: Optional[float] = None
    txn_status: str
    txn_created_at: Optional[str] = None    # UTC ISO8601 Z
    txn_processed_at: Optional[str] = None
    is_test_account: bool = False


class OrderPreviewResponse(BaseModel):
    order: OrderSnapshot
    existing_id: Optional[int] = None       # ledger row already holding this transaction
    warnings: List[OrderWarning] = Field(default_factory=list)


# --- staff-entered fields ---------------------------------------------------

class IncidentFields(BaseModel):
    reported_date: Optional[date] = None                    # 登记日期
    transfer_succeeded: TransferSucceeded = "unknown"
    frozen_scope: FrozenScope = "unknown"
    freeze_days: Optional[int] = Field(default=None, ge=0, le=36500)  # None = unknown
    freeze_note: Optional[str] = Field(default=None, max_length=500)
    authority: Authority = "unknown"
    client_source: Optional[ClientSource] = None
    client_bank_province: Optional[str] = Field(default=None, max_length=SHORT_MAX)
    client_bank_city: Optional[str] = Field(default=None, max_length=SHORT_MAX)
    trader_bank_province: Optional[str] = Field(default=None, max_length=SHORT_MAX)
    trader_bank_city: Optional[str] = Field(default=None, max_length=SHORT_MAX)
    id_province: Optional[str] = Field(default=None, max_length=SHORT_MAX)
    id_city: Optional[str] = Field(default=None, max_length=SHORT_MAX)
    ip_region: Optional[str] = Field(default=None, max_length=SHORT_MAX)
    pwa_domain: Optional[str] = Field(default=None, max_length=200)
    account_type: Optional[str] = Field(default=None, max_length=SHORT_MAX)
    venue_override: Optional[str] = Field(default=None, max_length=SHORT_MAX)
    event_text: Optional[str] = Field(default=None, max_length=TEXT_MAX)    # 事件
    result_text: Optional[str] = Field(default=None, max_length=TEXT_MAX)   # 查询结果


class IncidentCreate(IncidentFields):
    transaction_id: int = Field(gt=0)
    # Join an existing incident group (several orders of one event); None = new group.
    incident_group_id: Optional[int] = None


class IncidentUpdate(IncidentFields):
    version: int                            # optimistic lock; mismatch -> 409
    # Also copy event_text / result_text / authority to the other rows of the group.
    apply_to_group: bool = False


class Incident(IncidentFields):
    id: int
    incident_group_id: int
    group_size: int = 1
    transaction_id: Optional[int] = None    # None only on imported legacy rows
    order: Optional[OrderSnapshot] = None   # live values, snapshot when replica is down
    order_is_live: bool = False
    source: RowSource = "manual"
    version: int
    created_by: str
    created_at: str
    updated_by: str
    updated_at: str


class IncidentStatistics(BaseModel):
    query_time_ms: int
    replica_live: bool


class IncidentListResponse(BaseModel):
    data: List[Incident]
    total: int
    page: int
    page_size: int
    total_pages: int
    statistics: IncidentStatistics


class IncidentConflict(BaseModel):
    """Body of a 409: duplicate transaction id or stale version."""

    detail: str
    existing_id: Optional[int] = None
    current_version: Optional[int] = None
