from __future__ import annotations

from typing import List, Optional, Tuple
from datetime import datetime as _dt
import math
import os

import psycopg2
from psycopg2.extras import RealDictCursor


def _pg_dsn() -> str:
    """获取 PostgreSQL 连接字符串"""
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    db = os.getenv("POSTGRES_DBNAME_MT5", "MT5_ETL")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "")
    return f"host={host} port={port} dbname={db} user={user} password={password}"


def get_client_pnl_summary_paginated(
    page: int = 1,
    page_size: int = 50,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    search: Optional[str] = None,
) -> Tuple[List[dict], int, int, Optional[_dt]]:
    """分页查询 ClientID 盈亏汇总表
    
    Args:
        page: 页码（从1开始）
        page_size: 每页记录数
        sort_by: 排序字段
        sort_order: 排序方向（asc/desc）
        search: 搜索关键词（支持 client_id 或 account_id 精确匹配）
    
    Returns:
        (rows, total_count, total_pages, last_updated)
    """
    
    # 排序白名单（防注入）
    allowed_sort_fields = {
        "client_id", "client_name", "primary_server", "account_count",
        "total_balance_usd", "total_credit_usd", "total_floating_pnl_usd", "total_equity_usd",
        "total_closed_profit_usd", "total_commission_usd",
        "total_deposit_usd", "total_withdrawal_usd", "net_deposit_usd",
        "total_volume_lots", "total_overnight_volume_lots", "overnight_volume_ratio",
        "total_closed_count", "total_overnight_count",
        "closed_sell_volume_lots", "closed_sell_count", "closed_sell_profit_usd",
        "closed_buy_volume_lots", "closed_buy_count", "closed_buy_profit_usd",
        "last_updated",
    }
    
    where_conditions: List[str] = []
    params: List[object] = []
    
    # 搜索：支持 client_id 或 account_id（在 account_list 中）
    if search is not None:
        s = str(search).strip()
        if s:
            try:
                search_int = int(s)
                # 尝试匹配 client_id 或 account_list 中的 login
                where_conditions.append("(client_id = %s OR %s = ANY(account_list))")
                params.append(search_int)
                params.append(search_int)
            except ValueError:
                # 非数字，模糊匹配客户名称
                where_conditions.append("client_name ILIKE %s")
                params.append(f"%{s}%")
    
    where_clause = " WHERE " + " AND ".join(where_conditions) if where_conditions else ""
    
    base_select = (
        "SELECT client_id, client_name, primary_server, countries, currencies, "
        "account_count, account_list, "
        "total_balance_usd, total_credit_usd, total_floating_pnl_usd, total_equity_usd, "
        "total_closed_profit_usd, total_commission_usd, "
        "total_deposit_usd, total_withdrawal_usd, net_deposit_usd, "
        "total_volume_lots, total_overnight_volume_lots, overnight_volume_ratio, "
        "total_closed_count, total_overnight_count, "
        "closed_sell_volume_lots, closed_sell_count, closed_sell_profit_usd, closed_sell_swap_usd, "
        "closed_buy_volume_lots, closed_buy_count, closed_buy_profit_usd, closed_buy_swap_usd, "
        "last_updated "
        "FROM public.pnl_client_summary" + where_clause
    )
    
    # 排序
    order_clause = ""
    if sort_by and sort_by in allowed_sort_fields:
        direction = "DESC" if sort_order.lower() == "desc" else "ASC"
        # 对可能为 NULL 的字段添加 NULLS LAST
        order_clause = f" ORDER BY {sort_by} {direction} NULLS LAST, client_id ASC"
    else:
        # 默认按 client_id 升序
        order_clause = " ORDER BY client_id ASC"
    
    offset = (page - 1) * page_size
    paginated_sql = base_select + order_clause + " LIMIT %s OFFSET %s"
    
    count_sql = f"SELECT COUNT(*) FROM public.pnl_client_summary" + where_clause
    
    dsn = _pg_dsn()
    with psycopg2.connect(dsn) as conn:
        # 查询总数
        with conn.cursor() as cur:
            cur.execute(count_sql, params)
            total_count = cur.fetchone()[0]
        
        total_pages = math.ceil(total_count / page_size) if total_count > 0 else 0
        
        # 查询分页数据
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(paginated_sql, params + [page_size, offset])
            rows = cur.fetchall()
        
        # 为每个客户获取账户明细
        result_rows = []
        for row in rows:
            row_dict = dict(row)
            client_id = row_dict['client_id']
            
            # 查询该客户的所有账户
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT 
                        client_id, login, server, currency, user_name, user_group, country,
                        balance_usd, credit_usd, floating_pnl_usd, equity_usd,
                        closed_profit_usd, commission_usd, deposit_usd, withdrawal_usd,
                        volume_lots, last_updated
                    FROM public.pnl_client_accounts
                    WHERE client_id = %s
                    ORDER BY server, login
                    """,
                    (client_id,)
                )
                accounts = cur.fetchall()
                row_dict['accounts'] = [dict(a) for a in accounts]
            
            result_rows.append(row_dict)
        
        # 获取最后更新时间（取汇总表中的最大 last_updated）
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(last_updated) FROM public.pnl_client_summary")
            row = cur.fetchone()
            last_updated = row[0] if row else None
        
        return result_rows, total_count, total_pages, last_updated


def get_client_accounts(client_id: int) -> List[dict]:
    """获取某个客户的所有账户明细
    
    Args:
        client_id: 客户ID
    
    Returns:
        账户列表
    """
    dsn = _pg_dsn()
    with psycopg2.connect(dsn) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT 
                    client_id, login, server, currency, user_name, user_group, country,
                    balance_usd, credit_usd, floating_pnl_usd, equity_usd,
                    closed_profit_usd, commission_usd, deposit_usd, withdrawal_usd,
                    volume_lots, last_updated
                FROM public.pnl_client_accounts
                WHERE client_id = %s
                ORDER BY server, login
                """,
                (client_id,)
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows]


