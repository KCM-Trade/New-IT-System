from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Query, HTTPException

from app.schemas.etl_pg import (
    PnlUserSummaryItem,
    PaginatedPnlUserSummaryResponse,
    EtlRefreshRequest,
    EtlRefreshResponse,
)
from app.services.etl_pg_service import (
    get_pnl_user_summary_paginated,
    get_etl_watermark_last_updated,
    mt5_incremental_refresh,
    get_user_groups_from_user_summary,
)


router = APIRouter(prefix="/etl", tags=["etl"])


@router.get("/pnl-user-summary/paginated", response_model=PaginatedPnlUserSummaryResponse)
def get_pnl_user_summary(
    page: int = Query(1, ge=1, description="页码，从1开始"),
    page_size: int = Query(100, ge=1, le=1000, description="每页记录数"),
    sort_by: Optional[str] = Query(None, description="排序字段"),
    sort_order: str = Query("asc", description="排序方向: asc/desc"),
    user_groups: Optional[List[str]] = Query(None, description="用户组别筛选，使用重复键传递；例如 user_groups=G1&user_groups=G2"),
    search: Optional[str] = Query(None, description="统一搜索：支持客户ID(精确)或客户名称(模糊)"),
) -> PaginatedPnlUserSummaryResponse:
    try:
        groups_list: Optional[List[str]] = None
        if user_groups:
            # 支持重复键数组；兼容单元素里带逗号的旧调用
            flat: List[str] = []
            for g in user_groups:
                if g and "," in g:
                    flat.extend([x.strip() for x in g.split(",") if x.strip()])
                elif g and g.strip():
                    flat.append(g.strip())
            groups_list = flat or None

        rows, total_count, total_pages = get_pnl_user_summary_paginated(
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
            user_groups=groups_list,
            search=search,
        )

        watermark = get_etl_watermark_last_updated(dataset="pnl_user_summary")
        return PaginatedPnlUserSummaryResponse(
            ok=True,
            data=[PnlUserSummaryItem(**r) for r in rows],
            total=total_count,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            watermark_last_updated=watermark,
        )
    except Exception as e:
        return PaginatedPnlUserSummaryResponse(
            ok=False,
            data=[],
            total=0,
            page=page,
            page_size=page_size,
            total_pages=0,
            error=str(e),
        )


@router.post("/pnl-user-summary/refresh", response_model=EtlRefreshResponse)
def refresh_pnl_user_summary(body: EtlRefreshRequest) -> EtlRefreshResponse:
    """Trigger MT5 incremental refresh for pnl_user_summary.

    仅支持 MT5。强制使用 Postgres DB 名称 MT5_ETL（根据用户要求）。
    """
    if body.server != "MT5":
        raise HTTPException(status_code=400, detail="Only MT5 server is supported for refresh")
    try:
        r = mt5_incremental_refresh()
        status = "success" if r.get("success") else "error"
        return EtlRefreshResponse(
            status=status,
            message=r.get("message"),
            server=body.server,
            processed_rows=int(r.get("processed_rows") or 0),
            duration_seconds=float(r.get("duration_seconds") or 0.0),
            new_max_deal_id=r.get("new_max_deal_id"),
            new_trades_count=r.get("new_trades_count"),
            floating_only_count=r.get("floating_only_count"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/groups", response_model=List[str])
def get_groups() -> List[str]:
    """获取所有用户组别（来自 public.pnl_user_summary，已去重与规范化）。

    为与前端筛选对接，返回扁平列表，由前端按“测试/KCM*/AKCM*/其他”进行分类展示。
    """
    try:
        return get_user_groups_from_user_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

