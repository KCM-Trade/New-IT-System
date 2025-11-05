-- ============================================================================
-- ClientID 盈亏监控核心函数脚本
-- 步骤 2：创建核心函数
-- 数据库：MT5_ETL
-- 执行时间：约 2 秒
-- ============================================================================

-- ============================================================================
-- 函数 1：refresh_single_client_summary(client_id)
-- 用途：刷新单个客户的聚合数据（触发器调用）
-- 说明：
--   - 从 pnl_user_summary 和 pnl_user_summary_mt4live2 聚合数据
--   - 自动转换 CEN 币种为美元（金额和手数 ÷ 100）
--   - 同时更新汇总表和明细表
-- ============================================================================

CREATE OR REPLACE FUNCTION public.refresh_single_client_summary(
  p_client_id BIGINT
)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_client_name TEXT;
  v_primary_server TEXT;
  v_countries TEXT[];
  v_currencies TEXT[];
  v_account_count INTEGER;
  v_account_list BIGINT[];
  v_total_balance_usd NUMERIC(20,2);
  v_total_credit_usd NUMERIC(20,2);
  v_total_floating_pnl_usd NUMERIC(20,2);
  v_total_equity_usd NUMERIC(20,2);
  v_total_closed_profit_usd NUMERIC(20,2);
  v_total_commission_usd NUMERIC(20,2);
  v_total_deposit_usd NUMERIC(20,2);
  v_total_withdrawal_usd NUMERIC(20,2);
  v_net_deposit_usd NUMERIC(20,2);
  v_total_volume_lots NUMERIC(20,6);
  v_total_overnight_volume_lots NUMERIC(20,6);
  v_total_closed_count INTEGER;
  v_total_overnight_count INTEGER;
  v_closed_sell_volume_lots NUMERIC(20,6);
  v_closed_sell_count INTEGER;
  v_closed_sell_profit_usd NUMERIC(20,2);
  v_closed_sell_swap_usd NUMERIC(20,2);
  v_closed_buy_volume_lots NUMERIC(20,6);
  v_closed_buy_count INTEGER;
  v_closed_buy_profit_usd NUMERIC(20,2);
  v_closed_buy_swap_usd NUMERIC(20,2);
  v_overnight_volume_ratio NUMERIC(6,3);
  v_last_updated TIMESTAMPTZ;
