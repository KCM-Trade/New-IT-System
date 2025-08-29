from __future__ import annotations

from fastapi import APIRouter, Depends

from ....core.config import Settings, get_settings
from ....schemas.open_positions import OpenPositionsResponse
from ....services.open_positions_service import get_open_positions_today


router = APIRouter(prefix="/open-positions")


@router.get("/today", response_model=OpenPositionsResponse)
def get_open_positions(
    settings: Settings = Depends(get_settings),
    source: str = "mt4_live",
):
    return get_open_positions_today(settings, source=source)



