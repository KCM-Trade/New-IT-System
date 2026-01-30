## Equity Clean 化分析说明

### 背景目标
- 通过 `mt5_live.mt5_daily` 明细，重建“剔除净入金与 Credit 波动”的净值曲线。
- 让风控聚焦纯交易表现，避免资金操作干扰。
- 当前前端 `EquityMonitor` 静态页即基于此思路搭建，等待接入真实 API。

### 后端计算逻辑（概览）
- **核心指标**  
  - `trading_pnl`: 汇总每日与交易相关的盈亏与成本，排除净入金/credit。
  - `cash_credit_flow`: 汇总每日入金、出金、额度调整等资金流。
  - `cum_trading_pnl`: 对 `trading_pnl` 做按时间累积，用于构造“clean equity”曲线。
- **使用场景**  
  - “Clean Equity Curve” 折线：以起始风险资金为基数，加上 `cum_trading_pnl`。
  - “Daily Trading PnL vs Cash & Credit Flow” 柱状：将 `trading_pnl` 与 `cash_credit_flow` 并排展示，判断净值变化是否来自交易。
- **后续扩展**  
  - 将 `server / accountId` 等筛选条件挂到查询上。
  - 支持日期范围过滤；当前 SQL 示例默认查询全部历史，可按需追加时间条件。

### SQL 逻辑拆解
```sql
SELECT
    login,
    FROM_UNIXTIME(Datetime) AS dt,
    Balance,
    Credit,

    -- 1) 每日交易表现（剔除资金流）
    (
        DailyProfit
      + DailyStorage
      + DailyDividend
      + DailyInterest
      - (DailyCommInstant + DailyCommRound + DailyCommFee)
      - DailyTaxes
      - DailyAgent
    ) AS trading_pnl,

    -- 2) 每日资金 / Credit 调整
    (
        DailyBalance
      + DailyCredit
      + DailyCharge
      + DailyCorrection
      + DailyBonus
      + DailySOCompensation
      + DailySOCompensationCredit
    ) AS cash_credit_flow,

    -- 3) 累计交易 PnL（窗口函数）
    SUM(
        DailyProfit
      + DailyStorage
      + DailyDividend
      + DailyInterest
      - (DailyCommInstant + DailyCommRound + DailyCommFee)
      - DailyTaxes
      - DailyAgent
    ) OVER (
        PARTITION BY login
        ORDER BY Datetime
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cum_trading_pnl

FROM mt5_live.mt5_daily
WHERE login = '60010462'
ORDER BY Datetime;
```

- **字段解释**
  - `trading_pnl`：平仓盈亏 `DailyProfit` + 掉期 `DailyStorage` + 股息 `DailyDividend` + 利息 `DailyInterest` − 即时/回合/手续费 − 税费 − 代理佣金。
  - `cash_credit_flow`：`DailyBalance`（入金方向）+ `DailyCredit` + `DailyCharge` + `DailyCorrection` + `DailyBonus` + `DailySOCompensation` + `DailySOCompensationCredit`。
  - `cum_trading_pnl`：同 `trading_pnl` 公式做窗口累加，便于进一步计算 clean equity。

- **用途**  
  - `trading_pnl` 用于当日柱状图、异常波动判断。
  - `cash_credit_flow` 直接反映资金端操作（入金、credit、调整等）。
  - `cum_trading_pnl` 结合初始资金即可构建“Clean Equity”时间序列。

- **可选优化**  
  - 增加时间过滤（`Datetime BETWEEN ...`）与多账户查询。
  - 在 SQL 中直接输出 `clean_equity = base_funds + cum_trading_pnl`，前端可减少计算。
  - 若需要区分币种或服务器，可添加 `server`、`currency` 字段。

### 总结
- SQL 已将交易表现与资金流彻底拆分，满足风控“只看盈利质量”的需求。
- 前端只需接入 `login`, `dt`, `trading_pnl`, `cash_credit_flow`, `cum_trading_pnl` 即可填充当前 `EquityMonitor` 四个卡片（曲线、柱状、KPI、风险说明）。
- 后续按需扩展：日期筛选、批量账户、告警规则，保持此计算口径即可。