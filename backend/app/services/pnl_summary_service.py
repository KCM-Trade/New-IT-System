from __future__ import annotations

from typing import List, Tuple

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


def trigger_pnl_summary_sync(server: str, symbol: str) -> EtlResult:
    """同步执行ETL任务并返回详细结果。

    - 只允许 MT5，其他 server 直接跳过。
    - 现在改为同步执行，等待ETL完成后返回详细结果。
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

    # 调用新的ETL服务进行同步处理
    result = run_pnl_etl_sync(symbol=symbol, mode="incremental")
    return result


