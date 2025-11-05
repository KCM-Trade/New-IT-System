-- ============================================================================
-- ClientID 盈亏监控表创建脚本
-- 步骤 1：创建两个新表
-- 数据库：MT5_ETL
-- 执行时间：约 1 秒
-- ============================================================================

-- 连接到数据库（请手动确认）
-- \c MT5_ETL

-- ============================================================================
-- 表 1：pnl_client_summary（客户汇总表）
-- 用途：按 clientid 聚合的客户盈亏汇总数据，统一货币单位为美元
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.pnl_client_summary (
  -- 主键
  client_id BIGINT PRIMARY KEY,
  
  -- 客户基本信息（从账户中提取）
  client_name TEXT, -- 客户名称（取第一个非空的 user_name）
  primary_server TEXT, -- 主要服务器：'MT5' 或 'MT4Live2'（账户数多的优先）
  countries TEXT[], -- 客户账户所在国家列表（去重）
  currencies TEXT[], -- 客户使用的币种列表 ['USD', 'CEN', 'USDT']
  
  -- 账户统计
  account_count INTEGER NOT NULL DEFAULT 0, -- 账户总数
  account_list BIGINT[], -- 账户ID列表，用于快速查找 [12345, 67890, 11111]
  
  -- ========== 聚合金额字段（统一为美元） ==========
  -- 账户余额相关
  total_balance_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 所有账户余额总和（美元）
  total_credit_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 所有账户信用总和
  total_floating_pnl_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 所有账户浮动盈亏总和
  total_equity_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 所有账户净值总和
  
  -- 平仓盈亏相关
  total_closed_profit_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 平仓总盈亏（含 swap，美元）
  total_commission_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 总佣金（美元）
  
  -- 资金流动相关
  total_deposit_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 总入金（美元）
  total_withdrawal_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 总出金（美元）
  net_deposit_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 净入金（美元，deposit - withdrawal）
  
  -- ========== 聚合手数（统一为标准手数） ==========
  total_volume_lots NUMERIC(20,6) NOT NULL DEFAULT 0, -- 总交易手数（sell + buy）
  total_overnight_volume_lots NUMERIC(20,6) NOT NULL DEFAULT 0, -- 过夜交易手数
  overnight_volume_ratio NUMERIC(6,3), -- 过夜成交量占比（计算列）
  
  -- ========== 聚合订单数 ==========
  total_closed_count INTEGER NOT NULL DEFAULT 0, -- 总平仓订单数（sell + buy）
  total_overnight_count INTEGER NOT NULL DEFAULT 0, -- 过夜订单数
  
  -- 明细分类统计（用于更详细的分析）
  closed_sell_volume_lots NUMERIC(20,6) NOT NULL DEFAULT 0, -- SELL 平仓手数
  closed_sell_count INTEGER NOT NULL DEFAULT 0, -- SELL 平仓订单数
  closed_sell_profit_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- SELL 平仓盈亏
  closed_sell_swap_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- SELL swap
  
  closed_buy_volume_lots NUMERIC(20,6) NOT NULL DEFAULT 0, -- BUY 平仓手数
  closed_buy_count INTEGER NOT NULL DEFAULT 0, -- BUY 平仓订单数
  closed_buy_profit_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- BUY 平仓盈亏
  closed_buy_swap_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- BUY swap
  
  -- 审计字段
  last_updated TIMESTAMPTZ NOT NULL DEFAULT now(), -- 最后更新时间
  created_at TIMESTAMPTZ NOT NULL DEFAULT now() -- 创建时间
);

-- 创建索引（提升查询性能）
CREATE INDEX IF NOT EXISTS idx_pnl_client_summary_client_id 
  ON public.pnl_client_summary(client_id);
CREATE INDEX IF NOT EXISTS idx_pnl_client_summary_last_updated 
  ON public.pnl_client_summary(last_updated);
CREATE INDEX IF NOT EXISTS idx_pnl_client_summary_total_balance 
  ON public.pnl_client_summary(total_balance_usd DESC);
CREATE INDEX IF NOT EXISTS idx_pnl_client_summary_total_profit 
  ON public.pnl_client_summary(total_closed_profit_usd DESC);

-- 添加注释（方便理解）
COMMENT ON TABLE public.pnl_client_summary IS '按 clientid 聚合的客户盈亏汇总表，统一货币单位为美元';
COMMENT ON COLUMN public.pnl_client_summary.client_id IS '客户ID（user_id）';
COMMENT ON COLUMN public.pnl_client_summary.total_balance_usd IS '所有账户余额总和（美元），CEN账户已转换为美元';
COMMENT ON COLUMN public.pnl_client_summary.account_list IS '该客户的所有账户ID列表，用于快速查找';

-- ============================================================================
-- 表 2：pnl_client_accounts（客户账户明细表）
-- 用途：存储每个客户下的账户明细，用于展开显示
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.pnl_client_accounts (
  -- 复合主键
  client_id BIGINT NOT NULL,
  login BIGINT NOT NULL,
  server TEXT NOT NULL, -- 'MT5' 或 'MT4Live2'
  
  -- 账户基本信息
  currency TEXT, -- 'USD', 'CEN', 'USDT'
  user_name TEXT, -- 账户名称
  user_group TEXT, -- 用户组
  country TEXT, -- 国家
  
  -- 账户金额（统一为美元）
  balance_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 余额（美元）
  credit_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 信用（美元）
  floating_pnl_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 浮动盈亏（美元）
  equity_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 净值（美元）
  
  -- 账户交易统计（统一为美元）
  closed_profit_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 平仓盈亏（美元）
  commission_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 佣金（美元）
  deposit_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 入金（美元）
  withdrawal_usd NUMERIC(20,2) NOT NULL DEFAULT 0, -- 出金（美元）
  
  -- 账户交易手数（统一为标准手数）
  volume_lots NUMERIC(20,6) NOT NULL DEFAULT 0, -- 总交易手数
  
  -- 更新时间
  last_updated TIMESTAMPTZ NOT NULL DEFAULT now(),
  
  PRIMARY KEY (client_id, login, server)
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_pnl_client_accounts_client_id 
  ON public.pnl_client_accounts(client_id);
CREATE INDEX IF NOT EXISTS idx_pnl_client_accounts_login 
  ON public.pnl_client_accounts(login);
CREATE INDEX IF NOT EXISTS idx_pnl_client_accounts_server 
  ON public.pnl_client_accounts(server);

-- 添加注释
COMMENT ON TABLE public.pnl_client_accounts IS '客户账户明细表，用于展开显示客户下的所有账户';
COMMENT ON COLUMN public.pnl_client_accounts.client_id IS '客户ID（关联到 pnl_client_summary）';
COMMENT ON COLUMN public.pnl_client_accounts.server IS '服务器：MT5 或 MT4Live2';
COMMENT ON COLUMN public.pnl_client_accounts.balance_usd IS '账户余额（美元），CEN账户已转换';

-- ============================================================================
-- 验证表创建成功
-- ============================================================================

-- 查看表结构
\d public.pnl_client_summary
\d public.pnl_client_accounts

-- 查看索引
SELECT tablename, indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename IN ('pnl_client_summary', 'pnl_client_accounts');

-- 预期结果：两个表创建成功，索引创建成功

