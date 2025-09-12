from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple
import pymysql

from ..core.config import Settings


def _connect_mysql(settings: Settings):
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
    """Resolve mt4 trades table: prefer current DB.mt4_trades; fallback to mt4_live.mt4_trades if exists."""
    with conn.cursor() as cur:
        # check current database table
        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = 'mt4_trades'
            """
        )
        if (cur.fetchone() or {}).get("cnt", 0) > 0:
            return "mt4_trades"
        # fallback schema mt4_live
        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM information_schema.tables
            WHERE table_schema = 'mt4_live' AND table_name = 'mt4_trades'
            """
        )
        if (cur.fetchone() or {}).get("cnt", 0) > 0:
            return "mt4_live.mt4_trades"
    # last resort
    return "mt4_trades"


def _build_time_where(start: Optional[str], end: Optional[str]) -> Tuple[str, List[Any]]:
    conds: List[str] = []
    params: List[Any] = []
    if start:
        conds.append("t.CLOSE_TIME >= %s")
        params.append(start)
    if end:
        conds.append("t.CLOSE_TIME < %s")
        params.append(end)
    where = (" AND ".join(conds)) if conds else "1=1"
    return where, params


def _build_symbol_where(symbols: Optional[Sequence[str]]) -> Tuple[str, List[Any]]:
    if symbols:
        placeholders = ",".join(["%s"] * len(symbols))
        return f"AND t.SYMBOL IN ({placeholders})", list(symbols)
    return "", []


