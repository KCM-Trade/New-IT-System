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


class PaginationRequest(BaseModel):
    """分页请求参数"""
    page: int = Field(1, ge=1, description="页码，从1开始")
    page_size: int = Field(100, ge=1, le=1000, description="每页记录数，1-1000")
    sort_by: Optional[str] = Field(None, description="排序字段")
    sort_order: Optional[str] = Field("asc", description="排序方向: asc/desc")


class PaginatedPnlSummaryResponse(BaseModel):
    """分页查询响应"""
    ok: bool
    data: List[PnlSummaryItem] = []
    total: int = Field(0, description="总记录数")
    page: int = Field(1, description="当前页码")
    page_size: int = Field(100, description="每页记录数")
    total_pages: int = Field(0, description="总页数")
    error: Optional[str] = None
    # 产品配置信息，用于前端格式化显示
    product_config: Optional[Dict[str, Any]] = None


