from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional, List

from pydantic import BaseModel, Field


class DownloadsQuery(BaseModel):
    symbols: List[str] = Field(default_factory=lambda: ["XAU-CNH"])  # 产品代码列表
    start_date: date
    end_date: date
    source: Literal["mt4_live", "mt4_live2"] = "mt4_live"  # 交易服务器
    # 简化：只保留必要参数


class DownloadsRow(BaseModel):
    ticket: int
    account_id: int
    client_id: Optional[int] = None
    symbol: str
    volume: float
    open_time: Optional[datetime] = None
    close_time: Optional[datetime] = None
    modify_time: Optional[datetime] = None
    profit: float
    cmd: int
    open_price: float
    close_price: Optional[float] = None
    swaps: Optional[float] = None
    comment: Optional[str] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    ibid: Optional[str] = None


class DownloadsQueryResponse(BaseModel):
    ok: bool
    items: list[DownloadsRow] = []
    error: Optional[str] = None


class DownloadsExportRequest(DownloadsQuery):
    pass