def get_trading_analysis(
    settings: Settings,
    accounts: Sequence[str],
    start: Optional[str],
    end: Optional[str],
    symbols: Optional[Sequence[str]],
    limit_top: int,
) -> Dict[str, Any]:
    if not accounts:
        return {
            "summaryByAccount": {},
            "cashDetails": [],
            "tradeDetails": [],
            "topWinners": [],
            "topLosers": [],
        }

    conn = _connect_mysql(settings)
    try:
        with conn.cursor() as cur:
            table = _resolve_table(conn)
            # summary by account
            time_where, time_params = _build_time_where(start, end)
            sym_where, sym_params = _build_symbol_where(symbols)
            acc_placeholders = ",".join(["%s"] * len(accounts))

            summary_sql = f"""
            SELECT
              t.LOGIN AS login,
              COALESCE(SUM(CASE WHEN t.CMD IN (0,1) AND t.CLOSE_TIME != '1970-01-01 00:00:00' THEN t.PROFIT ELSE 0 END), 0) AS pnl_signed,
              ABS(COALESCE(SUM(CASE WHEN t.CMD IN (0,1) AND t.CLOSE_TIME != '1970-01-01 00:00:00' THEN t.PROFIT ELSE 0 END), 0)) AS pnl_net_abs,
              COALESCE(SUM(CASE WHEN t.CMD IN (0,1) AND t.CLOSE_TIME != '1970-01-01 00:00:00' THEN ABS(t.PROFIT) ELSE 0 END), 0) AS pnl_magnitude,
              COALESCE(SUM(CASE WHEN t.CMD IN (0,1) AND t.CLOSE_TIME != '1970-01-01 00:00:00' THEN 1 ELSE 0 END), 0) AS total_orders,
              COALESCE(SUM(CASE WHEN t.CMD = 0 AND t.CLOSE_TIME != '1970-01-01 00:00:00' THEN 1 ELSE 0 END), 0) AS buy_orders,
              COALESCE(SUM(CASE WHEN t.CMD = 1 AND t.CLOSE_TIME != '1970-01-01 00:00:00' THEN 1 ELSE 0 END), 0) AS sell_orders,
              COALESCE(SUM(CASE WHEN t.CMD IN (0,1) AND t.CLOSE_TIME != '1970-01-01 00:00:00' AND t.PROFIT > 0 THEN t.PROFIT ELSE 0 END), 0) AS win_profit_sum,
              COALESCE(SUM(CASE WHEN t.CMD IN (0,1) AND t.CLOSE_TIME != '1970-01-01 00:00:00' AND t.PROFIT < 0 THEN t.PROFIT ELSE 0 END), 0) AS loss_profit_sum,
              COALESCE(SUM(CASE WHEN t.CMD IN (0,1) AND t.CLOSE_TIME != '1970-01-01 00:00:00' AND t.PROFIT < 0 THEN -t.PROFIT ELSE 0 END), 0) AS loss_profit_abs_sum,
              COALESCE(SUM(CASE WHEN t.CMD IN (0,1) AND t.CLOSE_TIME != '1970-01-01 00:00:00' AND t.PROFIT > 0 THEN 1 ELSE 0 END), 0) AS win_trade_count,
              COALESCE(SUM(CASE WHEN t.CMD IN (0,1) AND t.CLOSE_TIME != '1970-01-01 00:00:00' AND t.PROFIT < 0 THEN 1 ELSE 0 END), 0) AS loss_trade_count,
              COALESCE(SUM(CASE WHEN t.CMD = 0 AND t.CLOSE_TIME != '1970-01-01 00:00:00' AND t.PROFIT > 0 THEN 1 ELSE 0 END), 0) AS win_buy_count,
              COALESCE(SUM(CASE WHEN t.CMD = 1 AND t.CLOSE_TIME != '1970-01-01 00:00:00' AND t.PROFIT > 0 THEN 1 ELSE 0 END), 0) AS win_sell_count,
              COALESCE(SUM(CASE WHEN t.CMD = 0 AND t.CLOSE_TIME != '1970-01-01 00:00:00' AND t.PROFIT < 0 THEN 1 ELSE 0 END), 0) AS loss_buy_count,
              COALESCE(SUM(CASE WHEN t.CMD = 1 AND t.CLOSE_TIME != '1970-01-01 00:00:00' AND t.PROFIT < 0 THEN 1 ELSE 0 END), 0) AS loss_sell_count,
              COALESCE(SUM(CASE WHEN t.CMD IN (0,1) AND t.CLOSE_TIME != '1970-01-01 00:00:00' THEN t.SWAPS ELSE 0 END), 0) AS swaps_sum,
              COALESCE(SUM(CASE WHEN t.CMD = 0 AND t.CLOSE_TIME != '1970-01-01 00:00:00' THEN t.SWAPS ELSE 0 END), 0) AS buy_swaps_sum,
              COALESCE(SUM(CASE WHEN t.CMD = 1 AND t.CLOSE_TIME != '1970-01-01 00:00:00' THEN t.SWAPS ELSE 0 END), 0) AS sell_swaps_sum,
              COALESCE(SUM(CASE WHEN t.CMD = 6 AND t.PROFIT > 0 THEN 1 ELSE 0 END), 0) AS deposit_count,
              COALESCE(SUM(CASE WHEN t.CMD = 6 AND t.PROFIT > 0 THEN t.PROFIT ELSE 0 END), 0) AS deposit_amount,
              COALESCE(SUM(CASE WHEN t.CMD = 6 AND t.PROFIT < 0 THEN 1 ELSE 0 END), 0) AS withdrawal_count,
              COALESCE(SUM(CASE WHEN t.CMD = 6 AND t.PROFIT < 0 THEN -t.PROFIT ELSE 0 END), 0) AS withdrawal_amount,
              COALESCE(SUM(CASE WHEN t.CMD = 6 THEN t.PROFIT ELSE 0 END), 0) AS cash_diff
            FROM {table} t
            WHERE t.LOGIN IN ({acc_placeholders})
              AND ({time_where})
              {sym_where}
            GROUP BY t.LOGIN
            """
            cur.execute(
                summary_sql,
                [*accounts, *time_params, *sym_params],
            )
            rows = cur.fetchall()
            summary: Dict[str, Any] = {str(r["login"]): r for r in rows}

            # cash details (CMD=6)
            cash_sql = f"""
            SELECT CAST(t.LOGIN AS CHAR) AS login, t.TICKET AS ticket,
                   DATE_FORMAT(t.CLOSE_TIME, '%%Y-%%m-%%d %%H:%%i:%%s') AS close_time,
                   t.PROFIT AS amount_signed,
                   ABS(t.PROFIT) AS amount_abs,
                   CASE WHEN t.PROFIT > 0 THEN 'deposit' WHEN t.PROFIT < 0 THEN 'withdrawal' END AS cash_type,
                   t.COMMENT
            FROM {table} t
            WHERE t.LOGIN IN ({acc_placeholders})
              AND t.CMD = 6 AND t.PROFIT <> 0
              AND ({time_where})
              {sym_where}
            ORDER BY t.CLOSE_TIME DESC
            """
            cur.execute(cash_sql, [*accounts, *time_params, *sym_params])
            cash_details = cur.fetchall()

            # trade details (CMD in 0,1) - 限制前10条
            trade_sql = f"""
            SELECT CAST(t.LOGIN AS CHAR) AS login, t.TICKET AS ticket, t.SYMBOL AS symbol,
                   CASE WHEN t.CMD=0 THEN 'buy' ELSE 'sell' END AS side,
                   t.VOLUME/100.0 AS lots,
                   DATE_FORMAT(t.OPEN_TIME, '%%Y-%%m-%%d %%H:%%i:%%s') AS open_time,
                   DATE_FORMAT(t.CLOSE_TIME, '%%Y-%%m-%%d %%H:%%i:%%s') AS close_time,
                   t.OPEN_PRICE AS open_price, t.CLOSE_PRICE AS close_price,
                   t.PROFIT AS profit, t.SWAPS AS swaps
            FROM {table} t
            WHERE t.LOGIN IN ({acc_placeholders})
              AND t.CMD IN (0,1)
              AND t.CLOSE_TIME != '1970-01-01 00:00:00'
              AND ({time_where})
              {sym_where}
            ORDER BY t.CLOSE_TIME DESC
            LIMIT 10
            """
            cur.execute(trade_sql, [*accounts, *time_params, *sym_params])
            trade_details = cur.fetchall()

            # top winners/losers (limit per overall filter)
            top_win_sql = f"""
            SELECT CAST(t.LOGIN AS CHAR) AS login, t.TICKET AS ticket, t.SYMBOL AS symbol,
                   CASE WHEN t.CMD=0 THEN 'buy' ELSE 'sell' END AS side,
                   t.VOLUME/100.0 AS lots,
                   DATE_FORMAT(t.OPEN_TIME, '%%Y-%%m-%%d %%H:%%i:%%s') AS open_time,
                   DATE_FORMAT(t.CLOSE_TIME, '%%Y-%%m-%%d %%H:%%i:%%s') AS close_time,
                   t.OPEN_PRICE AS open_price, t.CLOSE_PRICE AS close_price,
                   t.PROFIT AS profit, t.SWAPS AS swaps
            FROM {table} t
            WHERE t.LOGIN IN ({acc_placeholders})
              AND t.CMD IN (0,1)
              AND t.CLOSE_TIME != '1970-01-01 00:00:00'
              AND ({time_where})
              {sym_where}
            ORDER BY t.PROFIT DESC
            LIMIT %s
            """
            cur.execute(top_win_sql, [*accounts, *time_params, *sym_params, limit_top])
            top_winners = cur.fetchall()

            top_lose_sql = f"""
            SELECT CAST(t.LOGIN AS CHAR) AS login, t.TICKET AS ticket, t.SYMBOL AS symbol,
                   CASE WHEN t.CMD=0 THEN 'buy' ELSE 'sell' END AS side,
                   t.VOLUME/100.0 AS lots,
                   DATE_FORMAT(t.OPEN_TIME, '%%Y-%%m-%%d %%H:%%i:%%s') AS open_time,
                   DATE_FORMAT(t.CLOSE_TIME, '%%Y-%%m-%%d %%H:%%i:%%s') AS close_time,
                   t.OPEN_PRICE AS open_price, t.CLOSE_PRICE AS close_price,
                   t.PROFIT AS profit, t.SWAPS AS swaps
            FROM {table} t
            WHERE t.LOGIN IN ({acc_placeholders})
              AND t.CMD IN (0,1)
              AND t.CLOSE_TIME != '1970-01-01 00:00:00'
              AND ({time_where})
              {sym_where}
            ORDER BY t.PROFIT ASC
            LIMIT %s
            """
            cur.execute(top_lose_sql, [*accounts, *time_params, *sym_params, limit_top])
            top_losers = cur.fetchall()

        return {
            "summaryByAccount": summary,
            "cashDetails": cash_details,
            "tradeDetails": trade_details,
            "topWinners": top_winners,
            "topLosers": top_losers,
        }
    finally:
        conn.close()


