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
    resolve_table_and_dataset,
)


router = APIRouter(prefix="/etl", tags=["etl"])


@router.get("/pnl-user-summary/paginated", response_model=PaginatedPnlUserSummaryResponse)
def get_pnl_user_summary(
    server: str = Query("MT5", description="服务器名称：MT5 或 MT4Live2"),
    page: int = Query(1, ge=1, description="页码，从1开始"),
    page_size: int = Query(100, ge=1, le=1000, description="每页记录数"),
    sort_by: Optional[str] = Query(None, description="排序字段"),
    sort_order: str = Query("asc", description="排序方向: asc/desc"),
    user_groups: Optional[List[str]] = Query(None, description="用户组别筛选，使用重复键传递；例如 user_groups=G1&user_groups=G2"),
    search: Optional[str] = Query(None, description="统一搜索：支持 login/user_id(精确) 或 user_name(模糊)"),
) -> PaginatedPnlUserSummaryResponse:
    try:
        source_table, dataset = resolve_table_and_dataset(server)
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

        # 内部标识白名单校验（仅对以 __ 开头的标识进行校验，其余视为真实组名）
        if groups_list:
            allowed_internal = {
                "__ALL__",
                "__NONE__",
                "__USER_NAME_TEST__",
                "__EXCLUDE_USER_NAME_TEST__",
                "__EXCLUDE_GROUP_NAME_TEST__",
            }
            for token in groups_list:
                if token.startswith("__") and token not in allowed_internal:
                    raise HTTPException(status_code=422, detail=f"Invalid internal token in user_groups: {token}")

        rows, total_count, total_pages = get_pnl_user_summary_paginated(
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
            user_groups=groups_list,
            search=search,
            source_table=source_table,
        )

        watermark = get_etl_watermark_last_updated(dataset=dataset)
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
        if isinstance(e, ValueError):
            raise HTTPException(status_code=400, detail=str(e))
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
def get_groups(server: str = Query("MT5", description="服务器名称：MT5 或 MT4Live2")) -> List[str]:
    """获取所有用户组别（来自 public.pnl_user_summary，已去重与规范化）。

    为与前端筛选对接，返回扁平列表，由前端按“测试/KCM*/AKCM*/其他”进行分类展示。
    """
    try:
        source_table, _ = resolve_table_and_dataset(server)
        return get_user_groups_from_user_summary(source_table=source_table)
    except Exception as e:
        if isinstance(e, ValueError):
            raise HTTPException(status_code=400, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))

