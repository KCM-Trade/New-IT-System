from __future__ import annotations

from typing import Any

import pymysql

from ..core.config import Settings


def get_open_positions_today(settings: Settings, source: str = "mt4_live") -> dict[str, Any]:
    """
    Query all open positions using the consolidated mt4_trades table.
    - sid 1: MT4 Live
    - sid 6: MT4 Live2
    - sid 5: MT5
    - Uses closeDate = '1970-01-01' for optimized index usage.
    - Automatically handles cent accounts (profit / 100).
    """

    # Map source to sid
    sid_map = {
        "mt4_live": 1,
        "mt4_live2": 6,
        "mt5": 5
    }
    sid = sid_map.get(source, 1)

    sql = """
        SELECT
          t.SYMBOL AS symbol,
          -- Use 'lots' virtual column for volume
          SUM(CASE WHEN t.CMD = 0 THEN t.lots ELSE 0 END) AS volume_buy,
          SUM(CASE WHEN t.CMD = 1 THEN t.lots ELSE 0 END) AS volume_sell,
          -- Use 'totalProfit' (Profit+Swap+Comm) and handle cent accounts
          SUM(CASE WHEN t.CMD = 0 THEN 
            (CASE WHEN t.SYMBOL LIKE '%%.kcmc' OR t.SYMBOL LIKE '%%.cent' THEN t.totalProfit / 100 ELSE t.totalProfit END)
          ELSE 0 END) AS profit_buy,
          SUM(CASE WHEN t.CMD = 1 THEN 
            (CASE WHEN t.SYMBOL LIKE '%%.kcmc' OR t.SYMBOL LIKE '%%.cent' THEN t.totalProfit / 100 ELSE t.totalProfit END)
          ELSE 0 END) AS profit_sell,
          SUM(CASE WHEN t.SYMBOL LIKE '%%.kcmc' OR t.SYMBOL LIKE '%%.cent' THEN t.totalProfit / 100 ELSE t.totalProfit END) AS profit_total
        FROM mt4_trades t
        WHERE t.sid = %(sid)s
          AND t.closeDate = '1970-01-01'
          AND t.CMD IN (0, 1)
          AND t.LOGIN NOT LIKE '7%%'
          AND NOT EXISTS (
            SELECT 1
            FROM mt4_users u
            WHERE u.LOGIN = t.LOGIN 
              AND u.sid = t.sid
              AND (
                u.NAME LIKE %(like_test)s
                OR (
                    (u.`GROUP` LIKE %(like_test)s OR u.NAME LIKE %(like_test)s)
                    AND (u.`GROUP` LIKE %(like_kcm)s OR u.`GROUP` LIKE %(like_testkcm)s)
                )
              )
          )
        GROUP BY t.SYMBOL
        ORDER BY t.SYMBOL
        """

    params = {
        "sid": sid,
        "like_test": "%test%",
        "like_kcm": "KCM%",
        "like_testkcm": "testKCM%",
    }

    try:
        conn = pymysql.connect(
            host=settings.DB_HOST,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            database=settings.FXBACK_DB_NAME,
            port=int(settings.DB_PORT),
            charset=settings.DB_CHARSET,
            cursorclass=pymysql.cursors.DictCursor,
        )

        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        return {"ok": True, "items": rows}
    except Exception as exc:
        return {"ok": False, "items": [], "error": str(exc)}



