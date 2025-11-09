#!/usr/bin/env python3
import os
import sys
import time
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# External libraries expected in project venv:
# - psycopg2 (PostgreSQL client)
# - PyMySQL (MySQL client)
import psycopg2
import psycopg2.extras
import pymysql
import dotenv

dotenv.load_dotenv()

def get_env(name: str, default: Optional[str] = None) -> str:
    """
    Read environment variables and fail fast for required ones.
    """
    value = os.getenv(name, default)
    if value is None:
        print(f"[ERROR] Missing required environment variable: {name}")
        sys.exit(1)
    return value


def connect_postgres() -> psycopg2.extensions.connection:
    """
    Create a PostgreSQL connection to MT5_ETL database.
    """
    host = get_env("POSTGRES_HOST")
    user = get_env("POSTGRES_USER")
    password = get_env("POSTGRES_PASSWORD")
    port = int(get_env("POSTGRES_PORT", "5432"))
    dbname = get_env("POSTGRES_DBNAME_MT5", "MT5_ETL")

    conn = psycopg2.connect(
        host=host,
        user=user,
        password=password,
        port=port,
        dbname=dbname,
    )
    conn.autocommit = False
    return conn


def connect_mysql(dbname: str) -> pymysql.connections.Connection:
    """
    Create a MySQL connection. The same host/user/password/port are reused.
    """
    host = get_env("MYSQL_HOST")
    user = get_env("MYSQL_USER")
    password = get_env("MYSQL_PASSWORD")
    port = int(get_env("MYSQL_PORT", "3306"))

    conn = pymysql.connect(
        host=host,
        user=user,
        password=password,
        port=port,
        database=dbname,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )
    return conn


