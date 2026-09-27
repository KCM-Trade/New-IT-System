---
id: OPT-0064
title: AI 分析 agent 第一刀 —— `ai` 模块 + 独立 agent 容器（Microsoft Agent Framework + Azure OpenAI gpt-5.6）+ `/api/v1/ai/turn` SSE + 三个受信工具 + 三轴贯通 + 来源徽章 + 审计 + 配额
status: wip
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

（待填：02 §1 / §2.1 / §2.2 / §2.5 / §2.6 / §2.7 / §3.1–3.3 / §4.1–4.3 / §5 / §6 / §7 逐条 ✅/⚠，偏差写明原因。）

### 实施记录

（待填：每个 Day 做了什么 / 验证了什么 / 卡在哪。）

## 结果

（done 时填。）
