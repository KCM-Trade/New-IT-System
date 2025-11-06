#!/usr/bin/env python3
import os
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# External libs expected in venv: psycopg2, PyMySQL, python-dotenv
import psycopg2
import psycopg2.extras
import pymysql
import dotenv

dotenv.load_dotenv()


def get_env(name: str, default: Optional[str] = None) -> str:
    """Read env var or exit if required."""
    value = os.getenv(name, default)
    if value is None:
        print(f"[ERROR] Missing required environment variable: {name}")
        sys.exit(1)
    return value


def connect_postgres() -> psycopg2.extensions.connection:
    """Connect to MT5_ETL Postgres."""
    host = get_env("POSTGRES_HOST")
    user = get_env("POSTGRES_USER")
    password = get_env("POSTGRES_PASSWORD")
    port = int(get_env("POSTGRES_PORT", "5432"))
    dbname = get_env("POSTGRES_DBNAME_MT5", "MT5_ETL")
    conn = psycopg2.connect(host=host, user=user, password=password, port=port, dbname=dbname)
    conn.autocommit = False
    return conn


def connect_mysql(dbname: str) -> pymysql.connections.Connection:
    """Connect to MySQL with shared host/user/password/port."""
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
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def get_source_watermark(pg: psycopg2.extensions.connection, datasets: Sequence[str]) -> Optional[str]:
    """
    Fetch GREATEST(last_updated) among provided datasets from etl_watermarks.
    Returns text representation; None if not found.
    """
    if not datasets:
        return None
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT TO_CHAR(MAX(last_updated), 'YYYY-MM-DD HH24:MI:SSOF')
            FROM etl_watermarks
            WHERE dataset = ANY(%s)
            """,
            (list(datasets),),
        )
        row = cur.fetchone()
        return row[0] if row else None


def stage_candidates(pg: psycopg2.extensions.connection, watermark_text: Optional[str]) -> Tuple[int, int, int, Optional[str]]:
    """
    Build candidate client_id set into temp table temp_candidates(client_id, reason).
    - reason = 'lag': per-client source MAX(last_updated) > summary.last_updated
    - reason = 'missing': users exist in sources but absent in summary
    Note: watermark_text is ignored here (kept for API compatibility).
    Returns (total_count, missing_count, lag_count, watermark_text)
    """
    with pg.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS temp_candidates;")
        cur.execute(
            """
            CREATE TEMP TABLE temp_candidates (
              client_id BIGINT PRIMARY KEY,
              reason    TEXT
            ) ON COMMIT DROP;
            """
        )

        # 1) missing in summary (new clients)
        cur.execute(
            """
            INSERT INTO temp_candidates (client_id, reason)
            SELECT s.user_id AS client_id, 'missing' AS reason
            FROM (
              SELECT DISTINCT user_id FROM public.pnl_user_summary WHERE user_id IS NOT NULL
              UNION
              SELECT DISTINCT user_id FROM public.pnl_user_summary_mt4live2 WHERE user_id IS NOT NULL
            ) s
            LEFT JOIN public.pnl_client_summary cs ON cs.client_id = s.user_id
            WHERE cs.client_id IS NULL
            ON CONFLICT (client_id) DO NOTHING;
            """
        )

        # 2) lag per client: source MAX(last_updated) > summary.last_updated
        cur.execute(
            """
            WITH src_max AS (
              SELECT user_id, MAX(last_updated) AS src_lu
              FROM (
                SELECT user_id, last_updated FROM public.pnl_user_summary WHERE user_id IS NOT NULL
                UNION ALL
                SELECT user_id, last_updated FROM public.pnl_user_summary_mt4live2 WHERE user_id IS NOT NULL
              ) z
              GROUP BY user_id
            )
            INSERT INTO temp_candidates (client_id, reason)
            SELECT sm.user_id AS client_id, 'lag'::text AS reason
            FROM src_max sm
            JOIN public.pnl_client_summary cs ON cs.client_id = sm.user_id
            WHERE sm.src_lu > cs.last_updated
            ON CONFLICT (client_id) DO NOTHING;
            """
        )

        # Count
        cur.execute(
            """
            SELECT
              COUNT(*) AS total,
              COALESCE(SUM(CASE WHEN reason = 'missing' THEN 1 ELSE 0 END), 0) AS missing_cnt,
              COALESCE(SUM(CASE WHEN reason = 'lag' THEN 1 ELSE 0 END), 0)      AS lag_cnt
            FROM temp_candidates;
            """
        )
        total, missing_cnt, lag_cnt = cur.fetchone()
    return total, missing_cnt, lag_cnt, watermark_text


def build_accounts_for_candidates(pg: psycopg2.extensions.connection) -> int:
    """
    Build/UPSERT account rows for candidate clients only.
    """
    sql = r"""
    WITH src AS (
      SELECT
        s.user_id       AS user_id,
        s.login         AS login,
        'MT5'           AS server,
        s.currency      AS currency,
        s.user_name     AS user_name,
        s.user_group    AS user_group,
        s.country       AS country,
        s.user_balance  AS user_balance,
        s.equity        AS equity,
        s.positions_floating_pnl AS positions_floating_pnl,
        s.closed_total_profit_with_swap AS closed_total_profit_with_swap,
        s.total_commission AS total_commission,
        s.deposit_amount AS deposit_amount,
        s.withdrawal_amount AS withdrawal_amount,
        s.closed_sell_volume_lots AS closed_sell_volume_lots,
        s.closed_buy_volume_lots AS closed_buy_volume_lots,
        s.closed_sell_overnight_volume_lots AS closed_sell_overnight_volume_lots,
        s.closed_buy_overnight_volume_lots AS closed_buy_overnight_volume_lots,
        s.last_updated  AS last_updated
      FROM public.pnl_user_summary s
      JOIN temp_candidates c ON c.client_id = s.user_id
      WHERE s.user_id IS NOT NULL
      UNION ALL
      SELECT
        s.user_id       AS user_id,
        s.login         AS login,
        'MT4Live2'      AS server,
        s.currency      AS currency,
        s.user_name     AS user_name,
        s.user_group    AS user_group,
        s.country       AS country,
        s.user_balance  AS user_balance,
        s.equity        AS equity,
        s.positions_floating_pnl AS positions_floating_pnl,
        s.closed_total_profit_with_swap AS closed_total_profit_with_swap,
        s.total_commission AS total_commission,
        s.deposit_amount AS deposit_amount,
        s.withdrawal_amount AS withdrawal_amount,
        s.closed_sell_volume_lots AS closed_sell_volume_lots,
        s.closed_buy_volume_lots AS closed_buy_volume_lots,
        s.closed_sell_overnight_volume_lots AS closed_sell_overnight_volume_lots,
        s.closed_buy_overnight_volume_lots AS closed_buy_overnight_volume_lots,
        s.last_updated  AS last_updated
      FROM public.pnl_user_summary_mt4live2 s
      JOIN temp_candidates c ON c.client_id = s.user_id
      WHERE s.user_id IS NOT NULL
    ),
    combined AS (
      SELECT 
        s.user_id       AS client_id,
        s.login         AS login,
        s.server        AS server,
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
      FROM src s
    ),
    rolled AS (
      SELECT
        client_id,
        login,
        CASE WHEN MIN(server) = MAX(server) THEN MIN(server) ELSE MIN(server) END AS server,
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
      GROUP BY client_id, login
    )
    INSERT INTO public.pnl_client_accounts (
      client_id, login, server, currency, user_name, user_group, country,
      balance_usd, equity_usd, floating_pnl_usd, closed_profit_usd, commission_usd,
      deposit_usd, withdrawal_usd, volume_lots, overnight_volume_lots,
      auto_swap_free_status, last_updated
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
      CASE WHEN r.volume_lots = 0 THEN -1.0000 ELSE ROUND(1 - (r.overnight_volume_lots / NULLIF(r.volume_lots, 0)), 4) END,
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
        return cur.rowcount if cur.rowcount is not None else 0


def delete_orphan_accounts_for_candidates(pg: psycopg2.extensions.connection) -> int:
    """Delete accounts of candidates no longer present in sources."""
    sql = r"""
    DELETE FROM public.pnl_client_accounts ca
    USING temp_candidates c
    WHERE ca.client_id = c.client_id
      AND NOT EXISTS (
        SELECT 1 FROM public.pnl_user_summary s WHERE s.user_id = ca.client_id AND s.login = ca.login
        UNION ALL
        SELECT 1 FROM public.pnl_user_summary_mt4live2 s WHERE s.user_id = ca.client_id AND s.login = ca.login
      );
    """
    with pg.cursor() as cur:
        cur.execute(sql)
        return cur.rowcount if cur.rowcount is not None else 0


def fetch_mysql_fx_users_map(conn_fx: pymysql.connections.Connection, client_ids: List[int]) -> List[Tuple[int, Optional[str], Optional[int]]]:
    """Fetch zipcode & isEnabled mapping for client_ids from fxbackoffice.users."""
    results: List[Tuple[int, Optional[str], Optional[int]]] = []
    if not client_ids:
        return results
    sql = "SELECT id AS client_id, isEnabled, zipcode FROM users WHERE id IN ({placeholders})"
    with conn_fx.cursor() as cur:
        for batch in chunked(client_ids, 1000):
            ph = ",".join(["%s"] * len(batch))
            cur.execute(sql.format(placeholders=ph), batch)
            for r in cur.fetchall():
                cid = int(r.get("client_id")) if r.get("client_id") is not None else None
                zipcode = r.get("zipcode")
                is_enabled = r.get("isEnabled")
                mapped_enabled: Optional[int] = None
                if is_enabled is not None:
                    mapped_enabled = 1 if int(is_enabled) == 1 else 0
                if cid is not None:
                    results.append((cid, zipcode, mapped_enabled))
    return results


def load_fx_users_temp(pg: psycopg2.extensions.connection, mapping_rows: List[Tuple[int, Optional[str], Optional[int]]]) -> int:
    """Stage mapping into TEMP table for joins."""
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
        return len(mapping_rows or [])


def count_zipcode_changes(pg: psycopg2.extensions.connection) -> int:
    """
    Count how many candidates will change zipcode compared to current summary.
    """
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM temp_candidates c
            LEFT JOIN temp_fx_user_map m ON m.client_id = c.client_id
            LEFT JOIN public.pnl_client_summary s ON s.client_id = c.client_id
            WHERE m.zipcode IS NOT NULL AND (m.zipcode IS DISTINCT FROM s.zipcode);
            """
        )
        return cur.fetchone()[0]


def fetch_zipcode_change_details(pg: psycopg2.extensions.connection, limit: int = 20) -> List[Tuple[int, Optional[str], Optional[str]]]:
    """
    Return up to `limit` rows of zipcode changes: (client_id, old_zipcode, new_zipcode).
    Only considers clients in temp_candidates.
    """
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT c.client_id,
                   s.zipcode AS old_zipcode,
                   m.zipcode AS new_zipcode
            FROM temp_candidates c
            LEFT JOIN temp_fx_user_map m ON m.client_id = c.client_id
            LEFT JOIN public.pnl_client_summary s ON s.client_id = c.client_id
            WHERE m.zipcode IS NOT NULL AND (m.zipcode IS DISTINCT FROM s.zipcode)
            ORDER BY c.client_id
            LIMIT %s;
            """,
            (limit,),
        )
        rows = cur.fetchall()
        return [(int(r[0]), r[1], r[2]) for r in rows]