def chunked(seq: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    """
    Yield fixed-size chunks from a sequence.
    """
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def truncate_targets(pg: psycopg2.extensions.connection) -> None:
    """
    Truncate target tables for a clean full load.
    """
    with pg.cursor() as cur:
        cur.execute("TRUNCATE TABLE public.pnl_client_accounts RESTART IDENTITY CASCADE;")
        cur.execute("TRUNCATE TABLE public.pnl_client_summary RESTART IDENTITY CASCADE;")


def build_accounts(pg: psycopg2.extensions.connection) -> None:
    """
    Build account-level rows by aggregating from source tables into public.pnl_client_accounts.
    - Currency CEN normalization: divide by 100 for amounts and lots.
    - Compute volume_lots, overnight_volume_lots and auto_swap_free_status per account.
    - Use one-shot INSERT ... ON CONFLICT ... DO UPDATE for idempotency.
    """
    sql = r"""
    WITH combined AS (
      SELECT 
        s.user_id       AS client_id,
        s.login         AS login,
        'MT5'           AS server,
        s.currency      AS currency,
        s.user_name     AS user_name,
        s.user_group    AS user_group,
        s.country       AS country,
        -- amounts normalized to USD, 4 decimals
        CASE WHEN s.currency = 'CEN' THEN ROUND(COALESCE(s.user_balance, 0) / 100.0, 4) ELSE ROUND(COALESCE(s.user_balance, 0), 4) END AS balance_usd,
        CASE WHEN s.currency = 'CEN' THEN ROUND(COALESCE(s.equity, 0) / 100.0, 4) ELSE ROUND(COALESCE(s.equity, 0), 4) END AS equity_usd,
        CASE WHEN s.currency = 'CEN' THEN ROUND(COALESCE(s.positions_floating_pnl, 0) / 100.0, 4) ELSE ROUND(COALESCE(s.positions_floating_pnl, 0), 4) END AS floating_pnl_usd,
        CASE WHEN s.currency = 'CEN' THEN ROUND(COALESCE(s.closed_total_profit_with_swap, 0) / 100.0, 4) ELSE ROUND(COALESCE(s.closed_total_profit_with_swap, 0), 4) END AS closed_profit_usd,
        CASE WHEN s.currency = 'CEN' THEN ROUND(COALESCE(s.total_commission, 0) / 100.0, 4) ELSE ROUND(COALESCE(s.total_commission, 0), 4) END AS commission_usd,
        CASE WHEN s.currency = 'CEN' THEN ROUND(COALESCE(s.deposit_amount, 0) / 100.0, 4) ELSE ROUND(COALESCE(s.deposit_amount, 0), 4) END AS deposit_usd,
        CASE WHEN s.currency = 'CEN' THEN ROUND(COALESCE(s.withdrawal_amount, 0) / 100.0, 4) ELSE ROUND(COALESCE(s.withdrawal_amount, 0), 4) END AS withdrawal_usd,
        -- volumes
        CASE WHEN s.currency = 'CEN' THEN ROUND((COALESCE(s.closed_sell_volume_lots, 0) + COALESCE(s.closed_buy_volume_lots, 0)) / 100.0, 4)
             ELSE ROUND(COALESCE(s.closed_sell_volume_lots, 0) + COALESCE(s.closed_buy_volume_lots, 0), 4)
        END AS volume_lots,
        CASE WHEN s.currency = 'CEN' THEN ROUND((COALESCE(s.closed_sell_overnight_volume_lots, 0) + COALESCE(s.closed_buy_overnight_volume_lots, 0)) / 100.0, 4)
             ELSE ROUND(COALESCE(s.closed_sell_overnight_volume_lots, 0) + COALESCE(s.closed_buy_overnight_volume_lots, 0), 4)
        END AS overnight_volume_lots,
        s.last_updated  AS last_updated
      FROM public.pnl_user_summary s
      WHERE s.user_id IS NOT NULL

      UNION ALL

      SELECT 
        s.user_id       AS client_id,
        s.login         AS login,
        'MT4Live2'      AS server,
        s.currency      AS currency,
        s.user_name     AS user_name,
        s.user_group    AS user_group,
        s.country       AS country,
        CASE WHEN s.currency = 'CEN' THEN ROUND(COALESCE(s.user_balance, 0) / 100.0, 4) ELSE ROUND(COALESCE(s.user_balance, 0), 4) END AS balance_usd,
        CASE WHEN s.currency = 'CEN' THEN ROUND(COALESCE(s.equity, 0) / 100.0, 4) ELSE ROUND(COALESCE(s.equity, 0), 4) END AS equity_usd,
        CASE WHEN s.currency = 'CEN' THEN ROUND(COALESCE(s.positions_floating_pnl, 0) / 100.0, 4) ELSE ROUND(COALESCE(s.positions_floating_pnl, 0), 4) END AS floating_pnl_usd,
        CASE WHEN s.currency = 'CEN' THEN ROUND(COALESCE(s.closed_total_profit_with_swap, 0) / 100.0, 4) ELSE ROUND(COALESCE(s.closed_total_profit_with_swap, 0), 4) END AS closed_profit_usd,
        CASE WHEN s.currency = 'CEN' THEN ROUND(COALESCE(s.total_commission, 0) / 100.0, 4) ELSE ROUND(COALESCE(s.total_commission, 0), 4) END AS commission_usd,
        CASE WHEN s.currency = 'CEN' THEN ROUND(COALESCE(s.deposit_amount, 0) / 100.0, 4) ELSE ROUND(COALESCE(s.deposit_amount, 0), 4) END AS deposit_usd,
        CASE WHEN s.currency = 'CEN' THEN ROUND(COALESCE(s.withdrawal_amount, 0) / 100.0, 4) ELSE ROUND(COALESCE(s.withdrawal_amount, 0), 4) END AS withdrawal_usd,
        CASE WHEN s.currency = 'CEN' THEN ROUND((COALESCE(s.closed_sell_volume_lots, 0) + COALESCE(s.closed_buy_volume_lots, 0)) / 100.0, 4)
             ELSE ROUND(COALESCE(s.closed_sell_volume_lots, 0) + COALESCE(s.closed_buy_volume_lots, 0), 4)
        END AS volume_lots,
        CASE WHEN s.currency = 'CEN' THEN ROUND((COALESCE(s.closed_sell_overnight_volume_lots, 0) + COALESCE(s.closed_buy_overnight_volume_lots, 0)) / 100.0, 4)
             ELSE ROUND(COALESCE(s.closed_sell_overnight_volume_lots, 0) + COALESCE(s.closed_buy_overnight_volume_lots, 0), 4)
        END AS overnight_volume_lots,
        s.last_updated  AS last_updated
      FROM public.pnl_user_summary_mt4live2 s
      WHERE s.user_id IS NOT NULL
    ),
    rolled AS (
      SELECT
        client_id,
        login,
        server,
        MAX(currency)        AS currency,
        (array_agg(user_name ORDER BY user_name NULLS LAST))[1] AS user_name,
        (array_agg(user_group ORDER BY user_group NULLS LAST))[1] AS user_group,
        (array_agg(country ORDER BY country NULLS LAST))[1]     AS country,
        ROUND(SUM(balance_usd), 4)               AS balance_usd,
        ROUND(SUM(equity_usd), 4)                AS equity_usd,
        ROUND(SUM(floating_pnl_usd), 4)          AS floating_pnl_usd,
        ROUND(SUM(closed_profit_usd), 4)         AS closed_profit_usd,
        ROUND(SUM(commission_usd), 4)            AS commission_usd,
        ROUND(SUM(deposit_usd), 4)               AS deposit_usd,
        ROUND(SUM(withdrawal_usd), 4)            AS withdrawal_usd,
        ROUND(SUM(volume_lots), 4)               AS volume_lots,
        ROUND(SUM(overnight_volume_lots), 4)     AS overnight_volume_lots,
        MAX(last_updated)                         AS last_updated
      FROM combined
      GROUP BY client_id, login, server
    )
    INSERT INTO public.pnl_client_accounts (
      client_id,
      login,
      server,
      currency,
      user_name,
      user_group,
      country,
      balance_usd,
      equity_usd,
      floating_pnl_usd,
      closed_profit_usd,
      commission_usd,
      deposit_usd,
      withdrawal_usd,
      volume_lots,
      overnight_volume_lots,
      auto_swap_free_status,
      last_updated
    )
    SELECT
      r.client_id,
      r.login,
      r.server,
      r.currency,
      r.user_name,
      r.user_group,
      r.country,
      r.balance_usd,
      r.equity_usd,
      r.floating_pnl_usd,
      r.closed_profit_usd,
      r.commission_usd,
      r.deposit_usd,
      r.withdrawal_usd,
      r.volume_lots,
      r.overnight_volume_lots,
      CASE WHEN r.volume_lots = 0 THEN -1.0000
           ELSE ROUND(1 - (r.overnight_volume_lots / NULLIF(r.volume_lots, 0)), 4)
      END AS auto_swap_free_status,
      r.last_updated
    FROM rolled r
    ON CONFLICT (client_id, login, server) DO UPDATE SET
      currency = EXCLUDED.currency,
      user_name = EXCLUDED.user_name,
      user_group = EXCLUDED.user_group,
      country = EXCLUDED.country,
      balance_usd = EXCLUDED.balance_usd,
      equity_usd = EXCLUDED.equity_usd,
      floating_pnl_usd = EXCLUDED.floating_pnl_usd,
      closed_profit_usd = EXCLUDED.closed_profit_usd,
      commission_usd = EXCLUDED.commission_usd,
      deposit_usd = EXCLUDED.deposit_usd,
      withdrawal_usd = EXCLUDED.withdrawal_usd,
      volume_lots = EXCLUDED.volume_lots,
      overnight_volume_lots = EXCLUDED.overnight_volume_lots,
      auto_swap_free_status = EXCLUDED.auto_swap_free_status,
      last_updated = EXCLUDED.last_updated
    ;
    """
    with pg.cursor() as cur:
        cur.execute(sql)


def fetch_mysql_fx_users_map(conn_fx: pymysql.connections.Connection, client_ids: List[int]) -> List[Tuple[int, Optional[str], Optional[int]]]:
    """
    Fetch zipcode and isEnabled for a list of client_ids from fxbackoffice.users.
    Returns list of tuples (client_id, zipcode, is_enabled_as_smallint).
    """
    results: List[Tuple[int, Optional[str], Optional[int]]] = []
    if not client_ids:
        return results
    sql = "SELECT id AS client_id, isEnabled, zipcode FROM users WHERE id IN ({placeholders})"
    with conn_fx.cursor() as cur:
        for batch in chunked(client_ids, 1000):
            ph = ",".join(["%s"] * len(batch))
            cur.execute(sql.format(placeholders=ph), batch)
            rows = cur.fetchall()
            for r in rows:
                cid = int(r.get("client_id")) if r.get("client_id") is not None else None
                zipcode = r.get("zipcode")
                is_enabled = r.get("isEnabled")
                # Coerce to smallint 0/1 if not None
                mapped_enabled: Optional[int] = None
                if is_enabled is not None:
                    mapped_enabled = 1 if int(is_enabled) == 1 else 0
                if cid is not None:
                    results.append((cid, zipcode, mapped_enabled))
    return results


def load_fx_users_temp(pg: psycopg2.extensions.connection, mapping_rows: List[Tuple[int, Optional[str], Optional[int]]]) -> None:
    """
    Load mapping rows into a temporary table for joining with summary build.
    """
    with pg.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS temp_fx_user_map;")
        cur.execute(
            """
            CREATE TEMP TABLE temp_fx_user_map (
              client_id   BIGINT PRIMARY KEY,
              zipcode     TEXT,
              is_enabled  SMALLINT
            ) ON COMMIT DROP;
            """
        )
        if mapping_rows:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO temp_fx_user_map (client_id, zipcode, is_enabled) VALUES %s",
                mapping_rows,
            )


