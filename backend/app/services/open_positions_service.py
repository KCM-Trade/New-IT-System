from __future__ import annotations

from typing import Any

import pymysql

from ..core.config import Settings


def get_open_positions_today(settings: Settings, source: str = "mt4_live") -> dict[str, Any]:
    """
    Query all open positions (CLOSE_TIME = '1970-01-01 00:00:00') for today.
    Aggregate per symbol with separate buy/sell volume and profit, and total profit.
    Exclude test accounts according to business rules seen elsewhere.
    """

    # Choose source schema/table
    schema = "mt4_live2" if source == "mt4_live2" else "mt4_live"

    sql = f"""
        SELECT
          t.symbol AS symbol,
          SUM(CASE WHEN t.cmd = 0 THEN t.volume / POW(10, t.DIGITS) ELSE 0 END) AS volume_buy,
          SUM(CASE WHEN t.cmd = 1 THEN t.volume / POW(10, t.DIGITS) ELSE 0 END) AS volume_sell,
          SUM(CASE WHEN t.cmd = 0 THEN t.profit ELSE 0 END)      AS profit_buy,
          SUM(CASE WHEN t.cmd = 1 THEN t.profit ELSE 0 END)      AS profit_sell,
          SUM(t.profit)                                          AS profit_total
        FROM {schema}.mt4_trades t
        WHERE t.CLOSE_TIME = '1970-01-01 00:00:00'
          AND t.cmd IN (0,1)
          AND t.login NOT LIKE '7%%'
          AND NOT EXISTS (
            SELECT 1
            FROM {schema}.mt4_users u
            WHERE u.LOGIN = t.login
              AND (
                u.name LIKE %(like_test)s
                OR (
                    (u.`GROUP` LIKE %(like_test)s OR u.name LIKE %(like_test)s)
                    AND (u.`GROUP` LIKE %(like_kcm)s OR u.`GROUP` LIKE %(like_testkcm)s)
                )
              )
          )
        GROUP BY t.symbol
        ORDER BY t.symbol
        """

    params = {
        "like_test": "%test%",
        "like_kcm": "KCM%",
        "like_testkcm": "testKCM%",
    }

    try:
        conn = pymysql.connect(
            host=settings.DB_HOST,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            database=settings.DB_NAME,
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