def initialize_client_summary(force: bool = False) -> dict:
    """初始化客户聚合表
    
    Args:
        force: 是否强制重新初始化（清空现有数据）
    
    Returns:
        统计信息字典
    """
    dsn = _pg_dsn()
    with psycopg2.connect(dsn) as conn:
        # 如果 force=True，先清空数据
        if force:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE public.pnl_client_summary CASCADE")
                cur.execute("TRUNCATE TABLE public.pnl_client_accounts CASCADE")
                conn.commit()
        
        # 调用初始化函数
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM public.initialize_client_summary()")
            row = cur.fetchone()
            conn.commit()
            
            if row:
                return {
                    "total_clients": int(row[0]),
                    "total_accounts": int(row[1]),
                    "duration_seconds": float(row[2]),
                }
            return {
                "total_clients": 0,
                "total_accounts": 0,
                "duration_seconds": 0.0,
            }


def compare_client_summary(auto_fix: bool = False) -> List[dict]:
    """对比客户聚合表与源表的差异
    
    Args:
        auto_fix: 是否自动修复差异
    
    Returns:
        差异列表
    """
    dsn = _pg_dsn()
    with psycopg2.connect(dsn) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM public.compare_client_summary(%s)",
                (auto_fix,)
            )
            rows = cur.fetchall()
            conn.commit()
            return [dict(r) for r in rows]


def get_refresh_status() -> dict:
    """获取数据刷新状态
    
    Returns:
        状态信息字典
    """
    dsn = _pg_dsn()
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            # 获取最后更新时间和统计信息
            cur.execute(
                """
                SELECT 
                    MAX(last_updated) AS last_updated,
                    COUNT(*) AS total_clients
                FROM public.pnl_client_summary
                """
            )
            row = cur.fetchone()
            
            # 获取账户总数
            cur.execute("SELECT COUNT(*) FROM public.pnl_client_accounts")
            total_accounts = cur.fetchone()[0]
            
            return {
                "last_updated": row[0] if row else None,
                "total_clients": int(row[1]) if row and row[1] else 0,
                "total_accounts": int(total_accounts),
            }

