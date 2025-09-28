from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class PnlUserSummaryItem(BaseModel):
    # 主键与维度
    login: int
    symbol: str

    # 用户信息
    user_name: Optional[str] = None
    user_group: Optional[str] = None
    country: Optional[str] = None
    zipcode: Optional[str] = None
    user_id: Optional[int] = None

    # 账户与浮盈
    user_balance: float = 0.0
    user_credit: float = 0.0
    positions_floating_pnl: float = 0.0
    equity: float = 0.0

    # 平仓统计（SELL，平多）
    closed_sell_volume_lots: float = 0.0
    closed_sell_count: int = 0
    closed_sell_profit: float = 0.0
    closed_sell_swap: float = 0.0
    closed_sell_overnight_count: int = 0
    closed_sell_overnight_volume_lots: float = 0.0

    # 平仓统计（BUY，平空）
    closed_buy_volume_lots: float = 0.0
    closed_buy_count: int = 0
    closed_buy_profit: float = 0.0
    closed_buy_swap: float = 0.0
    closed_buy_overnight_count: int = 0
    closed_buy_overnight_volume_lots: float = 0.0

    # 佣金 & 资金
    total_commission: float = 0.0
    deposit_count: int = 0
    deposit_amount: float = 0.0
    withdrawal_count: int = 0
    withdrawal_amount: float = 0.0
    net_deposit: float = 0.0

    # 审计
    last_updated: datetime


class PaginatedPnlUserSummaryResponse(BaseModel):
    ok: bool
    data: List[PnlUserSummaryItem] = []
    total: int = Field(0, description="总记录数")
    page: int = Field(1, description="当前页码")
    page_size: int = Field(100, description="每页记录数")
    total_pages: int = Field(0, description="总页数")
    error: Optional[str] = None