def build_summary(pg: psycopg2.extensions.connection) -> None:
    """
    Build client-level summary from account table and temp_fx_user_map.
    Compute totals and auto_swap_free_status at client level (total-volume ratio).
    """
    sql = r"""
    WITH agg AS (
      SELECT
        a.client_id,
        (array_agg(a.user_name ORDER BY a.user_name NULLS LAST))[1] AS client_name,
        COUNT(DISTINCT (a.login, a.server)) AS account_count,

        ROUND(SUM(a.balance_usd), 4)                AS total_balance_usd,
        ROUND(SUM(a.equity_usd), 4)                 AS total_equity_usd,
        ROUND(SUM(a.floating_pnl_usd), 4)           AS total_floating_pnl_usd,
        ROUND(SUM(a.closed_profit_usd), 4)          AS total_closed_profit_usd,
        ROUND(SUM(a.commission_usd), 4)             AS total_commission_usd,
        ROUND(SUM(a.deposit_usd), 4)                AS total_deposit_usd,
        ROUND(SUM(a.withdrawal_usd), 4)             AS total_withdrawal_usd,
        ROUND(SUM(a.volume_lots), 4)                AS total_volume_lots,
        ROUND(SUM(a.overnight_volume_lots), 4)      AS total_overnight_volume_lots,
        MAX(a.last_updated)                          AS last_updated
      FROM public.pnl_client_accounts a
      GROUP BY a.client_id
    )
    INSERT INTO public.pnl_client_summary (
      client_id,
      client_name,
      zipcode,
      is_enabled,
      total_balance_usd,
      total_equity_usd,
      total_floating_pnl_usd,
      total_closed_profit_usd,
      total_commission_usd,
      total_deposit_usd,
      total_withdrawal_usd,
      total_volume_lots,
      total_overnight_volume_lots,
      auto_swap_free_status,
      account_count,
      last_updated
    )
    SELECT
      g.client_id,
      g.client_name,
      m.zipcode,
      COALESCE(m.is_enabled, 1) AS is_enabled,
      g.total_balance_usd,
      g.total_equity_usd,
      g.total_floating_pnl_usd,
      g.total_closed_profit_usd,
      g.total_commission_usd,
      g.total_deposit_usd,
      g.total_withdrawal_usd,
      g.total_volume_lots,
      g.total_overnight_volume_lots,
      CASE WHEN g.total_volume_lots = 0 THEN -1.0000
           ELSE ROUND(1 - (g.total_overnight_volume_lots / NULLIF(g.total_volume_lots, 0)), 4)
      END AS auto_swap_free_status,
      g.account_count,
      g.last_updated
    FROM agg g
    LEFT JOIN temp_fx_user_map m ON m.client_id = g.client_id
    ON CONFLICT (client_id) DO UPDATE SET
      client_name = EXCLUDED.client_name,
      zipcode = EXCLUDED.zipcode,
      is_enabled = EXCLUDED.is_enabled,
      total_balance_usd = EXCLUDED.total_balance_usd,
      total_equity_usd = EXCLUDED.total_equity_usd,
      total_floating_pnl_usd = EXCLUDED.total_floating_pnl_usd,
      total_closed_profit_usd = EXCLUDED.total_closed_profit_usd,
      total_commission_usd = EXCLUDED.total_commission_usd,
      total_deposit_usd = EXCLUDED.total_deposit_usd,
      total_withdrawal_usd = EXCLUDED.total_withdrawal_usd,
      total_volume_lots = EXCLUDED.total_volume_lots,
      total_overnight_volume_lots = EXCLUDED.total_overnight_volume_lots,
      auto_swap_free_status = EXCLUDED.auto_swap_free_status,
      account_count = EXCLUDED.account_count,
      last_updated = EXCLUDED.last_updated
    ;
    """
    with pg.cursor() as cur:
        cur.execute(sql)


