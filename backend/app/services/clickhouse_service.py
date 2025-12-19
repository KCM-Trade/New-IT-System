import os
import pandas as pd
import clickhouse_connect
from typing import List, Dict, Any, Optional
from datetime import datetime
import dotenv

dotenv.load_dotenv()

class ClickHouseService:
    def __init__(self):
        # 优先读取环境变量
        self.host = os.getenv("CLICKHOUSE_HOST", "dwsz2tfd9y.ap-northeast-1.aws.clickhouse.cloud")
        self.port = int(os.getenv("CLICKHOUSE_PORT", "8443"))
        self.username = os.getenv("CLICKHOUSE_USER", "default")
        
        # 处理密码
        raw_password = os.getenv("CLICKHOUSE_PASSWORD")
        self.password = raw_password.strip() if raw_password else None
        
        self.database = os.getenv("CLICKHOUSE_DB", "Fxbo_Trades") 
        self.secure = True # 强制开启 TLS

        # [可选] 启动时尝试轻量连接测试 (打印日志但不阻断启动)
        try:
            # 仅用于测试连接是否通畅，不复用此 client
            with clickhouse_connect.get_client(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                secure=self.secure,
                database=self.database
            ) as client:
                client.command('SELECT 1')
            print("✅ ClickHouse Connection Established Successfully.")
        except Exception as e:
            print(f"⚠️ ClickHouse Connection Warning: {e}")

    def get_client(self):
        return clickhouse_connect.get_client(
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            database=self.database,
            secure=self.secure
        )

    def get_pnl_analysis(
        self, 
        start_date: datetime, 
        end_date: datetime, 
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Query ClickHouse for client PnL analysis within a date range.
        Returns a dict containing 'data' (list of records) and 'statistics' (query metadata).
        """
        try:
            client = self.get_client()
            
            # 格式化日期字符串
            start_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
            end_str = end_date.strftime('%Y-%m-%d %H:%M:%S')
            
            # 构建 SQL
            sql = f"""
            WITH 
                ib_costs AS (
                    SELECT 
                        ticketSid, 
                        sum(commission) AS total_ib_cost
                    FROM fxbackoffice_ib_processed_tickets
                    WHERE close_time >= %(start_date)s 
                      AND close_time <= %(end_date)s
                    GROUP BY ticketSid
                )
            SELECT
                t.LOGIN AS account,
                m.userId AS client_id,     
                any(m.NAME) AS client_name,
                any(m.GROUP) AS group,
                any(m.ZIPCODE) AS zipcode,
                any(m.CURRENCY) AS currency,
                any(m.sid) AS sid,
                'MT4' AS server,            
                
                countIf(t.CMD IN (0, 1)) AS total_trades,
                
                sumIf(t.lots, t.CMD IN (0, 1)) / if(any(m.CURRENCY) = 'CEN', 100, 1) AS total_volume_lots,
                
                sumIf(t.PROFIT, t.CMD IN (0, 1)) / if(any(m.CURRENCY) = 'CEN', 100, 1) AS trade_profit_usd,
                
                sumIf(t.SWAPS, t.CMD IN (0, 1)) / if(any(m.CURRENCY) = 'CEN', 100, 1) AS swap_usd,
                sumIf(t.COMMISSION, t.CMD IN (0, 1)) / if(any(m.CURRENCY) = 'CEN', 100, 1) AS commission_usd,
                
                COALESCE(sum(ib.total_ib_cost), 0) AS ib_commission_usd,
                
                ((sumIf(t.PROFIT + t.SWAPS + t.COMMISSION, t.CMD IN (0, 1)) * -1) / if(any(m.CURRENCY) = 'CEN', 100, 1)) - COALESCE(sum(ib.total_ib_cost), 0) AS broker_net_revenue,

                sumIf(t.PROFIT, t.CMD = 6) / if(any(m.CURRENCY) = 'CEN', 100, 1) AS period_net_deposit

            FROM fxbackoffice_mt4_trades AS t
            INNER JOIN fxbackoffice_mt4_users AS m ON t.LOGIN = m.LOGIN
            LEFT JOIN fxbackoffice_users AS u ON m.userId = u.id
            LEFT JOIN ib_costs AS ib ON t.ticketSid = ib.ticketSid
            WHERE 
                t.CLOSE_TIME >= %(start_date)s 
                AND t.CLOSE_TIME <= %(end_date)s
                AND t.CMD IN (0, 1, 6)
                
                AND m.userId > 0                   
                AND COALESCE(u.isEmployee, 0) != 1 
            """
            
            parameters = {
                'start_date': start_str,
                'end_date': end_str
            }

            if search:
                # 性能优化：仅支持 ClientID 或 AccountID 搜索 (前缀匹配)
                # 移除 Name 搜索，移除前导 % 以利用 ClickHouse 索引优化
                clean_search = search.strip()
                if clean_search:
                    sql += " AND (toString(m.userId) LIKE %(search)s OR toString(t.LOGIN) LIKE %(search)s)"
                    parameters['search'] = f"{clean_search}%"

            sql += """
            GROUP BY t.LOGIN, m.userId
            HAVING total_volume_lots > 0 OR period_net_deposit != 0
            ORDER BY ib_commission_usd DESC
            """

            # 使用 client.query 获取包含 summary 的结果
            result = client.query(sql, parameters=parameters)
            
            # 获取列名
            columns = result.column_names
            
            # 转换为字典列表
            data = [dict(zip(columns, row)) for row in result.result_set]
            
            # 安全获取 elapsed 时间 (优先使用纳秒)
            elapsed_ns = result.summary.get('elapsed_ns', 0)
            if elapsed_ns:
                elapsed_seconds = float(elapsed_ns) / 1_000_000_000
            else:
                # Fallback: 某些旧版本驱动可能只有 elapsed (秒)
                elapsed_seconds = result.summary.get('elapsed', 0)

            # 如果没有数据，直接返回
            if not data:
                return {
                    "data": [], 
                    "statistics": {
                        "elapsed": elapsed_seconds,
                        "rows_read": result.summary.get('read_rows', 0),
                        "bytes_read": result.summary.get('read_bytes', 0)
                    }
                }

            # 使用 Pandas 处理数据清洗 (fillna 等)
            df = pd.DataFrame(data)
            df = df.fillna(0)
            
            processed_data = df.to_dict('records')

            return {
                "data": processed_data,
                "statistics": {
                    "elapsed": elapsed_seconds,
                    "rows_read": result.summary.get('read_rows', 0),
                    "bytes_read": result.summary.get('read_bytes', 0)
                }
            }
            
        except Exception as e:
            print(f"ClickHouse Query Error: {e}")
            import traceback
            traceback.print_exc()
            return {"data": [], "statistics": {}}

clickhouse_service = ClickHouseService()
