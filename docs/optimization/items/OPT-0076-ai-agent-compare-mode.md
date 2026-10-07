---
id: OPT-0076
title: AI agent 多模型对比模式（一次提问并发 2–3 个模型，用户选答案）
status: idea
priority: P2
area: mixed
effort: L
created: 2026-10-07
related: [[OPT-0075]], [[OPT-0073]]
---

## 问题

用户（2026-10-07）想要：提问后同时发给 2–3 个模型，并排显示结果，由用户选哪个更好 / 选用哪个结果。
前置是 OPT-0075（grok-4.7 + DeepSeek-V4-Pro 可单独选用）。

## 已有的调研结论（2026-10-07，三份联网调研 + 一份代码调研，来源在会话记录；这里只留结论）

- **成熟做法**（TypingMind / ChatJS / LibreChat / Open WebUI）：同一个 user message 下挂多个兄弟回答 + `selected`
  标记；只有选中的那条进入后续对话，没选中的留作评测数据。
- **用户选择有偏差**：人偏向自信、篇幅长的答案（Hosking ICLR 2024、Steyvers NMI 2025、Kim CHI 2025）。对策：
  并排显示每个模型的工具调用 + 关键数字，数字不一致时自动标出；可选盲标（A/B/C、随机顺序、选完揭晓）+ 理由码。
- **不要自动合并答案**（Self-MoA 2025：混合不同模型常拉低质量；对报数字的 agent 会把一对一错揉成一个看着合理的数）。
- **选择记录 = 评测集**：用户只有几个人，Elo / Bradley-Terry 收敛不了；攒 50–100 次选择后按问题类型定缺省模型。
- **本项目有利条件**：三家模型都走 Responses API，同一份 session blob 跨模型可续（OPT-0075 实测），
  所以「三个模型从同一份历史出发、选中者的 blob 写回」不需要格式转换。

## 要设计的（都还没定）

1. **会话分叉**：`ai_sessions` 现在一会话一 blob、同会话并发第二轮 409（`turn_started_at` 认领）。对比模式要：
   同一 parent blob 起 N 个 run；选中者的 blob 写回；没选就离开页面时的缺省规则；`ai_messages` 怎么存兄弟回答。
2. **从库负载**：每个模型各跑自己的工具循环 = N 份 SQL 并发（`rank_accounts` 一周窗口 ~10s；框架
   `allow_concurrent_invocation` 为 True）。要并发上限，或同一次对比内相同工具调用结果复用。
3. **配额与成本**：每人每天 100 轮 / $20——按一次提问还是按一次模型运行计。
4. **限流**：OPT-0075 实测两个新模型在一分钟 ~5 万 token 时就撞 rate limit，对比模式会放大。
5. **审计**：`ai.query.submit` 一轮一行 → 一行里带每个模型的 tools / tokens / cost + 用户选了谁（+ 理由码）。
6. **前端**：N 列并排流式（含工具指示）、一个共享的 Stop、单模型超时/失败不阻塞选择、差异高亮。
7. **入口**：做成每次提问可选的开关，不做成默认模式；缺省 2 个模型。
8. **部分失败 / 取消**：一个模型报错或慢时怎么展示；Stop 后后台 drain 保记忆的现有逻辑如何对 N 个 run 生效。
9. **逐条消息的模型名**（OPT-0075 冷审 #8，用户 2026-10-07 拍板并入本单）：`ai_sessions.model` 每轮被覆盖、
   `ai_messages` 不记模型，混用模型的会话回看时分不清哪句是谁答的（`routes/ai.py` 的 `append_turn_messages`）。
   对比模式要存兄弟回答，本来就得改这张表：加 `model` 列并在界面上标出每条回答的模型。

## 设计分析（2026-10-07，OPT-0075 上线后；是建议不是定案，带 ⚑ 的要用户拍板）

### 现状里对设计有利 / 有约束的事实（都已核对）

- **agent 容器无状态**：`POST http://ai-agent:8010/v1/turn` 接收 `{caller, scope, session_id, message, model, session_blob}`，
  跑完回 `session_state`（新 blob）。对同一个 parent blob 并发调 N 次、各带不同 `model`，容器侧**零改动**。
- **五个模型同一种接口**（Responses API），同一份 blob 跨模型可续（OPT-0075 实测）→ 选中者的 blob 直接成为会话的新 blob，无格式转换。
- **一会话一把锁**：`ai_usage_db.claim_turn`（`turn_started_at`，`core/ai_usage_db.py:349`）+ `routes/ai.py:279-284` 的 409。
  对比轮次只认领一次，锁要持有到 N 个 run 全部结束。
- **存储**：`ai_sessions(session_id PK, …, blob, turns, model)`、`ai_messages(session_id, seq UNIQUE, role, text, tools_json, usage_json, error_code, at)`
  （`core/ai_usage_db.py:71-94`）。`ai_messages` 没有 model 列（第 9 点），也表达不了「同一 seq 下多个兄弟回答」。
  新列只能走 `_migrate_add_column`（`:161-180`），`backend/data/` 是 dev/prod 共享挂载——dev 一重启就迁移 prod 的库。
- **主 API 一轮的顺序**（`routes/ai.py:216` 起）：认领 → `audit_deferred` → 配额检查 → `increment_turn` → `open_agent_stream`
  → 逐帧转发（记 tools / subjects / scope_denied / usage）→ 存 blob → 审计 `ai.query.submit` → `append_turn_messages` → 释放认领。
  断线后有 120s 后台 drain 保记忆（`DISCONNECT_DRAIN_SECONDS`）。
