from __future__ import annotations

from typing import List, Optional, Tuple
from datetime import datetime as _dt
import math
import os
import re
import subprocess
import sys

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
        "client_id",
        "client_name",
        "zipcode",
        "is_enabled",
        "account_count",
        "total_balance_usd",
        "total_floating_pnl_usd",
        "total_equity_usd",
        "total_closed_profit_usd",
        "total_commission_usd",
        "total_deposit_usd",
        "total_withdrawal_usd",
        "net_deposit_usd",
        "total_volume_lots",
        "total_overnight_volume_lots",
        "auto_swap_free_status",
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
        "SELECT "
        "  client_id, "
        "  client_name, "
        "  NULL::text AS primary_server, "
        "  zipcode, "
        "  is_enabled, "
        "  NULL::text[] AS countries, "
        "  NULL::text[] AS currencies, "
        "  account_count, "
        "  NULL::bigint[] AS account_list, "
        "  COALESCE(total_balance_usd, 0)::numeric AS total_balance_usd, "
        "  0.0::numeric AS total_credit_usd, "
        "  COALESCE(total_floating_pnl_usd, 0)::numeric AS total_floating_pnl_usd, "
        "  COALESCE(total_equity_usd, 0)::numeric AS total_equity_usd, "
        "  COALESCE(total_closed_profit_usd, 0)::numeric AS total_closed_profit_usd, "
        "  COALESCE(total_commission_usd, 0)::numeric AS total_commission_usd, "
        "  COALESCE(total_deposit_usd, 0)::numeric AS total_deposit_usd, "
        "  COALESCE(total_withdrawal_usd, 0)::numeric AS total_withdrawal_usd, "
        "  (COALESCE(total_deposit_usd, 0) - COALESCE(total_withdrawal_usd, 0))::numeric AS net_deposit_usd, "
        "  COALESCE(total_volume_lots, 0)::numeric AS total_volume_lots, "
        "  COALESCE(total_overnight_volume_lots, 0)::numeric AS total_overnight_volume_lots, "
        "  COALESCE(auto_swap_free_status, -1)::numeric AS auto_swap_free_status, "
        "  0::bigint AS total_closed_count, "
        "  0::bigint AS total_overnight_count, "
        "  0.0::numeric AS closed_sell_volume_lots, "
        "  0::bigint AS closed_sell_count, "
        "  0.0::numeric AS closed_sell_profit_usd, "
        "  0.0::numeric AS closed_sell_swap_usd, "
        "  0.0::numeric AS closed_buy_volume_lots, "
        "  0::bigint AS closed_buy_count, "
        "  0.0::numeric AS closed_buy_profit_usd, "
        "  0.0::numeric AS closed_buy_swap_usd, "
        "  last_updated "
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
            rows = [dict(r) for r in cur.fetchall()]
        
        # 获取最后更新时间（取汇总表中的最大 last_updated）
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(last_updated) FROM public.pnl_client_summary")
            row = cur.fetchone()
            last_updated = row[0] if row else None
        
        return rows, total_count, total_pages, last_updated


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
                    client_id,
                    login,
                    server,
                    currency,
                    user_name,
                    user_group,
                    country,
                    balance_usd,
                    floating_pnl_usd,
                    equity_usd,
                    closed_profit_usd,
                    commission_usd,
                    deposit_usd,
                    withdrawal_usd,
                    volume_lots,
                    auto_swap_free_status,
                    last_updated
                FROM public.pnl_client_accounts
                WHERE client_id = %s
                ORDER BY server, login
                """,
                (client_id,)
            )
            rows = cur.fetchall()
            # Add credit_usd field with default value 0.0 since it doesn't exist in the table
            # This maintains compatibility with the schema while the field is not in the database
            return [{**dict(r), 'credit_usd': 0.0} for r in rows]


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


def run_client_pnl_incremental_refresh() -> dict:
    """Run incremental refresh script for client PnL and parse progress output.

    Returns a dict with fields: success, message, duration_seconds, steps, raw_log,
    and counters parsed from the script's stdout.
    """
    # Build command to run the existing script with the same interpreter
    script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "incremental_refresh_client_pnl.py")
    # Fallback if relative path differs when packaged
    if not os.path.exists(script_path):
        script_path = os.path.join(os.getcwd(), "backend", "incremental_refresh_client_pnl.py")

    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        output = proc.stdout or ""

        # Parse timings
        timings_re = re.compile(r"Timings \(sec\) => watermark: (?P<watermark>[0-9.]+), candidates: (?P<candidates>[0-9.]+), accounts: (?P<accounts>[0-9.]+), delete_orphans: (?P<delete_orphans>[0-9.]+), mapping: (?P<mapping>[0-9.]+), summary: (?P<summary>[0-9.]+), stats: (?P<stats>[0-9.]+), total: (?P<total>[0-9.]+)")
        timings_match = timings_re.search(output)
        steps = []
        total_duration = None
        if timings_match:
            gd = timings_match.groupdict()
            steps = [
                {"name": "watermark", "duration_seconds": float(gd.get("watermark") or 0)},
                {"name": "candidates", "duration_seconds": float(gd.get("candidates") or 0)},
                {"name": "accounts_upsert", "duration_seconds": float(gd.get("accounts") or 0)},
                {"name": "delete_orphans", "duration_seconds": float(gd.get("delete_orphans") or 0)},
                {"name": "mapping", "duration_seconds": float(gd.get("mapping") or 0)},
                {"name": "summary_upsert", "duration_seconds": float(gd.get("summary") or 0)},
                {"name": "stats", "duration_seconds": float(gd.get("stats") or 0)},
            ]
            total_duration = float(gd.get("total") or 0)

        # Parse counters
        def extract_int(pattern: str) -> Optional[int]:
            m = re.search(pattern, output)
            if m:
                try:
                    return int(m.group(1))
                except Exception:
                    return None
            return None

        def extract_str(pattern: str) -> Optional[str]:
            m = re.search(pattern, output)
            return m.group(1) if m else None

        candidates_total = extract_int(r"Candidates \(clients\):\s+(\d+)")
        missing_cnt = extract_int(r"\| missing:\s+(\d+)")
        lag_cnt = extract_int(r"\| lag:\s+(\d+)")
        accounts_affected = extract_int(r"Accounts UPSERT affected rows:\s+(\d+)")
        orphan_deleted = extract_int(r"\| orphan deleted:\s+(\d+)")
        loaded_mapping = extract_int(r"Mapping loaded:\s+(\d+)")
        zipcode_changes = extract_int(r"\| zipcode changes:\s+(\d+)")
        summary_affected = extract_int(r"Summary UPSERT affected rows:\s+(\d+)")
        max_last_updated = extract_str(r"Max last_updated \(affected\):\s+([0-9\-:\+\s]+)")

        # Enrich steps with counters when known
        for s in steps:
            if s["name"] == "candidates":
                s.update({"total": candidates_total, "missing": missing_cnt, "lag": lag_cnt})
            elif s["name"] == "accounts_upsert":
                s.update({"affected_rows": accounts_affected})
            elif s["name"] == "delete_orphans":
                s.update({"affected_rows": orphan_deleted})
            elif s["name"] == "mapping":
                s.update({"loaded_mapping": loaded_mapping, "zipcode_changes": zipcode_changes})
            elif s["name"] == "summary_upsert":
                s.update({"affected_rows": summary_affected})

        success = proc.returncode == 0
        return {
            "success": success,
            "message": "Incremental refresh completed" if success else "Incremental refresh failed",
            "duration_seconds": total_duration if total_duration is not None else 0.0,
            "steps": steps,
            "raw_log": output,
            "candidates_total": candidates_total,
            "missing_cnt": missing_cnt,
            "lag_cnt": lag_cnt,
            "accounts_affected": accounts_affected,
            "orphan_deleted": orphan_deleted,
            "loaded_mapping": loaded_mapping,
            "zipcode_changes": zipcode_changes,
            "summary_affected": summary_affected,
            "max_last_updated": max_last_updated,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Incremental refresh failed: {e}",
            "duration_seconds": 0.0,
            "steps": [],
            "raw_log": str(e),
        }

