from __future__ import annotations

from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime as _dt
import math
import os

import psycopg2
from psycopg2.extras import RealDictCursor
import mysql.connector


def _pg_mt5_dsn() -> str:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    db = os.getenv("POSTGRES_DBNAME_MT5")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "")
    return f"host={host} port={port} dbname={db} user={user} password={password}"


def resolve_table_and_dataset(server: str) -> Tuple[str, str]:
    """Map server to source table and watermark dataset.

    - MT5       -> public.pnl_user_summary,          dataset='pnl_user_summary'
    - MT4Live2  -> public.pnl_user_summary_mt4live2, dataset='pnl_user_summary_mt4live2'
    - default   -> MT5 mapping
    """
    srv = (server or "").upper()
    if srv == "MT5":
        return "public.pnl_user_summary", "pnl_user_summary"
    if srv == "MT4LIVE2":
        return "public.pnl_user_summary_mt4live2", "pnl_user_summary_mt4live2"
    raise ValueError(f"Unsupported server: {server}")


def get_pnl_user_summary_paginated(
    page: int = 1,
    page_size: int = 100,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    user_groups: Optional[List[str]] = None,
    search: Optional[str] = None,
    source_table: str = "public.pnl_user_summary",
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
        "withdrawal_amount", "net_deposit", "overnight_volume_ratio", "last_updated",
        # 新增：平仓总盈亏（买+卖）
        "closed_total_profit",
        # 计算列（前端聚合列）的排序别名
        "overnight_volume_all", "total_volume_all", "overnight_order_all", "total_order_all",
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
                if "__EXCLUDE_GROUP_NAME_TEST__" in cleaned:
                    where_conditions.append("user_group NOT ILIKE %s")
                    params.append("%test%")
            elif "__NONE__" in cleaned:
                # 显式要求返回 0 行
                where_conditions.append("1 = 0")
            else:
                # 分离常规组别与特殊筛选
                regular_groups = [g for g in cleaned if g not in ["__USER_NAME_TEST__", "__EXCLUDE_USER_NAME_TEST__", "__EXCLUDE_GROUP_NAME_TEST__"]]
                has_user_name_test = "__USER_NAME_TEST__" in cleaned
                has_exclude_user_name_test = "__EXCLUDE_USER_NAME_TEST__" in cleaned
                has_exclude_group_name_test = "__EXCLUDE_GROUP_NAME_TEST__" in cleaned

                group_conditions: List[str] = []

                # 支持 'manager' 虚拟组别：匹配以 'managers\' 开头的真实组别
                include_manager = any(g.lower() == 'manager' for g in regular_groups)
                exact_groups = [g for g in regular_groups if g.lower() != 'manager']

                if exact_groups:
                    if len(exact_groups) == 1:
                        group_conditions.append("user_group = %s")
                        params.append(exact_groups[0])
                    else:
                        placeholders = ",".join(["%s"] * len(exact_groups))
                        group_conditions.append(f"user_group IN ({placeholders})")
                        params.extend(exact_groups)

                if include_manager:
                    # ILIKE 前缀匹配 'managers\%'; 指定 ESCAPE 字符避免 \
                    # 对 % 产生转义，确保 % 作为通配符生效
                    group_conditions.append("user_group ILIKE %s ESCAPE '|'")
                    params.append("managers\\%")

                if has_user_name_test:
                    group_conditions.append("user_name ILIKE %s")
                    params.append("%test%")

                if group_conditions:
                    combined = "(" + " OR ".join(group_conditions) + ")"
                    where_conditions.append(combined)

                if has_exclude_user_name_test:
                    where_conditions.append("user_name NOT ILIKE %s")
                    params.append("%test%")
                if has_exclude_group_name_test:
                    where_conditions.append("user_group NOT ILIKE %s")
                    params.append("%test%")

    # 统一搜索（login/user_id 精确 或 user_name 模糊）
    if search is not None:
        s = str(search).strip()
        if s:
            sub = []
            try:
                login_int = int(s)
                sub.append("login = %s")
                params.append(login_int)
                # 数值输入时，额外匹配 user_id 精确等于
                sub.append("user_id = %s")
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
        # 兼容性：即使数据库未添加生成列，也计算一个别名
        "(COALESCE(closed_buy_profit,0) + COALESCE(closed_sell_profit,0)) AS closed_total_profit, "
        "overnight_volume_ratio, last_updated "
        f"FROM {source_table}" + where_clause
    )

    # 排序
    order_clause = ""
    # 计算列映射到 SQL 可排序表达式（只使用白名单内的安全表达式）
    alias_sort_expressions = {
        "overnight_volume_all": "(closed_buy_overnight_volume_lots + closed_sell_overnight_volume_lots)",
        "total_volume_all": "(closed_buy_volume_lots + closed_sell_volume_lots)",
        "overnight_order_all": "(closed_buy_overnight_count + closed_sell_overnight_count)",
        "total_order_all": "(closed_buy_count + closed_sell_count)",
        # 新增：平仓总盈亏（表达式排序，兼容未添加生成列的数据库）
        "closed_total_profit": "(COALESCE(closed_buy_profit,0) + COALESCE(closed_sell_profit,0))",
    }

    if sort_by and sort_by in allowed_sort_fields:
        direction = "DESC" if sort_order.lower() == "desc" else "ASC"
        sort_expression = alias_sort_expressions.get(sort_by, sort_by)
        # 加入 login 作为二级排序，保证稳定分页顺序；对可能为 NULL 的字段添加 NULLS LAST
        order_clause = f" ORDER BY {sort_expression} {direction} NULLS LAST, login ASC"
    else:
        order_clause = " ORDER BY login ASC"

    offset = (page - 1) * page_size
    paginated_sql = base_select + order_clause + " LIMIT %s OFFSET %s"

    count_sql = f"SELECT COUNT(*) FROM {source_table}" + where_clause

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



