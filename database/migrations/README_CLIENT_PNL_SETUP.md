# ClientID 盈亏监控表创建和初始化指南

## 概述

本文档提供 ClientID 盈亏监控功能的数据库部署步骤。该功能按 `client_id` 聚合账户数据，统一货币单位为美元，支持自动触发更新。

---

## 前置条件

- PostgreSQL 数据库：`MT5_ETL`
- 现有表：`public.pnl_user_summary` 和 `public.pnl_user_summary_mt4live2`
- 数据库用户权限：CREATE TABLE、CREATE FUNCTION、CREATE TRIGGER
- 执行工具：psql 或任何 PostgreSQL 客户端

---

## 文件清单

| 文件名 | 用途 | 执行时间 |
|--------|------|----------|
| `01_create_client_pnl_tables.sql` | 创建两个新表及索引 | ~1秒 |
| `02_create_core_functions.sql` | 创建核心函数（刷新/初始化/对比） | ~2秒 |
| `03_create_triggers.sql` | 创建触发器自动更新 | ~1秒 |
| `04_initialize_and_test.sql` | 初始化历史数据并测试 | 10-30秒 |

---

## 执行步骤

### 步骤 1：连接数据库

```bash
# 方式1：使用 psql 命令行
psql -h <hostname> -p 5432 -U <username> -d MT5_ETL

# 方式2：使用环境变量
export PGHOST=<hostname>
export PGPORT=5432
export PGUSER=<username>
export PGDATABASE=MT5_ETL
psql
```

### 步骤 2：创建表结构

```bash
# 在 psql 中执行
\i /path/to/01_create_client_pnl_tables.sql
```

**预期结果：**
- ✅ 创建表 `public.pnl_client_summary`（客户汇总表）
- ✅ 创建表 `public.pnl_client_accounts`（客户账户明细表）
- ✅ 创建 8 个索引
- ✅ 显示表结构和索引列表

**验证命令：**
```sql
\d public.pnl_client_summary
\d public.pnl_client_accounts
```

---

### 步骤 3：创建核心函数

```bash
\i /path/to/02_create_core_functions.sql
```

**预期结果：**
- ✅ 创建函数 `refresh_single_client_summary(client_id)` - 刷新单个客户
- ✅ 创建函数 `initialize_client_summary()` - 首次初始化
- ✅ 创建函数 `compare_client_summary(auto_fix)` - 对比数据差异

**验证命令：**
```sql
SELECT proname FROM pg_proc WHERE proname LIKE '%client_summary%';
```

**预期输出：**
```
           proname            
------------------------------
 refresh_single_client_summary
 initialize_client_summary
 compare_client_summary
(3 rows)
```

---

### 步骤 4：创建触发器

```bash
\i /path/to/03_create_triggers.sql
```

**预期结果：**
- ✅ 创建触发器函数 `trigger_refresh_client_summary()`
- ✅ 在 `pnl_user_summary` 表上挂载触发器
- ✅ 在 `pnl_user_summary_mt4live2` 表上挂载触发器

**验证命令：**
```sql
SELECT tgname, tgrelid::regclass, tgenabled 
FROM pg_trigger 
WHERE tgname LIKE '%client_summary%';
```

**预期输出：**
```
              tgname               |        tgrelid         | tgenabled 
-----------------------------------+------------------------+-----------
 trigger_refresh_client_summary_mt5      | pnl_user_summary       | O
 trigger_refresh_client_summary_mt4live2 | pnl_user_summary_mt4live2 | O
(2 rows)
```

---

### 步骤 5：初始化历史数据

```bash
\i /path/to/04_initialize_and_test.sql
```

**该脚本包含：**
1. 初始化历史数据（调用 `initialize_client_summary()`）
2. 验证数据正确性
3. 测试触发器工作
4. 对比数据一致性

**预期结果：**
```
 total_clients | total_accounts | duration_seconds
---------------+----------------+------------------
          1523 |           2847 |            12.45
(1 row)

✅ 测试1通过：触发器自动创建聚合记录
✅ 测试2通过：触发器自动更新聚合余额
✅ 测试3通过：CEN币种自动转换为美元
```

