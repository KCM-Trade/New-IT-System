from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from ....core.config import Settings, get_settings
from ....schemas.ib_data import (
    IBAnalyticsRequest,
    IBAnalyticsResponse,
    LastQueryResponse,
)
from ....services.ib_data_service import (
    aggregate_ib_data,
    read_last_query_time,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ib-data")


@router.post("/query", response_model=IBAnalyticsResponse, status_code=status.HTTP_200_OK)
async def query_ib_data(payload: IBAnalyticsRequest, settings: Settings = Depends(get_settings)):
    """Query IB analytics data with concurrency control."""
    try:
        rows, totals, last_run = aggregate_ib_data(settings, payload.ib_ids, payload.start, payload.end)
        return IBAnalyticsResponse(rows=rows, totals=totals, last_query_time=last_run)
    except ValueError as exc:
        logger.warning(f"Validation error: {exc}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.error(f"Runtime error: {exc}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Unexpected error: {type(exc).__name__}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询失败: {str(exc)}"
        ) from exc


@router.get("/last-run", response_model=LastQueryResponse, status_code=status.HTTP_200_OK)
async def get_last_run(settings: Settings = Depends(get_settings)):
    """Expose the shared txt marker so the UI can show last execution time."""
    return LastQueryResponse(last_query_time=read_last_query_time(settings))


