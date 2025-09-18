from __future__ import annotations

import os
import sys
import subprocess
from typing import List, Tuple

import psycopg2

from ..core.config import get_settings


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


def trigger_pnl_summary_sync(server: str, symbol: str) -> str:
    """异步触发后端 ETL 同步。

    - 只允许 MT5，其他 server 直接跳过。
    - 使用 subprocess.Popen 异步执行，不阻塞当前请求。
    """
    if server != "MT5":
        return "Server not supported; skip trigger"

    settings = get_settings()

    # 找到仓库根，拼出脚本路径
    repo_root = settings.repo_root
    script_path = repo_root / "backend" / "data" / "sync_pnl_summary.py"

    # 构建命令：使用当前进程解释器，确保在容器或 .venv 内运行
    cmd = [
        sys.executable or "python",
        str(script_path),
        symbol,
        "--mode",
        "incremental",
    ]

    # 传递当前环境变量，确保 .env 或 Settings 生效
    env = os.environ.copy()

    # 启动子进程（不等待）
    subprocess.Popen(cmd, cwd=str(repo_root), env=env)
    return "Triggered"


