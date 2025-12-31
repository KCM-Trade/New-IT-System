# ClickHouse IB 旗下全量净入金汇总方案

## 1. 业务背景与需求变更
最初，“IB 净入金”被理解为客户直属上级 IB 的个人净入金。经过业务确认，该字段需调整为：**统计该 IB 旗下所有客户（包含 IB 自己）的历史累计净入金汇总。**

### 核心口径：
*   **计算公式**：`Net Deposit = SUM(deposit + withdrawal + ib withdrawal)`。
*   **金额换算**：如果 `currency == 'CEN'`，金额需除以 `100`，否则为 `1`。
*   **数据源**：
    *   事实表：`fxbackoffice_stats_transactions`（流水账）。
    *   关系表：`fxbackoffice_ib_tree_with_self`（存储 IB 及其所有下级 `referralId` 的闭包表）。

---

## 2. 架构设计：两层自动汇总 (Materialized Views)
为了确保前端页面在大规模查询时依然保持毫秒级响应，我们采用了 ClickHouse 的物化视图（Materialized View）方案，实现数据的**增量自动聚合**。

### 架构流程：
1.  **用户级汇总 (`user_net_deposit_agg`)**：自动累加每个 userId 的历史净入金状态。
2.  **IB 级汇总 (`ib_downline_net_deposit_agg`)**：在交易发生时，自动通过 JOIN 树表，将金额实时累加到其所有上级 IB 账号上。

---

## 3. 实施步骤 (SQL)

### 第一步：创建汇总存储表
使用 `AggregatingMergeTree` 引擎，存储二进制聚合状态（性能最高）。

```sql
-- 用户级汇总存储表
CREATE TABLE IF NOT EXISTS user_net_deposit_agg
(
  userId UInt64,
  net_deposit AggregateFunction(sum, Decimal(18, 4))
) ENGINE = AggregatingMergeTree() ORDER BY userId;

-- IB 下级全量汇总存储表
CREATE TABLE IF NOT EXISTS ib_downline_net_deposit_agg
(
  ibId UInt64,
  net_deposit AggregateFunction(sum, Decimal(18, 4))
) ENGINE = AggregatingMergeTree() ORDER BY ibId;
```

### 第二步：创建自动更新管道 (Materialized Views)
物化视图像“触发器”一样，每当事实表有新 `INSERT` 时，自动进行增量计算。

```sql
-- 用户级 MV
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_user_net_deposit_agg
TO user_net_deposit_agg
AS SELECT
  toUInt64(userId) AS userId,
  sumState(toDecimal64(amount / if(currency = 'CEN', 100.0, 1.0), 4)) AS net_deposit
FROM fxbackoffice_stats_transactions
WHERE type IN ('deposit', 'withdrawal', 'ib withdrawal')
GROUP BY userId;

-- IB 级 MV (核心：通过 referralId 关联到所有上级 ibId)
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_ib_downline_net_deposit_agg
TO ib_downline_net_deposit_agg
AS SELECT
  toUInt64(tree.ibId) AS ibId,
  sumState(toDecimal64(tx.amount / if(tx.currency = 'CEN', 100.0, 1.0), 4)) AS net_deposit
FROM fxbackoffice_stats_transactions AS tx
INNER JOIN fxbackoffice_ib_tree_with_self AS tree 
  ON toUInt64(tx.userId) = toUInt64(tree.referralId)
WHERE tx.type IN ('deposit', 'withdrawal', 'ib withdrawal')
GROUP BY ibId;
```

### 第三步：历史数据回填 (Backfill)
物化视图创建后仅对“新数据”有效。现有历史数据需要执行以下一次性回填：

```sql
-- 回填 IB 下级汇总数据
INSERT INTO ib_downline_net_deposit_agg
SELECT
  toUInt64(tree.ibId) AS ibId,
  sumState(toDecimal64(tx.amount / if(tx.currency = 'CEN', 100.0, 1.0), 4)) AS net_deposit
FROM fxbackoffice_stats_transactions AS tx
INNER JOIN fxbackoffice_ib_tree_with_self AS tree 
  ON toUInt64(tx.userId) = toUInt64(tree.referralId)
WHERE tx.type IN ('deposit', 'withdrawal', 'ib withdrawal')
GROUP BY ibId;
```

---

## 4. 查询与集成说明

### 如何查看结果 (解密乱码)
由于使用了 `AggregateFunction`，在查询时必须使用 `sumMerge` 函数来还原数值：

```sql
SELECT 
    ibId, 
    sumMerge(net_deposit) AS total_downline_pnl
FROM ib_downline_net_deposit_agg 
WHERE ibId = 100053
GROUP BY ibId;
```

### 后端集成 (clickhouse_service.py)
在主查询 SQL 中，通过 `partner_id` 与汇总表关联：

```sql
LEFT JOIN (
    SELECT 
        ibId, 
        sumMerge(net_deposit) AS net_deposit_usd
    FROM ib_downline_net_deposit_agg
    GROUP BY ibId
) AS ib_sum ON toString(u.partnerId) = toString(ib_sum.ibId)
```

---

## 5. 常见问题 (FAQ)

#### Q: 为什么在查询 `agg` 表时会看到“乱码”？
A: 这是 ClickHouse 的二进制聚合中间状态。请使用 `sumMerge()` 函数进行查询。

#### Q: 为什么 ClickHouse 提示“Fetching data ... can be expensive”？
A: 这是一个通用安全提示。因为 `AggregatingMergeTree` 需要在读取时进行 CPU 合并计算。但在本方案中，聚合后的 IB 数量远小于原始流水行数，因此实际性能比扫全表快得多。

#### Q: 如果 IB 树关系发生变化怎么办？
A: 物化视图是基于“交易发生时”的关系。如果 IB 关系发生大规模变动，建议执行 `TRUNCATE TABLE ib_downline_net_deposit_agg` 并重新跑一次第 3 步的回填 SQL。

