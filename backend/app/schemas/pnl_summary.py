from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field


class RefreshRequest(BaseModel):
    server: str = Field(..., description="Server name, e.g. MT5")
    symbol: str = Field(..., description="Trading symbol, e.g. XAUUSD.kcmc")


class RefreshResponse(BaseModel):
    status: str
    message: str
    server: str
    symbol: str
    # ETL执行结果详情
    processed_rows: int = 0
    duration_seconds: float = 0.0
    new_max_deal_id: int = 0
    error_details: Optional[str] = None
    # 新增：区分新交易和浮动盈亏更新
    new_trades_count: int = 0
    floating_only_count: int = 0


class PnlSummaryItem(BaseModel):
    login: int
    symbol: str
    user_group: Optional[str] = None
    user_name: Optional[str] = None
    country: Optional[str] = None
    balance: Optional[float] = None
    total_closed_trades: int
    buy_trades_count: int
    sell_trades_count: int
    total_closed_volume: float
    buy_closed_volume: float
    sell_closed_volume: float
    total_closed_pnl: float
    floating_pnl: float
    last_updated: Optional[datetime] = None


class PnlSummaryResponse(BaseModel):
    ok: bool
    data: List[PnlSummaryItem] = []
    rows: int = 0
    error: Optional[str] = None
    # 新增：产品配置信息，用于前端格式化显示
    product_config: Optional[Dict[str, Any]] = None


