import mysql.connector
import psycopg2
from psycopg2.extras import execute_values
import argparse
import sys
import os
from dotenv import load_dotenv

# --- 1. 配置 ---
# 从 .env 文件加载环境变量
load_dotenv()

MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST'),
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
    'database': os.getenv('MYSQL_DATABASE'),
    'ssl_ca': os.getenv('MYSQL_SSL_CA')
}

POSTGRES_CONFIG = {
    'host': os.getenv('POSTGRES_HOST'),
    'user': os.getenv('POSTGRES_USER'),
    'password': os.getenv('POSTGRES_PASSWORD'),
    'dbname': os.getenv('POSTGRES_DBNAME')
}

# 为不同品种配置 Volume 换算除数，非常重要！
VOLUME_DIVISORS = {
    'XAUUSD.kcmc': 10000.0,
    # 以后可以添加更多, e.g., 'EURUSD': 100.0
    '__default__': 100.0  # 提供一个默认值
}

class PnlEtlProcessor:
    def __init__(self):
        """初始化并建立数据库连接"""
        try:
            self.mysql_conn = mysql.connector.connect(**MYSQL_CONFIG)
            self.pg_conn = psycopg2.connect(**POSTGRES_CONFIG)
            self.pg_conn.autocommit = False # 我们手动控制事务
        except Exception as e:
            print(f"FATAL: Could not connect to databases. Error: {e}")
            sys.exit(1)

    def _get_watermark(self, symbol: str) -> int:
        """从 PostgreSQL 获取指定 symbol 的水位线 (last_deal_id)"""
        with self.pg_conn.cursor() as cursor:
            cursor.execute("SELECT last_deal_id FROM etl_watermarks WHERE symbol = %s", (symbol,))
            result = cursor.fetchone()
            return result[0] if result else 0

    def _update_watermark(self, symbol: str, new_deal_id: int):
        """更新 PostgreSQL 中的水位线"""
        with self.pg_conn.cursor() as cursor:
            # ON CONFLICT ... DO UPDATE 是一种高效的 "upsert" 操作
            sql = """
            INSERT INTO etl_watermarks (symbol, last_deal_id, last_updated)
            VALUES (%s, %s, NOW())
            ON CONFLICT (symbol) DO UPDATE SET
                last_deal_id = EXCLUDED.last_deal_id,
                last_updated = NOW();
            """
            cursor.execute(sql, (symbol, new_deal_id))

    def run_job(self, symbol: str, mode: str):
        """
        执行单个 symbol 的 ETL 任务
        :param symbol: 要处理的交易品种
        :param mode: 'full' 或 'incremental'
        """
        print(f"\n--- Starting job for symbol: {symbol}, mode: {mode} ---")
        
        # 1. 准备查询
        last_deal_id = 0
        if mode == 'incremental':
            last_deal_id = self._get_watermark(symbol)
            print(f"Incremental mode: Found last processed Deal ID: {last_deal_id}")
        
        divisor = VOLUME_DIVISORS.get(symbol, VOLUME_DIVISORS['__default__'])
        
        # 动态构建 SQL
        params = {
            'symbol': symbol,
            'divisor': divisor,
            'last_deal_id': last_deal_id
        }
        extract_sql = self._get_extract_sql_template(last_deal_id > 0)


        # 2. Extract
        print(f"Extracting data from MySQL...")
        with self.mysql_conn.cursor() as cursor:
            cursor.execute(extract_sql, params)
            data_to_load = cursor.fetchall()
            # 找到本次处理的最大 Deal ID 用于更新水位线
            new_max_deal_id = max([row[-1] for row in data_to_load]) if data_to_load else last_deal_id

        if not data_to_load:
            print("No new data found. Job finished.")
            return

        print(f"Extracted {len(data_to_load)} rows. Max Deal ID in this batch: {new_max_deal_id}")
        
        # 3. Load
        print("Loading data into PostgreSQL...")
        with self.pg_conn.cursor() as cursor:
            if mode == 'full':
                print(f"Full mode: Deleting existing data for {symbol}...")
                cursor.execute("DELETE FROM pnl_summary WHERE symbol = %s", (symbol,))
            
            # 使用 ON CONFLICT DO UPDATE 实现高效的 "upsert"
            # 只有在数据实际发生变化时才更新 last_updated，体现真实的数据活跃时间
            insert_sql = """
            INSERT INTO pnl_summary (
                login, symbol, user_group, user_name, country, balance,
                total_closed_trades, buy_trades_count, sell_trades_count,
                total_closed_volume, buy_closed_volume, sell_closed_volume, total_closed_pnl,
                floating_pnl
            ) VALUES %s
            ON CONFLICT (login, symbol) DO UPDATE SET
                user_group = EXCLUDED.user_group,
                user_name = EXCLUDED.user_name,
                country = EXCLUDED.country,
                balance = EXCLUDED.balance,
                total_closed_trades = pnl_summary.total_closed_trades + EXCLUDED.total_closed_trades,
                buy_trades_count = pnl_summary.buy_trades_count + EXCLUDED.buy_trades_count,
                sell_trades_count = pnl_summary.sell_trades_count + EXCLUDED.sell_trades_count,
                total_closed_volume = pnl_summary.total_closed_volume + EXCLUDED.total_closed_volume,
                buy_closed_volume = pnl_summary.buy_closed_volume + EXCLUDED.buy_closed_volume,
                sell_closed_volume = pnl_summary.sell_closed_volume + EXCLUDED.sell_closed_volume,
                total_closed_pnl = pnl_summary.total_closed_pnl + EXCLUDED.total_closed_pnl,
                floating_pnl = EXCLUDED.floating_pnl,
                -- 只要有任何数值变化（新交易、浮动盈亏变化），就更新为当前时间
                last_updated = CASE 
                    WHEN pnl_summary.total_closed_trades <> (pnl_summary.total_closed_trades + EXCLUDED.total_closed_trades)
                      OR pnl_summary.buy_trades_count <> (pnl_summary.buy_trades_count + EXCLUDED.buy_trades_count)
                      OR pnl_summary.sell_trades_count <> (pnl_summary.sell_trades_count + EXCLUDED.sell_trades_count)
                      OR pnl_summary.total_closed_volume <> (pnl_summary.total_closed_volume + EXCLUDED.total_closed_volume)
                      OR pnl_summary.buy_closed_volume <> (pnl_summary.buy_closed_volume + EXCLUDED.buy_closed_volume)
                      OR pnl_summary.sell_closed_volume <> (pnl_summary.sell_closed_volume + EXCLUDED.sell_closed_volume)
                      OR pnl_summary.total_closed_pnl <> (pnl_summary.total_closed_pnl + EXCLUDED.total_closed_pnl)
                      OR pnl_summary.floating_pnl <> EXCLUDED.floating_pnl
                      OR pnl_summary.balance <> EXCLUDED.balance
                    THEN NOW()
                    ELSE pnl_summary.last_updated
                END;
            """
            
            # 移除 max_deal_id，因为它不在 pnl_summary 表中
            clean_data = [row[:-1] for row in data_to_load]
            execute_values(cursor, insert_sql, clean_data)

            # 4. 更新水位线
            if new_max_deal_id > last_deal_id:
                self._update_watermark(symbol, new_max_deal_id)
                print(f"Updated watermark for {symbol} to {new_max_deal_id}")

            self.pg_conn.commit()
            print("PostgreSQL transaction committed successfully.")
    
    def _get_extract_sql_template(self, is_incremental: bool) -> str:
        # 定义 WHERE 子句，使用参数化查询占位符
        deals_where_clause = "d.symbol = %(symbol)s AND d.entry IN (1, 3)"
        if is_incremental:
            deals_where_clause += " AND d.Deal > %(last_deal_id)s"

        # 最终的 SQL 模板。所有 JOIN 都在 MySQL 端完成。
        return f"""
        WITH ClosedDealsSummary AS (
            SELECT
                Login,
                COUNT(Deal) AS total_closed_trades,
                SUM(CASE WHEN Action = 0 THEN 1 ELSE 0 END) AS buy_trades_count,
                SUM(CASE WHEN Action = 1 THEN 1 ELSE 0 END) AS sell_trades_count,
                SUM(Volume) / %(divisor)s AS total_closed_volume,
                SUM(CASE WHEN Action = 0 THEN Volume ELSE 0 END) / %(divisor)s AS buy_closed_volume,
                SUM(CASE WHEN Action = 1 THEN Volume ELSE 0 END) / %(divisor)s AS sell_closed_volume,
                SUM(Profit) AS total_closed_pnl,
                MAX(Deal) as max_deal_id
            FROM mt5_deals d
            WHERE {deals_where_clause}
            GROUP BY Login
        ),
        OpenPositionsSummary AS (
            SELECT
                Login,
                SUM(Profit) AS floating_pnl
            FROM mt5_positions
            WHERE symbol = %(symbol)s
            GROUP BY Login
        ),
        ActiveLogins AS (
            SELECT Login FROM ClosedDealsSummary
            UNION
            SELECT Login FROM OpenPositionsSummary
        )
        SELECT
            al.Login,
            %(symbol)s as symbol,
            u.`Group`,
            u.`Name`,
            u.Country,
            u.balance,
            COALESCE(cds.total_closed_trades, 0),
            COALESCE(cds.buy_trades_count, 0),
            COALESCE(cds.sell_trades_count, 0),
            COALESCE(cds.total_closed_volume, 0),
            COALESCE(cds.buy_closed_volume, 0),
            COALESCE(cds.sell_closed_volume, 0),
            COALESCE(cds.total_closed_pnl, 0),
            COALESCE(ops.floating_pnl, 0),
            COALESCE(cds.max_deal_id, %(last_deal_id)s)
        FROM ActiveLogins al
        JOIN mt5_users u ON al.Login = u.Login
        LEFT JOIN ClosedDealsSummary cds ON al.Login = cds.Login
        LEFT JOIN OpenPositionsSummary ops ON al.Login = ops.Login;
        """

    def close_connections(self):
        """关闭所有数据库连接"""
        if self.mysql_conn and self.mysql_conn.is_connected():
            self.mysql_conn.close()
            print("\nMySQL connection closed.")
        if self.pg_conn:
            self.pg_conn.close()
            print("PostgreSQL connection closed.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="ETL tool for syncing MT5 data to reporting DB.")
    parser.add_argument('symbol', type=str, help="The trading symbol to process (e.g., XAUUSD.kcmc).")
    parser.add_argument('--mode', type=str, choices=['full', 'incremental'], default='incremental',
                        help="ETL mode: 'full' for a complete reload, 'incremental' for updating with new data.")
    
    args = parser.parse_args()

    processor = PnlEtlProcessor()
    try:
        processor.run_job(symbol=args.symbol, mode=args.mode)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        processor.pg_conn.rollback()
    finally:
        processor.close_connections()