def collect_stats(pg: psycopg2.extensions.connection) -> Tuple[int, int, Optional[str]]:
    """
    Collect (client_count, account_count, max_last_updated_text) after build.
    """
    with pg.cursor() as cur:
        cur.execute("SELECT COUNT(DISTINCT client_id) FROM public.pnl_client_accounts;")
        client_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM public.pnl_client_accounts;")
        account_count = cur.fetchone()[0]
        cur.execute("SELECT TO_CHAR(MAX(last_updated), 'YYYY-MM-DD HH24:MI:SSOF') FROM public.pnl_client_accounts;")
        max_last_updated = cur.fetchone()[0]
    return client_count, account_count, max_last_updated


def update_watermark(pg: psycopg2.extensions.connection, max_last_updated_text: Optional[str]) -> None:
    """
    Update etl_watermarks for dataset='pnl_client' with last_updated = max_last_updated.
    If row exists, update; else insert (partition_key left NULL).
    """
    if not max_last_updated_text:
        return
    # Use a non-null partition_key to satisfy NOT NULL constraint (e.g., 'all')
    sql_upsert = r"""
    INSERT INTO etl_watermarks (dataset, partition_key, last_deal_id, last_time, last_login, last_updated)
    VALUES ('pnl_client', 'all', NULL, NULL, NULL, %(lu)s)
    ON CONFLICT (dataset, partition_key)
    DO UPDATE SET last_updated = EXCLUDED.last_updated;
    """
    with pg.cursor() as cur:
        cur.execute(sql_upsert, {"lu": max_last_updated_text})