BEGIN
  -- 如果 client_id 为空，直接返回
  IF p_client_id IS NULL THEN
    RETURN;
  END IF;
  
  -- 聚合该客户的所有账户数据
  SELECT
    (array_agg(user_name ORDER BY login) FILTER (WHERE user_name IS NOT NULL))[1] AS client_name,
    CASE 
      WHEN COUNT(*) FILTER (WHERE source = 'MT5') >= COUNT(*) FILTER (WHERE source = 'MT4Live2') 
      THEN 'MT5' 
      ELSE 'MT4Live2' 
    END AS primary_server,
    array_agg(DISTINCT country) FILTER (WHERE country IS NOT NULL) AS countries,
    array_agg(DISTINCT currency) FILTER (WHERE currency IS NOT NULL) AS currencies,
    COUNT(*) AS account_count,
    array_agg(login ORDER BY login) AS account_list,
    
    -- 金额聚合（CEN 除以 100）
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN COALESCE(user_balance, 0) / 100.0
        ELSE COALESCE(user_balance, 0)
      END
    ) AS total_balance_usd,
    
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN COALESCE(user_credit, 0) / 100.0
        ELSE COALESCE(user_credit, 0)
      END
    ) AS total_credit_usd,
    
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN COALESCE(positions_floating_pnl, 0) / 100.0
        ELSE COALESCE(positions_floating_pnl, 0)
      END
    ) AS total_floating_pnl_usd,
    
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN COALESCE(equity, 0) / 100.0
        ELSE COALESCE(equity, 0)
      END
    ) AS total_equity_usd,
    
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN COALESCE(closed_total_profit_with_swap, 0) / 100.0
        ELSE COALESCE(closed_total_profit_with_swap, 0)
      END
    ) AS total_closed_profit_usd,
    
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN COALESCE(total_commission, 0) / 100.0
        ELSE COALESCE(total_commission, 0)
      END
    ) AS total_commission_usd,
    
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN COALESCE(deposit_amount, 0) / 100.0
        ELSE COALESCE(deposit_amount, 0)
      END
    ) AS total_deposit_usd,
    
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN COALESCE(withdrawal_amount, 0) / 100.0
        ELSE COALESCE(withdrawal_amount, 0)
      END
    ) AS total_withdrawal_usd,
    
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN (COALESCE(deposit_amount, 0) - COALESCE(withdrawal_amount, 0)) / 100.0
        ELSE COALESCE(deposit_amount, 0) - COALESCE(withdrawal_amount, 0)
      END
    ) AS net_deposit_usd,
    
    -- 手数聚合（CEN 除以 100）
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN 
          (COALESCE(closed_sell_volume_lots, 0) + COALESCE(closed_buy_volume_lots, 0)) / 100.0
        ELSE 
          COALESCE(closed_sell_volume_lots, 0) + COALESCE(closed_buy_volume_lots, 0)
      END
    ) AS total_volume_lots,
    
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN 
          (COALESCE(closed_sell_overnight_volume_lots, 0) + COALESCE(closed_buy_overnight_volume_lots, 0)) / 100.0
        ELSE 
          COALESCE(closed_sell_overnight_volume_lots, 0) + COALESCE(closed_buy_overnight_volume_lots, 0)
      END
    ) AS total_overnight_volume_lots,
    
    -- 订单数（直接累加）
    SUM(COALESCE(closed_sell_count, 0) + COALESCE(closed_buy_count, 0)) AS total_closed_count,
    SUM(COALESCE(closed_sell_overnight_count, 0) + COALESCE(closed_buy_overnight_count, 0)) AS total_overnight_count,
    
    -- 明细分类统计
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN COALESCE(closed_sell_volume_lots, 0) / 100.0
        ELSE COALESCE(closed_sell_volume_lots, 0)
      END
    ) AS closed_sell_volume_lots,
    
    SUM(COALESCE(closed_sell_count, 0)) AS closed_sell_count,
    
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN COALESCE(closed_sell_profit, 0) / 100.0
        ELSE COALESCE(closed_sell_profit, 0)
      END
    ) AS closed_sell_profit_usd,
    
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN COALESCE(closed_sell_swap, 0) / 100.0
        ELSE COALESCE(closed_sell_swap, 0)
      END
    ) AS closed_sell_swap_usd,
    
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN COALESCE(closed_buy_volume_lots, 0) / 100.0
        ELSE COALESCE(closed_buy_volume_lots, 0)
      END
    ) AS closed_buy_volume_lots,
    
    SUM(COALESCE(closed_buy_count, 0)) AS closed_buy_count,
    
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN COALESCE(closed_buy_profit, 0) / 100.0
        ELSE COALESCE(closed_buy_profit, 0)
      END
    ) AS closed_buy_profit_usd,
    
    SUM(
      CASE 
        WHEN currency = 'CEN' THEN COALESCE(closed_buy_swap, 0) / 100.0
        ELSE COALESCE(closed_buy_swap, 0)
      END
    ) AS closed_buy_swap_usd,
    
    -- 过夜成交量占比
    CASE 
      WHEN SUM(
        CASE 
          WHEN currency = 'CEN' THEN 
            (COALESCE(closed_sell_volume_lots, 0) + COALESCE(closed_buy_volume_lots, 0)) / 100.0
          ELSE 
            COALESCE(closed_sell_volume_lots, 0) + COALESCE(closed_buy_volume_lots, 0)
        END
      ) > 0 THEN
        ROUND(
          SUM(
            CASE 
              WHEN currency = 'CEN' THEN 
                (COALESCE(closed_sell_overnight_volume_lots, 0) + COALESCE(closed_buy_overnight_volume_lots, 0)) / 100.0
              ELSE 
                COALESCE(closed_sell_overnight_volume_lots, 0) + COALESCE(closed_buy_overnight_volume_lots, 0)
            END
          ) / 
          SUM(
            CASE 
              WHEN currency = 'CEN' THEN 
                (COALESCE(closed_sell_volume_lots, 0) + COALESCE(closed_buy_volume_lots, 0)) / 100.0
              ELSE 
                COALESCE(closed_sell_volume_lots, 0) + COALESCE(closed_buy_volume_lots, 0)
            END
          ),
          3
        )
      ELSE -1
    END AS overnight_volume_ratio,
    
    MAX(last_updated) AS last_updated
    
  INTO
    v_client_name,
    v_primary_server,
    v_countries,
    v_currencies,
    v_account_count,
    v_account_list,
    v_total_balance_usd,
    v_total_credit_usd,
    v_total_floating_pnl_usd,
    v_total_equity_usd,
    v_total_closed_profit_usd,
    v_total_commission_usd,
    v_total_deposit_usd,
    v_total_withdrawal_usd,
    v_net_deposit_usd,
    v_total_volume_lots,
    v_total_overnight_volume_lots,
    v_total_closed_count,
    v_total_overnight_count,
    v_closed_sell_volume_lots,
    v_closed_sell_count,
    v_closed_sell_profit_usd,
    v_closed_sell_swap_usd,
    v_closed_buy_volume_lots,
    v_closed_buy_count,
    v_closed_buy_profit_usd,
    v_closed_buy_swap_usd,
    v_overnight_volume_ratio,
    v_last_updated
    
  FROM (
    -- 合并 MT5 和 MT4Live2 的数据
    SELECT 
      user_id,
      login,
      user_name,
      user_group,
      country,
      currency,
      user_balance,
      user_credit,
      positions_floating_pnl,
      equity,
      closed_sell_volume_lots,
      closed_sell_count,
      closed_sell_profit,
      closed_sell_swap,
      closed_sell_overnight_count,
      closed_sell_overnight_volume_lots,
      closed_buy_volume_lots,
      closed_buy_count,
      closed_buy_profit,
      closed_buy_swap,
      closed_buy_overnight_count,
      closed_buy_overnight_volume_lots,
      total_commission,
      deposit_amount,
      withdrawal_amount,
      closed_total_profit_with_swap,
      last_updated,
      'MT5' AS source
    FROM public.pnl_user_summary
    WHERE user_id = p_client_id
    
    UNION ALL
    
    SELECT 
      user_id,
      login,
      user_name,
      user_group,
      country,
      currency,
      user_balance,
      user_credit,
      positions_floating_pnl,
      equity,
      closed_sell_volume_lots,
      closed_sell_count,
      closed_sell_profit,
      closed_sell_swap,
      closed_sell_overnight_count,
      closed_sell_overnight_volume_lots,
      closed_buy_volume_lots,
      closed_buy_count,
      closed_buy_profit,
      closed_buy_swap,
      closed_buy_overnight_count,
      closed_buy_overnight_volume_lots,
      total_commission,
      deposit_amount,
      withdrawal_amount,
      closed_total_profit_with_swap,
      last_updated,
      'MT4Live2' AS source
    FROM public.pnl_user_summary_mt4live2
    WHERE user_id = p_client_id
  ) combined
  GROUP BY user_id;
  
  -- 如果没有找到数据（该客户的所有账户都被删除），删除聚合记录
  IF v_account_count IS NULL OR v_account_count = 0 THEN
    DELETE FROM public.pnl_client_summary WHERE client_id = p_client_id;
    DELETE FROM public.pnl_client_accounts WHERE client_id = p_client_id;
    RETURN;
  END IF;
  
  -- 更新或插入聚合汇总表
  INSERT INTO public.pnl_client_summary (
    client_id,
    client_name,
    primary_server,
    countries,
    currencies,
    account_count,
    account_list,
    total_balance_usd,
    total_credit_usd,
    total_floating_pnl_usd,
    total_equity_usd,
    total_closed_profit_usd,
    total_commission_usd,
    total_deposit_usd,
    total_withdrawal_usd,
    net_deposit_usd,
    total_volume_lots,
    total_overnight_volume_lots,
    total_closed_count,
    total_overnight_count,
    closed_sell_volume_lots,
    closed_sell_count,
    closed_sell_profit_usd,
    closed_sell_swap_usd,
    closed_buy_volume_lots,
    closed_buy_count,
    closed_buy_profit_usd,
    closed_buy_swap_usd,
    overnight_volume_ratio,
    last_updated
  ) VALUES (
    p_client_id,
    v_client_name,
    v_primary_server,
    v_countries,
    v_currencies,
    v_account_count,
    v_account_list,
    v_total_balance_usd,
    v_total_credit_usd,
    v_total_floating_pnl_usd,
    v_total_equity_usd,
    v_total_closed_profit_usd,
    v_total_commission_usd,
    v_total_deposit_usd,
    v_total_withdrawal_usd,
    v_net_deposit_usd,
    v_total_volume_lots,
    v_total_overnight_volume_lots,
    v_total_closed_count,
    v_total_overnight_count,
    v_closed_sell_volume_lots,
    v_closed_sell_count,
    v_closed_sell_profit_usd,
    v_closed_sell_swap_usd,
    v_closed_buy_volume_lots,
    v_closed_buy_count,
    v_closed_buy_profit_usd,
    v_closed_buy_swap_usd,
    v_overnight_volume_ratio,
    v_last_updated
  )
  ON CONFLICT (client_id) DO UPDATE SET
    client_name = EXCLUDED.client_name,
    primary_server = EXCLUDED.primary_server,
    countries = EXCLUDED.countries,
    currencies = EXCLUDED.currencies,
    account_count = EXCLUDED.account_count,
    account_list = EXCLUDED.account_list,
    total_balance_usd = EXCLUDED.total_balance_usd,
    total_credit_usd = EXCLUDED.total_credit_usd,
    total_floating_pnl_usd = EXCLUDED.total_floating_pnl_usd,
    total_equity_usd = EXCLUDED.total_equity_usd,
    total_closed_profit_usd = EXCLUDED.total_closed_profit_usd,
    total_commission_usd = EXCLUDED.total_commission_usd,
    total_deposit_usd = EXCLUDED.total_deposit_usd,
    total_withdrawal_usd = EXCLUDED.total_withdrawal_usd,
    net_deposit_usd = EXCLUDED.net_deposit_usd,
    total_volume_lots = EXCLUDED.total_volume_lots,
    total_overnight_volume_lots = EXCLUDED.total_overnight_volume_lots,
    total_closed_count = EXCLUDED.total_closed_count,
    total_overnight_count = EXCLUDED.total_overnight_count,
    closed_sell_volume_lots = EXCLUDED.closed_sell_volume_lots,
    closed_sell_count = EXCLUDED.closed_sell_count,
    closed_sell_profit_usd = EXCLUDED.closed_sell_profit_usd,
    closed_sell_swap_usd = EXCLUDED.closed_sell_swap_usd,
    closed_buy_volume_lots = EXCLUDED.closed_buy_volume_lots,
    closed_buy_count = EXCLUDED.closed_buy_count,
    closed_buy_profit_usd = EXCLUDED.closed_buy_profit_usd,
    closed_buy_swap_usd = EXCLUDED.closed_buy_swap_usd,
    overnight_volume_ratio = EXCLUDED.overnight_volume_ratio,
    last_updated = EXCLUDED.last_updated;
  
  -- 更新账户明细表（先删除该客户的所有账户，再重新插入）
  DELETE FROM public.pnl_client_accounts WHERE client_id = p_client_id;
  
  INSERT INTO public.pnl_client_accounts (
    client_id, 
    login, 
    server, 
    currency, 
    user_name, 
    user_group, 
    country,
    balance_usd, 
    credit_usd,
    floating_pnl_usd, 
    equity_usd,
    closed_profit_usd,
    commission_usd,
    deposit_usd,
    withdrawal_usd,
    volume_lots,
    last_updated
  )
  SELECT
    user_id AS client_id,
    login,
    'MT5' AS server,
    currency,
    user_name,
    user_group,
    country,
    CASE WHEN currency = 'CEN' THEN COALESCE(user_balance, 0) / 100.0 ELSE COALESCE(user_balance, 0) END AS balance_usd,
    CASE WHEN currency = 'CEN' THEN COALESCE(user_credit, 0) / 100.0 ELSE COALESCE(user_credit, 0) END AS credit_usd,
    CASE WHEN currency = 'CEN' THEN COALESCE(positions_floating_pnl, 0) / 100.0 ELSE COALESCE(positions_floating_pnl, 0) END AS floating_pnl_usd,
    CASE WHEN currency = 'CEN' THEN COALESCE(equity, 0) / 100.0 ELSE COALESCE(equity, 0) END AS equity_usd,
    CASE WHEN currency = 'CEN' THEN COALESCE(closed_total_profit_with_swap, 0) / 100.0 ELSE COALESCE(closed_total_profit_with_swap, 0) END AS closed_profit_usd,
    CASE WHEN currency = 'CEN' THEN COALESCE(total_commission, 0) / 100.0 ELSE COALESCE(total_commission, 0) END AS commission_usd,
    CASE WHEN currency = 'CEN' THEN COALESCE(deposit_amount, 0) / 100.0 ELSE COALESCE(deposit_amount, 0) END AS deposit_usd,
    CASE WHEN currency = 'CEN' THEN COALESCE(withdrawal_amount, 0) / 100.0 ELSE COALESCE(withdrawal_amount, 0) END AS withdrawal_usd,
    CASE WHEN currency = 'CEN' THEN (COALESCE(closed_sell_volume_lots, 0) + COALESCE(closed_buy_volume_lots, 0)) / 100.0 ELSE COALESCE(closed_sell_volume_lots, 0) + COALESCE(closed_buy_volume_lots, 0) END AS volume_lots,
    last_updated
  FROM public.pnl_user_summary
  WHERE user_id = p_client_id
  
  UNION ALL
  
  SELECT
    user_id AS client_id,
    login,
    'MT4Live2' AS server,
    currency,
    user_name,
    user_group,
    country,
    CASE WHEN currency = 'CEN' THEN COALESCE(user_balance, 0) / 100.0 ELSE COALESCE(user_balance, 0) END AS balance_usd,
    CASE WHEN currency = 'CEN' THEN COALESCE(user_credit, 0) / 100.0 ELSE COALESCE(user_credit, 0) END AS credit_usd,
    CASE WHEN currency = 'CEN' THEN COALESCE(positions_floating_pnl, 0) / 100.0 ELSE COALESCE(positions_floating_pnl, 0) END AS floating_pnl_usd,
    CASE WHEN currency = 'CEN' THEN COALESCE(equity, 0) / 100.0 ELSE COALESCE(equity, 0) END AS equity_usd,
    CASE WHEN currency = 'CEN' THEN COALESCE(closed_total_profit_with_swap, 0) / 100.0 ELSE COALESCE(closed_total_profit_with_swap, 0) END AS closed_profit_usd,
    CASE WHEN currency = 'CEN' THEN COALESCE(total_commission, 0) / 100.0 ELSE COALESCE(total_commission, 0) END AS commission_usd,
    CASE WHEN currency = 'CEN' THEN COALESCE(deposit_amount, 0) / 100.0 ELSE COALESCE(deposit_amount, 0) END AS deposit_usd,
    CASE WHEN currency = 'CEN' THEN COALESCE(withdrawal_amount, 0) / 100.0 ELSE COALESCE(withdrawal_amount, 0) END AS withdrawal_usd,
    CASE WHEN currency = 'CEN' THEN (COALESCE(closed_sell_volume_lots, 0) + COALESCE(closed_buy_volume_lots, 0)) / 100.0 ELSE COALESCE(closed_sell_volume_lots, 0) + COALESCE(closed_buy_volume_lots, 0) END AS volume_lots,
    last_updated
  FROM public.pnl_user_summary_mt4live2
  WHERE user_id = p_client_id;
  
