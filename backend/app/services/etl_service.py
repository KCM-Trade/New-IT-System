from __future__ import annotations
"""
ETL服务模块 - 盈亏汇总数据同步

将原本的 sync_pnl_summary.py 脚本重构为可导入的服务类，
支持同步调用和详细的执行结果返回。
"""

import os
from typing import List, Tuple, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime

import mysql.connector
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor

from ..core.config import get_settings
from ..core.logging_config import get_logger

# Use centralized logging configuration (no basicConfig needed)
logger = get_logger(__name__)


@dataclass
class EtlResult:
    """ETL执行结果数据类"""
    success: bool
    processed_rows: int
    new_max_deal_id: int
    start_time: datetime
    end_time: datetime
    error_message: Optional[str] = None
    # 新增：区分新交易和浮动盈亏更新
    new_trades_count: int = 0
    floating_only_count: int = 0
    
    @property
    def duration_seconds(self) -> float:
        """计算执行时长（秒）"""
        return (self.end_time - self.start_time).total_seconds()


# 产品配置：包含所有产品相关的元信息
PRODUCT_CONFIGS = {
    'XAUUSD.kcmc': {
        'account_type': 'cent',           # 美分账户
        'volume_divisor': 10000.0,        # 手数换算
        
        'display_divisor': 100.0,         # 💰 金额显示换算（美分账户需要/100）
        'currency': 'USD',
        'description': '黄金美分账户'
    },
    'XAUUSD.kcm': {
        'account_type': 'standard',       # 标准账户  
        'volume_divisor': 10000.0,
        'display_divisor': 1.0,           # 💰 标准账户不需要换算
        'currency': 'USD',
        'description': '黄金标准账户'
    },
    'XAUUSD': {
        'account_type': 'standard',
        'volume_divisor': 10000.0,
        'display_divisor': 1.0,
        'currency': 'USD', 
        'description': '黄金标准'
    },
    'XAUUSD.cent': {
        'account_type': 'cent',
        'volume_divisor': 10000.0,
        'display_divisor': 100.0,         # 💰 美分账户需要/100
        'currency': 'USD',
        'description': '黄金美分'
    },
    # 未来扩展示例
    # 'EURUSD': {
    #     'account_type': 'standard',
    #     'volume_divisor': 100.0,
    #     'display_divisor': 1.0,
    #     'currency': 'USD',
    #     'description': '欧美标准'
    # }
}

def get_product_config(symbol: str) -> dict:
    """获取产品配置，如果不存在则返回默认配置"""
    return PRODUCT_CONFIGS.get(symbol, {
        'account_type': 'standard',
        'volume_divisor': 100.0,
        'display_divisor': 1.0,
        'currency': 'USD',
        'description': '标准产品'
    })

# 兼容性：保持原有的VOLUME_DIVISORS，从新配置中提取
VOLUME_DIVISORS = {
    symbol: config['volume_divisor'] 
    for symbol, config in PRODUCT_CONFIGS.items()
}
VOLUME_DIVISORS['__default__'] = 100.0  # 提供一个默认值