**注意事项：**
- 初始化时间取决于客户数量（约 100 客户/秒）
- 如果有大量客户（10000+），可能需要 1-2 分钟
- 执行期间可以监控进度（脚本会输出提示）

---

## 验证部署成功

### 检查清单

```sql
-- ✅ 1. 检查表创建
SELECT COUNT(*) FROM public.pnl_client_summary;
SELECT COUNT(*) FROM public.pnl_client_accounts;

-- ✅ 2. 检查函数创建
SELECT COUNT(*) FROM pg_proc WHERE proname LIKE '%client_summary%';
-- 预期结果：3

-- ✅ 3. 检查触发器创建
SELECT COUNT(*) FROM pg_trigger WHERE tgname LIKE '%client_summary%';
-- 预期结果：2

-- ✅ 4. 检查数据一致性
SELECT * FROM public.compare_client_summary(auto_fix := FALSE);
-- 预期结果：status='OK'

-- ✅ 5. 检查聚合数据
SELECT 
  client_id,
  client_name,
  account_count,
  total_balance_usd,
  total_closed_profit_usd
FROM public.pnl_client_summary
ORDER BY total_closed_profit_usd DESC
LIMIT 10;
-- 预期结果：显示前10个盈利客户
```

---

## 触发器工作原理

### 自动更新时机

```
源表操作 → 触发器检测 → 刷新聚合表
```

**示例：**

1. **插入新账户**
   ```sql
   INSERT INTO pnl_user_summary (login, user_id, ...) VALUES (12345, 10001, ...);
   ```
   → 触发器自动调用 `refresh_single_client_summary(10001)`
   → 更新 `pnl_client_summary` 和 `pnl_client_accounts`

2. **更新账户余额**
   ```sql
   UPDATE pnl_user_summary SET user_balance = 50000 WHERE login = 12345;
   ```
   → 触发器自动刷新对应客户的聚合数据

3. **删除账户**
   ```sql
   DELETE FROM pnl_user_summary WHERE login = 12345;
   ```
   → 触发器自动更新（如果客户没有其他账户则删除聚合记录）

---

## 币种转换规则

| 原始币种 | 转换规则 | 示例 |
|----------|----------|------|
| USD/USDT | 保持不变 | 10000 → 10000 USD |
| CEN（美分） | 除以 100 | 1000000 CEN → 10000 USD |

**适用字段：**
- 所有金额字段（余额、盈亏、入金、出金等）
- 所有手数字段（volume_lots）

**验证转换：**
```sql
SELECT 
  cs.client_id,
  cs.currencies,
  cs.total_balance_usd,
  ca.currency,
  ca.balance_usd
FROM public.pnl_client_summary cs
JOIN public.pnl_client_accounts ca ON cs.client_id = ca.client_id
WHERE 'CEN' = ANY(cs.currencies)
LIMIT 5;
```

---

## 维护操作

### 手动刷新单个客户

```sql
-- 刷新 client_id = 10001 的数据
SELECT public.refresh_single_client_summary(10001);
```

### 对比数据差异

```sql
-- 检查差异（不自动修复）
SELECT * FROM public.compare_client_summary(auto_fix := FALSE);

-- 检查差异并自动修复
SELECT * FROM public.compare_client_summary(auto_fix := TRUE);
```

### 重新初始化全部数据

```sql
-- 清空现有数据
TRUNCATE TABLE public.pnl_client_summary CASCADE;
TRUNCATE TABLE public.pnl_client_accounts CASCADE;

-- 重新初始化
SELECT * FROM public.initialize_client_summary();
```

### 禁用/启用触发器

