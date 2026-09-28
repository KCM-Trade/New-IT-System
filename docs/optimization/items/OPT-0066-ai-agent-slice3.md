---
id: OPT-0066
title: AI 分析 agent 第三刀 —— Risk control 页面群接入（`get_risk_alerts` / `get_alert_orders` / `get_window_scan` + window-scan 超时/DST 前置修复）
status: done
priority: P1
area: mixed
effort: L
created: 2026-09-28
related: [[OPT-0065]] [[OPT-0064]] [[OPT-0062]]
---

> ⚠ 按 tracker 规则这是 net-new feature，但同 0062–0065 先例，由用户 kickoff（`docs/ai-agent/10`）要求 file 成 OPT。分支 **`feat/ai-agent-slice3`**。
> **本文件不是实施 SSOT。** 设计 / 对账表 / 契约 / 验收 / 实施会话第一条消息全在 `docs/ai-agent/11-slice3-risk-control.md`（本机资产，docs/** 被 gitignore）。决策 `01-decisions.md` T0–T4。
> 执行隔离铁律：本会话只做调研 + file，**实施换新会话**（第一条消息 = 11 §9）。

## 问题

第二刀后 agent 有单主体三工具 + `rank_accounts` + 日历 + `run_sql`，但没有「以规则 / tab / 时间窗为入口」的群体级受信工具。风控同事问「今天即日高收益 tab 触发了谁、为什么」「这周 gap-trade 最大的 3 个账户」「这个告警的订单像不像马丁」时，模型只能走未认证 `run_sql` 或拒答。Risk control 页面背后的数据是**规则已筛过的子集**（`risk_monitor.db` 30 天 ≈ 6.7 万条告警），agent 可以查。

## 范围（用户 2026-09-28 拍板，每项独立 commit、可分开上线）

| # | 项 | 落点 |
|---|---|---|
| 3.0 | 前置：`window_scan_service` 改 `connect_readonly()`（原无 `MAX_EXECUTION_TIME`）+ HK→MT 改 DST 版 `MT_SERVER_TZ`（原写死 UTC+3，11-01 入冬错 1h）；页面同步变正确 | `services/window_scan_service.py:82-83,:586` |
| 3.1 | `get_risk_alerts(tab/rule_ids, date_range≤31, group_by=alert|account|client|rule, top_n≤50, sort, sids, symbol, client_ids)` | `core/risk_monitor_db.py`（`include_user_id` 开关 / `user_ids=` / `get_alerts_by_ids(conn=)` / 新 `aggregate_alert_events`）+ `ai_agent/tools/{risk_bands,risk_alerts}.py` |
| 3.2 | `get_alert_orders(alert_ids≤3)`：逐单含开平价 / 持仓 / 描述性 `features`；唯一 MySQL 新 SQL | `services/alert_orders_service.py`（新）+ `ai_agent/tools/alert_orders.py` |
| 3.3 | `get_window_scan(anchor_hk, window_min, scan_by, …)` | `ai_agent/tools/window_scan.py` |

**注册门控**：三工具仅当 `ai` + `risk`（`"*"` 算）且 `scope is None`；scope 过滤 + `*_masked_by_scope` 仍在 impl 内（纵深）。`MODULE_MAP` / `ROUTE_SCOPE` / `SCOPED_MODULES` / nginx / compose / `.env` / WAL 保活名单均不动。

## 已拍板（别再问）

- [x] T1 ai + risk 双模块才注册（受限者即使误勾 risk 也不注册 + WARNING）
- [x] T2 保留可见遮蔽计数（`rank_accounts` 不改；第二刀冷审 #9 结案）
- [x] T3 window-scan 两缺陷作为本刀前置 commit 修
- [x] T4 范围 = A + B + C；观察清单 / CRR / swap-free / XAUUSD / login-ip 群组 / blowup·事件 AB 不做

## 开放问题（不阻塞，实施时定并写注记）

- [ ] martingale「最大加仓倍数」取明细表哪一列
- [ ] intraday 取单口径与公式 v3 逐字对齐（以 `rule_intraday_return_service` docstring 为准）

## 验收标准

= `docs/ai-agent/11-slice3-risk-control.md` §6 全绿。摘要：
- [ ] 与页面**按 id 集合**逐行对账（intraday-return 今天 / gap-trade 本周 71+81 / martingale 总数 == `/alerts/stats`）；window-scan top 20 与页面一致
- [ ] 受限 impl 单测 `rows_masked_by_scope > 0`（含 NULL user_id、gap 71 C 腿 `legs_masked_by_scope`）；活体：无 risk / 受限 → 三工具不在列表，回答「需要 Risk control 模块权限」
- [ ] 零 PII（姓名 / zipcode / shared_ips / 备注 / COMMENT）单测断言；`verdict` 恒 null；禁用词测试
- [ ] 活体三问 + 「FOMC 前后 5 分钟谁在开仓」
- [ ] window-scan DST 冬/夏单测 + 夏令时页面前后一致
- [ ] anti-drift：`TOOL_IMPLS`/`TOOL_DOCSTRINGS`/`build_tools` 键一致；`TAB_BANDS` ↔ `RISK_MONITOR_TABS`；`test_ai_run_sql_guard.py:466` 工具列表钉子更新
- [ ] `./verify.sh` 绿（基线 pytest 2292 / tsc 0 / vitest 315）；ai-agent rebuild；三镜像 `pre-ai-slice3-<p0|a|b|c>-<日期>`

## 笔记

- 成本：单问 ≈ $0.09、归并+下钻 ≈ $0.33（terra），配额不用改。
- 顺带发现（另开 OPT，不在本刀）：`routes/risk_monitor.py` 全部 `async def` 调同步 SQLite。

## 结果

**2026-09-28 实施 + 冷审收口 + 上线**（分支 `feat/ai-agent-slice3`，7 个 commit；回滚镜像 `new-it-system-{api,web,ai-agent}:pre-ai-slice3-all-20260928`）。完整记录 `docs/ai-agent/05-rollout.md` §9，契约与出入 `02-contracts.md` §14–§16。

| 项 | commit | 交付 vs AC |
|---|---|---|
| 3.0 window-scan DST + 超时 | `2376009` | ✅ 夏令 4 组改前改后逐行一致；DST 冬/夏/切换日单测；15s `MAX_EXECUTION_TIME`，超时 504 |
| 3.1 `get_risk_alerts` + R1–R4 | `a735b5a` | ✅ id 集合对账（intraday 8 / gap-trade 64）、martingale 总数 959 == stats、top-3 == 手写 SQL、31 天 0.18s |
| 3.2 `get_alert_orders` + `alert_orders_service` | `46ffbf3` | ✅ 各段取单对账；MT5 已平仓按开仓秒回落 |
| 3.3 `get_window_scan` + ai+risk 注册 | `3aa5e2a` | ✅ top 20 == 页面；门控五例单测 + 活体 |
| 活体修正 | `36b96ce` | 每轮 ≤2 次硬性、`accounts_in_rows`、intraday 主指标改 `peak_return_pct`、审计 subjects |
| 冷审收口 | `9a7b134` | 见下 |

**与 AC / plan 的偏差**：分刀是各自闸门绿的 commit，但工具在 3.3 才接进 harness → 整刀一次部署（回滚标签一个 `pre-ai-slice3-all`，不是四个）；leverage 主指标 `margin_level`、intraday `peak_return_pct`；intraday 取单 = 当日开仓（隔夜仓不列，caveat）。闸门 pytest 2468 / tsc 0 / vitest 315。

**Stage 1 冷审处理**（独立零上下文 agent，14 个变异 13 个被测试抓到）：
- 🔴 #1 冬令时下钻全空（告警时间固定 +03:00 存、按 DST 还原）→ 当场修，`9a7b134`
- 🟡 #2 gap-71 C 腿可由 pair 字段反推 → 当场修
- 🟡 #3 window-scan 受限时全公司计数 → 当场修
- 🟡 #4 martingale 跨周窗口丢最新加仓 → 当场修（按开仓秒取单；485096 71/86 → 86/86）
- 🟡 #5 `alert:<id>` 审计标签 30 天后失效 → 当场修（同时记 `client:<id>`）
- ⚪ #6 / #7 / #9 → 当场修 + 钉测试
- ⚪ #8 agent 内 SQLite 读无语句级超时 → **立新 hardening OPT-0067**（用户 2026-09-28 选择）

**Follow-up**：OPT-0067（#8 + 老工具「每轮 2 次」仍只是 prompt）；经济日历无过去日期；dev 活体里 2 次未解释的 stream 中断（上 prod 后留意首批轮次）；即日告警里 7 账户 / 5 客户数据完全相同，值得转 risk team。