END;
$$;

COMMENT ON FUNCTION public.refresh_single_client_summary(BIGINT) IS 
  '刷新单个客户的聚合数据，当源表更新时自动调用。CEN币种自动转换为美元。';

-- ============================================================================
-- 函数 2：initialize_client_summary()
-- 用途：首次初始化，遍历所有 distinct clientid 并填充数据
-- 返回：初始化的客户数量
-- ============================================================================

CREATE OR REPLACE FUNCTION public.initialize_client_summary()
RETURNS TABLE(
  total_clients INTEGER,
  total_accounts INTEGER,
  duration_seconds NUMERIC
)
LANGUAGE plpgsql
AS $$
DECLARE
  v_client_id BIGINT;
  v_start_time TIMESTAMPTZ;
  v_client_count INTEGER := 0;
  v_account_count INTEGER := 0;
BEGIN
  v_start_time := clock_timestamp();
  
  -- 清空现有数据（可选，根据需求决定）
  -- TRUNCATE TABLE public.pnl_client_summary;
  -- TRUNCATE TABLE public.pnl_client_accounts;
  
  -- 遍历所有 distinct clientid
  FOR v_client_id IN 
    SELECT DISTINCT user_id 
    FROM (
      SELECT user_id FROM public.pnl_user_summary WHERE user_id IS NOT NULL
      UNION
      SELECT user_id FROM public.pnl_user_summary_mt4live2 WHERE user_id IS NOT NULL
    ) t
    ORDER BY user_id
  LOOP
    -- 刷新该客户
    PERFORM public.refresh_single_client_summary(v_client_id);
    v_client_count := v_client_count + 1;
  END LOOP;
  
  -- 统计账户数
  SELECT COUNT(*) INTO v_account_count FROM public.pnl_client_accounts;
  
  -- 返回统计信息
  RETURN QUERY SELECT 
    v_client_count,
    v_account_count,
    ROUND(EXTRACT(EPOCH FROM (clock_timestamp() - v_start_time))::NUMERIC, 2);
