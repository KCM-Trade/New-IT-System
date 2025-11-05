-- ============================================================================
-- ClientID 盈亏监控初始化和测试脚本
-- 步骤 4：初始化历史数据并测试
-- 数据库：MT5_ETL
-- 执行时间：取决于客户数量（预估 10-30 秒）
-- ============================================================================

-- ============================================================================
-- 第一部分：初始化历史数据
-- ============================================================================

-- 方式1：调用初始化函数（推荐，返回统计信息）
SELECT * FROM public.initialize_client_summary();

-- 预期输出示例：
--  total_clients | total_accounts | duration_seconds
-- ---------------+----------------+------------------
--           1523 |           2847 |            12.45

-- 方式2：手动遍历（如果需要更多控制）
-- DO $$
-- DECLARE
--   v_client_id BIGINT;
--   v_count INTEGER := 0;
-- BEGIN
--   FOR v_client_id IN 
--     SELECT DISTINCT user_id 
--     FROM (
--       SELECT user_id FROM public.pnl_user_summary WHERE user_id IS NOT NULL
--       UNION
--       SELECT user_id FROM public.pnl_user_summary_mt4live2 WHERE user_id IS NOT NULL
--     ) t
--     ORDER BY user_id
--   LOOP
--     PERFORM public.refresh_single_client_summary(v_client_id);
--     v_count := v_count + 1;
--     IF v_count % 100 = 0 THEN
--       RAISE NOTICE '已处理 % 个客户...', v_count;
--     END IF;
--   END LOOP;
--   RAISE NOTICE '初始化完成，总计 % 个客户', v_count;
-- END $$;

-- ============================================================================
-- 第二部分：验证数据正确性
-- ============================================================================

-- 验证1：检查记录数
SELECT 
  (SELECT COUNT(*) FROM public.pnl_client_summary) AS summary_count,
  (SELECT COUNT(*) FROM public.pnl_client_accounts) AS accounts_count,
  (SELECT COUNT(DISTINCT user_id) FROM (
    SELECT user_id FROM public.pnl_user_summary WHERE user_id IS NOT NULL
    UNION
    SELECT user_id FROM public.pnl_user_summary_mt4live2 WHERE user_id IS NOT NULL
  ) t) AS source_distinct_clients;

-- 预期结果：summary_count 应等于 source_distinct_clients

-- 验证2：检查数据完整性
SELECT 
  client_id,
  client_name,
  account_count,
  total_balance_usd,
  total_closed_profit_usd,
  last_updated
FROM public.pnl_client_summary
ORDER BY total_closed_profit_usd DESC
LIMIT 10;

-- 预期结果：显示前10个盈利最高的客户

-- 验证3：检查币种转换是否正确（CEN账户）
SELECT 
  cs.client_id,
  cs.client_name,
  cs.currencies,
  cs.total_balance_usd,
  cs.account_count,
  ca.login,
  ca.currency,
  ca.balance_usd
FROM public.pnl_client_summary cs
JOIN public.pnl_client_accounts ca ON cs.client_id = ca.client_id
WHERE 'CEN' = ANY(cs.currencies)
LIMIT 5;

-- 预期结果：CEN账户的余额应该已经转换为美元（除以100）

-- 验证4：对比聚合值与明细值是否一致
SELECT 
  cs.client_id,
  cs.total_balance_usd AS summary_balance,
  SUM(ca.balance_usd) AS accounts_balance_sum,
  ROUND((cs.total_balance_usd - SUM(ca.balance_usd))::NUMERIC, 2) AS difference
FROM public.pnl_client_summary cs
JOIN public.pnl_client_accounts ca ON cs.client_id = ca.client_id
GROUP BY cs.client_id, cs.total_balance_usd
HAVING ABS(cs.total_balance_usd - SUM(ca.balance_usd)) > 0.01
LIMIT 10;

-- 预期结果：应该没有记录（或差异在0.01以内，容许浮点精度误差）

-- ============================================================================
-- 第三部分：测试触发器工作
-- ============================================================================

-- 测试1：插入新账户
DO $$
DECLARE
  v_test_client_id BIGINT := 999999;
  v_test_login BIGINT := 888888;
BEGIN
  -- 插入测试数据
  INSERT INTO public.pnl_user_summary (
    login, symbol, user_id, user_name, currency, user_balance, 
    closed_total_profit_with_swap, last_updated
  ) VALUES (
    v_test_login, 'ALL', v_test_client_id, 'Test Client', 'USD', 10000.00, 
    500.00, now()
  );
  
  -- 检查触发器是否自动创建聚合记录
  PERFORM pg_sleep(1); -- 等待触发器执行
  
  IF EXISTS (SELECT 1 FROM public.pnl_client_summary WHERE client_id = v_test_client_id) THEN
    RAISE NOTICE '✅ 测试1通过：触发器自动创建聚合记录';
  ELSE
    RAISE WARNING '❌ 测试1失败：触发器未创建聚合记录';
  END IF;
  
  -- 清理测试数据
  DELETE FROM public.pnl_user_summary WHERE login = v_test_login;
  DELETE FROM public.pnl_client_summary WHERE client_id = v_test_client_id;
  DELETE FROM public.pnl_client_accounts WHERE client_id = v_test_client_id;
END $$;

-- 测试2：更新账户余额
DO $$
DECLARE
  v_test_client_id BIGINT;
  v_test_login BIGINT;
  v_old_balance NUMERIC;
  v_new_balance NUMERIC;
