from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from ....core.config import Settings, get_settings
from ....schemas.downloads import (
    DownloadsQuery,
    DownloadsQueryResponse,
    DownloadsExportRequest,
)
from ....services.downloads_service import query_downloads, export_downloads_csv


router = APIRouter(prefix="/downloads")


@router.post("/query", response_model=DownloadsQueryResponse)
def post_downloads_query(
    req: DownloadsQuery,
    settings: Settings = Depends(get_settings),
):
    return query_downloads(settings, req)


@router.post("/export")
def post_downloads_export(
    req: DownloadsExportRequest,
    settings: Settings = Depends(get_settings),
):
    result = export_downloads_csv(settings, req)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error")}

    filename = result["filename"]
    content = result["content"]
    headers = {
        "Content-Disposition": f"attachment; filename={filename}",
        "Content-Type": "text/csv; charset=utf-8",
    }
    return Response(content=content, media_type="text/csv", headers=headers)


