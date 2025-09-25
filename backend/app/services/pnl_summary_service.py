from __future__ import annotations

from typing import List, Tuple, Optional
import math

import psycopg2

from ..core.config import get_settings
from .etl_service import run_pnl_etl_sync, EtlResult


def get_pnl_summary_from_db(symbol: str) -> Tuple[List[dict], int]:
    """查询报表库中的 pnl_summary。返回 (rows, count)。

    fresh grad note: 使用 dict 游标可以直接得到列名到值的映射，前端更易消费。
    """
    settings = get_settings()
    dsn = settings.postgres_dsn()
    sql = (
        "SELECT login, symbol, user_group, user_name, country, balance, "
        "total_closed_trades, buy_trades_count, sell_trades_count, "
        "total_closed_volume, buy_closed_volume, sell_closed_volume, "
        "total_closed_pnl, floating_pnl, last_updated "
        "FROM pnl_summary WHERE symbol = %s"
    )

    # 使用 DictCursor 需要 extras
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(dsn) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (symbol,))
            rows = cur.fetchall()
            return [dict(r) for r in rows], len(rows)


def get_pnl_summary_paginated(
    symbol: str,
    page: int = 1,
    page_size: int = 100,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    customer_id: Optional[str] = None,
    user_groups: Optional[List[str]] = None
) -> Tuple[List[dict], int, int]:
    """分页查询报表库中的 pnl_summary。
    
    Args:
        symbol: 交易品种，支持'__ALL__'查询所有产品
        page: 页码，从1开始
        page_size: 每页记录数
        sort_by: 排序字段
        sort_order: 排序方向 (asc/desc)
        customer_id: 客户ID筛选，为空则查询所有客户
        user_groups: 用户组别筛选列表，为空或包含'__ALL__'则查询所有组别
    
    Returns:
        Tuple[rows, total_count, total_pages]: (数据行, 总记录数, 总页数)
    """
    settings = get_settings()
    dsn = settings.postgres_dsn()
    
    # 允许的排序字段 (防止SQL注入)
    allowed_sort_fields = {
        "login", "user_name", "balance", "total_closed_trades",
        "buy_trades_count", "sell_trades_count", "total_closed_volume",
        "buy_closed_volume", "sell_closed_volume", "total_closed_pnl",
        "floating_pnl", "last_updated"
    }
    
    # 构建WHERE条件
    where_conditions = []
    query_params = []
    
    # 产品筛选条件
    if symbol == "__ALL__":
        # 查询所有产品 - 不添加symbol条件
        pass
    else:
        where_conditions.append("symbol = %s")
        query_params.append(symbol)
    
    # 客户ID筛选条件  
    if customer_id:
        try:
            # 验证customer_id是数字
            customer_id_int = int(customer_id)
            where_conditions.append("login = %s")
            query_params.append(customer_id_int)
        except ValueError:
            # 如果不是有效数字，忽略此筛选条件
            pass
    
    # 用户组别筛选条件
    if user_groups:
        if "__ALL__" in user_groups:
            # 选择了"全部组别"，不添加筛选条件（查询所有数据）
            pass
        elif "__NONE__" in user_groups:
            # 没有选择任何组别，添加永远不匹配的条件（返回0条数据）
            where_conditions.append("1 = 0")
        else:
            # 选择了具体组别，添加筛选条件
            if len(user_groups) == 1:
                where_conditions.append("user_group = %s")
                query_params.append(user_groups[0])
            else:
                # 多个组别用IN查询
                placeholders = ",".join(["%s"] * len(user_groups))
                where_conditions.append(f"user_group IN ({placeholders})")
                query_params.extend(user_groups)
    
    # 构建WHERE子句
    where_clause = " WHERE " + " AND ".join(where_conditions) if where_conditions else ""
    
    # 构建基础查询
    base_select = (
        "SELECT login, symbol, user_group, user_name, country, balance, "
        "total_closed_trades, buy_trades_count, sell_trades_count, "
        "total_closed_volume, buy_closed_volume, sell_closed_volume, "
        "total_closed_pnl, floating_pnl, last_updated "
        f"FROM pnl_summary{where_clause}"
    )
    
    # 构建排序子句
    order_clause = ""
    if sort_by and sort_by in allowed_sort_fields:
        sort_direction = "DESC" if sort_order.lower() == "desc" else "ASC"
        order_clause = f" ORDER BY {sort_by} {sort_direction}"
    else:
        # 默认按login排序，确保结果稳定
        order_clause = " ORDER BY login ASC"
    
    # 计算OFFSET
    offset = (page - 1) * page_size
    
    # 构建分页查询
    paginated_sql = base_select + order_clause + " LIMIT %s OFFSET %s"
    
    # 总数查询
    count_sql = f"SELECT COUNT(*) FROM pnl_summary{where_clause}"
    
    from psycopg2.extras import RealDictCursor
    
    with psycopg2.connect(dsn) as conn:
        # 获取总记录数（使用普通cursor）
        with conn.cursor() as cur:
            cur.execute(count_sql, query_params)
            total_count = cur.fetchone()[0]
        
        # 计算总页数
        total_pages = math.ceil(total_count / page_size) if total_count > 0 else 0
        
        # 获取分页数据（使用RealDictCursor）
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 分页查询参数：原查询参数 + page_size + offset
            paginated_params = query_params + [page_size, offset]
            cur.execute(paginated_sql, paginated_params)
            rows = cur.fetchall()
            
            return [dict(r) for r in rows], total_count, total_pages


