

十、Client PnL（pnl_client_accounts / pnl_client_summary）与刷新脚本

1) 表结构（Schema）

```sql
-- 账户级（Account-level）
CREATE TABLE IF NOT EXISTS public.pnl_client_accounts (
  client_id              BIGINT NOT NULL,
  login                  BIGINT NOT NULL,
  server                 TEXT   NOT NULL,   -- 'MT5' / 'MT4Live2'
  currency               TEXT,
  user_name              TEXT,
  user_group             TEXT,
  country                TEXT,

  balance_usd            NUMERIC(20,4) NOT NULL DEFAULT 0,
  equity_usd             NUMERIC(20,4) NOT NULL DEFAULT 0,
  floating_pnl_usd       NUMERIC(20,4) NOT NULL DEFAULT 0,
  closed_profit_usd      NUMERIC(20,4) NOT NULL DEFAULT 0,
  commission_usd         NUMERIC(20,4) NOT NULL DEFAULT 0,
  deposit_usd            NUMERIC(20,4) NOT NULL DEFAULT 0,
  withdrawal_usd         NUMERIC(20,4) NOT NULL DEFAULT 0,
  volume_lots            NUMERIC(20,4) NOT NULL DEFAULT 0,
  overnight_volume_lots  NUMERIC(20,4) NOT NULL DEFAULT 0,

  auto_swap_free_status  NUMERIC(6,4)  NOT NULL DEFAULT -1.0000, -- volume=0 → -1，否则 0~1
  last_updated           TIMESTAMPTZ   NOT NULL,                  -- 来自源表 max(last_updated)

  PRIMARY KEY (client_id, login, server)
);
```

```sql
-- 客户级（Client-level）
CREATE TABLE IF NOT EXISTS public.pnl_client_summary (
  client_id                   BIGINT PRIMARY KEY,
  client_name                 TEXT,
  zipcode                     TEXT,
  is_enabled                  SMALLINT NOT NULL DEFAULT 1,        -- 0/1，来自 MySQL fxbackoffice.users

  total_balance_usd           NUMERIC(20,4) NOT NULL DEFAULT 0,
  total_equity_usd            NUMERIC(20,4) NOT NULL DEFAULT 0,
  total_floating_pnl_usd      NUMERIC(20,4) NOT NULL DEFAULT 0,
  total_closed_profit_usd     NUMERIC(20,4) NOT NULL DEFAULT 0,
  total_commission_usd        NUMERIC(20,4) NOT NULL DEFAULT 0,
  total_deposit_usd           NUMERIC(20,4) NOT NULL DEFAULT 0,
  total_withdrawal_usd        NUMERIC(20,4) NOT NULL DEFAULT 0,
  total_volume_lots           NUMERIC(20,4) NOT NULL DEFAULT 0,
  total_overnight_volume_lots NUMERIC(20,4) NOT NULL DEFAULT 0,

  auto_swap_free_status       NUMERIC(6,4)  NOT NULL DEFAULT -1.0000, -- total_volume=0 → -1，否则 0~1
  account_count               INTEGER       NOT NULL DEFAULT 0,
  last_updated                TIMESTAMPTZ   NOT NULL                 -- 本 client 账户层的 MAX(last_updated)
);
```

2) 字段来源与计算规则
- 统一精度：所有 USD 金额与 lots 手数保留 4 位小数；CEN 币种上游未统一时在脚本中 ÷100
- 账户级（来自 public.pnl_user_summary 和 public.pnl_user_summary_mt4live2）
  - balance/equity/floating/closed_profit/commission/deposit/withdrawal ← 源字段（CEN÷100）
  - volume_lots = closed_sell_volume_lots + closed_buy_volume_lots（CEN÷100）
  - overnight_volume_lots = closed_sell_overnight_volume_lots + closed_buy_overnight_volume_lots（CEN÷100）
  - auto_swap_free_status = CASE WHEN volume=0 THEN -1 ELSE 1 - (overnight/total) END
  - last_updated ← 该账户在源表的 MAX(last_updated)
- 客户级（由账户级聚合）
  - total_* 指标 = 账户级对应字段 SUM；account_count = COUNT(DISTINCT (login, server))
  - auto_swap_free_status = CASE WHEN total_volume=0 THEN -1 ELSE 1 - (total_overnight/total) END（注意“总量比”）
  - zipcode, is_enabled ← MySQL fxbackoffice.users(id=client_id)
  - last_updated = 本 client 账户 last_updated 的 MAX

3) 脚本：全量加载（backend/full_load_client_pnl.py）
- 读取环境变量：
  - MySQL: MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_PORT, MYSQL_DATABASE_FXBACKOFFICE
  - Postgres: POSTGRES_HOST, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_PORT, POSTGRES_DBNAME_MT5
- 流程：
  - 清空目标表（TRUNCATE）
  - 从两源表聚合写入 pnl_client_accounts（含 CEN 转换与账户级 auto_swap_free_status）
  - 从 MySQL 拉取 zipcode / isEnabled，装载临时表
  - 从账户表聚合写入 pnl_client_summary（客户级 auto_swap_free_status）
  - 成功提交后，更新 etl_watermarks：dataset='pnl_client', partition_key='all', last_updated=账户层 MAX(last_updated)
- 终端输出：client 数、account 数、max last_updated、运行时间
- 运行示例：
  - python backend/full_load_client_pnl.py

4) 脚本：增量刷新（backend/incremental_refresh_client_pnl.py）
- 候选集（仅处理候选 client）：
  - missing：源表存在但 summary 缺失的 client_id
  - lag：按 client 粒度比较 src_max_vs_summary：
    - src_max = 两源表对每个 user_id 的 MAX(last_updated)
    - 若 src_max > pnl_client_summary.last_updated，则该 client 需要刷新
- 刷新步骤：
  - 仅针对候选集 UPSERT 账户层与客户层；若候选=0，直接退出且不访问 MySQL 映射
  - 候选范围内清理孤儿账户（源无该 login/server）
  - 统计与监控：missing/lag 数、UPSERT 影响行数、zipcode 变化数量与前 20 条明细、各步骤耗时
- 环境变量：INCR_SOURCE_DATASETS（默认 'pnl_user_summary,pnl_user_summary_mt4live2'，用于读取源水位以供参考）
- 运行示例：
  - python backend/incremental_refresh_client_pnl.py

5) 性能与实践建议
- 避免“全局水位与目标对比”导致的伪增量；以“按 client 的 src_max 与 summary.last_updated”判定真正变化
- 仅为候选 client 拉取 MySQL 映射；批量 IN 建议 1000~5000/批，确保 users.id 有主键/索引
- 聚合/写入一律 UPSERT 幂等；失败回滚事务以便安全重试
- 输出耗时拆分（watermark/candidates/accounts/delete_orphans/mapping/summary/stats/total），便于定位瓶颈
