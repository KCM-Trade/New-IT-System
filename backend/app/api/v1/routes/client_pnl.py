from __future__ import annotations

from typing import Optional, Dict, Any
import json

from fastapi import APIRouter, HTTPException, Path, Query

from app.schemas.client_pnl import (
    PaginatedClientPnLSummaryResponse,
    ClientAccountsResponse,
    ClientAccountItem,
)
from app.services.client_pnl_service import (
    get_client_pnl_summary_paginated,
    get_client_accounts,
)


router = APIRouter(prefix="/client-pnl", tags=["client-pnl"])


@router.get("/summary/paginated", response_model=PaginatedClientPnLSummaryResponse)
def get_client_summary_paginated(
    page: int = Query(1, ge=1, description="页码，从1开始"),
    page_size: int = Query(50, ge=1, le=1000, description="每页记录数"),
    sort_by: Optional[str] = Query(None, description="排序字段"),
    sort_order: str = Query("asc", description="排序方向: asc/desc"),
    search: Optional[str] = Query(None, description="统一搜索：支持 client_id 或 account_id(精确)，客户名(模糊)"),
    filters_json: Optional[str] = Query(None, description="筛选条件 JSON，格式：{join:'AND'|'OR', rules:[{field,op,value,value2?}]}"),
) -> PaginatedClientPnLSummaryResponse:
    try:
        filters_dict: Optional[Dict[str, Any]] = None
        if filters_json:
            try:
                filters_dict = json.loads(filters_json)
                if not isinstance(filters_dict, dict):
                    raise ValueError("filters_json must be a JSON object")
                if "join" not in filters_dict or "rules" not in filters_dict:
                    raise ValueError("filters_json must contain 'join' and 'rules' fields")
                if filters_dict["join"] not in ["AND", "OR"]:
                    raise ValueError("join must be 'AND' or 'OR'")
                if not isinstance(filters_dict["rules"], list):
                    raise ValueError("rules must be an array")
            except json.JSONDecodeError as e:
                raise HTTPException(status_code=422, detail=f"Invalid filters_json format: {str(e)}")
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))

        rows, total_count, total_pages, last_updated = get_client_pnl_summary_paginated(
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
            search=search,
            filters=filters_dict,
        )
        return PaginatedClientPnLSummaryResponse(
            ok=True,
            data=rows,
            total=total_count,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            last_updated=last_updated,
        )
    except Exception as e:
        return PaginatedClientPnLSummaryResponse(
            ok=False,
            data=[],
            total=0,
            page=page,
            page_size=page_size,
            total_pages=0,
            error=str(e),
        )


@router.get("/{client_id}/accounts", response_model=ClientAccountsResponse)
def get_accounts_for_client(
    client_id: int = Path(..., ge=1, description="客户ID")
) -> ClientAccountsResponse:
    """获取某个客户的所有账户明细（用于 AG Grid 行展开）"""
    try:
        accounts_raw = get_client_accounts(client_id=client_id)
        # Convert dict to ClientAccountItem to ensure proper data format
        accounts = [ClientAccountItem(**acc) for acc in accounts_raw]
        return ClientAccountsResponse(ok=True, client_id=client_id, accounts=accounts)
    except Exception as e:
        return ClientAccountsResponse(ok=False, client_id=client_id, accounts=[], error=str(e))


