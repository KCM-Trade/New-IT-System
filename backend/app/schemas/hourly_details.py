from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class HourlyDetailsRequest(BaseModel):
    """按小时段查询交易明细的请求模型"""
    start_time: str = Field(..., description="开始时间，格式: YYYY-MM-DD HH:00:00")
    end_time: str = Field(..., description="结束时间，格式: YYYY-MM-DD HH:59:59")
    symbol: str = Field(default="XAUUSD", description="交易品种")
    time_type: str = Field(default="open", description="时间类型: open(开仓时间) 或 close(平仓时间)")
    limit: int = Field(default=100, ge=1, le=1000, description="返回记录数限制")


class HourlyTradeDetail(BaseModel):
    """小时段交易明细"""
    login: str
    ticket: int
    symbol: str
    side: str  # buy/sell
    lots: float
    open_time: str
    close_time: str
    open_price: float
    close_price: float
    profit: float
    swaps: float


class HourlyDetailsResponse(BaseModel):
    """小时段交易明细响应"""
    trades: List[HourlyTradeDetail]
    total_count: int
    total_profit: float
    time_range: str
    symbol: str
