from __future__ import annotations

from typing import Any, Dict, List
import pymysql

from ..core.config import Settings


def _connect_mysql(settings: Settings):
    """连接MySQL数据库"""
    return pymysql.connect(
        host=settings.DB_HOST,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME,
        port=settings.DB_PORT,
        charset=settings.DB_CHARSET,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _resolve_table(conn) -> str:
    """解析mt4交易表：优先使用当前DB.mt4_trades；如果存在则回退到mt4_live.mt4_trades"""
    with conn.cursor() as cur:
        # 检查当前数据库表
        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = 'mt4_trades'
            """
        )
        if (cur.fetchone() or {}).get("cnt", 0) > 0:
            return "mt4_trades"
        # 回退到mt4_live schema
        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM information_schema.tables
            WHERE table_schema = 'mt4_live' AND table_name = 'mt4_trades'
            """
        )
        if (cur.fetchone() or {}).get("cnt", 0) > 0:
            return "mt4_live.mt4_trades"
    # 最后的选择
    return "mt4_trades"


def get_hourly_trade_details(
    settings: Settings,
    start_time: str,
    end_time: str,
    symbol: str = "XAUUSD",
    time_type: str = "open",
    limit: int = 100,
) -> Dict[str, Any]:
    """
    获取指定小时段内的交易明细
    
    Args:
        settings: 配置设置
        start_time: 开始时间 (格式: YYYY-MM-DD HH:00:00)
        end_time: 结束时间 (格式: YYYY-MM-DD HH:59:59)
        symbol: 交易品种
        time_type: 时间类型 ('open' 表示按开仓时间，'close' 表示按平仓时间)
        limit: 返回记录数限制
        
    Returns:
        包含交易明细和汇总信息的字典
    """
    conn = _connect_mysql(settings)
    try:
        with conn.cursor() as cur:
            table = _resolve_table(conn)
            
            # 根据time_type选择时间字段
            time_field = "t.OPEN_TIME" if time_type == "open" else "t.CLOSE_TIME"
            
            # 构建SQL查询 - 与aggregation_service保持一致的过滤条件
            sql = f"""
            SELECT 
                CAST(t.LOGIN AS CHAR) AS login,
                t.TICKET AS ticket,
                t.SYMBOL AS symbol,
                CASE WHEN t.CMD=0 THEN 'buy' ELSE 'sell' END AS side,
                t.VOLUME/100.0 AS lots,
                DATE_FORMAT(t.OPEN_TIME, '%%Y-%%m-%%d %%H:%%i:%%s') AS open_time,
                DATE_FORMAT(t.CLOSE_TIME, '%%Y-%%m-%%d %%H:%%i:%%s') AS close_time,
                t.OPEN_PRICE AS open_price,
                t.CLOSE_PRICE AS close_price,
                t.PROFIT AS profit,
                t.SWAPS AS swaps
            FROM {table} t
            WHERE t.CMD IN (0,1)
              AND t.SYMBOL = %s
              AND {time_field} BETWEEN %s AND %s
              AND t.CLOSE_TIME != '1970-01-01 00:00:00'
              AND t.login NOT IN (
                SELECT LOGIN FROM mt4_live.mt4_users 
                WHERE ((`GROUP` LIKE %s) OR (name LIKE %s)) 
                  AND ((`GROUP` LIKE %s) OR (`GROUP` LIKE %s))
              )
            ORDER BY {time_field} DESC
            LIMIT %s
            """
            
            # 执行查询获取交易明细 - 添加测试账户过滤参数
            cur.execute(sql, [symbol, start_time, end_time, "%test%", "%test%", "KCM%", "testKCM%", limit])
            trades = cur.fetchall()
            
            # 获取总记录数 - 与明细查询保持一致的过滤条件
            count_sql = f"""
            SELECT COUNT(*) AS total_count,
                   COALESCE(SUM(t.PROFIT), 0) AS total_profit
            FROM {table} t
            WHERE t.CMD IN (0,1)
              AND t.SYMBOL = %s
              AND {time_field} BETWEEN %s AND %s
              AND t.CLOSE_TIME != '1970-01-01 00:00:00'
              AND t.login NOT IN (
                SELECT LOGIN FROM mt4_live.mt4_users 
                WHERE ((`GROUP` LIKE %s) OR (name LIKE %s)) 
                  AND ((`GROUP` LIKE %s) OR (`GROUP` LIKE %s))
              )
            """
            
            cur.execute(count_sql, [symbol, start_time, end_time, "%test%", "%test%", "KCM%", "testKCM%"])
            summary = cur.fetchone() or {}
            
            return {
                "trades": trades,
                "total_count": summary.get("total_count", 0),
                "total_profit": float(summary.get("total_profit", 0)),
                "time_range": f"{start_time} - {end_time}",
                "symbol": symbol
            }
    finally:
        conn.close()
