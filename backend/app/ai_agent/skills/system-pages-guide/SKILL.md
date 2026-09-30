---
name: system-pages-guide
description: Map of the KCM analysis system — which page shows what and which module permission it needs. Use for 边个 page 睇/喺边度查/哪个页面/在哪里看, "where can I see…", 冇权限/看不到页面/403, links like /risk-monitor?tab=, /client-return-rate, /window-scan, /login-ips, /exec-compensation, IB pages, 频繁出入金, 实时持仓, dashboard, 告警邮件.
---

# System pages guide (which page answers what)

## When to use
- The user asks where to see something, pastes a page link, or says they cannot open a page.
- You answered with a tool and want to point to the page that shows the same thing in full.
- A question needs data you cannot fetch, but a page shows it — point to the page instead of
  guessing.

## Key facts
- The app groups pages in the left sidebar by department. Each group is a **module** permission:

| module key | sidebar group (EN / 中文) | who grants it |
|---|---|---|
| `dashboard` | Dashboard / 首页 | a manager, in the user-management page |
| `cs` | CS Department / 客服部 | same |
| `data` | Data Query / 数据查询 | same |
| `risk` | Risk Control / 风险控制 | same |
| `other` | Other / 其他 | same |
| `ai` | AI Assistant / AI 助手 | same |

- Managers see every page. Settings, Search and View Profiles are open to every signed-in user.
  The internal docs site and the user-management page are manager-only.
- Without a module the page shows a "no permission" screen and its data requests are refused —
  hiding is not the only barrier. Wording: "that page needs the <module> module (需要 <X> 模块权限);
  ask a manager to grant it". New colleagues start with no modules.
- Some colleagues are limited to part of the client base (country data scope) on some pages and in
  your tools. If two colleagues see different numbers for the same query, that can be the reason.
- Times: most pages show Hong Kong time; the Risk Rule Alerts page shows MT server time (labelled
  "MT 时间 · UTC+3"); Trade Window Scan takes Hong Kong time input and shows both.

## Question → page (full descriptions in references/pages.md)

| The user wants… | Page (path) | Module | Your tool for the same thing |
|---|---|---|---|
| Rule alerts: 批量下单 / 快开快平 / 快速获利 / 对冲 / 杠杆 / 马丁 / 即日高收益 / Gap | Risk Rule Alerts `/risk-monitor?tab=<tab>` | risk | get_risk_alerts, get_alert_orders |
| Who traded / took profit around a moment (e.g. a data release) | Trade Window Scan `/window-scan` (Close tab `?tab=close`) | risk | get_window_scan |
| All clients by trading status (持仓中 / 近7天 …), net gain, CRM tags | Client Activity Monitor `/risk-watchlist` | risk | get_client_overview (per client) |
| Return rates, ROACE, max drawdown, 扛单率 for many clients | Client Return Rate 客户收益率 `/client-return-rate` | risk | none for the list; get_client_overview per client |
| Set up / change risk alert emails | Alert Mail Center 告警邮件中心 `/risk-alert-mail` | risk | none |
| Swap-free zipcode distribution / change log | Swap Free Control `/swap-free-control` | risk | none |
| Which accounts share login IPs with watched accounts; IP search | MT LoginIP 监测 `/login-ips` | cs (+risk for the Trade IP Profit tab) | get_risk_signals (shared order IPs, one client) |
| Raw traded lots of an IB / client / account, hold <10s / 10s-3min / ≥3min | IB及旗下客户交易查询 `/ibid-lots` | cs | none |
| Clients cycling deposits/withdrawals with little trading | 频繁出入金监控 `/cs/fund-flow-monitor` | cs | none |
| IB chain of a client (上级代理链) | IB Tree查询 `/cs/ib-tree` | cs | none |
| An IB's deposits/withdrawals | IB 出入金查询 `/cs/ib-deposits` (cs) or `/warehouse/ib-data` (data, also CN/Global totals) | cs / data | none |
| P/L by time-of-day slot and hold bucket (trend, T-1) | 持仓时间分析 `/hold-bucket-report` | data | get_trade_activity(group_by="hold_bucket") per client |
| Watched IBs' / clients' deposits, withdrawals, equity; daily report email | IB 资金监控 `/ib-financial-monitor` | data | none |
| Per-product open / closed-today / closed-yesterday lots and P/L | 产品交易汇总 `/warehouse/products` | data | none |
| Company-wide open position by symbol; XAUUSD history chart | 实时持仓 `/position` | data | rank_open_positions (client ranking) |
| Price-difference compensation for MT5 market orders | 成交价差补偿 `/exec-compensation` | data | none |
| Firm overview: positions, return-rate widget, 2-day client P/L by country/group | Dashboard `/` (history: `/dashboard/pnl-history`) | dashboard | none |
| This assistant | 分析助手 `/ai/assistant` | ai | — |

## How to answer
- Give the page name as it appears in the sidebar (both languages if the user mixes them), the path,
  and the module. For Risk Rule Alerts give the tab key (e.g. `/risk-monitor?tab=martingale`).
- If a tool covers the question, answer with the tool first, then add "full list on <page>".
- If no tool covers it (rows marked "none"), say so and point to the page. For a list the user can
  export from a page (e.g. Client Return Rate CSV), offer: "paste up to 50 client ids and I will
  look them up with get_client_overview".
- If the user says a page is missing from their sidebar, name the module it needs.

## What you cannot do today (say so plainly)
- Open, read or screenshot a page, or see what filters the user has set on it.
- Read Client Return Rate, Fund Flow, IBID Lots, IB Tree, IB Financial Monitor, Product Trade
  Summary, Execution Compensation, Swap Free or Alert Mail Center data — no tool reads them.
- Grant permissions or change page settings.
- Explain MT4/MT5 Manager or Admin terminal screens: they are not part of this system and you
  have no documentation for them — say so.
- There is no page that lists all accounts close to stop-out right now. The Risk Rule Alerts
  滥用杠杆 tab shows margin level only at the moment of opening.

## Wording rules
- Do not invent pages, tabs, buttons or menu paths. If a page is not in references/pages.md, say
  you do not know of one.
- Do not promise a page shows something it does not (see each page's "does not show" line).
