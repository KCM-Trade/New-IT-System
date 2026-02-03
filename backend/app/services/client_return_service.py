"""
Service layer for Client Return Rate analysis.

This module handles ClickHouse queries for calculating client return rates
based on historical deposits, equity, and trading profits.
"""

import hashlib
import json
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging_config import get_logger
from app.services.clickhouse_service import clickhouse_service

logger = get_logger(__name__)


def get_client_return_rate_data(
    page: int = 1,
    page_size: int = 50,
    sort_by: Optional[str] = "month_trade_profit",
    sort_order: str = "desc",
    search: Optional[str] = None,
    deposit_bucket: Optional[str] = None,
    month_start: Optional[str] = None,
    month_end: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Query ClickHouse for client return rate data.
    
    Args:
        page: Page number (1-indexed)
        page_size: Items per page
        sort_by: Column to sort by
        sort_order: 'asc' or 'desc'
        search: Search by client_id
        deposit_bucket: Filter by deposit bucket ('0-2000', '2000-5000', '5000-50000', '50000+')
        month_start: Start date for current month (default: first day of current month)
        month_end: End date for current month (default: today)
    
    Returns:
        Dict with 'data', 'total', 'page', 'page_size', 'total_pages', 'statistics'
    """
    
    # Default to current month if not specified
    if not month_start:
        today = datetime.now()
        month_start = today.replace(day=1).strftime("%Y-%m-%d")
    if not month_end:
        month_end = datetime.now().strftime("%Y-%m-%d")
    
    # Validate sort_by to prevent SQL injection
    allowed_sort_columns = {
        "client_id", "net_deposit_hist", "net_deposit_month", "equity",
        "profit_hist", "month_trade_profit", "deposit_avg", "deposit_bucket",
        "return_non_adjusted", "return_adjusted",
        "adj_0_2000", "adj_2000_5000", "adj_5000_50000", "adj_50000_plus"
    }
    if sort_by not in allowed_sort_columns:
        sort_by = "month_trade_profit"
    
    sort_order = "DESC" if sort_order.lower() == "desc" else "ASC"
    
    # Generate cache key
    cache_params = f"client_return_v1_{month_start}_{month_end}_{search}_{deposit_bucket}_{sort_by}_{sort_order}_{page}_{page_size}"
    cache_key = f"app:client_return:cache:{hashlib.md5(cache_params.encode()).hexdigest()}"
    
    # Try to get from Redis cache
    try:
        if clickhouse_service.redis_client:
            cached_data = clickhouse_service.redis_client.get(cache_key)
            if cached_data:
                logger.info(f"Redis cache hit for client return rate: {cache_key[:50]}...")
                result = json.loads(cached_data)
                result["statistics"]["from_cache"] = True
                return result
    except Exception as e:
        logger.warning(f"Redis read error: {e}")
    
    try:
        start_time = datetime.now()
        
        # Build the main SQL query
        # Note: Using default ClickHouse connection (Fxbo_Trades database)
        # Simplified SQL without nested CTEs to avoid ClickHouse scope issues
        sql = """
        SELECT
            tm.client_id AS client_id,
            round(COALESCE(th.deposits_hist, 0) + COALESCE(th.withdrawals_hist, 0), 2) AS net_deposit_hist,
            round(COALESCE(txm.deposits_month, 0) + COALESCE(txm.withdrawals_month, 0), 2) AS net_deposit_month,
            round(COALESCE(eq.equity, 0), 2) AS equity,
            round(COALESCE(eq.equity, 0) - (COALESCE(th.deposits_hist, 0) + COALESCE(th.withdrawals_hist, 0)), 2) AS profit_hist,
            round(tm.month_trade_profit, 2) AS month_trade_profit,
            
            -- Deposit bucket for filtering
            multiIf(
                COALESCE(th.deposits_hist, 0) / greatest(COALESCE(th.deposit_count, 1), 1) < 2000, '0-2000',
                COALESCE(th.deposits_hist, 0) / greatest(COALESCE(th.deposit_count, 1), 1) < 5000, '2000-5000',
                COALESCE(th.deposits_hist, 0) / greatest(COALESCE(th.deposit_count, 1), 1) < 50000, '5000-50000',
                '50000+'
            ) AS deposit_bucket,
            
            -- Adjusted return rates by bucket (only one column has value per row)
            if(
                (COALESCE(th.deposits_hist, 0) + COALESCE(th.withdrawals_hist, 0)) <= 0 
                AND COALESCE(th.deposits_hist, 0) / greatest(COALESCE(th.deposit_count, 1), 1) < 2000,
                round(COALESCE(eq.equity, 0) / 2000 * 100, 2),
                NULL
            ) AS adj_0_2000,
            
            if(
                (COALESCE(th.deposits_hist, 0) + COALESCE(th.withdrawals_hist, 0)) <= 0 
                AND COALESCE(th.deposits_hist, 0) / greatest(COALESCE(th.deposit_count, 1), 1) >= 2000
                AND COALESCE(th.deposits_hist, 0) / greatest(COALESCE(th.deposit_count, 1), 1) < 5000,
                round(COALESCE(eq.equity, 0) / 5000 * 100, 2),
                NULL
            ) AS adj_2000_5000,
            
            if(
                (COALESCE(th.deposits_hist, 0) + COALESCE(th.withdrawals_hist, 0)) <= 0 
                AND COALESCE(th.deposits_hist, 0) / greatest(COALESCE(th.deposit_count, 1), 1) >= 5000
                AND COALESCE(th.deposits_hist, 0) / greatest(COALESCE(th.deposit_count, 1), 1) < 50000,
                round(COALESCE(eq.equity, 0) / 50000 * 100, 2),
                NULL
            ) AS adj_5000_50000,
            
            if(
                (COALESCE(th.deposits_hist, 0) + COALESCE(th.withdrawals_hist, 0)) <= 0 
                AND COALESCE(th.deposits_hist, 0) / greatest(COALESCE(th.deposit_count, 1), 1) >= 50000,
                round(COALESCE(eq.equity, 0) / 60000 * 100, 2),
                NULL
            ) AS adj_50000_plus,
            
            -- Non-adjusted return rate (when net_deposit > 0)
            if(
                (COALESCE(th.deposits_hist, 0) + COALESCE(th.withdrawals_hist, 0)) > 0,
                round(
                    (COALESCE(eq.equity, 0) - (COALESCE(th.deposits_hist, 0) + COALESCE(th.withdrawals_hist, 0))) 
                    / (COALESCE(th.deposits_hist, 0) + COALESCE(th.withdrawals_hist, 0)) * 100,
                    2
                ),
                NULL
            ) AS return_non_adjusted

        FROM (
            -- Current month trading profit (BUY/SELL orders only)
            SELECT
                mu.userId AS client_id,
                SUM(if(mu.CURRENCY = 'CEN', toFloat64(t.PROFIT) / 100.0, toFloat64(t.PROFIT))) AS month_trade_profit
            FROM fxbackoffice_mt4_trades t
            INNER JOIN fxbackoffice_mt4_users mu ON t.LOGIN = mu.LOGIN
            WHERE toDate(t.CLOSE_TIME) BETWEEN %(month_start)s AND %(month_end)s
              AND t.CMD IN (0, 1)
              AND mu.userId > 0
              AND mu.sid IN (1, 5, 6)
            GROUP BY mu.userId
        ) AS tm
        
        LEFT JOIN (
            -- Client real-time equity
            SELECT
                userId AS client_id,
                SUM(if(upper(CURRENCY) = 'CEN', toFloat64(EQUITY) / 100.0, toFloat64(EQUITY))) AS equity
            FROM fxbackoffice_mt4_users
            WHERE userId > 0 AND sid IN (1, 5, 6)
            GROUP BY userId
        ) AS eq ON tm.client_id = eq.client_id
        
        LEFT JOIN (
            -- Historical deposit/withdrawal (CMD=6 is Balance)
            SELECT
                mu.userId AS client_id,
                sumIf(if(mu.CURRENCY = 'CEN', toFloat64(t.PROFIT) / 100.0, toFloat64(t.PROFIT)), t.PROFIT > 0) AS deposits_hist,
                sumIf(if(mu.CURRENCY = 'CEN', toFloat64(t.PROFIT) / 100.0, toFloat64(t.PROFIT)), t.PROFIT < 0) AS withdrawals_hist,
                countIf(t.PROFIT > 0) AS deposit_count
            FROM fxbackoffice_mt4_trades t
            INNER JOIN fxbackoffice_mt4_users mu ON t.LOGIN = mu.LOGIN
            WHERE t.CMD = 6 AND mu.userId > 0 AND mu.sid IN (1, 5, 6)
            GROUP BY mu.userId
        ) AS th ON tm.client_id = th.client_id
        
        LEFT JOIN (
            -- Current month deposit/withdrawal
            SELECT
                mu.userId AS client_id,
                sumIf(if(mu.CURRENCY = 'CEN', toFloat64(t.PROFIT) / 100.0, toFloat64(t.PROFIT)), t.PROFIT > 0) AS deposits_month,
                sumIf(if(mu.CURRENCY = 'CEN', toFloat64(t.PROFIT) / 100.0, toFloat64(t.PROFIT)), t.PROFIT < 0) AS withdrawals_month
            FROM fxbackoffice_mt4_trades t
            INNER JOIN fxbackoffice_mt4_users mu ON t.LOGIN = mu.LOGIN
            WHERE t.CMD = 6
              AND toDate(t.CLOSE_TIME) BETWEEN %(month_start)s AND %(month_end)s
              AND mu.userId > 0 AND mu.sid IN (1, 5, 6)
            GROUP BY mu.userId
        ) AS txm ON tm.client_id = txm.client_id
        
        WHERE 1=1
        """
        
        params = {
            "month_start": month_start,
            "month_end": month_end,
        }
        
        # Add search filter
        if search:
            clean_search = search.strip()
            if clean_search.isdigit():
                sql += " AND tm.client_id = %(search_id)s"
                params["search_id"] = int(clean_search)
        
        # Add sorting (use column alias)
        sql += f" ORDER BY {sort_by} {sort_order} NULLS LAST"
        
        logger.info(f"Executing client return rate query: month={month_start}~{month_end}, search={search}, bucket={deposit_bucket}")
        
        # Execute query using default ClickHouse connection
        with clickhouse_service.get_client(use_prod=False) as client:
            result = client.query(sql, parameters=params)
            
            # Get query statistics
            query_info = result.summary if hasattr(result, 'summary') else {}
            rows_read = query_info.get('read_rows', 0) if query_info else 0
            bytes_read = query_info.get('read_bytes', 0) if query_info else 0
            
            columns = result.column_names
            all_data = [dict(zip(columns, row)) for row in result.result_set]
            
            # Apply deposit_bucket filter in memory (since it's a computed column)
            if deposit_bucket:
                all_data = [row for row in all_data if row.get("deposit_bucket") == deposit_bucket]
            
            # Calculate total and pagination
            total = len(all_data)
            total_pages = math.ceil(total / page_size) if total > 0 else 1
            
            # Apply pagination in memory (for simplicity)
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            paginated_data = all_data[start_idx:end_idx]
            
            # Remove deposit_bucket from output (used internally for filtering)
            for row in paginated_data:
                row.pop("deposit_bucket", None)
            
            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            response = {
                "data": paginated_data,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "statistics": {
                    "query_time_ms": round(elapsed_ms, 2),
                    "from_cache": False,
                    "month_range": f"{month_start} ~ {month_end}",
                    "rows_read": rows_read,
                    "bytes_read": bytes_read
                }
            }
            
            # Cache the result (TTL 30 minutes)
            try:
                if clickhouse_service.redis_client:
                    clickhouse_service.redis_client.setex(
                        cache_key,
                        1800,
                        json.dumps(response, default=str)
                    )
                    logger.info(f"Redis cache saved for client return rate: {cache_key[:50]}...")
            except Exception as e:
                logger.warning(f"Redis save error: {e}")
            
            return response
            
    except Exception as e:
        logger.exception("Error in get_client_return_rate_data")
        raise e
