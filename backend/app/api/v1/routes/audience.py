from fastapi import APIRouter, Depends

from ....core.config import Settings, get_settings
from ....schemas.audience import AudiencePreviewRequest, AudiencePreviewResponse
from ....services.audience_service import audience_preview


router = APIRouter(prefix="/audience")


@router.post("/preview", response_model=AudiencePreviewResponse)
def post_audience_preview(
    req: AudiencePreviewRequest,
    settings: Settings = Depends(get_settings),
):
    return audience_preview(settings, req)