- **`/api/v1/ai/turn` 在三张表里有特殊待遇**：nginx `location = /api/v1/ai/turn`（关 buffering）、
  `api_key_middleware.SESSION_ONLY_PATHS`、`data_scope.ROUTE_SCOPE`（filter）。**新开一条 SSE 路径 = 三处都要加 + rebuild web 镜像**。
- **前端**：`pages/AiAssistant.tsx`（480 行）+ `hooks/useAiTurn.ts`（405 行，单流状态机）+ `components/ai/*`。

### 建议的形态

1. **不新开 SSE 路径**：`POST /ai/turn` 的请求体加可选 `compare_models: [..]`（2–3 个，必须都在 `AiModel` 里）；缺省 = 现在的单模型行为。
   SSE 事件加一个 `run` 字段（模型名）区分来源。省掉 nginx / 豁免表 / `ROUTE_SCOPE` 的改动，老客户端不受影响。
2. **主 API 做扇出**：一次认领、一次配额检查，然后对 agent 并发开 N 条流，合并成一条 SSE 回浏览器。
   单个 run 失败 / 超时只标记那一个，不取消其他；Stop 取消全部；断线 drain 对 N 条都生效。
3. **候选表**：新表 `ai_turn_candidates(session_id, turn_seq, model, text, tools_json, usage_json, error_code, blob, selected, selected_at, reason)`。
   对比轮次结束时**不推进** `ai_sessions.blob`，会话进入「待选择」；`POST /ai/sessions/{id}/select {model, reason?}` 把选中者的 blob
   写回、写 `ai_messages`（带 `model`）、把未选中者的 blob 置空（只留文本 + 工具 + 用量做评测数据——blob 只增不减是已知问题，别再乘 N）。
4. **待选择期间再发新一轮 → 409**（新错误码，前端提示「先选一个答案」）。⚑ 没选就离开：建议**不自动选**，回到会话时仍显示候选。
5. **审计**：`ai.query.submit` 仍一行，`new_value.runs[]` 每个模型一项（tools / tokens / cost / error）；选择是「人做的 + 改了状态」，
   新 action `ai.compare.select`（记 model、reason、另外几个候选的模型名）。
6. **配额** ⚑：成本自然相加；轮次建议按 N 计（否则对比模式是绕过 100 轮上限的办法）。
7. **负载**：N 上限 3、缺省 2；主 API 侧对「对比轮次」加一个全服并发上限（如同时 2 个），超了回 `busy` 而不是排队。
   同一次对比内相同工具调用的结果复用 **v1 不做**（要在 agent 侧引入跨 run 状态，破坏无状态）。
8. **前端**：N 列并排，各自流式 + 工具指示；每列顶部显示调了哪些工具（`tool_use.input` 已有）；「选这个」按钮 + 可选理由码
   （数字对 / 更清楚 / 更快）。**数字差异自动高亮 v1 不做**，先把工具调用并排露出来——这是抵抗「选文笔好的」最便宜的一步。
9. **盲标** ⚑：建议 v1 **带标签**（用户明确想比较 Grok / DeepSeek / GPT，盲标与这个目的相悖）；评测口径上注明是带标签的偏好。

### 立项五问（`docs/ai-agent/09-agent-patterns-guide.md` §6）

1. 一次调用 + 一个工具能答？不能——这是把现有 ReAct 轮次并行跑 N 份，不是新工具。
2. 步骤固定吗？扇出 / 收集 / 选择是固定的 → 写成主 API 里的确定性编排，**不**让模型决定「要不要对比」「谁更好」。
3. 模型自己决定顺序？每个 run 内部仍是现有模式 7，护栏（工具门控 / scope / 超时 / 审计）原样继承；run 之间无交互。
4. 会让模型写内容再执行吗？不新增。`run_sql` 的现有守卫对每个 run 各自生效。**不做**自动合并 / LLM 裁判。
5. 会改状态吗？只有「选择」改会话状态 → 走审计（`ai.compare.select`），不需要审批流。

### 分刀建议

- **3a 后端契约 + 存储 + 扇出**（主 API、`ai_usage_db`、schema、审计、测试；agent 容器不动）
- **3b 前端**（`useAiTurn` 改成多 run 状态、并排列、选择、待选择态、历史回显兄弟回答）
- 先冻结契约（02 新增一节：请求体 / `run` 字段 / select 接口 / 409 码 / 审计形状），再并行做 3a / 3b。
- 与 OPT-0077 的关系：0077 的 `GET /ai/models` 能让模型多选框不再硬编码，**不是硬前置**；先做 0076 的话，模型多选框沿用现有 `AI_MODELS`。

### 要用户拍板的（升级 Ready 的门槛）

| # | 问题 | 建议 |
|---|---|---|
| ⚑1 | 没选就离开会话 | 不自动选，保持待选择；回来再选 |
| ⚑2 | 配额口径 | 轮次按 N 计，成本相加 |
| ⚑3 | 缺省模型组合 / N | 缺省 2 个：`gpt-5.6-terra` + 用户上次选的另一个；上限 3 |
| ⚑4 | 盲标还是带标签 | 带标签 |
| ⚑5 | 理由码 | 可选不必填 |
| ⚑6 | 谁能用 | 所有持 `ai` 的人（含受限用户）——受限用户每个 run 的工具门控不变 |

## 升级到 Ready 的条件

- OPT-0075 上线，并且用户拿真实问题单独用过两个新模型（否则对比的对象本身不可用）。
- 按 `docs/ai-agent/09-agent-patterns-guide.md` §6 立项五问写 plan。
- 用户拍板上面 1（缺省规则）、3（配额口径）、7（缺省模型组合）。

## 结果

（未开始）
