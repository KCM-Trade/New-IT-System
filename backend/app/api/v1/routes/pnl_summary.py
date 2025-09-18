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


router = APIRouter(prefix="/pnl", tags=["pnl-summary"])


@router.post("/summary/refresh", response_model=RefreshResponse)
def refresh_summary(body: RefreshRequest) -> RefreshResponse:
    try:
        msg = trigger_pnl_summary_sync(server=body.server, symbol=body.symbol)
        return RefreshResponse(status="ok", message=msg, server=body.server, symbol=body.symbol)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary", response_model=PnlSummaryResponse)
def get_summary(server: str = Query(...), symbol: str = Query(...)) -> PnlSummaryResponse:
    if server != "MT5":
        return PnlSummaryResponse(ok=True, data=[], rows=0)
    try:
        rows, count = get_pnl_summary_from_db(symbol=symbol)
        return PnlSummaryResponse(ok=True, data=rows, rows=count)
    except Exception as e:
        return PnlSummaryResponse(ok=False, data=[], rows=0, error=str(e))