END;
$$;

COMMENT ON FUNCTION public.initialize_client_summary() IS 
  '首次初始化客户聚合表，遍历所有distinct clientid并填充数据。返回统计信息。';

-- ============================================================================
-- 函数 3：compare_client_summary()
-- 用途：对比源表与新表的 clientid 差异
-- 返回：缺失和多余的 clientid 列表
-- ============================================================================

CREATE OR REPLACE FUNCTION public.compare_client_summary(
  auto_fix BOOLEAN DEFAULT FALSE
)
RETURNS TABLE(
  status TEXT,
  client_id BIGINT,
  description TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
  v_missing_client_id BIGINT;
  v_orphan_client_id BIGINT;
BEGIN
  -- 查找源表有但新表缺失的 clientid
  FOR v_missing_client_id IN 
    SELECT DISTINCT user_id
    FROM (
      SELECT user_id FROM public.pnl_user_summary WHERE user_id IS NOT NULL
      UNION
      SELECT user_id FROM public.pnl_user_summary_mt4live2 WHERE user_id IS NOT NULL
    ) t
    WHERE user_id NOT IN (SELECT client_id FROM public.pnl_client_summary)
  LOOP
    RETURN QUERY SELECT 
      'MISSING'::TEXT,
      v_missing_client_id,
      '源表存在但聚合表缺失'::TEXT;
    
    -- 自动修复
    IF auto_fix THEN
      PERFORM public.refresh_single_client_summary(v_missing_client_id);
    END IF;
  END LOOP;
  
  -- 查找新表有但源表不存在的 clientid（孤儿数据）
  FOR v_orphan_client_id IN 
    SELECT client_id
    FROM public.pnl_client_summary
    WHERE client_id NOT IN (
      SELECT DISTINCT user_id
      FROM (
        SELECT user_id FROM public.pnl_user_summary WHERE user_id IS NOT NULL
        UNION
        SELECT user_id FROM public.pnl_user_summary_mt4live2 WHERE user_id IS NOT NULL
      ) t
    )
  LOOP
    RETURN QUERY SELECT 
      'ORPHAN'::TEXT,
      v_orphan_client_id,
      '聚合表存在但源表已删除（孤儿数据）'::TEXT;
    
    -- 自动修复（删除孤儿数据）
    IF auto_fix THEN
      DELETE FROM public.pnl_client_summary WHERE client_id = v_orphan_client_id;
      DELETE FROM public.pnl_client_accounts WHERE client_id = v_orphan_client_id;
    END IF;
  END LOOP;
  
  -- 如果没有差异，返回 OK
  IF NOT FOUND THEN
    RETURN QUERY SELECT 
      'OK'::TEXT,
      NULL::BIGINT,
      '数据一致，无差异'::TEXT;
  END IF;
END;
$$;

COMMENT ON FUNCTION public.compare_client_summary(BOOLEAN) IS 
  '对比源表与聚合表的clientid差异。auto_fix=true时自动修复差异。';

-- ============================================================================
-- 验证函数创建成功
-- ============================================================================

-- 查看函数列表
SELECT proname, prosrc FROM pg_proc WHERE proname LIKE '%client_summary%';

-- 预期结果：3个函数创建成功
-- - refresh_single_client_summary
-- - initialize_client_summary
-- - compare_client_summary

