---
id: OPT-0064
title: AI 分析 agent 第一刀 —— `ai` 模块 + 独立 agent 容器（Microsoft Agent Framework + Azure OpenAI gpt-5.6）+ `/api/v1/ai/turn` SSE + 三个受信工具 + 三轴贯通 + 来源徽章 + 审计 + 配额
status: done
priority: P1
area: mixed
effort: XL
created: 2026-09-27
claimed: 2026-09-27
related: [[OPT-0063]] [[OPT-0062]]
---

> ⚠ 按 tracker 规则这是 net-new feature，本应走 `feat/` 分支不进 tracker；用户 2026-09-27 明确要求「用 optimization-tracker file 一个 OPT 并 claim」（同 OPT-0062 / OPT-0063 先例），照办。分支名按用户指定 **`feat/ai-agent-slice1`**（不是 `opt/…`）。
> ⚠ 执行隔离铁律（file 后换 worker）本次由用户显式豁免：kickoff prompt（`docs/ai-agent/07-kickoff-prompt.md`）要求同一会话 file → plan → 实施，并允许按文件所有权 fork 并行。
> **本文件不是实施 SSOT。** 决策 / 契约 / 分刀 / 验收全部在 `docs/ai-agent/`（唯一入口 `index.md`）；这里只记 tracker 需要的元信息、与契约的对账结果、实施偏差与 follow-up。实施者先读 `docs/ai-agent/07-kickoff-prompt.md` 那 10 条硬约束。

## 问题

risk team 要一个登录后按人隔离的分析 agent：问「客户 123456 怎么样 / 怎么交易的 / 命中过什么风控信号」，得到**每个数字都有认证口径来源**的回答。老板愿景见 `docs/analysis/ai-risk-platform-blueprint.md`；需求 2026-09-25 收窄到 risk team 分析场（`docs/ai-agent/01-decisions.md` R0–R5）。

第一刀 = 让同事在 dev 上看见并用起来的最小闭环：功能少（3 个工具、无历史、无多会话），但**数字必须对**（01 C9：砍广度不砍正确性）。

## 背景（已定、勿重推）

| 事项 | 状态 | 出处 |
|---|---|---|
| harness = Microsoft Agent Framework（Python `agent-framework-openai`），模型 = IT 名下 Azure OpenAI `kcm-ai-agent-east-us` 上的 `gpt-5.6-terra`（深度分析 `gpt-5.6-sol`） | ✅ 09-26 拍板 + 资源已建 + curl 验证 | 01 R6 / C13 / C16，04 §0.1.2 |
| 凭据 `backend/.env.ai-agent`（0600，gitignored） | ✅ 已存在 | 04 §0.1.3 |
| `ai` 模块 key（第 6 个，全新能力型 → **不回填活库**，不进 `SCOPED_MODULES`） | 契约定稿 | 02 §1 |
| 三个受信工具的签名 / 信封 / 错误码 / 上限 | 契约定稿 | 02 §2–§3 |
| 内部接口：浏览器 →(session cookie, SSE)→ 主 API `/api/v1/ai/turn` →(`X-Internal-Token`)→ `ai-agent:8010/v1/turn` | 契约定稿 | 02 §4 |
| 审计 `ai.query.submit` 每轮一行含失败；配额表 `ai_usage_daily` 在新 SQLite `backend/data/ai_agent.db` | 契约定稿 | 02 §5–§6 |
| 验收清单（授权 / 正确性 / 安全 / 留痕配额 / 链路 / 部署） | 定稿 | 05 §2 |

安装版 MAF 实测（2026-09-27，`pip download` 到 scratchpad 看源码，未装）：`agent-framework-openai 1.14.4` + `agent-framework-core 1.19.0`；装饰器是 **`@tool`**（`agent_framework._tools.tool`），`Agent(client=OpenAIChatClient(model=, base_url=, api_key=), instructions=, tools=[...])`，`agent.run(msg, stream=True, session=AgentSession())` 产出 `AgentResponseUpdate.contents[]`，每个 `Content.type ∈ {text, function_call, function_result, usage, …}`，`usage_details` 有 `input_token_count / output_token_count / cache_read_input_token_count`。

## 假设 / 待验证

