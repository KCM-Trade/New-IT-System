from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, HTTPException

from app.schemas.etl_pg import (
    PnlUserSummaryItem,
    PaginatedPnlUserSummaryResponse,
    EtlRefreshRequest,
    EtlRefreshResponse,
)
from app.schemas.client_pnl import ClientPnlRefreshResponse, ClientPnlRefreshStep
from app.services.etl_pg_service import (
    get_pnl_user_summary_paginated,
    get_etl_watermark_last_updated,
    mt5_incremental_refresh,
    mt4live2_incremental_refresh,
    get_user_groups_from_user_summary,
    resolve_table_and_dataset,
)
from app.services.client_pnl_service import run_client_pnl_incremental_refresh
from app.core.client_pnl_refresh_logger import log_refresh_event


router = APIRouter(prefix="/etl", tags=["etl"])
logger = logging.getLogger(__name__)


@router.get("/pnl-user-summary/paginated", response_model=PaginatedPnlUserSummaryResponse)
def get_pnl_user_summary(
    server: str = Query("MT5", description="服务器名称：MT5 或 MT4Live2"),
    page: int = Query(1, ge=1, description="页码，从1开始"),
    page_size: int = Query(100, ge=1, le=1000, description="每页记录数"),
    sort_by: Optional[str] = Query(None, description="排序字段"),
    sort_order: str = Query("asc", description="排序方向: asc/desc"),
    user_groups: Optional[List[str]] = Query(None, description="用户组别筛选，使用重复键传递；例如 user_groups=G1&user_groups=G2"),
    search: Optional[str] = Query(None, description="统一搜索：支持 login/user_id(精确) 或 user_name(模糊)"),
    filters_json: Optional[str] = Query(None, description="筛选条件 JSON，格式：{join:'AND'|'OR', rules:[{field,op,value,value2?}]}"),
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

        # 解析筛选条件 JSON
        filters_dict: Optional[Dict[str, Any]] = None
        if filters_json:
            try:
                filters_dict = json.loads(filters_json)
                # 基本结构校验
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

        rows, total_count, total_pages = get_pnl_user_summary_paginated(
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
            user_groups=groups_list,
            search=search,
            source_table=source_table,
            filters=filters_dict,
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
    """Trigger incremental refresh for pnl_user_summary by server.

    - MT5       -> mt5_incremental_refresh (Postgres MT5_ETL + MySQL mt5_live)
    - MT4Live2  -> mt4live2_incremental_refresh (Postgres MT5_ETL + MySQL mt4_live2)
    """
    server = (body.server or "").upper()
    request_payload = body.dict()
    try:
        logger.info(f"Starting refresh for server: {server}")
        if server == "MT5":
            r = mt5_incremental_refresh()
        elif server == "MT4LIVE2":
            r = mt4live2_incremental_refresh()
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported server for refresh: {body.server}")

        # fresh grad note: ensure error cases have meaningful messages
        if not r.get("success"):
            error_msg = r.get("message") or "Refresh failed without specific error message"
            logger.warning(f"Refresh failed for {server}: {error_msg}")
        else:
            logger.info(f"Refresh completed for {server}: processed_rows={r.get('processed_rows')}, duration={r.get('duration_seconds')}s")

        status = "success" if r.get("success") else "error"
        log_refresh_event(
            "pnl_user_summary_refresh",
            {
                "server": server,
                "request": request_payload,
                "status": status,
                "service_result": r,
            },
        )
        return EtlRefreshResponse(
            status=status,
            message=r.get("message") or ("Refresh completed" if r.get("success") else "Refresh failed"),
            server=body.server,
            processed_rows=int(r.get("processed_rows") or 0),
            duration_seconds=float(r.get("duration_seconds") or 0.0),
            new_max_deal_id=r.get("new_max_deal_id"),
            new_trades_count=r.get("new_trades_count"),
            floating_only_count=r.get("floating_only_count"),
        )
    except HTTPException as http_exc:
        log_refresh_event(
            "pnl_user_summary_refresh_error",
            {
                "server": server,
                "request": request_payload,
                "status_code": http_exc.status_code,
                "error": http_exc.detail,
            },
        )
        raise
    except Exception as e:
        error_detail = f"Refresh failed for {server}: {str(e)}"
        logger.error(error_detail, exc_info=True)
        log_refresh_event(
            "pnl_user_summary_refresh_error",
            {
                "server": server,
                "request": request_payload,
                "error": error_detail,
                "exception_type": type(e).__name__,
            },
        )
        raise HTTPException(status_code=500, detail=error_detail)


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


@router.post("/client-pnl/refresh", response_model=ClientPnlRefreshResponse)
def refresh_client_pnl() -> ClientPnlRefreshResponse:
    """触发 pnl_client_summary / pnl_client_accounts 的增量刷新（按 candidate 客户集合）。

    返回详细步骤与耗时，供前端 Banner 展示。
    """
    try:
        r = run_client_pnl_incremental_refresh()
        status = "success" if r.get("success") else "error"
        steps_raw = r.get("steps") or []
        steps: List[ClientPnlRefreshStep] = [ClientPnlRefreshStep(**s) for s in steps_raw if isinstance(s, dict)]
        log_refresh_event(
            "client_pnl_refresh",
            {
                "status": status,
                "raw_result": r,
            },
        )
        return ClientPnlRefreshResponse(
            status=status,
            message=r.get("message"),
            duration_seconds=float(r.get("duration_seconds") or 0.0),
            steps=steps,
            max_last_updated=r.get("max_last_updated"),
            raw_log=r.get("raw_log"),
        )
    except Exception as e:
        logger.error(f"client-pnl refresh failed: {e}", exc_info=True)
        log_refresh_event(
            "client_pnl_refresh_error",
            {
                "error": str(e),
                "exception_type": type(e).__name__,
            },
        )
        raise HTTPException(status_code=500, detail=str(e))

