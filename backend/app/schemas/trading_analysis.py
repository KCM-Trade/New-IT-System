from __future__ import annotations

from typing import List, Optional, Dict
from pydantic import BaseModel, Field


class TradingAnalysisRequest(BaseModel):
    accounts: List[str] = Field(..., description="MT4 login list")
    startDate: Optional[str] = Field(None, description="Start datetime (inclusive), e.g. 2025-09-01 00:00:00")
    endDate: Optional[str] = Field(None, description="End datetime (exclusive), e.g. 2025-10-01 00:00:00")
    symbols: Optional[List[str]] = Field(None, description="Symbols filter; null/empty = all")
    limitTop: int = Field(10, ge=1, le=100, description="Top winners/losers size")


class TradingSummaryByAccount(BaseModel):
    pnl_signed: float = 0.0
    pnl_net_abs: float = 0.0
    pnl_magnitude: float = 0.0
    total_orders: int = 0
    buy_orders: int = 0
    sell_orders: int = 0
    win_profit_sum: float = 0.0
    loss_profit_sum: float = 0.0
    loss_profit_abs_sum: float = 0.0
    win_trade_count: int = 0
    loss_trade_count: int = 0
    win_buy_count: int = 0
    win_sell_count: int = 0
    loss_buy_count: int = 0
    loss_sell_count: int = 0
    swaps_sum: float = 0.0
    buy_swaps_sum: float = 0.0
    sell_swaps_sum: float = 0.0
    deposit_count: int = 0
    deposit_amount: float = 0.0
    withdrawal_count: int = 0
    withdrawal_amount: float = 0.0
    cash_diff: float = 0.0


class TradingCashDetail(BaseModel):
    login: str
    ticket: int
    close_time: str
    amount_signed: float
    amount_abs: float
    cash_type: str
    comment: Optional[str] = None


class TradingTradeDetail(BaseModel):
    login: str
    ticket: int
    symbol: str
    side: str
    lots: float
    open_time: str
    close_time: str
    open_price: float
    close_price: float
    profit: float
    swaps: float


class TradingAnalysisResponse(BaseModel):
    summaryByAccount: Dict[str, TradingSummaryByAccount]
    cashDetails: List[TradingCashDetail]
    tradeDetails: List[TradingTradeDetail]
    topWinners: List[TradingTradeDetail]
    topLosers: List[TradingTradeDetail]