def trigger_pnl_summary_sync(server: str, symbol: str) -> EtlResult:
    """同步执行ETL任务并返回详细结果。

    - 只允许 MT5，其他 server 直接跳过。
    - 现在改为同步执行，等待ETL完成后返回详细结果。
    - 支持 symbol="__ALL__" 来同步所有已知产品
    """
    if server != "MT5":
        # 为不支持的服务器返回空结果
        from datetime import datetime
        now = datetime.now()
        return EtlResult(
            success=False,
            processed_rows=0,
            new_max_deal_id=0,
            start_time=now,
            end_time=now,
            error_message="Server not supported; only MT5 is currently supported",
            new_trades_count=0,
            floating_only_count=0
        )

    # 处理"所有产品"的情况
    if symbol == "__ALL__":
        return _sync_all_products()
    
    # 调用新的ETL服务进行同步处理
    result = run_pnl_etl_sync(symbol=symbol, mode="incremental")
    return result


def _sync_all_products() -> EtlResult:
    """同步所有已知产品的ETL任务"""
    from datetime import datetime
    from .etl_service import PRODUCT_CONFIGS
    
    start_time = datetime.now()
    total_processed_rows = 0
    total_new_trades = 0
    total_floating_only = 0
    max_deal_id = 0
    failed_products = []
    
    # 获取所有已知产品
    products = list(PRODUCT_CONFIGS.keys())
    
    for product_symbol in products:
        try:
            result = run_pnl_etl_sync(symbol=product_symbol, mode="incremental")
            if result.success:
                total_processed_rows += result.processed_rows
                total_new_trades += result.new_trades_count
                total_floating_only += result.floating_only_count
                max_deal_id = max(max_deal_id, result.new_max_deal_id)
            else:
                failed_products.append(f"{product_symbol}: {result.error_message}")
        except Exception as e:
            failed_products.append(f"{product_symbol}: {str(e)}")
    
    end_time = datetime.now()
    
    # 构建结果
    if failed_products:
        # 部分失败
        error_msg = f"部分产品同步失败: {', '.join(failed_products)}"
        success = len(failed_products) < len(products)  # 只要有部分成功就算成功
    else:
        error_msg = None
        success = True
    
    return EtlResult(
        success=success,
        processed_rows=total_processed_rows,
        new_max_deal_id=max_deal_id,
        start_time=start_time,
        end_time=end_time,
        error_message=error_msg,
        new_trades_count=total_new_trades,
        floating_only_count=total_floating_only
    )


def get_user_groups() -> List[str]:
    """获取所有用户组别，去重并排序
    
    从pnl_summary表中查询所有不同的user_group值，
    用于前端筛选组件显示。
    
    Returns:
        List[str]: 去重后的用户组别列表，按字母顺序排序
    """
    settings = get_settings()
    dsn = settings.postgres_dsn()
    
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            # 查询所有非空的用户组别，去重并排序
            cur.execute("""
                SELECT DISTINCT user_group 
                FROM pnl_summary 
                WHERE user_group IS NOT NULL 
                  AND user_group != '' 
                  AND TRIM(user_group) != ''
                ORDER BY user_group ASC
            """)
            rows = cur.fetchall()
            return [row[0] for row in rows]


