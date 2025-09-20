from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas.pnl_summary import (
    RefreshRequest,
    RefreshResponse,
    PnlSummaryResponse,
)
from app.services.pnl_summary_service import (
    get_pnl_summary_from_db,
    trigger_pnl_summary_sync,
)
from app.services.etl_service import get_product_config


router = APIRouter(prefix="/pnl", tags=["pnl-summary"])


@router.post("/summary/refresh", response_model=RefreshResponse)
def refresh_summary(body: RefreshRequest) -> RefreshResponse:
    try:
        # 执行ETL同步并获取详细结果
        etl_result = trigger_pnl_summary_sync(server=body.server, symbol=body.symbol)
        
        if etl_result.success:
            if etl_result.processed_rows == 0:
                message = "同步完成，无新数据需要处理"
            elif etl_result.new_trades_count > 0 and etl_result.floating_only_count > 0:
                message = f"同步完成，{etl_result.new_trades_count}行新交易，{etl_result.floating_only_count}行浮动盈亏更新"
            elif etl_result.new_trades_count > 0:
                message = f"同步完成，处理 {etl_result.new_trades_count} 行新交易数据"
            elif etl_result.floating_only_count > 0:
                message = f"同步完成，{etl_result.floating_only_count}行浮动盈亏更新（无新交易）"
            else:
                message = f"同步完成，处理 {etl_result.processed_rows} 行数据"
        else:
            message = f"同步失败：{etl_result.error_message}"
        
        return RefreshResponse(
            status="success" if etl_result.success else "error",
            message=message,
            server=body.server,
            symbol=body.symbol,
            processed_rows=etl_result.processed_rows,
            duration_seconds=etl_result.duration_seconds,
            new_max_deal_id=etl_result.new_max_deal_id,
            error_details=etl_result.error_message if not etl_result.success else None,
            new_trades_count=etl_result.new_trades_count,
            floating_only_count=etl_result.floating_only_count
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary", response_model=PnlSummaryResponse)
def get_summary(server: str = Query(...), symbol: str = Query(...)) -> PnlSummaryResponse:
    if server != "MT5":
        return PnlSummaryResponse(ok=True, data=[], rows=0)
    try:
        rows, count = get_pnl_summary_from_db(symbol=symbol)
        # 获取产品配置信息，用于前端格式化显示
        product_config = get_product_config(symbol)
        return PnlSummaryResponse(ok=True, data=rows, rows=count, product_config=product_config)
    except Exception as e:
        return PnlSummaryResponse(ok=False, data=[], rows=0, error=str(e))


