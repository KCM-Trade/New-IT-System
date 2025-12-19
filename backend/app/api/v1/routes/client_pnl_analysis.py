from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional, Dict, Any
from datetime import date, datetime

from app.services.clickhouse_service import clickhouse_service

router = APIRouter(prefix="/client-pnl-analysis")

@router.get("/query", response_model=Dict[str, Any])
def query_client_pnl_analysis(
    start_date: date = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: date = Query(..., description="End date (YYYY-MM-DD)"),
    search: Optional[str] = Query(None, description="Search by Client ID or Name"),
):
    """
    Query ClickHouse for client PnL analysis based on a date range.
    Returns a list of records.
    """
    print(f"👉 [DEBUG] Received Query: start={start_date}, end={end_date}, search={search}")
    try:
        # Convert date to datetime for the service if needed, or adjust service to accept date
        # Assuming service handles datetime
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        
        print(f"👉 [DEBUG] Calling ClickHouse Service...")
        result = clickhouse_service.get_pnl_analysis(start_dt, end_dt, search)
        data = result.get("data", [])
        stats = result.get("statistics", {})
        print(f"👉 [DEBUG] ClickHouse Service returned {len(data)} rows")
        
        return {
            "ok": True,
            "data": data,
            "statistics": stats,
            "count": len(data)
        }
    except Exception as e:
        print(f"❌ [ERROR] API Error: {str(e)}")
        # Check if it's likely a connection/waking up issue
        err_msg = str(e)
        if "timeout" in err_msg.lower() or "connection" in err_msg.lower() or "502" in err_msg:
            # Return specific code for connection issues
            raise HTTPException(
                status_code=503, 
                detail="ClickHouse database might be waking up (Paused). Please try again in 30-60 seconds."
            )
        
        raise HTTPException(status_code=500, detail=f"Query failed: {err_msg}")

