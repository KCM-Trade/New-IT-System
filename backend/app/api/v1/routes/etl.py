from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Query

from app.schemas.etl_pg import (
    PnlUserSummaryItem,
    PaginatedPnlUserSummaryResponse,
)
from app.services.etl_pg_service import get_pnl_user_summary_paginated


router = APIRouter(prefix="/etl", tags=["etl"])


@router.get("/pnl-user-summary/paginated", response_model=PaginatedPnlUserSummaryResponse)
def get_pnl_user_summary(
    page: int = Query(1, ge=1, description="页码，从1开始"),
    page_size: int = Query(100, ge=1, le=1000, description="每页记录数"),
    sort_by: Optional[str] = Query(None, description="排序字段"),
    sort_order: str = Query("asc", description="排序方向: asc/desc"),
    user_groups: Optional[str] = Query(None, description="用户组别筛选，多个用逗号分隔；__ALL__ 表示全部"),
    search: Optional[str] = Query(None, description="统一搜索：支持客户ID(精确)或客户名称(模糊)"),
) -> PaginatedPnlUserSummaryResponse:
    try:
        groups_list: Optional[List[str]] = None
        if user_groups:
            groups_list = [g.strip() for g in user_groups.split(",") if g.strip()]

        rows, total_count, total_pages = get_pnl_user_summary_paginated(
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
            user_groups=groups_list,
            search=search,
        )

        return PaginatedPnlUserSummaryResponse(
            ok=True,
            data=[PnlUserSummaryItem(**r) for r in rows],
            total=total_count,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
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


