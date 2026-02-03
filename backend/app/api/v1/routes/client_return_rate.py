"""
API routes for Client Return Rate analysis.

Provides endpoints for querying client return rate data from ClickHouse.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
import csv
import io

from app.schemas.client_return_rate import (
    ClientReturnRateResponse,
)
from app.services.client_return_service import get_client_return_rate_data
from app.core.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/client-return-rate")


@router.get("/query", response_model=ClientReturnRateResponse)
async def query_client_return_rate(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=10000, description="Items per page"),
    sort_by: Optional[str] = Query("month_trade_profit", description="Column to sort by"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$", description="Sort direction"),
    search: Optional[str] = Query(None, description="Search by client_id"),
    deposit_bucket: Optional[str] = Query(None, description="Filter by deposit bucket"),
    month_start: Optional[str] = Query(None, description="Month start date (YYYY-MM-DD)"),
    month_end: Optional[str] = Query(None, description="Month end date (YYYY-MM-DD)"),
):
    """
    Query client return rate data with pagination and filtering.
    
    - **page**: Page number (1-indexed)
    - **page_size**: Number of items per page (max 500)
    - **sort_by**: Column to sort by
    - **sort_order**: 'asc' or 'desc'
    - **search**: Filter by client_id (exact match)
    - **deposit_bucket**: Filter by deposit bucket ('0-2000', '2000-5000', '5000-50000', '50000+')
    - **month_start**: Start date for current month calculation
    - **month_end**: End date for current month calculation
    """
    try:
        result = get_client_return_rate_data(
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
            search=search,
            deposit_bucket=deposit_bucket,
            month_start=month_start,
            month_end=month_end,
        )
        return result
    except Exception as e:
        logger.exception("Error querying client return rate")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export")
async def export_client_return_rate(
    search: Optional[str] = Query(None, description="Search by client_id"),
    deposit_bucket: Optional[str] = Query(None, description="Filter by deposit bucket"),
    month_start: Optional[str] = Query(None, description="Month start date"),
    month_end: Optional[str] = Query(None, description="Month end date"),
):
    """
    Export client return rate data as CSV file.
    
    Returns all matching records (no pagination).
    """
    try:
        # Get all data (large page size to get everything)
        result = get_client_return_rate_data(
            page=1,
            page_size=10000,  # Export limit
            sort_by="month_trade_profit",
            sort_order="desc",
            search=search,
            deposit_bucket=deposit_bucket,
            month_start=month_start,
            month_end=month_end,
        )
        
        data = result.get("data", [])
        
        if not data:
            raise HTTPException(status_code=404, detail="No data to export")
        
        # Create CSV in memory
        output = io.StringIO()
        
        # Define column headers (Chinese)
        headers = {
            "client_id": "客户ID",
            "net_deposit_hist": "历史净入金",
            "net_deposit_month": "当月净入金",
            "equity": "现时账户余额",
            "profit_hist": "历史利润",
            "month_trade_profit": "本月利润",
            "adj_0_2000": "调整后收益率(2K以下)%",
            "adj_2000_5000": "调整后收益率(2K-5K)%",
            "adj_5000_50000": "调整后收益率(5K-50K)%",
            "adj_50000_plus": "调整后收益率(50K以上)%",
            "return_non_adjusted": "非调整收益率%",
        }
        
        # Write CSV with BOM for Excel compatibility
        output.write('\ufeff')  # UTF-8 BOM
        writer = csv.DictWriter(output, fieldnames=headers.keys())
        
        # Write header row with Chinese names
        writer.writerow(headers)
        
        # Write data rows
        for row in data:
            writer.writerow({k: row.get(k, "") for k in headers.keys()})
        
        # Create streaming response
        output.seek(0)
        
        from datetime import datetime
        filename = f"client_return_rate_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error exporting client return rate")
        raise HTTPException(status_code=500, detail=str(e))
