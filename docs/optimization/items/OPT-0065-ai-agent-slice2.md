---
id: OPT-0065
title: AI 分析 agent 第二刀 —— 会话记忆（MAF session blob 落库）+ `ai` 进数据范围 + `run_sql` 未认证逃生口 + 群体级受信工具（`rank_accounts` / `get_economic_calendar`）
status: wip
priority: P1
area: mixed
effort: XL
created: 2026-09-27
related: [[OPT-0064]] [[OPT-0063]]
---

> ⚠ 按 tracker 规则这是 net-new feature；用户 2026-09-27 明确要求「用 optimization-tracker file 新 OPT（不要往 OPT-0064 里塞）」，同 0062/0063/0064 先例，照办。分支名按先例 **`feat/ai-agent-slice2`**。
> **本文件不是实施 SSOT。** 架构（给人看）在 `docs/ai-agent/03-architecture.md`；决策 `01-decisions.md` S1–S6；契约 `02-contracts.md` §8–§13（第二刀草案）；分刀与验收 `05-rollout.md` §3 / §6；下一会话 kickoff `08-next-session-prompt.md`。这里只记 tracker 元信息、开放问题、对账结果。
> 执行隔离铁律适用：本会话只 file，实施换新 worker（从 `08-next-session-prompt.md` 起手）。

## 问题

第一刀（OPT-0064）上 prod 后用户试用 15 轮（2026-09-27），暴露三类第一刀答不了的问题：
1. **追问不带 id**（2 轮）：每轮新建 `AgentSession`，prompt 明写「no memory」，第 2 轮「我想分析这个客户」零工具拒答。
2. **群体级问题**（1 轮）：「上一周胜率最高的 5 个交易账户」——三个工具都是 `subject` 必填的单主体签名，模型零工具拒答。
3. **联网**（1 轮）：「未来一个月重要数据日」——02 §4.2 明令不接 hosted web search。

用户判断「无法遍历所有问题、当前不够灵活」成立；联网调研（03 §6）证实 2026 主流是 semantic-first hybrid：受信工具主路径 + 受控 SQL 逃生口 + UI 区分认证/未认证。

## 范围（按用户 2026-09-27 晚确认的顺序，每项独立 commit、可分开上线）

| # | 项 | 落点 | 前置 |
|---|---|---|---|
| 1 | 会话记忆：`ai_sessions` + `ai_messages`（`ai_agent.db`，主 API 写）；agent `store:False` + `InMemoryHistoryProvider` + compaction；内部接口 `session_blob` 入 / `session_state` 事件出；浏览器 `GET/DELETE/PATCH /ai/sessions*`；prompt 规则 1 改写；前端 `SessionList.tsx` + `useAiTurn` resume | `core/ai_usage_db.py`、`routes/ai.py`、`services/ai_gateway_service.py`、`app/ai_agent/{harness,server,prompt}.py`、`components/ai/`、`hooks/useAiTurn.ts` | 无 |
| 2 | `SCOPED_MODULES` 加 `ai`；`ROUTE_SCOPE`：`/ai/turn` filter、`/ai/usage/today` + `/ai/sessions*` open | `core/data_scope.py`、`tests/test_data_scope*.py` | 走 `docs/operations/runbooks/data-scope-change.md` |
| 3 | `run_sql`：七道门槛（内核只读 / 单条 SELECT sqlglot / 三道超时 / 200 行 / 白名单 / 受限用户禁用 / 审计 `sql[]`）+ `certified:false` 徽章 + SQL 原样展示 | `app/ai_agent/tools/run_sql.py`、`SourceBadge.tsx` 未认证态、`requirements-ai-agent.txt`（sqlglot） | 硬前置**已取消**（用户 09-27 晚）：用共享 `readonly` + 02 §10.1a 的 MySQL 侧 AST 加固（每条单测）；`sqlglot` 进 `requirements-ai-agent.txt` |
| 4 | `rank_accounts`（SQL 聚合、min_orders 门槛、出参 scope 过滤 + `rows_masked_by_scope`、≤92 天）+ `get_economic_calendar`（BLS iCal / Fed FOMC / FRED → 主 API scheduler 每日缓存 `econ_calendar_cache`，agent 只读） | `services/rank_accounts_service.py`、`services/econ_calendar_service.py`、scheduler job、`tools/` | 第 2 项（第一个出参扇出到任意客户的工具） |
| 5 | 后置：`resolve_subject`、`get_group_correlation` | — | 零真实需求，本刀不做除非用户点名 |

## 假设 / 开放问题（待用户决策）

- [x] ~~MySQL `ai_agent_ro` 是否建——第 3 项硬前置~~ **用户 09-27 晚拍板：不是硬前置**，`run_sql` 用共享 `readonly` 上线；补偿控制 = 02 §10.1a（AST 根类型 / 单语句连接 / 节点黑名单 / 表白名单 / 超时 / 审计 `sql[]`），每条要单测。专用账号是非阻塞待办，别再问。
- [ ] `rank_accounts` 口径：指标集合（win_rate / net_profit / lots / orders / return_pct）、`min_orders` 默认 20、`date_range` 上限 92 天、`return_pct` 分母（期初净值取不到则 null）。
- [ ] 会话保留期 `AI_SESSION_RETENTION_DAYS` 默认 90（0 = 永久）。
- [ ] compaction 摘要模型用 `gpt-5.6-luna`（摘要输入含客户数据，留在同租户）。
- [ ] `FRED_API_KEY` 由谁申请（免费）；无 key 时日历只有 BLS + FOMC 两源。

## 验收标准

= `docs/ai-agent/05-rollout.md` §6.3 全绿。摘要：
- [ ] 记忆：同 session 第 2 轮不带 id 的追问命中第 1 轮客户（活体）；他人 session_id → 404；Azure 侧无 conversation 残留（`store:false`）；20k 工具结果两轮后被折叠（input_tokens 不线性增长）；审计每轮一行含 `resumed`
- [ ] 数据范围：`ROUTE_SCOPE` 双向 anti-drift 绿；受限用户 `/ai/turn` 走 filter；`run_sql` 对受限用户 `scope_denied`
- [ ] run_sql：`SELECT 1` 通过；`DELETE` / `WITH d AS (DELETE…) SELECT` / `FLUSH TABLES` / `LOCK TABLES` / 多语句 / `INTO OUTFILE` / `SLEEP()` → `invalid_argument`（AST 挡）；`readonly` 实测 `DELETE` 被账号拒；长查询 → `upstream_timeout` 15s；UI 显示 SQL 原文 + ⚠ 徽章；审计 `sql[]`
- [ ] rank_accounts：top 10 与手工 SQL 对账一致；cent 不出 100×；受限用户 `rows_masked_by_scope > 0`
- [ ] calendar：下月 NFP 日期与 BLS 页面一致；抓取失败返回旧缓存 + `stale_since`
- [ ] 部署：`pre-ai-slice2-<日期>` 三镜像；ai-agent rebuild + 活体；`./verify.sh` 绿（基线 pytest 1986 / tsc 0 / vitest 304）

## 笔记

- 硬约束沿用 07 那 10 条 + 08 的 6 条（WAL keepalive / py3.11 TypedDict / `connect_readonly` + `core/sse.py` / `.dockerignore` 护栏 / prompt Today 段 / 两处 .env 与三镜像标签）。
- 不做：主 agent 接 web search（Tier 4 留第三刀）；回填活库 `ai`；`get_orders`（只记候选）。

## 结果

（未开始。）
