# MT5 数据库核心表结构与查询优化指南

本文档旨在阐明 MetaTrader 5 (MT5) 数据库中核心交易表的结构、关系，并提供针对自定义报表查询的性能优化建议。

## 一、核心交易表关系总览 (The Shopping Analogy)

理解 `orders`, `orders_history`, `deals`, 和 `positions` 这四个表是理解整个 MT5 交易流水的基石。我们可以用一个网上购物的例子来类比：

-   **`mt5_orders` (购物车 - 实时指令)**: 存放当前有效、但还未执行的指令，主要是挂单 (Pending Orders)。这是一个动态、临时的表。
-   **`mt5_orders_history` (订单历史)**: 存档所有已经终结的指令，无论是成交、取消还是过期。这是一个只增不减的历史记录表。
-   **`mt5_deals` (银行账单/收据)**: 记录**每一笔实际发生的资金或仓位变动**。这是计算真实盈亏的**唯一真相来源 (Source of Truth)**，是不可更改的金融级流水账。
-   **`mt5_positions` (实时库存/持仓)**: 提供一个实时的快照，显示账户**当前持有**的净头寸及其**浮动盈亏 (Floating PnL)**。这是一个高度易变的实时状态表。

### 交易生命周期示例

1.  **下单**: 在 `mt5_orders` 中出现一条挂单记录。
2.  **成交**:
    -   `mt5_orders` 中的记录被移除。
    -   `mt5_orders_history` 中增加一条“已成交”的记录。
    -   `mt5_deals` 中产生一笔**成交记录 (Deal)**。
    -   `mt5_positions` 中出现或更新一条**持仓记录 (Position)**。
3.  **平仓**:
    -   `mt5_deals` 中再产生一笔**平仓的成交记录**。
    -   `mt5_positions` 中的持仓记录被移除或更新。

---

## 二、`mt5_deals` 表字段详解

这是所有分析和报表的核心。

### `Action` 列 (成交类型)

对应 `ENUM_DEAL_TYPE`，表示这笔成交是什么性质的金融活动。

| 值  | 枚举名                | 中文解释       |
| :-- | :-------------------- | :------------- |
| 0   | `DEAL_BUY`            | 买入成交       |
| 1   | `DEAL_SELL`           | 卖出成交       |
| 2   | `DEAL_BALANCE`        | 余额变动(入/出金) |
| 3   | `DEAL_CREDIT`         | 信用           |
| 4   | `DEAL_CHARGE`         | 费用/手续费    |
| 5   | `DEAL_CORRECTION`     | 更正           |
| 6   | `DEAL_BONUS`          | 奖金           |
| 7   | `DEAL_COMMISSION`     | 佣金           |
| 8   | `DEAL_COMMISSION_DAILY` | 每日佣金     |
| 9   | `DEAL_COMMISSION_MONTHLY` | 每月佣金   |
| 12  | `DEAL_INTEREST`       | 利息           |
| 15  | `DEAL_DIVIDEND`       | 红利           |
| 17  | `DEAL_TAX`            | 税费           |

### `Entry` 列 (出入场类型)

对应 `ENUM_DEAL_ENTRY`，描述成交对持仓的作用。

| 值  | 枚举名             | 中文解释                               | 适用模式      |
| :-- | :----------------- | :------------------------------------- | :------------ |
| 0   | `ENTRY_IN`         | **进场** (开仓或加仓)                  | 通用          |
| 1   | `ENTRY_OUT`        | **出场** (平仓或减仓)                  | 通用          |
| 2   | `ENTRY_INOUT`      | **反转** (平掉旧仓并开反向新仓)        | Netting (单边净值) |
| 3   | `ENTRY_OUT_BY`     | **对锁平仓** (两个相反仓位互相抵消)    | Hedging (锁仓) |

**注意**: 在统计已平仓交易时，应同时包含 `entry IN (1, 3)` 以确保数据完整。

### 其他重要字段

-   `Deal`: 成交的唯一ID (主键)。
-   `Login`: 交易账户号。
-   `Symbol`: 交易品种代码。
-   `Profit`: 该笔成交的**已实现盈亏** (以账户货币计)。
-   `Volume`: 成交量 (通常是 `手数 * 100` 或更高精度，需要换算)。
-   `PositionID`: 关联的持仓ID。一整个持仓生命周期中的所有相关Deals共享同一个PositionID。
-   `Timestamp`: Unix时间戳 (秒)，**有索引**，适合高性能时间范围查询。
-   `Time`: `yyyy-mm-dd hh:mm:ss` 格式的时间，默认**无索引**，不适合直接用于范围查询。