- [ ] 只读 DB 账号 `ai_agent_ro` 是否已开（05 §4 第 2 条）。未开 → 先用现有账号跑通，**报告标红 TODO**。本机 auto-mode 拦了 `SHOW GRANTS` 这类对生产库的读，需用户自己跑。
- [ ] `backend/data/risk_monitor.db`（WAL 模式）以 `:ro` bind mount 给 agent 容器后能否只读打开（`-wal`/`-shm` 存在时 SQLite ≥3.22 可只读读取；不行则 `get_risk_signals` 的告警腿改经主 API 内部只读端点）。
- [ ] SSE 经 `BaseHTTPMiddleware` 链（Trace/CORS/APIKey/Auth/AuditMissing）流式不被聚合——现有 `/risk-monitor/alerts/stream` 已证明可行，但 POST 体 + 长流要再验一次。

## 验收标准

= `docs/ai-agent/05-rollout.md` §2 全绿（授权 5 条 / 正确性 3 条 / 安全 4 条 / 留痕配额 2 条 / 链路 2 条 / 部署 2 条），其中「部署」两条本刀只做到 dev 可演示、不上 prod。另加 kickoff prompt 第 8 条的**最少测试集**：
- [ ] `/api/v1/ai/*` 被 `test_app_assembly.py` 两条 MODULE_MAP anti-drift 覆盖
- [ ] 无 `ai` 模块 → 403 非 401；`AUTH_ENABLED=false` 闸恒过
- [ ] 工具级：`scope_denied` / `range_too_wide` / `subject_excluded` 三种结构化错误（不是异常）
- [ ] `quota_exceeded`（`AI_DAILY_TURNS_LIMIT=2` 第 3 轮）
- [ ] 每轮 `audit_log` 一行 `ai.query.submit`（含失败轮）
- [ ] agent 容器不可达 → `event: error / agent_unavailable`，主 API 不崩
- [ ] `./verify.sh` 绿（tsc + vitest + pytest，不跑 `--full`）

## 笔记

### 契约对账（主线程逐条对 02，实施完成后填）

| 02 条目 | 状态 | 说明 |
|---|---|---|
| §1.1 落点四处 | ✅ | `MODULE_KEYS` + 目录 + `MODULE_MAP ("ai",)` 同一 commit；前端走 `PAGE_POLICIES`（代码里没有 `<ModuleRoute module=…>`，语义相同） |
| §1.2 不回填 / §1.3 不进 SCOPED_MODULES | ✅ | 活库未动；anson/rose 未勾；受限者调 `/ai/*` 由覆盖闸 403（单测钉住） |
| §1.4 anti-drift | ✅ | `test_app_assembly.py` 两条自动覆盖 + `test_module_gate.py` probe 加 `/ai/turn` `/ai/usage/today` |
| §2.1 闭包 + `is None` | ✅ | `build_tools(ctx, emit)` 每请求构造；静态 grep 无 falsy scope 判定；`scope_denied` 走 `_refusal_log_decision`；`auth_events` 行由**主 API**在收到 `tool_done scope_denied` 时写（容器内 users.db 只读，冷审 H1） |
| §2.2 口径分解 | ✅ | 交易净入金 / IB 提现分列；CEN ÷100；demo/员工 → `subject_excluded`；sid=5 归一化；`.cent/.kcmc` 判 cent；日界 `MT_SERVER_TZ` |
| §2.3 主体只收精确 ID | ✅ | `client_id` / `login_sid`；MT 账户无 CRM 归属 → `subject_excluded(no_crm_user)` |
| §2.4 366 天 | ✅ | `range_too_wide` 单测；活体里模型自己拆段 |
| §2.5 信封 | ✅ | `definition` + `source.certified=true` + `scope.cids_applied` + `truncated` 每次都带 |
| §2.6 错误码 | ✅+ | 六个都有；**新增** `invalid_argument`（格式错） |
| §2.7 上限 | ✅ | 366 / 200 行 / 500 告警 / MySQL 5s·20s·15s / 单工具 25s |
| §3.1 overview | ✅ | `net_gain_definition` 用服务真实 STRICT 公式；账户实时 `mt4_users`（契约原点名的 `client_pnl_service.get_client_accounts` 是 ETL 快照、`credit` 恒 0）——**02 §3.1 已于 09-27 回填** |
| §3.2 trade_activity | ✅ | 新 `trade_activity_service.by_subject`，口径 helper 全部 import `window_scan_service` |
| §3.3 risk_signals | ⚠ | `verdict` 恒 null ✅；`severity` 恒 null（源无列）；`days_cooccur` null（源无共现天数）；共用 IP 截最近 30 天；对端按 scope 过滤 + `peers_masked_by_scope` ✅；IP /24 ✅ |
| §4.1 `/ai/turn` | ✅ | POST + SSE、免 API key、`async def` + `to_thread`；前端 `apiFetch` 流式读（`EventSource` 做不到 POST，用户拍板 A，**02 §4.1 已回填**） |
| §4.2 内部接口 | ✅ | `X-Internal-Token` compare_digest、无宿主端口、身份整体传、每请求 `Agent`+`AgentSession`、只三个工具 |
| §4.3 事件 | ✅+ | 七种事件；`tool_done` 失败带 `error_code`；`usage.cost_usd` 由主 API 填 |
| §5 审计 | ✅ | 每轮一行含失败，`finally` 写；`audit_deferred` 机制；未进 `AUDIT_EXEMPT_ROUTES`；`audit-log-design.md` 已加行 |
| §6 配额 | ✅ | `backend/data/ai_agent.db`；转发前拦截；100 轮 / $20 env 可调；状态条 |
| §7 禁止项 | ⚠ | 无文件/Bash/网络工具 ✅（MAF 结构性）；无宿主端口 ✅；不挂 `backend/.env` ✅；🔴 **DB 账号仍是共享账号**（`ai_agent_ro` 待开）；mounts 无 honeypot ✅；返回无姓名/邮箱/手机/完整 IP ✅ |