class PnlEtlService:
    """盈亏汇总ETL服务类"""
    
    def __init__(self):
        """初始化并建立数据库连接"""
        self.settings = get_settings()
        
        # MySQL配置 - 从环境变量加载
        self.mysql_config = {
            'host': os.getenv('MYSQL_HOST'),
            'user': os.getenv('MYSQL_USER'),
            'password': os.getenv('MYSQL_PASSWORD'),
            'database': os.getenv('MYSQL_DATABASE'),
            'ssl_ca': os.getenv('MYSQL_SSL_CA')
        }
        
        # PostgreSQL配置 - 从环境变量加载
        self.postgres_config = {
            'host': os.getenv('POSTGRES_HOST'),
            'user': os.getenv('POSTGRES_USER'),
            'password': os.getenv('POSTGRES_PASSWORD'),
            'dbname': os.getenv('POSTGRES_DBNAME')
        }
        
        # 数据库连接
        self.mysql_conn = None
        self.pg_conn = None
    
    def __enter__(self):
        """上下文管理器入口 - 建立数据库连接"""
        try:
            self.mysql_conn = mysql.connector.connect(**self.mysql_config)
            self.pg_conn = psycopg2.connect(**self.postgres_config)
            self.pg_conn.autocommit = False  # 手动控制事务
            return self
        except Exception as e:
            self._close_connections()
            raise Exception(f"数据库连接失败: {e}")
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口 - 关闭数据库连接"""
        self._close_connections()
    
    def _close_connections(self):
        """关闭所有数据库连接"""
        if self.mysql_conn and self.mysql_conn.is_connected():
            self.mysql_conn.close()
        if self.pg_conn:
            self.pg_conn.close()
    
    def _get_watermark(self, symbol: str) -> int:
        """从 PostgreSQL 获取指定 symbol 的水位线 (last_deal_id)"""
        with self.pg_conn.cursor() as cursor:
            cursor.execute("SELECT last_deal_id FROM etl_watermarks WHERE symbol = %s", (symbol,))
            result = cursor.fetchone()
            watermark = result[0] if result else 0
            logger.debug(f"🏁 查询水位线: symbol={symbol}, watermark={watermark}")
            return watermark
    
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
    
    def _get_extract_sql_template(self, is_incremental: bool) -> str:
        """构建ETL提取数据的SQL模板"""
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
            -- 🔥 修复：只有真正有新交易时才返回新的max_deal_id，否则返回NULL
            CASE 
                WHEN cds.max_deal_id IS NOT NULL AND cds.max_deal_id > %(last_deal_id)s 
                THEN cds.max_deal_id 
                ELSE NULL 
            END as max_deal_id
        FROM ActiveLogins al
        JOIN mt5_users u ON al.Login = u.Login
        LEFT JOIN ClosedDealsSummary cds ON al.Login = cds.Login
        LEFT JOIN OpenPositionsSummary ops ON al.Login = ops.Login;
        """
    
    def run_pnl_sync(self, symbol: str, mode: str = "incremental") -> EtlResult:
        """
        执行盈亏汇总ETL同步任务
        
        Args:
            symbol: 要处理的交易品种 (如 'XAUUSD.kcmc')
            mode: 'full' 或 'incremental' (默认增量)
            
        Returns:
            EtlResult: 包含详细执行结果的数据对象
        """
        start_time = datetime.now()
        logger.info(f"🚀 开始ETL同步: symbol={symbol}, mode={mode}")
        
        try:
            # 1. 准备查询参数
            last_deal_id = 0
            if mode == 'incremental':
                last_deal_id = self._get_watermark(symbol)
                logger.info(f"📊 获取水位线: symbol={symbol}, last_deal_id={last_deal_id}")
            
            divisor = VOLUME_DIVISORS.get(symbol, VOLUME_DIVISORS['__default__'])
            logger.info(f"⚙️  配置参数: divisor={divisor}")
            
            # 动态构建 SQL
            params = {
                'symbol': symbol,
                'divisor': divisor,
                'last_deal_id': last_deal_id
            }
            extract_sql = self._get_extract_sql_template(last_deal_id > 0)

            # 2. Extract - 从MySQL提取数据
            logger.info(f"🔍 开始从MySQL提取数据...")
            logger.info(f"🔍 查询条件: symbol={symbol}, last_deal_id>{last_deal_id}")
            
            with self.mysql_conn.cursor() as cursor:
                cursor.execute(extract_sql, params)
                data_to_load = cursor.fetchall()
                
                # 🔥 修复：正确计算新的最大Deal ID
                # 只考虑非NULL的max_deal_id，排除只有浮动盈亏变化的记录
                valid_deal_ids = [row[-1] for row in data_to_load if row[-1] is not None]
                new_max_deal_id = max(valid_deal_ids) if valid_deal_ids else last_deal_id
                
                # 区分真正的新交易数据和仅浮动盈亏变化的数据
                new_trades_count = len(valid_deal_ids)
                floating_only_count = len(data_to_load) - new_trades_count

            logger.info(f"📈 MySQL查询结果: 提取到 {len(data_to_load)} 行数据")
            logger.info(f"📊 数据分析: {new_trades_count}行新交易, {floating_only_count}行仅浮动盈亏变化")
            logger.info(f"🎯 new_max_deal_id={new_max_deal_id} (last_deal_id={last_deal_id})")
            
            # 调试信息：显示Deal ID范围
            if valid_deal_ids:
                min_deal_id = min(valid_deal_ids)
                logger.info(f"🔢 新交易Deal ID范围: {min_deal_id} ~ {new_max_deal_id} (共{len(set(valid_deal_ids))}个唯一Deal)")
            else:
                logger.info(f"🔢 无新交易数据，仅浮动盈亏更新")

            if not data_to_load:
                end_time = datetime.now()
                logger.info(f"✅ ETL完成: 无新数据需要处理 (耗时: {(end_time - start_time).total_seconds():.1f}秒)")
                return EtlResult(
                    success=True,
                    processed_rows=0,
                    new_max_deal_id=last_deal_id,
                    start_time=start_time,
                    end_time=end_time,
                    new_trades_count=0,
                    floating_only_count=0
                )

            # 3. Load - 加载到PostgreSQL
            logger.info(f"💾 开始加载数据到PostgreSQL...")
            with self.pg_conn.cursor() as cursor:
                if mode == 'full':
                    cursor.execute("DELETE FROM pnl_summary WHERE symbol = %s", (symbol,))
                    logger.info(f"🗑️  全量模式: 已删除 {symbol} 的现有数据")
                
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
                logger.info(f"📝 已执行UPSERT操作: {len(clean_data)} 行数据")

                # 4. 更新水位线
                if new_max_deal_id > last_deal_id:
                    self._update_watermark(symbol, new_max_deal_id)
                    logger.info(f"🔄 水位线已更新: {last_deal_id} → {new_max_deal_id}")
                else:
                    logger.info(f"⏸️  水位线无需更新: 保持 {last_deal_id}")

                self.pg_conn.commit()
                logger.info(f"✅ PostgreSQL事务已提交")

            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            logger.info(f"🎉 ETL成功完成: symbol={symbol}, 处理={len(data_to_load)}行, 耗时={duration:.1f}秒")
            
            return EtlResult(
                success=True,
                processed_rows=len(data_to_load),
                new_max_deal_id=new_max_deal_id,
                start_time=start_time,
                end_time=end_time,
                new_trades_count=new_trades_count,
                floating_only_count=floating_only_count
            )
            
        except Exception as e:
            self.pg_conn.rollback()
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            logger.error(f"❌ ETL执行失败: symbol={symbol}, 错误={str(e)}, 耗时={duration:.1f}秒")
            logger.error(f"📋 错误详情: {type(e).__name__}: {str(e)}")
            
            return EtlResult(
                success=False,
                processed_rows=0,
                new_max_deal_id=last_deal_id,
                start_time=start_time,
                end_time=end_time,
                error_message=str(e),
                new_trades_count=0,
                floating_only_count=0
            )


def run_pnl_etl_sync(symbol: str, mode: str = "incremental") -> EtlResult:
    """
    便利函数：执行盈亏汇总ETL同步
    
    这是一个封装函数，自动处理数据库连接的建立和关闭
    """
    with PnlEtlService() as etl_service:
        return etl_service.run_pnl_sync(symbol, mode)
