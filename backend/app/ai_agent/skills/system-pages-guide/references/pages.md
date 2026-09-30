# Pages of the analysis system — what each shows

Format: **Sidebar name (EN / 中文)** · path · module. Then what it shows, useful facts, what it does
NOT show. Only pages listed here exist in the sidebar; do not invent others.

## Dashboard (module `dashboard`)
**Dashboard / 首页** · `/` (alias `/home`)
- Widgets: 实时持仓 (cross-server open position by symbol, XAUUSD default), 客户收益率 (return-rate
  table, 6 h / 24 h), 近两日客户平仓净盈亏 by country and by account group, a 可疑客户 table.
**Client closed P/L history** · `/dashboard/pnl-history` (from the "历史" button on the P/L card)
- Up to 30 days: daily client closed P/L (excluding rebate) stacked by country, and IB commission;
  drill down date → country → sales team. MT server days.

## CS Department / 客服部 (module `cs`)
**Login IP Monitoring / MT LoginIP 监测** · `/login-ips`
- Daily: which login IPs each account used on MT4 Live / MT4 Live2 / MT5. For a maintained watch
  list, flags other real accounts that shared an IP — same day and past 7 days — with a morning email.
- Tabs: Daily report · Watchlist · Search · Operations · Trade IP Profit (the last one also needs
  the Risk Control module).
- Does not show: per-order IPs for all clients; login IPs are refreshed once a day (early morning).
**IBID Lots Lookup / IB及旗下客户交易查询** · `/ibid-lots`
- Input one IB ID, client ID or trading account + date range → RAW traded lots (not the
  commission-table lots the CRM shows), split by hold time <10 s / 10 s-3 min / ≥3 min, by product and
  by client. One ID per query; date span at most 366 days.
**Frequent Fund Flow / 频繁出入金监控** · `/cs/fund-flow-monitor`
- Clients with frequent deposits/withdrawals but few trades. Weekly snapshot every Monday 08:00 HK
  for the previous Mon-Sun, kept 90 days, plus an ad-hoc query with adjustable thresholds.
**IB Tree Lookup / IB Tree查询** · `/cs/ib-tree`
- Input a CRM client ID → the IB chain from the sales code down to the client, ready to paste
  (auto-copied). Staff accounts show their sales code, not their name.
**IB Deposits & Withdrawals / IB 出入金查询** · `/cs/ib-deposits`
- Deposits/withdrawals for given IB IDs. Same table as the IB part of `/warehouse/ib-data`, without
  the CN/Global company totals.

## Data Query / 数据查询 (module `data`)
**Hold Duration Analysis / 持仓时间分析** · `/hold-bucket-report`
- A T-1 trend report: closed-trade P/L by time-of-day slot and hold bucket across days/months, with
  top clients per slot. "Which hours structurally lose/win" — not "who traded at 03:00 last night"
  (that is Trade Window Scan). Closed orders only.
**IB Financial Monitor / IB 资金监控** · `/ib-financial-monitor`
- A managed list of IB IDs or client IDs (an IB is expanded to its downstream clients): deposits,
  withdrawals, equity, differences; manual or daily scheduled report email. Changes need an email
  verification code.
**Product Trade Summary / 产品交易汇总** · `/warehouse/products`
- Per product: open positions, closed today, closed yesterday — buy/sell lots and P/L, split into
  same-day vs overnight positions.
**Open Positions / 实时持仓** · `/position`
- Company-wide open position for a symbol family across MT4 Live, MT4 Live2 and MT5 (server subtotals
  + TOTAL, expandable to symbols), plus an XAUUSD minute-level snapshot chart.
- Does not show: which client holds what (use rank_open_positions).
**IB & Region Deposits / IB / 地区出入金** · `/warehouse/ib-data`
- Deposits/withdrawals by IB ID, and company totals by region (CN / Global).
**Execution Compensation / 成交价差补偿** · `/exec-compensation`
- MT5 only. Input a CRM client ID or one MT5 account (`5-<login>`) + dates → per-deal difference
  between the fill price and the price at the moment the client sent the order (request price),
  in USD; total "正负相抵" (net, the main figure) and "只补正数" (positive only); by account / symbol /
  day and per deal; export.
- Fixed rules shown on the page: only client market orders (open and close) — stop-loss, pending
  triggers and stop-outs are excluded; data up to the day before the query; positions not fully
  closed by then are excluded whole (so the same dates can give a different total later).
- Over 150,000 deals in one query is refused: narrow the dates or ask IT for an offline export.

## Risk Control / 风险控制 (module `risk`)
**Risk Rule Alerts** (page title in both languages) · `/risk-monitor?tab=…`
- Tabs: burst-open 批量下单 · quick-open-close 快开快平 · quick-profit 快速获利 · hedge-open 对冲刷单 ·
  leverage-abuse 滥用杠杆 · martingale 马丁策略 · intraday-return 即日高收益 · gap-trade Gap Trade.
  Alerts kept 30 days. Times shown in MT server time. Details: the `risk-monitor-rules` skill, if it is in your skill list.
**Client Activity Monitor** · `/risk-watchlist`
- All clients (one row per client, all accounts merged) in trading-status buckets: 持仓中 · 近1天 ·
  近7天 (default) · 近30天 · 近90天 · 超90天未交易 · 入金未交易 · 新注册(30d)未入金 · 长期未入金;
  refreshes every 60 s; money columns incl. net gain and CRM Tags; filters by country, CRM
  attributes and CRM tags. Position columns have values only for 持仓中 rows.
- Does not have: 已阅 / 已处置 marking (not built).
**Trade Window Scan** · `/window-scan` (Entry tab) · `/window-scan?tab=close` (Close tab)
- Enter a Hong Kong date + time and ±1/3/5/10/15 min → clients who opened (Entry) or closed (Close)
  orders in that window and whose closed orders there net > 0; hold-time bucket filter; shows HK and
  MT time.
**Swap Free Control** · `/swap-free-control`
- Current zipcode distribution, zipcode change logs, excluded groups, client change frequency.
  How a zipcode maps to swap-free treatment is not documented here — do not explain it.
**Client Return Rate / 客户收益率** · `/client-return-rate`
- Clients with closed trades in a chosen range: trading net deposit (history / range, excluding IB
  commission withdrawals, shown in separate columns), equity, historical and range profit, adjusted
  return rates, ROACE, return incl. floating, 扛单率 (floating burden), max drawdown 30d/90d/180d/
  365d/all (windows fixed to today), 已归零 / 曾穿仓 flags. Filters: country CN/Global, AKCM tag,
  USDT-deposit tag, client ID. CSV export.
**Alert Mail Center / 告警邮件中心** · `/risk-alert-mail`
- The risk team subscribes to alert emails per detection module: conditions, recipients, realtime or
  daily digest. A new subscription only sends alerts from then on (no backlog).

## AI Assistant / AI 助手 (module `ai`)
**Analyst / 分析助手** · `/ai/assistant` — this assistant.

## Open to everyone signed in
Settings `/settings` · Search `/search` · View Profiles `/cfg/view-profiles` (saved column/filter
layouts, device, own sessions).

## Manager only
User management `/cfg/managers` (grant modules) · internal docs site.