```sql
-- 禁用触发器（批量操作前）
ALTER TABLE public.pnl_user_summary DISABLE TRIGGER trigger_refresh_client_summary_mt5;
ALTER TABLE public.pnl_user_summary_mt4live2 DISABLE TRIGGER trigger_refresh_client_summary_mt4live2;

-- ... 执行批量操作 ...

-- 启用触发器（批量操作后）
ALTER TABLE public.pnl_user_summary ENABLE TRIGGER trigger_refresh_client_summary_mt5;
ALTER TABLE public.pnl_user_summary_mt4live2 ENABLE TRIGGER trigger_refresh_client_summary_mt4live2;

-- 手动全量刷新
SELECT * FROM public.initialize_client_summary();
```

---

## 性能优化建议

### 批量操作优化

如果需要批量更新大量账户数据：

1. 禁用触发器
2. 执行批量操作
3. 启用触发器
4. 调用 `initialize_client_summary()` 全量刷新

### 监控触发器性能

```sql
-- 开启慢查询日志（postgresql.conf）
log_min_duration_statement = 1000  -- 记录超过1秒的查询

-- 或使用 pg_stat_statements 扩展
```

---

## 故障排查

### 问题1：触发器未自动更新

**症状：** 更新源表后，聚合表数据未变化

**排查：**
```sql
-- 检查触发器状态
SELECT tgname, tgenabled FROM pg_trigger WHERE tgname LIKE '%client_summary%';
-- tgenabled 应该是 'O'（启用状态）

-- 检查警告日志
SHOW log_destination;
-- 查看 PostgreSQL 日志文件
```

**解决：**
```sql
-- 手动刷新
SELECT public.refresh_single_client_summary(<client_id>);
```

---

### 问题2：CEN币种转换错误

**症状：** CEN账户金额显示异常

**排查：**
```sql
-- 检查某个CEN账户的转换
SELECT 
  login,
  currency,
  user_balance AS original_balance,
  CASE WHEN currency = 'CEN' THEN user_balance / 100.0 ELSE user_balance END AS converted_balance
FROM public.pnl_user_summary
WHERE currency = 'CEN'
LIMIT 5;
```

---

### 问题3：数据不一致

**症状：** `compare_client_summary()` 返回差异

**解决：**
```sql
-- 自动修复差异
SELECT * FROM public.compare_client_summary(auto_fix := TRUE);
```

---

## 回滚方案

如果需要回滚（删除所有创建的对象）：

```sql
-- 删除触发器
DROP TRIGGER IF EXISTS trigger_refresh_client_summary_mt5 ON public.pnl_user_summary;
DROP TRIGGER IF EXISTS trigger_refresh_client_summary_mt4live2 ON public.pnl_user_summary_mt4live2;

-- 删除函数
DROP FUNCTION IF EXISTS public.trigger_refresh_client_summary();
DROP FUNCTION IF EXISTS public.refresh_single_client_summary(BIGINT);
DROP FUNCTION IF EXISTS public.initialize_client_summary();
DROP FUNCTION IF EXISTS public.compare_client_summary(BOOLEAN);

-- 删除表
DROP TABLE IF EXISTS public.pnl_client_accounts CASCADE;
DROP TABLE IF EXISTS public.pnl_client_summary CASCADE;
```

---

## 下一步

数据库部署完成后，下一步工作：

1. ✅ 数据库表和触发器已就绪
2. 📝 创建后端 API 接口（`/api/v1/client-pnl-summary/...`）
3. 📝 前端对接 API（已创建 `ClientPnLMonitor.tsx` 页面）
4. 📝 实现账户明细展开功能（Master-Detail）

---

## 技术支持

如果遇到问题，请检查：
1. PostgreSQL 日志文件
2. 触发器状态（`pg_trigger`）
3. 函数定义（`pg_proc`）
4. 数据一致性（`compare_client_summary()`）

---

## 总结

本部署创建了：
- ✅ 2 个新表（汇总表 + 明细表）
- ✅ 3 个核心函数（刷新/初始化/对比）
- ✅ 2 个触发器（自动更新）
- ✅ 币种统一转换（CEN → USD）
- ✅ 增量更新机制

执行总时间：约 15-35 秒（取决于客户数量）