def get_etl_watermark_last_updated(dataset: str = "pnl_user_summary") -> Optional[_dt]:
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


def get_user_groups_from_user_summary(source_table: str = "public.pnl_user_summary") -> List[str]:
    """从 public.pnl_user_summary 去重获取组别，并将以 'managers\' 开头的归并为 'manager'。

    Returns:
        List[str]: 归一化后的组别列表，按字母排序（不区分大小写）。
    """
    dsn = _pg_mt5_dsn()
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT user_group
                FROM {source_table}
                WHERE user_group IS NOT NULL
                  AND TRIM(user_group) != ''
                """
            )
            rows = cur.fetchall()

    normalized: List[str] = []
    for r in rows:
        g = str(r[0]).strip()
        if g.lower().startswith('managers\\'):
            normalized.append('manager')
        else:
            normalized.append(g)

    # 去重并按不区分大小写排序
    dedup = sorted(set(normalized), key=lambda s: s.lower())
    return dedup


# ------------------- Incremental refresh (MT5) -------------------

def _pg_mt5_dsn_forced_db(dbname: str) -> str:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "")
    return f"host={host} port={port} dbname={dbname} user={user} password={password}"


def _mysql_mt5_cfg() -> Dict[str, Any]:
    return {
        "host": os.getenv("MYSQL_HOST"),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_DATABASE"),
        "ssl_ca": os.getenv("MYSQL_SSL_CA"),
    }


def mt5_incremental_refresh() -> Dict[str, Any]:
    """Run incremental ETL per provided reference design.

    - Force Postgres dbname = MT5_ETL
    - Use advisory lock to prevent concurrent runs
    - Ensure etl_watermarks, read last_deal_id
    - Insert new logins; aggregate deals deltas; upsert; update floating pnl; update watermarks
    - Return metrics for UI display
    """
    pg_dsn = _pg_mt5_dsn_forced_db("MT5_ETL")
    mysql_cfg = _mysql_mt5_cfg()

    result: Dict[str, Any] = {
        "success": False,
        "processed_rows": 0,
        "duration_seconds": 0.0,
        "new_max_deal_id": None,
        "new_trades_count": 0,
        "floating_only_count": 0,
        "message": None,
    }

    import time
    start_ts = time.time()

    # Basic env validation
    if not all([mysql_cfg.get("host"), mysql_cfg.get("user"), mysql_cfg.get("password"), mysql_cfg.get("database")]):
        raise RuntimeError("Missing required MySQL env vars for MT5 incremental refresh")

    with psycopg2.connect(pg_dsn) as pg_conn:
        pg_conn.autocommit = False
        with pg_conn.cursor() as cur:
            # advisory lock
            cur.execute("SELECT pg_try_advisory_lock(%s)", (937_000_001,))
            locked = bool(cur.fetchone()[0])
            pg_conn.commit()
        if not locked:
            # another run is in progress
            result["success"] = True
            result["message"] = "Another incremental run is in progress"
            return result

        try:
            # ensure watermarks table
            with pg_conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS public.etl_watermarks (
                      dataset       text        NOT NULL,
                      partition_key text        NOT NULL DEFAULT 'ALL',
                      last_deal_id  bigint,
                      last_time     timestamptz,
                      last_login    bigint,
                      last_updated  timestamptz NOT NULL DEFAULT now(),
                      CONSTRAINT pk_etl_watermarks PRIMARY KEY (dataset, partition_key)
                    );
                    """
                )
                pg_conn.commit()

            # read last_deal_id
            last_deal_id: int = 0
            with pg_conn.cursor() as cur:
                cur.execute(
                    "SELECT last_deal_id FROM public.etl_watermarks WHERE dataset=%s AND partition_key=%s",
                    ("pnl_user_summary", "ALL"),
                )
                row = cur.fetchone()
                if row and row[0] is not None:
                    last_deal_id = int(row[0])
                else:
                    cur.execute(
                        "INSERT INTO public.etl_watermarks (dataset, partition_key, last_deal_id, last_updated) VALUES (%s,%s,%s, now()) ON CONFLICT DO NOTHING",
                        ("pnl_user_summary", "ALL", 0),
                    )
                    pg_conn.commit()
                    last_deal_id = 0

            # connect to MySQL
            mysql_conn = mysql.connector.connect(**mysql_cfg)
            try:
                # Step 0: insert new logins skeleton rows
                to_insert = []
                with mysql_conn.cursor(dictionary=True) as cur:
                    cur.execute("SELECT u.Login, u.Name, u.Country, u.ZipCode, u.ID, u.Balance, u.Credit FROM mt5_live.mt5_users u")
                    for r in cur.fetchall():
                        to_insert.append((
                            int(r['Login']), 'ALL', r.get('Name'), r.get('Country'), r.get('ZipCode'), r.get('ID') or None,
                            r.get('Balance') or 0, r.get('Credit') or 0, 0,
                        ))
                if to_insert:
                    from psycopg2.extras import execute_values
                    with pg_conn.cursor() as cur:
                        execute_values(
                            cur,
                            "INSERT INTO public.pnl_user_summary (login, symbol, user_name, country, zipcode, user_id, user_balance, user_credit, positions_floating_pnl) VALUES %s "
                            "ON CONFLICT (login, symbol) DO UPDATE SET "
                            "  user_name=EXCLUDED.user_name, country=EXCLUDED.country, zipcode=EXCLUDED.zipcode, user_id=EXCLUDED.user_id, "
                            "  user_balance=EXCLUDED.user_balance, user_credit=EXCLUDED.user_credit",
                            to_insert,
                            page_size=5000,
                        )

                # Step 1: aggregate deltas since last_deal_id
                sql = """
                WITH
                deals_window AS (
                  SELECT * FROM mt5_live.mt5_deals WHERE Deal > %s
                ),
                closed_deals AS (
                  SELECT
                    d.Login,
                    d.Action,
                    d.Entry,
                    d.Time AS close_time,
                    d.VolumeClosed AS volume_closed,
                    d.Profit,
                    d.Storage,
                    (
                      SELECT MIN(d2.Time)
                      FROM mt5_live.mt5_deals d2
                      WHERE d2.Login = d.Login AND d2.PositionID = d.PositionID AND d2.Entry = 0 AND d2.Time <= d.Time
                    ) AS open_time_for_close
                  FROM deals_window d
                  WHERE d.Entry IN (1, 3)
                ),
                closed_agg AS (
                  SELECT
                    Login,
                    CASE WHEN Action = 0 THEN 'BUY' WHEN Action = 1 THEN 'SELL' ELSE 'OTHER' END AS side,
                    SUM(volume_closed) / 10000.0 AS volume_lots,
                    COUNT(*) AS trade_count,
                    SUM(Profit) AS profit_sum,
                    SUM(Storage) AS swap_sum,
                    SUM(CASE WHEN DATE(close_time) <> DATE(open_time_for_close) THEN 1 ELSE 0 END) AS overnight_count,
                    SUM(CASE WHEN DATE(close_time) <> DATE(open_time_for_close) THEN volume_closed/10000.0 ELSE 0 END) AS overnight_volume_lots
                  FROM closed_deals GROUP BY Login, side
                ),
                commission_agg AS (
                  SELECT Login, SUM(Commission) AS total_commission
                  FROM deals_window
                  WHERE Entry IN (0, 2) AND Action IN (0, 1)
                  GROUP BY Login
                ),
                balance_agg AS (
                  SELECT
                    Login,
                    SUM(CASE WHEN Action = 2 AND Profit > 0 THEN 1 ELSE 0 END) AS deposit_count,
                    SUM(CASE WHEN Action = 2 AND Profit > 0 THEN Profit ELSE 0 END) AS deposit_amount,
                    SUM(CASE WHEN Action = 2 AND Profit < 0 THEN 1 ELSE 0 END) AS withdrawal_count,
                    SUM(CASE WHEN Action = 2 AND Profit < 0 THEN -Profit ELSE 0 END) AS withdrawal_amount
                  FROM deals_window GROUP BY Login
                ),
                max_deal AS (
                  SELECT COALESCE(MAX(Deal), %s) AS mx FROM deals_window
                )
                SELECT
                  u.Login,
                  u.Name          AS user_name,
                  u.Country,
                  u.ZipCode       AS zipcode,
                  u.ID            AS user_id,
                  u.Balance       AS user_balance,
                  u.Credit        AS user_credit,

                  COALESCE(MAX(CASE WHEN ca.side='SELL' THEN ca.volume_lots END), 0) AS closed_sell_volume_lots,
                  COALESCE(MAX(CASE WHEN ca.side='SELL' THEN ca.trade_count END), 0) AS closed_sell_count,
                  COALESCE(MAX(CASE WHEN ca.side='SELL' THEN ca.profit_sum END), 0) AS closed_sell_profit,
                  COALESCE(MAX(CASE WHEN ca.side='SELL' THEN ca.swap_sum END), 0) AS closed_sell_swap,
                  COALESCE(MAX(CASE WHEN ca.side='SELL' THEN ca.overnight_count END), 0) AS closed_sell_overnight_count,
                  COALESCE(MAX(CASE WHEN ca.side='SELL' THEN ca.overnight_volume_lots END), 0) AS closed_sell_overnight_volume_lots,

                  COALESCE(MAX(CASE WHEN ca.side='BUY' THEN ca.volume_lots END), 0) AS closed_buy_volume_lots,
                  COALESCE(MAX(CASE WHEN ca.side='BUY' THEN ca.trade_count END), 0) AS closed_buy_count,
                  COALESCE(MAX(CASE WHEN ca.side='BUY' THEN ca.profit_sum END), 0) AS closed_buy_profit,
                  COALESCE(MAX(CASE WHEN ca.side='BUY' THEN ca.swap_sum END), 0) AS closed_buy_swap,
                  COALESCE(MAX(CASE WHEN ca.side='BUY' THEN ca.overnight_count END), 0) AS closed_buy_overnight_count,
                  COALESCE(MAX(CASE WHEN ca.side='BUY' THEN ca.overnight_volume_lots END), 0) AS closed_buy_overnight_volume_lots,

                  COALESCE(cm.total_commission, 0) AS total_commission,
                  COALESCE(b.deposit_count, 0)     AS deposit_count,
                  COALESCE(b.deposit_amount, 0)    AS deposit_amount,
                  COALESCE(b.withdrawal_count, 0)  AS withdrawal_count,
                  COALESCE(b.withdrawal_amount, 0) AS withdrawal_amount,
                  (SELECT mx FROM max_deal) AS max_deal_id
                FROM mt5_live.mt5_users u
                LEFT JOIN closed_agg ca ON ca.Login = u.Login
                LEFT JOIN commission_agg cm ON cm.Login = u.Login
                LEFT JOIN balance_agg b ON b.Login = u.Login
                WHERE u.Login IN (
                  SELECT DISTINCT Login FROM deals_window
                )
                GROUP BY u.Login, u.Name, u.Country, u.ZipCode, u.ID, u.Balance, u.Credit, cm.total_commission, b.deposit_count, b.deposit_amount, b.withdrawal_count, b.withdrawal_amount
                """

                rows: List[Tuple] = []
                max_deal_id = last_deal_id
                new_trades_count = 0
                with mysql_conn.cursor(dictionary=True) as cur:
                    cur.execute(sql, (last_deal_id, last_deal_id))
                    fetched = cur.fetchall()
                    new_trades_count = len(fetched)
                    for r in fetched:
                        max_deal_id = max(max_deal_id, int(r['max_deal_id'] or last_deal_id))
                        rows.append((
                            int(r['Login']), 'ALL',
                            r.get('user_name'), r.get('Country'), r.get('ZipCode'), r.get('user_id') or None,
                            r.get('user_balance') or 0, r.get('user_credit') or 0,
                            r.get('closed_sell_volume_lots') or 0, r.get('closed_sell_count') or 0, r.get('closed_sell_profit') or 0, r.get('closed_sell_swap') or 0, r.get('closed_sell_overnight_count') or 0, r.get('closed_sell_overnight_volume_lots') or 0,
                            r.get('closed_buy_volume_lots') or 0, r.get('closed_buy_count') or 0, r.get('closed_buy_profit') or 0, r.get('closed_buy_swap') or 0, r.get('closed_buy_overnight_count') or 0, r.get('closed_buy_overnight_volume_lots') or 0,
                            r.get('total_commission') or 0,
                            r.get('deposit_count') or 0, r.get('deposit_amount') or 0, r.get('withdrawal_count') or 0, r.get('withdrawal_amount') or 0
                        ))

                affected = 0
                if rows:
                    from psycopg2.extras import execute_values
                    upsert_sql = """
                    INSERT INTO public.pnl_user_summary (
                      login, symbol,
                      user_name, country, zipcode, user_id,
                      user_balance, user_credit,
                      closed_sell_volume_lots, closed_sell_count, closed_sell_profit, closed_sell_swap, closed_sell_overnight_count, closed_sell_overnight_volume_lots,
                      closed_buy_volume_lots,  closed_buy_count,  closed_buy_profit,  closed_buy_swap,  closed_buy_overnight_count,  closed_buy_overnight_volume_lots,
                      total_commission,
                      deposit_count, deposit_amount, withdrawal_count, withdrawal_amount
                    ) VALUES %s
                    ON CONFLICT (login, symbol) DO UPDATE SET
                      user_name = EXCLUDED.user_name,
                      country = EXCLUDED.country,
                      zipcode = EXCLUDED.zipcode,
                      user_id = EXCLUDED.user_id,
                      user_balance = EXCLUDED.user_balance,
                      user_credit = EXCLUDED.user_credit,

                      closed_sell_volume_lots = public.pnl_user_summary.closed_sell_volume_lots + EXCLUDED.closed_sell_volume_lots,
                      closed_sell_count       = public.pnl_user_summary.closed_sell_count       + EXCLUDED.closed_sell_count,
                      closed_sell_profit      = public.pnl_user_summary.closed_sell_profit      + EXCLUDED.closed_sell_profit,
                      closed_sell_swap        = public.pnl_user_summary.closed_sell_swap        + EXCLUDED.closed_sell_swap,
                      closed_sell_overnight_count = public.pnl_user_summary.closed_sell_overnight_count + EXCLUDED.closed_sell_overnight_count,
                      closed_sell_overnight_volume_lots = public.pnl_user_summary.closed_sell_overnight_volume_lots + EXCLUDED.closed_sell_overnight_volume_lots,

                      closed_buy_volume_lots  = public.pnl_user_summary.closed_buy_volume_lots  + EXCLUDED.closed_buy_volume_lots,
                      closed_buy_count        = public.pnl_user_summary.closed_buy_count        + EXCLUDED.closed_buy_count,
                      closed_buy_profit       = public.pnl_user_summary.closed_buy_profit       + EXCLUDED.closed_buy_profit,
                      closed_buy_swap         = public.pnl_user_summary.closed_buy_swap         + EXCLUDED.closed_buy_swap,
                      closed_buy_overnight_count = public.pnl_user_summary.closed_buy_overnight_count + EXCLUDED.closed_buy_overnight_count,
                      closed_buy_overnight_volume_lots = public.pnl_user_summary.closed_buy_overnight_volume_lots + EXCLUDED.closed_buy_overnight_volume_lots,

                      total_commission        = public.pnl_user_summary.total_commission        + EXCLUDED.total_commission,

                      deposit_count           = public.pnl_user_summary.deposit_count           + EXCLUDED.deposit_count,
                      deposit_amount          = public.pnl_user_summary.deposit_amount          + EXCLUDED.deposit_amount,
                      withdrawal_count        = public.pnl_user_summary.withdrawal_count        + EXCLUDED.withdrawal_count,
                      withdrawal_amount       = public.pnl_user_summary.withdrawal_amount       + EXCLUDED.withdrawal_amount,

                      last_updated = now()
                    """
                    with pg_conn.cursor() as cur:
                        execute_values(cur, upsert_sql, rows, page_size=5000)
                        affected = len(rows)

                # Step 2: update floating pnl
                floating_pairs = []
                with mysql_conn.cursor() as cur:
                    cur.execute("SELECT Login, SUM(COALESCE(Profit,0) + COALESCE(Storage,0)) AS floating_pnl_total FROM mt5_live.mt5_positions GROUP BY Login")
                    for r in cur.fetchall():
                        floating_pairs.append((int(r[0]), float(r[1] or 0.0)))

                if floating_pairs:
                    from psycopg2.extras import execute_values
                    with pg_conn.cursor() as cur:
                        cur.execute("CREATE TEMP TABLE tmp_floating (login bigint, symbol text, floating_pnl numeric) ON COMMIT DROP")
                        execute_values(cur, "INSERT INTO tmp_floating (login, symbol, floating_pnl) VALUES %s", [(login, 'ALL', pnl) for login, pnl in floating_pairs], page_size=5000)
                        cur.execute(
                            "UPDATE public.pnl_user_summary s "
                            "SET positions_floating_pnl = t.floating_pnl, "
                            "    last_updated = CASE WHEN s.positions_floating_pnl IS DISTINCT FROM t.floating_pnl THEN now() ELSE s.last_updated END "
                            "FROM tmp_floating t "
                            "WHERE s.login = t.login AND s.symbol = t.symbol "
                            "  AND s.positions_floating_pnl IS DISTINCT FROM t.floating_pnl"
                        )

                        # watermark last_time for floating
                        cur.execute(
                            "INSERT INTO public.etl_watermarks (dataset, partition_key, last_time, last_updated) VALUES (%s,%s, now(), now()) "
                            "ON CONFLICT (dataset, partition_key) DO UPDATE SET last_time=now(), last_updated=now()",
                            ("pnl_user_summary", "ALL"),
                        )

                # Step 3: update watermark last_deal_id
                with pg_conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO public.etl_watermarks (dataset, partition_key, last_deal_id, last_updated) VALUES (%s,%s,%s, now()) "
                        "ON CONFLICT (dataset, partition_key) DO UPDATE SET last_deal_id=EXCLUDED.last_deal_id, last_updated=now()",
                        ("pnl_user_summary", "ALL", max_deal_id),
                    )

                pg_conn.commit()
                result["success"] = True
                result["processed_rows"] = affected
                result["new_max_deal_id"] = max_deal_id
                result["new_trades_count"] = new_trades_count
                # floating_only_count 粗略估计：positions 行数（用于展示，不要求精确）
                result["floating_only_count"] = len(floating_pairs)
            finally:
                try:
                    if mysql_conn and mysql_conn.is_connected():
                        mysql_conn.close()
                except Exception:
                    pass
        except Exception as e:
            pg_conn.rollback()
            raise
        finally:
            with pg_conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (937_000_001,))
                pg_conn.commit()

    result["duration_seconds"] = round(time.time() - start_ts, 3)
    return result