BEGIN
  -- 选择一个真实的客户ID进行测试
  SELECT client_id INTO v_test_client_id 
  FROM public.pnl_client_summary 
  LIMIT 1;
  
  IF v_test_client_id IS NULL THEN
    RAISE WARNING '跳过测试2：没有客户数据';
    RETURN;
  END IF;
  
  -- 获取该客户的第一个账户
  SELECT login INTO v_test_login
  FROM public.pnl_client_accounts
  WHERE client_id = v_test_client_id
  LIMIT 1;
  
  -- 记录旧余额
  SELECT total_balance_usd INTO v_old_balance
  FROM public.pnl_client_summary
  WHERE client_id = v_test_client_id;
  
  -- 更新账户余额
  UPDATE public.pnl_user_summary
  SET user_balance = user_balance + 1000
  WHERE login = v_test_login AND user_id = v_test_client_id;
  
  -- 检查触发器是否自动更新聚合记录
  PERFORM pg_sleep(1);
  
  SELECT total_balance_usd INTO v_new_balance
  FROM public.pnl_client_summary
  WHERE client_id = v_test_client_id;
  
  IF v_new_balance > v_old_balance THEN
    RAISE NOTICE '✅ 测试2通过：触发器自动更新聚合余额（旧=%，新=%）', v_old_balance, v_new_balance;
  ELSE
    RAISE WARNING '❌ 测试2失败：触发器未更新聚合余额';
  END IF;
  
  -- 回滚更新
  UPDATE public.pnl_user_summary
  SET user_balance = user_balance - 1000
  WHERE login = v_test_login AND user_id = v_test_client_id;
END $$;

-- 测试3：测试CEN币种转换
DO $$
DECLARE
  v_test_client_id BIGINT := 999998;
  v_test_login BIGINT := 888887;
  v_balance_usd NUMERIC;
BEGIN
  -- 插入CEN账户（1000000美分 = 10000美元）
  INSERT INTO public.pnl_user_summary (
    login, symbol, user_id, user_name, currency, user_balance, 
    closed_total_profit_with_swap, last_updated
  ) VALUES (
    v_test_login, 'ALL', v_test_client_id, 'Test CEN Client', 'CEN', 1000000, 
    50000, now()
  );
  
  PERFORM pg_sleep(1);
  
  -- 检查转换是否正确
  SELECT total_balance_usd INTO v_balance_usd
  FROM public.pnl_client_summary
  WHERE client_id = v_test_client_id;
  
  IF v_balance_usd = 10000.00 THEN
    RAISE NOTICE '✅ 测试3通过：CEN币种自动转换为美元（1000000 CEN -> 10000 USD）';
  ELSE
    RAISE WARNING '❌ 测试3失败：CEN转换错误（期望10000，实际%）', v_balance_usd;
  END IF;
  
  -- 清理测试数据
  DELETE FROM public.pnl_user_summary WHERE login = v_test_login;
  DELETE FROM public.pnl_client_summary WHERE client_id = v_test_client_id;
  DELETE FROM public.pnl_client_accounts WHERE client_id = v_test_client_id;
END $$;

-- ============================================================================
-- 第四部分：对比数据一致性
-- ============================================================================

-- 运行对比函数
SELECT * FROM public.compare_client_summary(auto_fix := FALSE);

-- 预期结果：
--  status | client_id | description
-- --------+-----------+------------------
--  OK     |           | 数据一致，无差异

-- 如果发现差异，可以自动修复
-- SELECT * FROM public.compare_client_summary(auto_fix := TRUE);

-- ============================================================================
-- 第五部分：性能测试
-- ============================================================================

-- 测试查询性能
EXPLAIN ANALYZE
SELECT 
  client_id,
  client_name,
  total_balance_usd,
  total_closed_profit_usd
FROM public.pnl_client_summary
WHERE total_closed_profit_usd > 0
ORDER BY total_closed_profit_usd DESC
LIMIT 100;

-- 预期结果：查询时间应在 1-10ms 内（取决于数据量）

-- ============================================================================
-- 初始化完成检查清单
-- ============================================================================

-- ✅ 检查项1：表创建成功
SELECT COUNT(*) FROM public.pnl_client_summary;
SELECT COUNT(*) FROM public.pnl_client_accounts;

-- ✅ 检查项2：函数创建成功
SELECT proname FROM pg_proc WHERE proname LIKE '%client_summary%';

-- ✅ 检查项3：触发器创建成功
SELECT tgname FROM pg_trigger WHERE tgname LIKE '%client_summary%';

-- ✅ 检查项4：数据已初始化
SELECT 
  (SELECT COUNT(*) FROM public.pnl_client_summary) AS clients,
  (SELECT COUNT(*) FROM public.pnl_client_accounts) AS accounts;

-- ✅ 检查项5：触发器工作正常
-- （见上方测试结果）

-- ============================================================================
-- 如果需要重新初始化
-- ============================================================================

-- 清空数据（保留表结构）
-- TRUNCATE TABLE public.pnl_client_summary CASCADE;
-- TRUNCATE TABLE public.pnl_client_accounts CASCADE;

-- 重新初始化
-- SELECT * FROM public.initialize_client_summary();

RAISE NOTICE '============================================================';
RAISE NOTICE '初始化和测试完成！';
RAISE NOTICE '下一步：在后端创建 API 接口，前端对接数据';
RAISE NOTICE '============================================================';

