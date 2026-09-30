# SOURCES — system-pages-guide (not shown to the model)

## Port notes (OPT-0069, 2026-09-30)
This file is NEVER shown to the model (harness resource_filter; pinned by tests/test_ai_agent_skills.py).
File:line references below point at the pre-port tree and may have drifted.
- Swap-free zipcode TODO replaced by "not documented here — do not explain it".
- mt-manager-howto reference removed (skill excluded).


## SKILL.md

| Claim | Source |
|---|---|
| Six module keys | `frontend/src/lib/modules.ts:27`; `backend/app/schemas/admin.py:70-77` |
| Module labels EN/中文 | `backend/app/schemas/admin.py:70-77` |
| Sidebar groups | `frontend/src/components/app-sidebar.tsx:73-136` |
| Page → module table | `frontend/src/lib/modules.ts:76-155` (PAGE_POLICIES) |
| Managers see every page | `frontend/src/lib/modules.ts` `hasModule()` (isManager → true, ~line 185) |
| Settings / Search / View Profiles open to all | `frontend/src/lib/modules.ts:59,93-98` |
| /cfg/managers + docs manager-only | `frontend/src/lib/modules.ts:67,147`; `app-sidebar.tsx:164-182`; CLAUDE.md (auth: `/docs/` manager-only since 2026-08-18) |
| Refused page shows no-permission screen; server also refuses data | `frontend/src/components/ModuleRoute.tsx:15-34,84`; CLAUDE.md ("模块闸是 API 闸不是页面闸") |
| New colleagues start with no modules | CLAUDE.md (JIT 建号默认 `[]`); `ModuleRoute.tsx:75-78` |
| Managers grant modules on the user-management page | CLAUDE.md ("改单个人的模块一律走 /cfg/managers") |
| Country data scope limits some colleagues | CLAUDE.md "数据范围 / 行级国家隔离"; `docs/architecture/data-scope-design.md` |
| Risk Rule Alerts shows MT time "UTC+3"; other pages HK | `frontend/src/pages/RiskMonitor.tsx:828-833,1705`; `.cursor/skills/risk-monitor/SKILL.md:155`; CLAUDE.md ("frontend renders in Asia/Hong_Kong") |
| Window scan HK input, both times shown | `docs/features/window-scan.md:73` |
| Tool ↔ page mapping (risk-monitor, window-scan) | `backend/app/ai_agent/prompt.py:189-193`; `tools/window_scan.py:248-250` |
| No tool for CRR / fund-flow / swap-free / alert-mail etc. | tool list `backend/app/ai_agent/tools/`; `docs/ai-agent/11-slice3-risk-control.md:53-66` |
| get_client_overview takes ≤50 subjects | prompt.py:17-18,81-83 |
| Trade IP Profit tab needs risk too | `frontend/src/pages/login-ip/tabs.ts` (LOGIN_IP_TRADE_PROFIT_TAB comment "risk-only inside the cs page") |
| No page lists near-stop-out accounts; leverage tab = margin at open | grep `margin_level` in `frontend/src/pages` → only `RiskMonitor.tsx` (2026-09-30); `.cursor/skills/risk-monitor/references/rules-catalog.md:137-146` |
| Sidebar labels | `frontend/src/i18n/locales/en-US.ts:33-74`; `zh-CN.ts:25-75` |

## references/pages.md

| Claim | Source |
|---|---|
| Dashboard widgets, `/home` alias | `docs/features/dashboard.md:1-60` |
| PnL history ≤30 days, country → sales team, Profit excl. rebate, IB commission | `docs/features/dashboard-pnl-history.md:1-30` |
| Login IP: daily download, watchlist, same day + 7 days, morning email | `docs/features/login-ip.md:9-30` |
| Login IP tabs | `frontend/src/pages/login-ip/tabs.ts`; `en-US.ts:506-512` |
| IBID lots input, raw lots, <10s/10s-3min/≥3min, one ID, 366 days | `docs/features/ibid-lots.md:1-30` |
| Fund flow weekly Mon 08:00 HK, prev Mon-Sun, 90 days, ad-hoc | `docs/features/fund-flow-monitor.md:1-30` |
| IB tree chain, auto copy, staff shown as code | `docs/features/ib-tree-query.md:1-25` |
| /cs/ib-deposits = IB half of /warehouse/ib-data; region totals data-only | `docs/features/ib-data.md:1-30` |
| Hold bucket report T-1 trend, top 10 per slot, closed only | `docs/features/window-scan.md:48-60` (§1.1 comparison); `docs/features/hold-bucket-report.md:1-10` |
| IB Financial Monitor watchlist, IB expansion, reports, email code | `docs/features/ib-financial-monitor.md:1-22` |
| Product Trade Summary groups | `frontend/src/pages/WarehouseProducts.tsx` TradeSummaryItem type (grp 正在持仓/当日已平/昨日已平, settlement 当天/过夜, direction) |
| Position page cross-server summary + XAUUSD chart | `docs/features/position-monitor.md:1-30` |
| Exec compensation rules, 150,000 limit | `docs/exec-compensation/README.md` ("系统会做成什么样"); `docs/features/exec-compensation.md:9-14,32,54` |
| Risk Monitor tabs, 30 days | `frontend/src/pages/RiskMonitor.tsx:1413-1437`; `backend/app/core/risk_monitor_db.py:186` |
| Client Activity Monitor buckets, default 近7天, 60 s, CRM tags, filters, position cols only 持仓中 | `docs/features/risk-watchlist.md:52-63,301-330,465-470` |
| No 已阅/已处置 marking | grep 已阅 in `frontend/src` → none; `docs/features/risk-watchlist.md:31-37` |
| Window scan description | `docs/features/window-scan.md:17-45,62-75` |
| Swap Free Control cards | `frontend/src/pages/SwapFreeControl.tsx:336,399,506,626` |
| Client Return Rate columns/filters/export, MDD windows fixed, ib withdrawal excluded | `docs/features/client-return-rate.md:14-40,67-97,99-102` |
| Alert Mail Center subscriptions, no backlog | `docs/features/alert-mail-center.md:1-30` |
| AI page | `frontend/src/lib/modules.ts:138-143`; `en-US.ts:68-69` |

## Unsourced / needs business confirmation
- TODO(business): a user-facing one-liner on what the swap-free "zipcode" means (the page only shows
  zipcode distribution/logs; the business meaning is not documented for users in the repo).
- The Dashboard "可疑客户" widget content is not documented beyond its title; described only by name.

## Notes for implementer
1. Hidden routes exist but are not in the sidebar (`/gold`, `/warehouse`, `/warehouse/others`,
   `/warehouse/agent-global`, `/profit`, `/client-pnl-analysis`, `/template`, six `/cfg/*`
   placeholders — `frontend/src/App.tsx:113-161`, `modules.ts:76-155`). Deliberately left out of the
   skill so the model does not send users to legacy/placeholder pages. Add them if the business wants.
2. `docs/features/ib-financial-monitor.md:5` says the page is "under Risk Control" but the sidebar and
   `modules.ts` put it in Data Query (`data`). Skill follows the code.
3. `docs/features/hold-bucket-report.md` header still says "实施中"; the page is in the sidebar and
   routed. Skill treats it as live.
4. The Risk Rule Alerts page's fixed UTC+3 display will be 1 h off the MT clock in winter (see
   risk-monitor-rules SOURCES note 5).