### 实施记录

见 `docs/ai-agent/05-rollout.md` §5（实施记录，含 12 条实现层偏差、dev 实测数字、待办）。冷审（独立 Opus agent，2026-09-27）结论与处理：H1 容器内 `record_auth_event` 必失败 → 改主 API 写；H2 `risk_monitor_db` 私有符号重写 → 改公开 `query_alert_events(conn=, logins=)`；H3 共用 IP 腿失败拖垮整个工具 → 降级为 null+caveat；M4 MySQL 连接拷贝 → 收口 `core/mysql_readonly.py`；M5 对他人 SQL 常量 `.replace` → 服务暴露 `activity_status_case()`；M6 SSE 序列化三份 → `core/sse.py`；M7 每工具重解主体 → per-turn memo；L10 `requirements-ai-agent.txt` 被 `*.txt` gitignore → 加 `!`；L11 测试收集依赖 `agent_framework` → importorskip；前端每 token 全量 re-render → rAF 批量 + memo。未采纳：L8 删 API key 豁免（kickoff 硬约束 4 要求豁免）；L9 dev 整目录挂载（硬约束 6 要求挂载无 honeypot）。

## 结果

**2026-10-07 补 close**（账面清理，无代码改动）：两个 close 条件——UI 二轮与 PG `ai_agent_ro`——都已在 2026-09-27 15:44 那次部署落地，此后一直没走关闭流程。MySQL 侧 `ai_agent_ro` 用户 09-27 拍板不建，不算遗留。后续刀次见 OPT-0065 / 0066 / 0069 / 0075。

**2026-09-27 14:24 已上 prod**：merge `ac21437`、`aaf9b21`，回滚标签 `pre-ai-slice1-20260927`；闸门 pytest 1971 / tsc 0 / vitest 304。待办见 `docs/ai-agent/05-rollout.md` §5.4–5.6。

**2026-09-27 15:44 第二次部署**：UI 二轮（`9f89e2b`，ChatGPT 式居中输入框、模型下拉）+ prompt 每轮注入当前日期（`1cf088d`，修「最近 N 天」按训练截止日解析的 bug）+ PG 只读角色 `ai_agent_ro` 接上 prod/dev；回滚 `new-it-system-{api,web,ai-agent}:pre-ai-ui2-20260927`；闸门 pytest 1986 / tsc 0 / vitest 304。剩：MySQL `ai_agent_ro`（用户自建）、第二刀。