def main() -> None:
    start = time.perf_counter()
    print("[INFO] Starting full load for pnl_client_* ...")

    # Prepare connections
    pg = connect_postgres()
    fx_db_name = get_env("MYSQL_DATABASE_FXBACKOFFICE", "fxbackoffice")
    mysql_fx = connect_mysql(fx_db_name)

    try:
        # 1) Truncate targets for a clean full load
        truncate_targets(pg)

        # 2) Build accounts from source tables
        build_accounts(pg)

        # 3) Fetch client_ids, then pull MySQL mapping and stage into temp table
        with pg.cursor() as cur:
            cur.execute("SELECT DISTINCT client_id FROM public.pnl_client_accounts;")
            client_ids = [int(r[0]) for r in cur.fetchall()]

        mapping_rows = fetch_mysql_fx_users_map(mysql_fx, client_ids)
        load_fx_users_temp(pg, mapping_rows)

        # 4) Build client summary (joining mapping), then commit
        build_summary(pg)

        client_count, account_count, max_last_updated = collect_stats(pg)

        # Commit main data writes before watermark update
        pg.commit()

        # 5) Update watermark after successful data load
        update_watermark(pg, max_last_updated)
        pg.commit()

        elapsed = time.perf_counter() - start
        print("[INFO] Full load completed.")
        print(f"[INFO] Clients: {client_count}")
        print(f"[INFO] Accounts: {account_count}")
        print(f"[INFO] Max last_updated: {max_last_updated}")
        print(f"[INFO] Elapsed: {elapsed:.2f} sec")

    except Exception as e:
        pg.rollback()
        print(f"[ERROR] Full load failed: {e}")
        raise
    finally:
        try:
            mysql_fx.close()
        except Exception:
            pass
        try:
            pg.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()


