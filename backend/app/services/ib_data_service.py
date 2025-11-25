from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable, List, Tuple

import fcntl
import pymysql

from ..core.config import Settings

logger = logging.getLogger(__name__)


IB_QUERY = """
WITH params AS (
    SELECT
        %s AS target_ib,
        %s AS start_time,
        %s AS end_time
),
tx_referrals AS (
    SELECT it.referralId
    FROM fxbackoffice.ib_tree_with_self it
    JOIN params p ON p.target_ib = it.ibid
),
tx_totals AS (
    SELECT
        SUM(CASE WHEN t.type = 'deposit'       THEN normalized_amount ELSE 0 END) AS deposit_usd,
        SUM(CASE WHEN t.type = 'withdrawal'    THEN normalized_amount ELSE 0 END) AS withdrawal_usd,
        SUM(CASE WHEN t.type = 'ib withdrawal' THEN normalized_amount ELSE 0 END) AS ib_withdrawal_usd
    FROM (
        SELECT
            t.type,
            CASE
                WHEN UPPER(t.processedCurrency) = 'CEN' THEN t.processedAmount / 100.0
                ELSE t.processedAmount
            END AS normalized_amount
        FROM fxbackoffice.transactions t
        JOIN params p
        WHERE t.status = 'approved'
          AND t.type IN ('deposit', 'withdrawal', 'ib withdrawal')
          AND t.processedAt >= p.start_time
          AND t.processedAt <= p.end_time
          AND t.fromUserId IN (SELECT referralId FROM tx_referrals)
    ) t
),
wallet_referrals AS (
    SELECT it.referralId
    FROM fxbackoffice.ib_tree_with_self it
    JOIN params p ON p.target_ib = it.ibid
),
wallet_total AS (
    SELECT IFNULL(SUM(mu.balance), 0) AS ib_wallet_balance
    FROM fxbackoffice.mt4_users mu
    WHERE mu.`GROUP` LIKE 'IB-WALLET%%'
      AND mu.userId IN (SELECT referralId FROM wallet_referrals)
)
SELECT
    p.target_ib AS ibid,
    IFNULL(tx.deposit_usd, 0) AS deposit_usd,
    IFNULL(tx.withdrawal_usd, 0) + IFNULL(tx.ib_withdrawal_usd, 0) AS total_withdrawal_usd,
    IFNULL(tx.ib_withdrawal_usd, 0) AS ib_withdrawal_usd,
    IFNULL(wt.ib_wallet_balance, 0) AS ib_wallet_balance,
    IFNULL(tx.deposit_usd, 0)
        + (IFNULL(tx.withdrawal_usd, 0) + IFNULL(tx.ib_withdrawal_usd, 0))
        - IFNULL(wt.ib_wallet_balance, 0) AS net_deposit_usd
FROM params p
LEFT JOIN tx_totals tx ON 1=1
LEFT JOIN wallet_total wt ON 1=1
"""

LAST_QUERY_FILENAME = "ib_data_last_query.txt"
LOCK_FILENAME = "ib_data_last_query.lock"


def _connect(settings: Settings):
    """Create a MySQL connection using shared FX backoffice credentials."""
    if not settings.DB_HOST:
        raise RuntimeError("DB_HOST is not configured")

    return pymysql.connect(
        host=settings.DB_HOST,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME,
        port=int(settings.DB_PORT),
        charset=settings.DB_CHARSET,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _last_query_path(settings: Settings) -> Path:
    return settings.parquet_dir / LAST_QUERY_FILENAME


def _lock_path(settings: Settings) -> Path:
    return settings.parquet_dir / LOCK_FILENAME


@contextmanager
def _file_lock(path: Path):
    """Advisory lock to block concurrent heavy queries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w+") as fp:
        fcntl.flock(fp, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fp, fcntl.LOCK_UN)


def _to_float(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_range(dt: datetime) -> str:
    """Format datetime for SQL layer."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _query_single_ib(conn, ibid: str, start_str: str, end_str: str) -> dict:
    """Execute SQL query for a single IB ID and return normalized metrics."""
    try:
        with conn.cursor() as cur:
            cur.execute(IB_QUERY, (ibid, start_str, end_str))
            row = cur.fetchone() or {}
        return {
            "ibid": str(row.get("ibid", ibid)),
            "deposit_usd": _to_float(row.get("deposit_usd")),
            "total_withdrawal_usd": _to_float(row.get("total_withdrawal_usd")),
            "ib_withdrawal_usd": _to_float(row.get("ib_withdrawal_usd")),
            "ib_wallet_balance": _to_float(row.get("ib_wallet_balance")),
            "net_deposit_usd": _to_float(row.get("net_deposit_usd")),
        }
    except Exception as e:
        logger.error(f"Query failed for IB ID {ibid}: {type(e).__name__}: {e}")
        raise RuntimeError(f"查询 IB {ibid} 时发生错误: {str(e)}") from e


def _sum_rows(rows: Iterable[dict]) -> dict:
    totals = {
        "deposit_usd": 0.0,
        "total_withdrawal_usd": 0.0,
        "ib_withdrawal_usd": 0.0,
        "ib_wallet_balance": 0.0,
        "net_deposit_usd": 0.0,
    }
    for row in rows:
        for key in totals.keys():
            totals[key] += float(row.get(key, 0.0) or 0.0)
    return totals


def read_last_query_time(settings: Settings) -> datetime | None:
    path = _last_query_path(settings)
    try:
        raw = path.read_text().strip()
        if not raw:
            return None
        return datetime.fromisoformat(raw)
    except FileNotFoundError:
        return None
    except ValueError:
        return None


def _write_last_query_time(settings: Settings, ts: datetime) -> None:
    path = _last_query_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ts.isoformat())


def aggregate_ib_data(
    settings: Settings,
    ib_ids: List[str],
    start: datetime,
    end: datetime,
) -> Tuple[list[dict], dict, datetime]:
    """Aggregate IB data for given IDs and time range. Returns (rows, totals, timestamp)."""
    if not ib_ids:
        raise ValueError("ib_ids cannot be empty")
    if end < start:
        raise ValueError("end must be greater than or equal to start")

    start_str = _normalize_range(start)
    end_str = _normalize_range(end)

    logger.info(f"Starting IB data aggregation: ib_ids={ib_ids}, start={start_str}, end={end_str}")

    try:
        with _file_lock(_lock_path(settings)):
            try:
                conn = _connect(settings)
            except Exception as e:
                logger.error(f"Database connection failed: {type(e).__name__}: {e}")
                raise RuntimeError(f"数据库连接失败: {str(e)}") from e

            try:
                rows = []
                for ibid in ib_ids:
                    try:
                        row = _query_single_ib(conn, ibid, start_str, end_str)
                        rows.append(row)
                    except Exception as e:
                        logger.error(f"Failed to query IB {ibid}: {e}")
                        # Continue with other IB IDs instead of failing completely
                        rows.append({
                            "ibid": ibid,
                            "deposit_usd": 0.0,
                            "total_withdrawal_usd": 0.0,
                            "ib_withdrawal_usd": 0.0,
                            "ib_wallet_balance": 0.0,
                            "net_deposit_usd": 0.0,
                        })
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

            totals = _sum_rows(rows)
            timestamp = datetime.now(timezone.utc)
            try:
                _write_last_query_time(settings, timestamp)
            except Exception as e:
                logger.warning(f"Failed to write last query time: {e}")

            logger.info(f"IB data aggregation completed: {len(rows)} rows")
            return rows, totals, timestamp
    except Exception as e:
        logger.error(f"IB data aggregation failed: {type(e).__name__}: {e}", exc_info=True)
        raise


