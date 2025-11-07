from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ClientID 汇总数据模型
class ClientPnLSummaryItem(BaseModel):
    # 主键
    client_id: int
    
    # 客户基本信息
    client_name: Optional[str] = None
    primary_server: Optional[str] = None
    zipcode: Optional[str] = None
    is_enabled: Optional[int] = None
    countries: Optional[List[str]] = None
    currencies: Optional[List[str]] = None
    
    # 账户统计
    account_count: int = 0
    account_list: Optional[List[int]] = None
    
    # 聚合金额（统一美元）
    total_balance_usd: float = 0.0
    total_credit_usd: float = 0.0
    total_floating_pnl_usd: float = 0.0
    total_equity_usd: float = 0.0
    
    # 平仓盈亏
    total_closed_profit_usd: float = 0.0
    total_commission_usd: float = 0.0
    
    # 资金流动
    total_deposit_usd: float = 0.0
    total_withdrawal_usd: float = 0.0
    net_deposit_usd: float = 0.0
    
    # 聚合手数
    total_volume_lots: float = 0.0
    total_overnight_volume_lots: float = 0.0
    auto_swap_free_status: Optional[float] = None
    
    # 聚合订单数
    total_closed_count: int = 0
    total_overnight_count: int = 0
    
    # 明细分类统计
    closed_sell_volume_lots: float = 0.0
    closed_sell_count: int = 0
    closed_sell_profit_usd: float = 0.0
    closed_sell_swap_usd: float = 0.0
    
    closed_buy_volume_lots: float = 0.0
    closed_buy_count: int = 0
    closed_buy_profit_usd: float = 0.0
    closed_buy_swap_usd: float = 0.0
    
    # 更新时间
    last_updated: datetime


# 分页查询响应
class PaginatedClientPnLSummaryResponse(BaseModel):
    ok: bool
    data: List[ClientPnLSummaryItem] = []
    total: int = Field(0, description="总记录数")
    page: int = Field(1, description="当前页码")
    page_size: int = Field(50, description="每页记录数")
    total_pages: int = Field(0, description="总页数")
    error: Optional[str] = None
    # 数据最后更新时间（从 etl_watermarks 或汇总表取最大值）
    last_updated: Optional[datetime] = None


# 客户账户明细模型
class ClientAccountItem(BaseModel):
    # 主键
    client_id: int
    login: int
    server: str  # 'MT5' 或 'MT4Live2'
    
    # 账户信息
    currency: Optional[str] = None
    user_name: Optional[str] = None
    user_group: Optional[str] = None
    country: Optional[str] = None
    
    # 账户金额（统一美元）
    balance_usd: float = 0.0
    credit_usd: float = 0.0
    floating_pnl_usd: float = 0.0
    equity_usd: float = 0.0
    
    # 账户交易统计
    closed_profit_usd: float = 0.0
    commission_usd: float = 0.0
    deposit_usd: float = 0.0
    withdrawal_usd: float = 0.0
    volume_lots: float = 0.0
    auto_swap_free_status: Optional[float] = None
    
    # 更新时间
    last_updated: datetime


# 账户明细查询响应
class ClientAccountsResponse(BaseModel):
    ok: bool
    client_id: int
    accounts: List[ClientAccountItem] = []
    error: Optional[str] = None


# 初始化请求和响应
class InitializeRequest(BaseModel):
    force: bool = Field(False, description="是否强制重新初始化（清空现有数据）")


class InitializeResponse(BaseModel):
    ok: bool
    total_clients: int = 0
    total_accounts: int = 0
    duration_seconds: float = 0.0
    message: Optional[str] = None
    error: Optional[str] = None


# 对比数据请求和响应
class CompareRequest(BaseModel):
    auto_fix: bool = Field(False, description="是否自动修复差异")


class CompareDifference(BaseModel):
    status: str  # 'MISSING' 或 'ORPHAN' 或 'OK'
    client_id: Optional[int] = None
    description: str


class CompareResponse(BaseModel):
    ok: bool
    differences: List[CompareDifference] = []
    total_missing: int = 0
    total_orphan: int = 0
    fixed: bool = False
    error: Optional[str] = None


# 数据刷新状态响应
class RefreshStatusResponse(BaseModel):
    ok: bool
    last_updated: Optional[datetime] = None
    total_clients: int = 0
    total_accounts: int = 0
    data_source: str = "pnl_client_summary"
    error: Optional[str] = None


# 刷新步骤与响应（用于前端详细进度 Banner）
class ClientPnlRefreshStep(BaseModel):
    name: str
    duration_seconds: float = 0.0
    # 以下字段可选，按步骤类型返回
    affected_rows: Optional[int] = None
    total: Optional[int] = None
    missing: Optional[int] = None
    lag: Optional[int] = None
    loaded_mapping: Optional[int] = None
    zipcode_changes: Optional[int] = None
    # 新增：zipcode 变更详情（前端用于展示哪个 client 从什么变成什么）
    zipcode_details: Optional[List[dict]] = None


class ClientPnlRefreshResponse(BaseModel):
    status: str
    message: Optional[str] = None
    duration_seconds: float = 0.0
    steps: List[ClientPnlRefreshStep] = []
    max_last_updated: Optional[str] = None
    raw_log: Optional[str] = None

