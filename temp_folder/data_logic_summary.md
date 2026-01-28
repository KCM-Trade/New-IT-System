# 数据抓取与逻辑分析汇总 (2026-01-26)

## 1. 导出 CSV 字段定义 (Data Dictionary)

| 字段名 (CSV Column)   | 定义                   | 计算逻辑 / 来源                                    |
| :-------------------- | :--------------------- | :------------------------------------------------- |
| **account**           | MT4 账号 ID            | `mt4_trades.LOGIN`                                 |
| **client_id**         | 客户唯一识别码         | `mt4_users.userId` (CRM 关联 ID)                   |
| **client_name**       | 客户姓名               | `mt4_users.NAME`                                   |
| **group**             | 账户组别               | `mt4_users.GROUP`                                  |
| **country**           | 所属国家               | `fxbackoffice.users.country`                       |
| **currency**          | 账户货币               | `USD` 或 `CEN` (美分)                              |
| **trade_profit_usd**  | **本月**平仓盈亏       | `SUM(PROFIT)`，CEN 账户自动除以 100                |
| **total_volume_lots** | **本月**交易手数       | `SUM(lots)`，CEN 账户自动除以 100                  |
| **total_trades**      | **本月**交易单数       | `COUNT(*)` 仅统计 CMD 0 和 1                       |
| **balance**           | 客户**总余额**         | `stats_balances` 1月26日快照，多账号已累加         |
| **equity**            | 客户**总净值**         | `stats_balances` 1月26日快照，多账号已累加         |
| **deposits**          | **历史累计**总入金     | `stats_transactions` 中所有 `deposit` 类型总和     |
| **total_withdrawal**  | **历史累计**总出金     | `withdrawal` + `ib withdrawal` (注意：数值为负数)  |
| **net_deposit**       | **历史累计**净入金     | `deposits + total_withdrawal` (反映资金净留存)     |
| **return_multiplier** | **资产回报倍数 (ROI)** | `(Equity + ABS(Total Withdrawal)) / Total Deposit` |

---

## 2. 后端核心计算逻辑

为了确保大数据量下的查询性能，采用了 **“Python 高性能拆分查询模式”**：

### 2.1 交易指标 (账号维度)

- **SQL**: `SELECT loginSid, COUNT(*), SUM(lots), SUM(PROFIT) FROM mt4_trades WHERE closeDate BETWEEN '2026-01-01' AND '2026-01-26' AND CMD IN (0, 1) GROUP BY loginSid`
- **处理**: 在 Python 中根据 `currency` 字段对 `PROFIT` 和 `VOLUME` 进行标准化处理。

### 2.2 资金流水 (客户维度 - 全量历史)

- **SQL**: `SELECT userId, SUM(amount) FROM stats_transactions WHERE type IN ('deposit', 'withdrawal', 'ib withdrawal') GROUP BY userId`
- **逻辑**: 移除了时间范围限制，确保计算 `return_multiplier` 时使用的是客户完整的历史成本基准。

### 2.3 资产快照 (客户维度)

- **SQL**: `SELECT userId, SUM(endingBalance), SUM(endingEquity) FROM stats_balances WHERE date = '2026-01-26' GROUP BY userId`


---

**脚本位置**: `temp_folder/fetch_mysql_pnl.py`
**输出 CSV**: `temp_folder/account_pnl_with_client_metrics_*.csv`
