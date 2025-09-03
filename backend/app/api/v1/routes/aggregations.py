from fastapi import APIRouter, Depends

from ....core.config import Settings, get_settings
from ....schemas.aggregation import AggregateRequest, AggregateResponse, RefreshResponse, LastRefreshResponse
from ....services.aggregation_service import aggregate_to_json, refresh_aggregations


router = APIRouter(prefix="/aggregate")


@router.post("/to-json", response_model=AggregateResponse)
def post_aggregate_to_json(
    req: AggregateRequest,
    settings: Settings = Depends(get_settings),
):
    result = aggregate_to_json(settings, req.symbol, req.start, req.end, basis=req.basis)
    return result



@router.post("/refresh", response_model=RefreshResponse)
def post_refresh(
    settings: Settings = Depends(get_settings),
):
    """
    Refresh both open/close aggregations:
    - start: next hour after latest (date,hour) in existing JSON (UTC+3)
    - end: now in UTC+3
    """
    result = refresh_aggregations(settings)
    return result


@router.get("/last-refresh", response_model=LastRefreshResponse)
def get_last_refresh(
    settings: Settings = Depends(get_settings),
):
    marker = settings.public_export_dir / "profit_last_refresh.txt"
    refreshed_at = None
    try:
        if marker.exists():
            refreshed_at = marker.read_text(encoding="utf-8").strip()
    except Exception:
        refreshed_at = None
    return {"refreshed_at": refreshed_at}

