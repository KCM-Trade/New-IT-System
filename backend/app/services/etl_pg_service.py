from __future__ import annotations

from typing import List, Optional, Tuple
import math
import os

import psycopg2
from psycopg2.extras import RealDictCursor


def _pg_mt5_dsn() -> str:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    db = os.getenv("POSTGRES_DBNAME_MT5")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "")
    return f"host={host} port={port} dbname={db} user={user} password={password}"


def get_pnl_user_summary_paginated(
    page: int = 1,
    page_size: int = 100,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    user_groups: Optional[List[str]] = None,
    search: Optional[str] = None,
) -> Tuple[List[dict], int, int]:
    """分页查询 public.pnl_user_summary

    Returns: rows, total_count, total_pages
    """

    # 排序白名单（防注入）
    allowed_sort_fields = {
        "login", "symbol", "user_name", "user_group", "country", "zipcode", "user_id",
        "user_balance", "user_credit", "positions_floating_pnl", "equity",
        "closed_sell_volume_lots", "closed_sell_count", "closed_sell_profit", "closed_sell_swap",
        "closed_sell_overnight_count", "closed_sell_overnight_volume_lots",
        "closed_buy_volume_lots", "closed_buy_count", "closed_buy_profit", "closed_buy_swap",
        "closed_buy_overnight_count", "closed_buy_overnight_volume_lots",
        "total_commission", "deposit_count", "deposit_amount", "withdrawal_count",
        "withdrawal_amount", "net_deposit", "last_updated",
    }

    where_conditions: List[str] = []
    params: List[object] = []

    # 组别筛选 + 特殊选项（与旧 /pnl 行为保持一致）
    if user_groups:
        cleaned = [g.strip() for g in user_groups if g and g.strip()]
        if cleaned:
            if "__ALL__" in cleaned:
                # 全部组别：不加组别条件，但允许处理排除客户名 test 的条件
                if "__EXCLUDE_USER_NAME_TEST__" in cleaned:
                    where_conditions.append("user_name NOT ILIKE %s")
                    params.append("%test%")
            elif "__NONE__" in cleaned:
                # 显式要求返回 0 行
                where_conditions.append("1 = 0")
            else:
                # 分离常规组别与特殊筛选
                regular_groups = [g for g in cleaned if g not in ["__USER_NAME_TEST__", "__EXCLUDE_USER_NAME_TEST__"]]
                has_user_name_test = "__USER_NAME_TEST__" in cleaned
                has_exclude_user_name_test = "__EXCLUDE_USER_NAME_TEST__" in cleaned

                group_conditions: List[str] = []

                if regular_groups:
                    if len(regular_groups) == 1:
                        group_conditions.append("user_group = %s")
                        params.append(regular_groups[0])
                    else:
                        placeholders = ",".join(["%s"] * len(regular_groups))
                        group_conditions.append(f"user_group IN ({placeholders})")
                        params.extend(regular_groups)

                if has_user_name_test:
                    group_conditions.append("user_name ILIKE %s")
                    params.append("%test%")

                if group_conditions:
                    combined = "(" + " OR ".join(group_conditions) + ")"
                    where_conditions.append(combined)

                if has_exclude_user_name_test:
                    where_conditions.append("user_name NOT ILIKE %s")
                    params.append("%test%")

    # 统一搜索（login 精确 或 user_name 模糊）
    if search is not None:
        s = str(search).strip()
        if s:
            sub = []
            try:
                login_int = int(s)
                sub.append("login = %s")
                params.append(login_int)
            except ValueError:
                pass
            sub.append("user_name ILIKE %s")
            params.append(f"%{s}%")
            where_conditions.append("(" + " OR ".join(sub) + ")")

    where_clause = " WHERE " + " AND ".join(where_conditions) if where_conditions else ""

    base_select = (
        "SELECT login, symbol, user_name, user_group, country, zipcode, user_id, "
        "user_balance, user_credit, positions_floating_pnl, equity, "
        "closed_sell_volume_lots, closed_sell_count, closed_sell_profit, closed_sell_swap, "
        "closed_sell_overnight_count, closed_sell_overnight_volume_lots, "
        "closed_buy_volume_lots, closed_buy_count, closed_buy_profit, closed_buy_swap, "
        "closed_buy_overnight_count, closed_buy_overnight_volume_lots, "
        "total_commission, deposit_count, deposit_amount, withdrawal_count, withdrawal_amount, net_deposit, "
        "last_updated "
        "FROM public.pnl_user_summary" + where_clause
    )

    # 排序
    order_clause = ""
    if sort_by and sort_by in allowed_sort_fields:
        direction = "DESC" if sort_order.lower() == "desc" else "ASC"
        order_clause = f" ORDER BY {sort_by} {direction}"
    else:
        order_clause = " ORDER BY login ASC"

    offset = (page - 1) * page_size
    paginated_sql = base_select + order_clause + " LIMIT %s OFFSET %s"

    count_sql = "SELECT COUNT(*) FROM public.pnl_user_summary" + where_clause

    dsn = _pg_mt5_dsn()
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(count_sql, params)
            total_count = cur.fetchone()[0]

        total_pages = math.ceil(total_count / page_size) if total_count > 0 else 0

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(paginated_sql, params + [page_size, offset])
            rows = cur.fetchall()
            return [dict(r) for r in rows], total_count, total_pages



def get_etl_watermark_last_updated(dataset: str = "pnl_user_summary") -> Optional["datetime"]:
    """查询 public.etl_watermarks 中指定 dataset 的 last_updated（UTC+0）

    返回 None 表示不存在记录。
    """
    dsn = _pg_mt5_dsn()
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_updated FROM public.etl_watermarks WHERE dataset = %s LIMIT 1",
                (dataset,),
            )
            row = cur.fetchone()
            return row[0] if row else None

