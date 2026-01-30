# IB 净入金汇总表方案 (IB Net Deposit Summary)

## 1. 数据来源与计算逻辑
汇总表 `ib_net_deposit_daily_summary` 的数据源自基础交易表 `fxbackoffice_stats_transactions`。

*   **过滤条件**: 仅统计 `type` 为 `ib withdrawal`, `deposit`, `withdrawal` 的记录。
*   **计算公式**: 
    *   `Net Deposit = SUM(amount / factor)`
    *   其中 `factor`: 如果 `currency == 'CEN'` 则为 `100`，否则为 `1`。
*   **聚合维度**: 按 `userId` (即 IB 的 ID) 进行汇总。

## 2. 表结构设计 (ClickHouse)
使用 `ReplacingMergeTree` 引擎，通过 `userId` 作为主键，确保每个 IB 只有一行最新的汇总数据。

```sql
-- Create the summary table
CREATE TABLE IF NOT EXISTS ib_net_deposit_daily_summary (
    userId UInt64,                          -- IB User ID
    net_deposit_usd Decimal(18, 4),         -- Calculated Net Deposit
    updated_at DateTime DEFAULT now()       -- Last update timestamp
) 
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY userId;
```

## 3. 数据维护方式

### 方案 A：手动/定时脚本更新 (推荐)
如果您的数据如前所述每天更新一次，可以使用以下脚本进行全量刷新。

**更新逻辑**:
1.  **清空旧数据**: `TRUNCATE TABLE ib_net_deposit_daily_summary;`
2.  **插入新数据**:
```sql
INSERT INTO ib_net_deposit_daily_summary (userId, net_deposit_usd, updated_at)
SELECT 
    userId,
    SUM(amount / IF(currency = 'CEN', 100, 1)) AS net_deposit_usd,
    now()
FROM fxbackoffice_stats_transactions
WHERE type IN ('ib withdrawal', 'deposit', 'withdrawal')
  AND userId > 0
GROUP BY userId;
```

### 方案 B：自动更新 (物化视图 Materialized View)
如果您希望 **`fxbackoffice_stats_transactions` 表一旦有新数据，汇总表就自动更新**，则需要使用 **物化视图**。

**物化视图原理**:
它像一个“触发器”。每当有新行写入原始表时，ClickHouse 会自动计算增量并更新到汇总表中。

```sql
-- Create Materialized View for automatic updates
CREATE MATERIALIZED VIEW IF NOT EXISTS ib_net_deposit_mv
TO ib_net_deposit_daily_summary
AS SELECT 
    userId,
    SUM(amount / IF(currency = 'CEN', 100, 1)) AS net_deposit_usd,
    now() as updated_at
FROM fxbackoffice_stats_transactions
WHERE type IN ('ib withdrawal', 'deposit', 'withdrawal')
GROUP BY userId;
```

## 4. 常见问题 (FAQ)

### Q: 如果 `fxbackoffice_stats_transactions` 更新，汇总表会自动更新吗？
*   **如果您采用方案 A (普通表 + 脚本)**：**不会**自动更新。您需要通过定时任务（如 Linux Cron Job 或 Airflow）每天运行一次插入脚本。
*   **如果您采用方案 B (物化视图)**：**会**自动更新。只要原始表有新数据插入，物化视图就会实时将计算后的增量累加到汇总表中。

### Q: 为什么建议先用方案 A？
虽然方案 B（自动更新）看起来更高级，但方案 A 有以下优势：
1.  **调试简单**: 逻辑清晰，手动运行 SQL 即可校验结果。
2.  **性能可控**: 每天只在低峰期运行一次，不会在白天交易高峰期占用数据库计算资源。
3.  **容错性强**: 如果原始表数据录入有误需要回滚，手动刷新汇总表非常方便。

## 5. 后端集成建议
在后端 `clickhouse_service.py` 中，只需将主查询与此表进行 `LEFT JOIN`，关联键为 `u.partnerId = ib_sum.userId`。由于汇总表数据量小（仅包含 IB 级别的行），查询性能将保持在毫秒级。

