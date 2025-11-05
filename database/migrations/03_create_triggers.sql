-- ============================================================================
-- ClientID 盈亏监控触发器脚本
-- 步骤 3：创建触发器
-- 数据库：MT5_ETL
-- 执行时间：约 1 秒
-- ============================================================================

-- ============================================================================
-- 触发器函数：trigger_refresh_client_summary()
-- 用途：当源表发生 INSERT/UPDATE/DELETE 时自动调用
-- 说明：
--   - 提取受影响的 client_id（OLD 或 NEW）
--   - 调用 refresh_single_client_summary() 刷新数据
--   - 错误不中断主事务，记录 WARNING
-- ============================================================================

CREATE OR REPLACE FUNCTION public.trigger_refresh_client_summary()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  v_old_client_id BIGINT;
  v_new_client_id BIGINT;
BEGIN
  -- 获取受影响的 client_id
  IF TG_OP = 'DELETE' THEN
    -- 删除操作：使用 OLD 记录
    v_old_client_id := OLD.user_id;
    v_new_client_id := NULL;
  ELSIF TG_OP = 'UPDATE' THEN
    -- 更新操作：检查 client_id 是否改变
    v_old_client_id := OLD.user_id;
    v_new_client_id := NEW.user_id;
  ELSE
    -- INSERT 操作：使用 NEW 记录
    v_old_client_id := NULL;
    v_new_client_id := NEW.user_id;
  END IF;
  
  -- 刷新旧的 client_id（如果存在且与新的不同）
  IF v_old_client_id IS NOT NULL AND (v_new_client_id IS NULL OR v_old_client_id != v_new_client_id) THEN
    BEGIN
      PERFORM public.refresh_single_client_summary(v_old_client_id);
    EXCEPTION
      WHEN OTHERS THEN
        -- 记录错误但不中断事务
        RAISE WARNING 'Failed to refresh client summary for client_id %: %', 
          v_old_client_id, SQLERRM;
    END;
  END IF;
  
  -- 刷新新的 client_id（如果存在）
  IF v_new_client_id IS NOT NULL THEN
    BEGIN
      PERFORM public.refresh_single_client_summary(v_new_client_id);
    EXCEPTION
      WHEN OTHERS THEN
        -- 记录错误但不中断事务
        RAISE WARNING 'Failed to refresh client summary for client_id %: %', 
          v_new_client_id, SQLERRM;
    END;
  END IF;
  
  -- 返回适当的记录
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  ELSE
    RETURN NEW;
  END IF;
END;
$$;

COMMENT ON FUNCTION public.trigger_refresh_client_summary() IS 
  '触发器函数：当 pnl_user_summary 或 pnl_user_summary_mt4live2 更新时，自动刷新对应的客户聚合数据';

-- ============================================================================
-- 在源表上创建触发器
-- ============================================================================

-- 触发器 1：pnl_user_summary 表
CREATE TRIGGER trigger_refresh_client_summary_mt5
  AFTER INSERT OR UPDATE OR DELETE 
  ON public.pnl_user_summary
  FOR EACH ROW
  EXECUTE FUNCTION public.trigger_refresh_client_summary();

COMMENT ON TRIGGER trigger_refresh_client_summary_mt5 ON public.pnl_user_summary IS 
  '自动刷新客户聚合表：当 MT5 账户数据更新时触发';

-- 触发器 2：pnl_user_summary_mt4live2 表
CREATE TRIGGER trigger_refresh_client_summary_mt4live2
  AFTER INSERT OR UPDATE OR DELETE 
  ON public.pnl_user_summary_mt4live2
  FOR EACH ROW
  EXECUTE FUNCTION public.trigger_refresh_client_summary();

COMMENT ON TRIGGER trigger_refresh_client_summary_mt4live2 ON public.pnl_user_summary_mt4live2 IS 
  '自动刷新客户聚合表：当 MT4Live2 账户数据更新时触发';

-- ============================================================================
-- 验证触发器创建成功
-- ============================================================================

-- 查看所有触发器
SELECT 
  tgname AS trigger_name,
  tgrelid::regclass AS table_name,
  tgenabled AS enabled,
  pg_get_triggerdef(oid) AS definition
FROM pg_trigger
WHERE tgname LIKE '%client_summary%'
ORDER BY tgname;

-- 预期结果：2个触发器创建成功
-- - trigger_refresh_client_summary_mt5 (ON pnl_user_summary)
-- - trigger_refresh_client_summary_mt4live2 (ON pnl_user_summary_mt4live2)

-- ============================================================================
-- 触发器管理命令（供维护使用）
-- ============================================================================

-- 禁用触发器（维护时使用）
-- ALTER TABLE public.pnl_user_summary DISABLE TRIGGER trigger_refresh_client_summary_mt5;
-- ALTER TABLE public.pnl_user_summary_mt4live2 DISABLE TRIGGER trigger_refresh_client_summary_mt4live2;

-- 启用触发器
-- ALTER TABLE public.pnl_user_summary ENABLE TRIGGER trigger_refresh_client_summary_mt5;
-- ALTER TABLE public.pnl_user_summary_mt4live2 ENABLE TRIGGER trigger_refresh_client_summary_mt4live2;

-- 删除触发器（如需重建）
-- DROP TRIGGER IF EXISTS trigger_refresh_client_summary_mt5 ON public.pnl_user_summary;
-- DROP TRIGGER IF EXISTS trigger_refresh_client_summary_mt4live2 ON public.pnl_user_summary_mt4live2;

