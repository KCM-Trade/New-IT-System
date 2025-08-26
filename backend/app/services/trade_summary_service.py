from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pymysql

from ..core.config import Settings



def _compute_day_bounds(target_date: date) -> tuple[str, str, str]:
    """Compute yday_start, today_start, tomorrow_start as strings.
    Return ISO-like '%Y-%m-%d %H:%M:%S' strings.
    """

    today_start_dt = datetime.combine(target_date, datetime.min.time())
    yday_start_dt = today_start_dt - timedelta(days=1)
    tomorrow_start_dt = today_start_dt + timedelta(days=1)
    fmt = "%Y-%m-%d %H:%M:%S"
    return (
        yday_start_dt.strftime(fmt),
        today_start_dt.strftime(fmt),
        tomorrow_start_dt.strftime(fmt),
    )


def get_trade_summary(settings: Settings, target_date: date, symbol: str) -> dict[str, Any]:
    """按给定日期与品种返回分组汇总。
    - grp: 正在持仓/当日已平/昨日已平（周一“昨日”按上周五计算）
    - settlement: 过夜/当天（参考前端查询逻辑）
    - direction: buy/sell
    """

    # 当天与相邻日期边界
    yday_start, today_start, tomorrow_start = _compute_day_bounds(target_date)

    # 计算“昨日”区间：若为周一，则按上周五 00:00:00 至 周六 00:00:00
    # 否则为自然昨日 00:00:00 至 今日 00:00:00
    if target_date.weekday() == 0:  # Monday
        friday_date = target_date - timedelta(days=3)
        prev_start = f"{friday_date.strftime('%Y-%m-%d')} 00:00:00"
        prev_end = f"{(friday_date + timedelta(days=1)).strftime('%Y-%m-%d')} 00:00:00"
    else:
        prev_start = yday_start
        prev_end = today_start

    # 统一查询：
    # - 第一部分为过夜（swaps <> 0）
    # - 第二部分为当天（不区分 swaps）
    sql = (
        """
        SELECT
          core.grp AS grp,
          '过夜' AS settlement,
          core.direction AS direction,
          SUM(core.volume)/100 AS total_volume,
          SUM(core.profit) AS total_profit
        FROM (
          SELECT
            CASE
              WHEN CLOSE_TIME = '1970-01-01 00:00:00' THEN '正在持仓'
              WHEN CLOSE_TIME >= %(today_start)s AND CLOSE_TIME < %(tomorrow_start)s THEN '当日已平'
              WHEN CLOSE_TIME >= %(prev_start)s  AND CLOSE_TIME < %(prev_end)s       THEN '昨日已平'
            END AS grp,
            CASE WHEN cmd = 0 THEN 'buy' ELSE 'sell' END AS direction,
            swaps,
            volume,
            profit
          FROM mt4_live.mt4_trades
          WHERE symbol = %(symbol)s
            AND (
              CLOSE_TIME = '1970-01-01 00:00:00'
              OR (CLOSE_TIME >= %(prev_start)s AND CLOSE_TIME < %(prev_end)s)
              OR (CLOSE_TIME >= %(today_start)s AND CLOSE_TIME < %(tomorrow_start)s)
            ) AND NOT EXISTS (
              SELECT 1
              FROM mt4_live.mt4_users u
              WHERE u.LOGIN = mt4_live.mt4_trades.login
                  AND (
                  u.name LIKE %(like_test)s
                  OR (
                      (u.`GROUP` LIKE %(like_test)s OR u.name LIKE %(like_test)s)
                      AND (u.`GROUP` LIKE %(like_kcm)s OR u.`GROUP` LIKE %(like_testkcm)s)
                  )
                  )
              )
        ) core
        WHERE core.swaps <> 0
        GROUP BY core.grp, core.direction

        UNION ALL

        SELECT
          core.grp AS grp,
          '当天' AS settlement,
          core.direction AS direction,
          SUM(core.volume)/100 AS total_volume,
          SUM(core.profit) AS total_profit
        FROM (
          SELECT
            CASE
              WHEN CLOSE_TIME = '1970-01-01 00:00:00' THEN '正在持仓'
              WHEN CLOSE_TIME >= %(today_start)s AND CLOSE_TIME < %(tomorrow_start)s THEN '当日已平'
              WHEN CLOSE_TIME >= %(prev_start)s  AND CLOSE_TIME < %(prev_end)s       THEN '昨日已平'
            END AS grp,
            CASE WHEN cmd = 0 THEN 'buy' ELSE 'sell' END AS direction,
            volume,
            profit
          FROM mt4_live.mt4_trades
          WHERE symbol = %(symbol)s
            AND (
              CLOSE_TIME = '1970-01-01 00:00:00'
              OR (CLOSE_TIME >= %(prev_start)s AND CLOSE_TIME < %(prev_end)s)
              OR (CLOSE_TIME >= %(today_start)s AND CLOSE_TIME < %(tomorrow_start)s)
            ) AND NOT EXISTS (
              SELECT 1
              FROM mt4_live.mt4_users u
              WHERE u.LOGIN = mt4_live.mt4_trades.login
                  AND (
                  u.name LIKE %(like_test)s
                  OR (
                      (u.`GROUP` LIKE %(like_test)s OR u.name LIKE %(like_test)s)
                      AND (u.`GROUP` LIKE %(like_kcm)s OR u.`GROUP` LIKE %(like_testkcm)s)
                  )
                  )
              )
        ) core
        GROUP BY core.grp, core.direction
        """
    )

    params = {
        "symbol": symbol,
        "prev_start": prev_start,
        "prev_end": prev_end,
        "today_start": today_start,
        "tomorrow_start": tomorrow_start,
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
    except Exception as exc:  # keep simple handling; refine as needed
        return {"ok": False, "items": [], "error": str(exc)}