---

## 三、数据库性能与索引

### 为什么查询慢？

当 `WHERE` 或 `ORDER BY` 子句中使用的列没有索引时，数据库必须执行**全表扫描 (Full Table Scan)**，即从头到尾检查每一行数据，导致性能急剧下降。

### 如何查看现有索引

使用以下命令可以查看表上已创建的所有索引：

```sql
SHOW INDEX FROM mt5_live.mt5_deals;
```

默认索引通常包括 `Deal` (主键), `Timestamp`, `Login`, `PositionID`。这些索引服务于MT5平台的核心功能，但**不一定能满足自定义的报表需求**。

### 如何创建优化索引

为了高效地查询特定品种的交易数据，**强烈建议**创建一个复合索引。

```sql
-- 这个索引能极大地提升按品种、按出入场类型筛选并按时间排序的查询速度
CREATE INDEX idx_deals_symbol_entry_time 
ON mt5_live.mt5_deals (symbol, entry, time);
```

**好处**:
1.  **极速定位**: 瞬间找到特定 `symbol` 和 `entry` 的数据。
2.  **避免排序**: 由于索引本身按 `time` 有序，`ORDER BY time` 的查询无需额外计算。
3.  **覆盖索引**: 如果查询的列都包含在索引中，数据库甚至无需读取主表数据，达到最高性能。

### 时间范围查询的最佳实践

**优先使用已有索引的 `timestamp` 列**进行时间范围查询，以获得最佳性能。

**高效查询 (推荐)**:
```sql
-- 将 'yyyy-mm-dd' 时间转换为 Unix 时间戳后再查询
SELECT * FROM mt5_live.mt5_deals
WHERE timestamp BETWEEN 1698249600 AND 1698335999;
```

**低效查询 (不推荐，除非给 `time` 列也加上索引)**:
```sql
SELECT * FROM mt5_live.mt5_deals
WHERE time BETWEEN '2023-10-26 00:00:00' AND '2023-10-26 23:59:59';
```

---

## 四、实用 SQL 查询示例

以下 SQL 用于生成指定品种 (以 `XAUUSD.kcmc` 为例) 的**已平仓交易汇总**及**当前浮动盈亏**报表。

```sql
-- 使用 WITH 子句 (CTE) 使逻辑更清晰

-- 第一部分：计算 XAUUSD.kcmc 已平仓交易的各项指标
WITH ClosedDealsSummary AS (
    SELECT
        Login,
        COUNT(Deal) AS total_closed_trades,
        -- 使用 SUM + CASE WHEN 做条件计数
        SUM(CASE WHEN Action = 0 THEN 1 ELSE 0 END) AS buy_trades_count,
        SUM(CASE WHEN Action = 1 THEN 1 ELSE 0 END) AS sell_trades_count,
        SUM(Profit) AS total_closed_pnl -- 累计已平仓利润
    FROM
        mt5_live.mt5_deals
    WHERE
        symbol = 'XAUUSD.kcmc' AND entry IN (1, 3) -- 包含所有平仓类型
    GROUP BY
        Login
),
-- 第二部分：获取 XAUUSD.kcmc 当前持仓的浮动盈亏
-- !!! 假设实时持仓表名为 'mt5_positions'
OpenPositionsSummary AS (
    SELECT
        Login,
        SUM(Profit) AS floating_pnl -- 在持仓表中, Profit 通常代表浮动盈亏
    FROM
        mt5_live.mt5_positions
    WHERE
        symbol = 'XAUUSD.kcmc'
    GROUP BY
        Login
)
-- 最后，将两部分数据通过 Login 左连接（LEFT JOIN）起来
SELECT
    cds.Login,
    cds.total_closed_trades,
    cds.buy_trades_count,
    cds.sell_trades_count,
    cds.total_closed_pnl,
    -- 如果该账户没有持仓，则浮动盈亏为0
    COALESCE(ops.floating_pnl, 0) AS floating_pnl
FROM
    ClosedDealsSummary cds
LEFT JOIN
    OpenPositionsSummary ops ON cds.Login = ops.Login
ORDER BY
    total_closed_pnl DESC;
```
