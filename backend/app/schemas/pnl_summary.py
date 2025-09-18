from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class RefreshRequest(BaseModel):
    server: str = Field(..., description="Server name, e.g. MT5")
    symbol: str = Field(..., description="Trading symbol, e.g. XAUUSD.kcmc")


class RefreshResponse(BaseModel):
    status: str
    message: str
    server: str
    symbol: str


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


