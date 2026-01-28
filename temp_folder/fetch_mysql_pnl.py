import os
import pandas as pd
import mysql.connector
from dotenv import load_dotenv
from datetime import datetime

# 加载当前目录下的 .env 文件
env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path)

def fetch_data():
    # 1. 基础配置读取
    db_config = {
        'host': os.getenv('DB_HOST'),
        'user': os.getenv('DB_USER'),
        'password': os.getenv('DB_PASSWORD'),
        'port': int(os.getenv('DB_PORT', 3306)),
        'charset': os.getenv('DB_CHARSET', 'utf8mb4')
    }
    fxback_db = os.getenv('FXBACK_DB_NAME', 'fxbackoffice').replace("'", "").replace("\"", "").strip()
    
    print(f"--- 🚀 启动账号维度全指标聚合模式 [{datetime.now().strftime('%H:%M:%S')}] ---")
    
    try:
        conn = mysql.connector.connect(**db_config)
        
        # --- 步骤 1: 交易指标 (账号级别 - Account Level) ---
        print(f"🔍 [1/6] 正在提取账号交易指标 (mt4_trades)...")
        trades_sql = f"""
        SELECT 
            loginSid,
            COUNT(*) AS total_trades,
            SUM(lots) AS raw_volume,
            SUM(PROFIT) AS raw_profit,
            SUM(SWAPS) AS raw_swaps,
            SUM(COMMISSION) AS raw_comm
        FROM {fxback_db}.mt4_trades
        WHERE closeDate >= '2026-01-01' AND closeDate <= '2026-01-26'
          AND CMD IN (0, 1)
        GROUP BY loginSid
        """
        df_trades = pd.read_sql(trades_sql, conn)

        # --- 步骤 2: 账号元数据与客户 ID 映射 ---
        print(f"🔍 [2/6] 正在提取账号与客户映射关系 (mt4_users)...")
        users_sql = f"""
        SELECT 
            mu.loginSid,
            mu.LOGIN AS account,
            mu.userId AS client_id,
            mu.NAME AS client_name,
            mu.`GROUP` AS `group`,
            mu.ZIPCODE AS zipcode,
            mu.CURRENCY AS currency,
            mu.sid,
            u.partnerId AS partner_id,
            u.country,
            COALESCE(u.isEmployee, 0) AS is_employee
        FROM {fxback_db}.mt4_users mu
        LEFT JOIN {fxback_db}.users u ON mu.userId = u.id
        WHERE mu.userId > 0
        """
        df_users = pd.read_sql(users_sql, conn)

        # --- 步骤 3: 客户级别资金流水 (Client Level - 全量历史数据) ---
        print(f"🔍 [3/6] 正在提取客户全量历史资金流水 (stats_transactions)...")
        tx_sql = f"""
        SELECT 
            userId AS client_id,
            SUM(CASE WHEN type = 'deposit' THEN (CASE WHEN UPPER(currency) = 'CEN' THEN amount / 100.0 ELSE amount END) ELSE 0 END) AS deposits,
            SUM(CASE WHEN type = 'withdrawal' THEN (CASE WHEN UPPER(currency) = 'CEN' THEN amount / 100.0 ELSE amount END) ELSE 0 END) AS withdrawal,
            SUM(CASE WHEN type = 'ib withdrawal' THEN (CASE WHEN UPPER(currency) = 'CEN' THEN amount / 100.0 ELSE amount END) ELSE 0 END) AS ib_withdrawal
        FROM {fxback_db}.stats_transactions
        WHERE type IN ('deposit', 'withdrawal', 'ib withdrawal')
        GROUP BY userId
        """
        df_tx = pd.read_sql(tx_sql, conn)

        # --- 步骤 4: 客户级别资产快照 (Client Level) ---
        print(f"🔍 [4/6] 正在提取客户维度资产快照 (stats_balances)...")
        # IB 钱包的 loginSid 以 '2-' 开头，需单独统计
        bal_sql = f"""
        SELECT 
            userId AS client_id,
            SUM(CASE WHEN loginSid NOT LIKE '2-%' THEN (CASE WHEN UPPER(currency) = 'CEN' THEN endingBalance / 100.0 ELSE endingBalance END) ELSE 0 END) AS balance,
            SUM(CASE WHEN loginSid NOT LIKE '2-%' THEN (CASE WHEN UPPER(currency) = 'CEN' THEN endingEquity / 100.0 ELSE endingEquity END) ELSE 0 END) AS equity,
            SUM(CASE WHEN loginSid LIKE '2-%' THEN endingBalance ELSE 0 END) AS ib_wallet_balance
        FROM {fxback_db}.stats_balances
        WHERE date = '2026-01-26'
        GROUP BY userId
        """
        df_bal = pd.read_sql(bal_sql, conn)

        # --- 步骤 5: 内存大合并与逻辑计算 ---
        print(f"🧠 [5/6] 正在进行内存数据合并与指标计算...")
        
        # A. 合并账号交易数据与元数据
        df_acc = pd.merge(df_trades, df_users, on='loginSid', how='inner')
        
        # B. 转换账号级别 CEN 逻辑 (标准化为 USD)
        is_cent = (df_acc['currency'] == 'CEN')
        divisor = is_cent.map({True: 100.0, False: 1.0})
        df_acc['total_volume_lots'] = df_acc['raw_volume'] / divisor
        df_acc['trade_profit_usd'] = df_acc['raw_profit'] / divisor
        df_acc['swap_usd'] = df_acc['raw_swaps'] / divisor
        df_acc['commission_usd'] = df_acc['raw_comm'] / divisor

        # C. 排除员工账号
        df_acc = df_acc[df_acc['is_employee'] != 1].copy()

        # D. 关联客户维度的出入金和余额数据 (根据 client_id)
        # 注意：这里是 Left Join，意味着每个账号行都会带上所属客户的总出入金和总余额
        df_final = pd.merge(df_acc, df_tx, on='client_id', how='left')
        df_final = pd.merge(df_final, df_bal, on='client_id', how='left')

        # E. 计算派生指标
        df_final.fillna(0, inplace=True)
        # withdrawal 在 SQL 中已是负数，所以相加
        df_final['total_withdrawal'] = df_final['withdrawal'] + df_final['ib_withdrawal']
        df_final['net_deposit'] = df_final['deposits'] + df_final['total_withdrawal']
        
        # 计算 ROI 指标: (Equity + ABS(Total Withdrawal)) / Total Deposit
        # Exclusion: IB Wallet data is NOT included in equity/balance/return_multiplier calculation
        df_final['return_multiplier'] = (df_final['equity'] + df_final['total_withdrawal'].abs()) / df_final['deposits'].replace(0, float('nan'))

        # --- 步骤 6: 导出结果 ---
        filename = f"account_pnl_with_client_metrics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        filepath = os.path.join(os.path.dirname(__file__), filename)
        
        # 整理最终列顺序 (以账号分析为主，同时带上客户级别资金数据)
        cols = [
            'account', 'client_id', 'client_name', 'group', 'country', 'currency', 'sid',
            'trade_profit_usd', 'total_volume_lots', 'total_trades', 'swap_usd', 'commission_usd',
            'balance', 'equity', 'ib_wallet_balance', 'deposits', 'total_withdrawal', 'net_deposit', 'return_multiplier'
        ]
        
        # 按照账号盈亏排序 (从亏损到盈利)
        df_final[cols].sort_values(by='trade_profit_usd', ascending=True).to_csv(filepath, index=False, encoding='utf-8-sig')
        
        print(f"\n--- 🎉 数据汇总任务圆满完成 ---")
        print(f"📁 文件位置: {filepath}")
        print(f"📊 总账号数: {len(df_final)}")

    except Exception as e:
        print(f"❌ 运行中发生错误: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        if 'conn' in locals() and conn.is_connected():
            conn.close()
            print("🔌 数据库连接已安全关闭")

if __name__ == "__main__":
    fetch_data()
