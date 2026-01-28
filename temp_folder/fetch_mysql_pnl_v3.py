import os
from datetime import datetime

import mysql.connector
import pandas as pd
from dotenv import load_dotenv

# Load .env from current folder
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)


def fetch_data():
    # Basic DB config
    db_config = {
        "host": os.getenv("DB_HOST"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "charset": os.getenv("DB_CHARSET", "utf8mb4"),
    }
    fxback_db = (
        os.getenv("FXBACK_DB_NAME", "fxbackoffice")
        .replace("'", "")
        .replace('"', "")
        .strip()
    )

    today = datetime.now().date()
    month_start = today.replace(day=1)
    today_str = today.strftime("%Y-%m-%d")
    month_start_str = month_start.strftime("%Y-%m-%d")

    print(
        f"--- 🚀 启动客户维度收益率分析V3 [{datetime.now().strftime('%H:%M:%S')}] ---"
    )

    try:
        conn = mysql.connector.connect(**db_config)

        # --- Step 1: Current equity from mt4_users (real-time, exclude IB wallet) ---
        print("🔍 [1/6] 正在提取客户实时净值 (mt4_users)...")
        equity_sql = f"""
        SELECT
            userId AS client_id,
            SUM(CASE WHEN UPPER(currency) = 'CEN' THEN equity / 100.0 ELSE equity END) AS equity
        FROM {fxback_db}.mt4_users
        WHERE userId > 0
          AND sid IN (1, 5, 6)
        GROUP BY userId
        """
        df_equity = pd.read_sql(equity_sql, conn)

        # --- Step 2: Deposit stats (history, deposit only) ---
        print("🔍 [2/6] 正在提取客户历史入金统计 (stats_transactions)...")
        deposit_sql = f"""
        SELECT
            userId AS client_id,
            SUM(CASE WHEN type = 'deposit' THEN COALESCE(countTransactions, 0) ELSE 0 END) AS deposit_count,
            SUM(CASE WHEN UPPER(currency) = 'CEN' THEN amount / 100.0 ELSE amount END) AS deposit_sum,
            CASE
                WHEN SUM(CASE WHEN type = 'deposit' THEN COALESCE(countTransactions, 0) ELSE 0 END) = 0 THEN 0
                ELSE SUM(CASE WHEN UPPER(currency) = 'CEN' THEN amount / 100.0 ELSE amount END)
                     / SUM(CASE WHEN type = 'deposit' THEN COALESCE(countTransactions, 0) ELSE 0 END)
            END AS deposit_avg
        FROM {fxback_db}.stats_transactions
        WHERE type = 'deposit'
          AND LEFT(loginSid, 2) != '2-'
        GROUP BY userId
        """
        df_deposit = pd.read_sql(deposit_sql, conn)

        # --- Step 3: Net deposit (history) ---
        print("🔍 [3/6] 正在提取客户历史净入金 (stats_transactions)...")
        tx_hist_sql = f"""
        SELECT 
            userId AS client_id,
            SUM(CASE WHEN type = 'deposit' THEN (CASE WHEN UPPER(currency) = 'CEN' THEN amount / 100.0 ELSE amount END) ELSE 0 END) AS deposits_hist,
            SUM(CASE WHEN type = 'withdrawal' THEN (CASE WHEN UPPER(currency) = 'CEN' THEN amount / 100.0 ELSE amount END) ELSE 0 END) AS withdrawals_hist
        FROM {fxback_db}.stats_transactions
        WHERE type IN ('deposit', 'withdrawal')
          AND LEFT(loginSid, 2) != '2-'
        GROUP BY userId
        """
        df_tx_hist = pd.read_sql(tx_hist_sql, conn)

        # --- Step 4: Net deposit (current month) ---
        print("🔍 [4/6] 正在提取客户当月净入金 (stats_transactions)...")
        tx_month_sql = f"""
        SELECT 
            userId AS client_id,
            SUM(CASE WHEN type = 'deposit' THEN (CASE WHEN UPPER(currency) = 'CEN' THEN amount / 100.0 ELSE amount END) ELSE 0 END) AS deposits_month,
            SUM(CASE WHEN type = 'withdrawal' THEN (CASE WHEN UPPER(currency) = 'CEN' THEN amount / 100.0 ELSE amount END) ELSE 0 END) AS withdrawals_month
        FROM {fxback_db}.stats_transactions
        WHERE type IN ('deposit', 'withdrawal')
          AND LEFT(loginSid, 2) != '2-'
          AND date >= '{month_start_str}' AND date <= '{today_str}'
        GROUP BY userId
        """
        df_tx_month = pd.read_sql(tx_month_sql, conn)

        # --- Step 5: Month trade profit (client-level, CEN adjusted) ---
        print("🔍 [5/6] 正在提取客户当月交易利润 (mt4_trades)...")
        trade_month_sql = f"""
        SELECT
            mu.userId AS client_id,
            SUM(CASE WHEN mu.CURRENCY = 'CEN' THEN t.PROFIT / 100.0 ELSE t.PROFIT END) AS month_trade_profit
        FROM {fxback_db}.mt4_trades t
        INNER JOIN {fxback_db}.mt4_users mu ON t.loginSid = mu.loginSid
        WHERE t.closeDate >= '{month_start_str}' AND t.closeDate <= '{today_str}'
          AND t.CMD IN (0, 1)
          AND mu.userId > 0
          AND mu.sid IN (1, 5, 6)
        GROUP BY mu.userId
        """
        df_trade_month = pd.read_sql(trade_month_sql, conn)

        # --- Step 6: Employee filter (isEmployee = 0) ---
        print("🔍 [6/7] 正在提取非员工客户 (users)...")
        employee_sql = f"""
        SELECT
            id AS client_id,
            COALESCE(isEmployee, 0) AS is_employee
        FROM {fxback_db}.users
        WHERE COALESCE(isEmployee, 0) = 0
        """
        df_employee = pd.read_sql(employee_sql, conn)

        # --- Step 7: Merge and compute metrics ---
        print("🧠 [7/7] 正在进行内存数据合并与指标计算...")
        # Base on active traders only (clients with trades this month)
        df_final = pd.merge(df_trade_month, df_equity, on="client_id", how="left")
        df_final = pd.merge(df_final, df_tx_hist, on="client_id", how="left")
        df_final = pd.merge(df_final, df_tx_month, on="client_id", how="left")
        df_final = pd.merge(df_final, df_deposit, on="client_id", how="left")
        df_final = pd.merge(df_final, df_employee, on="client_id", how="inner")
        df_final.fillna(0, inplace=True)

        df_final["net_deposit_hist"] = (
            df_final["deposits_hist"] + df_final["withdrawals_hist"]
        )
        df_final["net_deposit_month"] = (
            df_final["deposits_month"] + df_final["withdrawals_month"]
        )
        df_final["profit_hist"] = df_final["equity"] - df_final["net_deposit_hist"]

        # Bucket by average deposit amount
        def bucket_deposit_avg(value):
            if value < 2000:
                return "0-2000"
            if value < 5000:
                return "2000-5000"
            if value < 50000:
                return "5000-50000"
            return "50000+"

        df_final["deposit_avg_bucket"] = df_final["deposit_avg"].apply(
            bucket_deposit_avg
        )

        # K mapping for adjusted return
        def bucket_to_k(bucket):
            if bucket == "0-2000":
                return 2000
            if bucket == "2000-5000":
                return 5000
            if bucket == "5000-50000":
                return 50000
            return 60000

        df_final["k_base"] = df_final["deposit_avg_bucket"].apply(bucket_to_k)

        # Return metrics:
        # - Non-adjusted: (equity - net_deposit_hist) / net_deposit_hist
        # - Adjusted (net_deposit_hist <= 0): equity / K
        def non_adjusted_return(row):
            if row["net_deposit_hist"] > 0:
                return (
                    row["equity"] - row["net_deposit_hist"]
                ) / row["net_deposit_hist"] * 100
            return None

        def adjusted_return(row):
            if row["net_deposit_hist"] <= 0:
                return row["equity"] / row["k_base"] * 100
            return None

        df_final["return_non_adjusted"] = df_final.apply(non_adjusted_return, axis=1)
        df_final["return_adjusted"] = df_final.apply(adjusted_return, axis=1)

        # Only fill one adjusted column based on bucket
        df_final["adj_0_2000"] = df_final.apply(
            lambda r: r["return_adjusted"]
            if r["deposit_avg_bucket"] == "0-2000"
            else None,
            axis=1,
        )
        df_final["adj_2000_5000"] = df_final.apply(
            lambda r: r["return_adjusted"]
            if r["deposit_avg_bucket"] == "2000-5000"
            else None,
            axis=1,
        )
        df_final["adj_5000_50000"] = df_final.apply(
            lambda r: r["return_adjusted"]
            if r["deposit_avg_bucket"] == "5000-50000"
            else None,
            axis=1,
        )
        df_final["adj_50000_plus"] = df_final.apply(
            lambda r: r["return_adjusted"]
            if r["deposit_avg_bucket"] == "50000+"
            else None,
            axis=1,
        )

        filename = f"account_pnl_with_client_metrics_v3_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        filepath = os.path.join(os.path.dirname(__file__), filename)

        cols = [
            "client_id",
            "net_deposit_hist",
            "net_deposit_month",
            "equity",
            "profit_hist",
            "month_trade_profit",
            "adj_0_2000",
            "adj_2000_5000",
            "adj_5000_50000",
            "adj_50000_plus",
            "return_non_adjusted",
        ]

        chinese_columns = {
            "client_id": "客户ID",
            "net_deposit_hist": "历史净入金",
            "net_deposit_month": "当月净入金",
            "equity": "现时账户余额",
            "profit_hist": "历史利润",
            "month_trade_profit": "本月利润",
            "adj_0_2000": "调整后收益率(2K以下)%",
            "adj_2000_5000": "调整后收益率(2K-5K)%",
            "adj_5000_50000": "调整后收益率(5K-50K)%",
            "adj_50000_plus": "调整后收益率(50K以上)%",
            "return_non_adjusted": "非调整收益率%",
        }

        df_final[cols].rename(columns=chinese_columns).to_csv(
            filepath, index=False, encoding="utf-8-sig"
        )

        print("\n--- 🎉 数据汇总任务圆满完成 ---")
        print(f"📁 文件位置: {filepath}")
        print(f"📊 总客户数: {len(df_final)}")

    except Exception as e:
        print(f"❌ 运行中发生错误: {str(e)}")
        import traceback

        traceback.print_exc()
    finally:
        if "conn" in locals() and conn.is_connected():
            conn.close()
            print("🔌 数据库连接已安全关闭")


if __name__ == "__main__":
    fetch_data()
