from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from ..schemas.client_pnl import (
    PaginatedClientPnLSummaryResponse,
    ClientAccountsResponse,
    InitializeRequest,
    InitializeResponse,
    CompareRequest,
    CompareResponse,
    CompareDifference,
    RefreshStatusResponse,
    ClientPnLSummaryItem,
    ClientAccountItem,
)
from ..services import client_pnl_service


router = APIRouter(prefix="/api/client-pnl", tags=["ClientID盈亏监控"])


@router.get("/summary", response_model=PaginatedClientPnLSummaryResponse)
def get_client_pnl_summary(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(50, ge=1, le=500, description="每页记录数"),
    sort_by: Optional[str] = Query(None, description="排序字段"),
    sort_order: str = Query("asc", regex="^(asc|desc)$", description="排序方向"),
    search: Optional[str] = Query(None, description="搜索关键词（client_id或account_id）"),
):
    """分页查询 ClientID 盈亏汇总表
    
    搜索功能：
    - 输入数字：精确匹配 client_id 或 account_id（login）
    - 输入非数字：忽略搜索（不添加任何条件）
    """
    try:
        rows, total, total_pages, last_updated = client_pnl_service.get_client_pnl_summary_paginated(
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
            search=search,
        )
        
        items = [ClientPnLSummaryItem(**row) for row in rows]
        
        return PaginatedClientPnLSummaryResponse(
            ok=True,
            data=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            last_updated=last_updated,
        )
    except Exception as e:
        return PaginatedClientPnLSummaryResponse(
            ok=False,
            error=f"查询失败：{str(e)}",
        )


@router.get("/accounts/{client_id}", response_model=ClientAccountsResponse)
def get_client_accounts(client_id: int):
    """获取某个客户的所有账户明细（用于 AG Grid 行展开）"""
    try:
        accounts = client_pnl_service.get_client_accounts(client_id)
        items = [ClientAccountItem(**acc) for acc in accounts]
        
        return ClientAccountsResponse(
            ok=True,
            client_id=client_id,
            accounts=items,
        )
    except Exception as e:
        return ClientAccountsResponse(
            ok=False,
            client_id=client_id,
            error=f"查询失败：{str(e)}",
        )


@router.post("/initialize", response_model=InitializeResponse)
def initialize_client_summary(req: InitializeRequest):
    """初始化客户聚合表
    
    使用场景：
    - 首次部署时填充历史数据
    - 数据修复或重建
    - 触发器被禁用后的批量补偿更新
    
    注意：force=True 会清空现有数据后重新初始化
    """
    try:
        result = client_pnl_service.initialize_client_summary(force=req.force)
        
        return InitializeResponse(
            ok=True,
            total_clients=result["total_clients"],
            total_accounts=result["total_accounts"],
            duration_seconds=result["duration_seconds"],
            message=f"成功初始化 {result['total_clients']} 个客户，{result['total_accounts']} 个账户",
        )
    except Exception as e:
        return InitializeResponse(
            ok=False,
            error=f"初始化失败：{str(e)}",
        )


@router.post("/compare", response_model=CompareResponse)
def compare_client_summary(req: CompareRequest):
    """对比客户聚合表与源表的差异
    
    使用场景：
    - 定期检查数据一致性（每日/每周）
    - 发现触发器遗漏的更新
    - 排查数据异常
    
    返回：
    - MISSING：源表有但聚合表没有的 client_id
    - ORPHAN：聚合表有但源表没有的 client_id
    
    auto_fix=True 时会自动修复差异
    """
    try:
        differences = client_pnl_service.compare_client_summary(auto_fix=req.auto_fix)
        
        # 解析差异
        items = []
        total_missing = 0
        total_orphan = 0
        
        for diff in differences:
            issue_type = diff.get("issue_type", "")
            client_id = diff.get("client_id")
            message = diff.get("message", "")
            
            if "MISSING" in issue_type or "missing" in message.lower():
                status = "MISSING"
                total_missing += 1
            elif "ORPHAN" in issue_type or "orphan" in message.lower():
                status = "ORPHAN"
                total_orphan += 1
            else:
                status = "OK"
            
            items.append(
                CompareDifference(
                    status=status,
                    client_id=client_id,
                    description=message,
                )
            )
        
        return CompareResponse(
            ok=True,
            differences=items,
            total_missing=total_missing,
            total_orphan=total_orphan,
            fixed=req.auto_fix,
        )
    except Exception as e:
        return CompareResponse(
            ok=False,
            error=f"对比失败：{str(e)}",
        )


@router.get("/status", response_model=RefreshStatusResponse)
def get_refresh_status():
    """获取数据刷新状态
    
    返回：
    - last_updated：最后更新时间
    - total_clients：客户总数
    - total_accounts：账户总数
    """
    try:
        status = client_pnl_service.get_refresh_status()
        
        return RefreshStatusResponse(
            ok=True,
            last_updated=status["last_updated"],
            total_clients=status["total_clients"],
            total_accounts=status["total_accounts"],
        )
    except Exception as e:
        return RefreshStatusResponse(
            ok=False,
            error=f"查询失败：{str(e)}",
        )

