from __future__ import annotations

from datetime import datetime
from io import StringIO
from typing import Any

import csv
import pymysql

from ..core.config import Settings


def query_downloads(settings: Settings, req: Any) -> dict[str, Any]:
    """简化版：不分页，按 OPEN_TIME DESC 返回所有命中记录。
    参数：symbols, start_date, end_date, source
    """
    schema = "mt4_live2" if getattr(req, "source", "mt4_live") == "mt4_live2" else "mt4_live"
    params: dict[str, Any] = {
        "symbols": tuple(req.symbols) if req.symbols else tuple(["XAU-CNH"]),
        "start_dt": f"{req.start_date} 00:00:00",
        "end_dt": f"{req.end_date} 23:59:59",
        "like_test": "%test%",
        "like_kcm": "KCM%",
        "like_testkcm": "testKCM%",
    }

    sql = f"""
        SELECT
          t.TICKET AS ticket,
          t.login AS account_id,
          CAST(NULLIF(u2.id, '') AS SIGNED) AS client_id,
          t.symbol AS symbol,
          t.volume / POW(10, t.DIGITS) AS volume,
          t.OPEN_TIME AS open_time,
          t.CLOSE_TIME AS close_time,
          t.MODIFY_TIME AS modify_time,
          t.PROFIT AS profit,
          t.CMD AS cmd,
          t.OPEN_PRICE AS open_price,
          t.CLOSE_PRICE AS close_price,
          t.SWAPS AS swaps,
          t.COMMENT AS comment,
          t.SL AS sl,
          t.TP AS tp,
          GROUP_CONCAT(DISTINCT ib.ibid SEPARATOR ', ') AS ibid
        FROM {schema}.mt4_trades t
        LEFT JOIN {schema}.mt4_users u2 ON t.login = u2.LOGIN
        LEFT JOIN fxbackoffice.ib_tree ib ON u2.id = ib.referralId
        WHERE t.SYMBOL IN %(symbols)s
          AND (
            (t.OPEN_TIME BETWEEN %(start_dt)s AND %(end_dt)s)
            OR (t.CLOSE_TIME BETWEEN %(start_dt)s AND %(end_dt)s)
          )
          AND t.cmd IN (0,1)
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
        GROUP BY t.TICKET, t.login, t.symbol, t.volume, t.DIGITS, t.OPEN_TIME, t.CLOSE_TIME, t.MODIFY_TIME, t.PROFIT, t.CMD, t.OPEN_PRICE, t.CLOSE_PRICE, t.SWAPS, t.COMMENT, t.SL, t.TP
        ORDER BY t.OPEN_TIME DESC
    """

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
                items = cur.fetchall()
        return {"ok": True, "items": items}
    except Exception as exc:
        return {"ok": False, "items": [], "error": str(exc)}


def export_downloads_csv(settings: Settings, req: Any) -> dict[str, Any]:
    """返回 CSV 文本（由路由设置 text/csv 下载），不落盘。简化为不分页导出。
    """
    schema = "mt4_live2" if getattr(req, "source", "mt4_live") == "mt4_live2" else "mt4_live"
    params: dict[str, Any] = {
        "symbols": tuple(req.symbols) if req.symbols else tuple(["XAU-CNH"]),
        "start_dt": f"{req.start_date} 00:00:00",
        "end_dt": f"{req.end_date} 23:59:59",
        "like_test": "%test%",
        "like_kcm": "KCM%",
        "like_testkcm": "testKCM%",
    }

    sql = f"""
        SELECT
          t.TICKET AS ticket,
          t.login AS account_id,
          CAST(NULLIF(u2.id, '') AS SIGNED) AS client_id,
          t.symbol AS symbol,
          t.volume / POW(10, t.DIGITS) AS volume,
          t.OPEN_TIME AS open_time,
          t.CLOSE_TIME AS close_time,
          t.MODIFY_TIME AS modify_time,
          t.PROFIT AS profit,
          t.CMD AS cmd,
          t.OPEN_PRICE AS open_price,
          t.CLOSE_PRICE AS close_price,
          t.SWAPS AS swaps,
          t.COMMENT AS comment,
          t.SL AS sl,
          t.TP AS tp,
          GROUP_CONCAT(DISTINCT ib.ibid SEPARATOR ', ') AS ibid
        FROM {schema}.mt4_trades t
        LEFT JOIN {schema}.mt4_users u2 ON t.login = u2.LOGIN
        LEFT JOIN fxbackoffice.ib_tree ib ON u2.id = ib.referralId
        WHERE t.SYMBOL IN %(symbols)s
          AND (
            (t.OPEN_TIME BETWEEN %(start_dt)s AND %(end_dt)s)
            OR (t.CLOSE_TIME BETWEEN %(start_dt)s AND %(end_dt)s)
          )
          AND t.cmd IN (0,1)
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
        GROUP BY t.TICKET, t.login, t.symbol, t.volume, t.DIGITS, t.OPEN_TIME, t.CLOSE_TIME, t.MODIFY_TIME, t.PROFIT, t.CMD, t.OPEN_PRICE, t.CLOSE_PRICE, t.SWAPS, t.COMMENT, t.SL, t.TP
        ORDER BY t.OPEN_TIME DESC
    """

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

        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["ticket", "account_id", "client_id", "symbol", "volume", "open_time", "close_time", "profit", "cmd", "open_price", "ibid"])
        for r in rows:
            writer.writerow([
                r.get("ticket"), r.get("account_id"), r.get("client_id"), r.get("symbol"),
                r.get("volume"), r.get("open_time"), r.get("close_time"), r.get("profit"),
                r.get("cmd"), r.get("open_price"), r.get("ibid")
            ])
        csv_text = output.getvalue()
        output.close()

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"downloads_{ts}.csv"
        return {"ok": True, "filename": fname, "content": csv_text}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