def build_summary_for_candidates(pg: psycopg2.extensions.connection) -> int:
    """Build/UPSERT summary only for candidate clients."""
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
      JOIN temp_candidates c ON c.client_id = a.client_id
      GROUP BY a.client_id
    )
    INSERT INTO public.pnl_client_summary (
      client_id, client_name, zipcode, is_enabled,
      total_balance_usd, total_equity_usd, total_floating_pnl_usd, total_closed_profit_usd,
      total_commission_usd, total_deposit_usd, total_withdrawal_usd,
      total_volume_lots, total_overnight_volume_lots, auto_swap_free_status,
      account_count, last_updated
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
      CASE WHEN g.total_volume_lots = 0 THEN -1.0000 ELSE ROUND(1 - (g.total_overnight_volume_lots / NULLIF(g.total_volume_lots, 0)), 4) END,
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
        return cur.rowcount if cur.rowcount is not None else 0


def collect_run_stats(pg: psycopg2.extensions.connection) -> Tuple[int, int, Optional[str]]:
    with pg.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM temp_candidates;")
        candidates = cur.fetchone()[0]
        cur.execute(
            """
            SELECT COUNT(*) FROM public.pnl_client_accounts a
            JOIN temp_candidates c ON c.client_id = a.client_id
            """
        )
        accounts = cur.fetchone()[0]
        cur.execute(
            """
            SELECT TO_CHAR(MAX(last_updated), 'YYYY-MM-DD HH24:MI:SSOF')
            FROM public.pnl_client_accounts a JOIN temp_candidates c ON c.client_id = a.client_id
            """
        )
        max_lu = cur.fetchone()[0]
    return candidates, accounts, max_lu


def main() -> None:
    start = time.perf_counter()
    print("[INFO] Starting incremental refresh for pnl_client_* ...")

    # Connect
    pg = connect_postgres()
    mysql_fx = connect_mysql(get_env("MYSQL_DATABASE_FXBACKOFFICE", "fxbackoffice"))

    # Datasets whose last_updated watermark we rely on (customize via env)
    datasets_csv = os.getenv("INCR_SOURCE_DATASETS", "pnl_user_summary,pnl_user_summary_mt4live2")
    datasets = [d.strip() for d in datasets_csv.split(",") if d.strip()]

    try:
        # 1) Get source watermark (upper bound)
        t0 = time.perf_counter()
        watermark_text = get_source_watermark(pg, datasets)
        t1 = time.perf_counter()

        # 2) Stage candidates: time-lagged targets + missing clients
        cand_total, missing_cnt, lag_cnt, _ = stage_candidates(pg, watermark_text)
        t2 = time.perf_counter()
        if cand_total == 0:
            pg.rollback()
            print("[INFO] No candidates. Nothing to refresh.")
            return

        # 3) Accounts upsert for candidates
        acc_t0 = time.perf_counter()
        accounts_affected = build_accounts_for_candidates(pg)
        acc_t1 = time.perf_counter()

        # 4) Orphan accounts cleanup (for candidates only)
        del_t0 = time.perf_counter()
        orphan_deleted = delete_orphan_accounts_for_candidates(pg)
        del_t1 = time.perf_counter()

        # 5) Fetch mapping from MySQL and stage
        with pg.cursor() as cur:
            cur.execute("SELECT client_id FROM temp_candidates;")
            client_ids = [int(r[0]) for r in cur.fetchall()]
        map_t0 = time.perf_counter()
        mapping_rows = fetch_mysql_fx_users_map(mysql_fx, client_ids)
        loaded_mapping = load_fx_users_temp(pg, mapping_rows)
        zipcode_changes = count_zipcode_changes(pg)
        zipcode_details = fetch_zipcode_change_details(pg) if zipcode_changes > 0 else []
        map_t1 = time.perf_counter()

        # 6) Summary upsert for candidates
        sum_t0 = time.perf_counter()
        summary_affected = build_summary_for_candidates(pg)
        sum_t1 = time.perf_counter()

        # 7) Stats and commit
        stats_t0 = time.perf_counter()
        candidates, accounts, max_lu = collect_run_stats(pg)
        stats_t1 = time.perf_counter()
        pg.commit()

        elapsed = time.perf_counter() - start
        print("[INFO] Incremental refresh completed.")
        print(f"[INFO] Candidates (clients): {candidates}  | missing: {missing_cnt}  | lag: {lag_cnt}")
        print(f"[INFO] Accounts UPSERT affected rows: {accounts_affected}  | orphan deleted: {orphan_deleted}")
        print(f"[INFO] Mapping loaded: {loaded_mapping}  | zipcode changes: {zipcode_changes}")
        if zipcode_changes > 0:
            print("[INFO] Zipcode change details (first 20):")
            for cid, old_zc, new_zc in zipcode_details:
                print(f"[INFO]   client_id={cid}  old_zipcode={old_zc}  new_zipcode={new_zc}")
        print(f"[INFO] Summary UPSERT affected rows: {summary_affected}")
        print(f"[INFO] Max last_updated (affected): {max_lu}")
        print(
            f"[INFO] Timings (sec) => watermark: {t1-t0:.2f}, candidates: {t2-t1:.2f}, "
            f"accounts: {acc_t1-acc_t0:.2f}, delete_orphans: {del_t1-del_t0:.2f}, mapping: {map_t1-map_t0:.2f}, "
            f"summary: {sum_t1-sum_t0:.2f}, stats: {stats_t1-stats_t0:.2f}, total: {elapsed:.2f}"
        )
        print(f"[INFO] Elapsed: {elapsed:.2f} sec")

    except Exception as e:
        pg.rollback()
        print(f"[ERROR] Incremental refresh failed: {e}")
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


