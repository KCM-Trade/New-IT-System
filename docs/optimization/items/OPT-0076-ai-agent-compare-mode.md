---
id: OPT-0076
title: AI agent 多模型对比模式（一次提问并发 2–3 个模型，用户选答案）
status: done
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

### 用户拍板（2026-10-07，六项全部按建议）

| # | 问题 | 决定 |
|---|---|---|
| 1 | 没选就离开会话 | **不自动选**，会话保持待选择；回来时仍显示候选 |
| 2 | 配额口径 | 轮次**按模型数计**，成本相加 |
| 3 | 缺省模型组合 / 上限 | 缺省 **2 个**：`gpt-5.6-terra` + 用户上次选的另一个；上限 **3** |
| 4 | 盲标还是带标签 | **带模型名** |
| 5 | 理由码 | **可选**，不必填 |
| 6 | 谁能用 | **所有持 `ai` 的人**（含受限用户）；每个 run 的工具门控不变 |

用户同日追加的两条要求：

- **对比模式必须由用户手动打开 / 关闭**：缺省关闭，页面上有明确的开关；关着时行为与现在的单模型完全一样。
  （开关状态是「怎么看数据」的用户偏好 → 按 CLAUDE.md 的过滤器持久化约定走 `useFilterPersist`，并决定是否进 View Profiles 的
  `FILTER_STATE_KEYS`；开关**不**改变「待选择」会话必须先选的规则。）
- **前端要看得清楚**：不能是三列挤在一起的小字。并排布局的最小列宽、窄屏降级方式（标签页 / 上下堆叠）、模型名常驻、
  工具调用常驻可见，都是验收项。开工前先看下面「前端方案调研」。

### 前端方案调研（2026-10-07，联网调研 agent；只读了源码 / 文档，没有跑过任何一个 UI）

**结论**：没有现成的「对比视图」组件库可以直接装。自己写一个小布局（约 150 行），形态借鉴开源实现，复用本项目已有的
`components/ai/MarkdownMessage.tsx`（react-markdown + remark-gfm）和 `SourceBadge.tsx`。

| 开源实现 | 许可 | 栈 | 布局 | 怎么选 | 能否抄代码 |
|---|---|---|---|---|---|
| LibreChat（45k★，维护中） | MIT | React + TS | `flex-col md:flex-row`，每列 `min-w-0 flex-1` 带边框；窄屏上下堆叠；每列头部有图标 + 模型名 + 分支按钮；**首帧就渲染占位列** | 每列一个分支按钮 | ✅ |
| ChatJS（1.2k★，维护中） | Apache-2.0 | React 19 + Tailwind 4 | **不是分列**：一个主答案 + 一排小卡片（模型名 + 状态） | 点卡片切为当前路径；其余继续在后台流，各自有 stop | ✅ |
| big-AGI「Beam」（7k★） | MIT | React 18 + MUI | 全屏覆盖层，CSS grid 自适应，最小列宽约 390px | 选一个带回对话，或合并 | ✅（MUI，借形不借码） |
| Open WebUI（154k★） | 自定义 BSD-3 + 品牌条款 | Svelte | 横向滚动卡片，最小宽 320px；可设置成标签页 | 点卡片设为当前；有「合并回答」 | ❌ 只借设计 |
| LobeHub（83k★） | 社区许可（禁衍生） | React | 有 compare 概念，UI 未核实 | 未核实 | ❌ 只借设计 |
| assistant-ui / HF chat-ui | MIT / Apache-2.0 | React / Svelte | 只有一次看一个的分支切换，没有并排 | — | — |

可选的积木（都不是必须）：Vercel AI Elements 的 `Tool` 折叠组件（Apache-2.0，shadcn registry，README 假设 Next.js + AI SDK，
在 Vite 下要改类型，**未实测**）；`use-stick-to-bottom`（MIT，流式时每列贴底）；`react-resizable-panels`（MIT，shadcn Resizable 的底座）。
没有找到「数字不一致高亮」的现成库。

可读性依据：正文行长 50–75 字符为宜（Baymard；WCAG 1.4.8 上限 80），14–15px 字号下约 420–640px 文本宽。
Open WebUI 的 320px / big-AGI 的 390px 低于这个下限，就是用户明确不要的「挤」。没有找到关于「分列 vs 标签页」的 HCI 研究，
下面的阈值是由行长推出来的，属推断。

**推荐布局（调研 agent 的设计，作为起点；假设侧栏约 260px——这个假设不成立，以文末「布局方案」为准）**

- **规则：一列至少 480px，否则退成标签页。** 用容器查询（container query），不要用视口断点。对比块突破普通对话的最大宽度，占满内容区。
- **2 个答案**：所有目标屏宽都是等宽两列（1280 约 490px / 1440 约 570px / 1920 封顶 720px）。
- **3 个答案**：1920（侧栏开）或 1440（侧栏收起）→ 三列，每列 ≥480px；1440 / 1280 → 顶部标签（模型名 + 状态点 + 工具调用数），
  一次看一个，另提供「固定第二个」切成两列。
- **每一列从上到下**：① 吸顶头部：模型名、状态（生成中 / 完成 / 失败可重试）、耗时与 token；② 工具条，**始终可见**、可换行，
  每个 chip 显示 `工具(参数…)`，点开看详情；只有一个模型调用过的工具加描边提示；③ Markdown 正文，自然高度、整页一个滚动条，
  表格在列内横向滚动；④ 底部一个占满列宽的主按钮「用 <模型> 的回答继续」。复制等次要操作放头部做成图标，避免与选择按钮混淆。
- **选完之后**：选中的那列收成一条普通消息，带「从 N 个中选出」标记；理由输入框内联出现、不阻塞；
  没选中的折进「查看其他回答（N−1）」，留在历史里。
- **出错**：失败的那列保留头部和工具条，正文显示错误，不影响其他列。
- 不做同步滚动、不做等高列（调研到的四个实现都没做）。

## 事实核对（2026-10-07 plan 阶段，对照当前 main `d91606d`）

「设计分析」里的行号与流程描述都还对得上（`claim_turn` `ai_usage_db.py:349`、409 `routes/ai.py:279-284`、表结构 `:71-94`、
`_migrate_add_column` `:161-180`、一轮的顺序、120s drain、`/ai/turn` 的三处特殊待遇、前端两个文件的行数）。
agent 容器确认不用改：`server.py` 只校验 `model ∈ allowed_models()`，`harness.run_turn` 不使用 `session_id`、没有跨请求状态，
工具门控（`risk_tools_enabled` / `run_sql_enabled` / skill 受众）只读 `ctx`。

不一致或需要修正的有六处：

1. **前端可用宽度比调研假设的小得多。** 调研按「侧栏约 260px」算；实际左边有两栏：应用侧栏 288px（`collapsible="offcanvas"`，收起为 0）
   和 AI 页自己的历史栏 240px + 16px 间距，另有页面内边距 48px、滚动条约 15px。
   所以「2 个答案在所有目标屏宽都是两列」和「1440 侧栏收起可三列」都不成立——见下方「布局方案」的实测矩阵。
2. **`TURN_CLAIM_STALE_SECONDS = 360` 小于 `TURN_TOTAL_SECONDS = 560`**（`ai_usage_db.py:346` 的注释还写着 300s）。
   一轮跑过 6 分钟时，第二个标签页能抢走认领，正是这把锁要防的事。既有缺陷；本单改成 720。
3. **「`rank_accounts` 一周窗口 ~10s」已过时**：2026-10-06 起约 0.75s/天，每条 SQL 上限 30s。从库负载的担心变小，并发上限仍保留。
4. **候选表拆成两张**（轮次级 `ai_compare_turns` + 候选级 `ai_turn_candidates`），不是 item 里的一张：问题文本、状态、`base_seq`
   是轮次级的，重复写进每个候选行会让「是否待选择」变成多行判定。
5. **待选择期间不写 `ai_messages`**（item 原写的是选择时才写 assistant 行，没说 user 行）。两行都推迟到选择那一刻：
   dev/prod 共享同一个 `ai_agent.db`，当前 prod 代码不认识新表，这样它看到的是「这轮还没发生」。
   旧代码若在待选择期间推进了会话，选择时用 `base_seq` 发现并回 `409 compare stale`。回滚镜像也靠同一条规则，不需要回滚数据。
6. **审计行有 2000 字符上限**（`audit.MAX_VALUE_LEN`），超了尾部截断、JSON 不可解析。`runs[]` 必须精简，`subjects` / `sql` 不在每个 run 里重复。

推荐布局里「失败可重试」v1 不做（要另一个接口，且重试的 run 与其余 run 不再同一时刻）。

## Plan（2026-10-07，待用户确认后实施）

### 立项五问（`docs/ai-agent/09-agent-patterns-guide.md` §6，核对后照抄）

1. 一次调用 + 一个工具能答？不能——这是把现有 ReAct 轮次并行跑 N 份，不是新工具。
2. 步骤固定吗？扇出 / 收集 / 选择是固定的 → 写成主 API 里的确定性编排，**不**让模型决定「要不要对比」「谁更好」。
3. 模型自己决定顺序？每个 run 内部仍是现有模式 7，四条护栏（工具门控 / scope / 超时 / 审计）原样继承；run 之间无交互。
4. 会让模型写内容再执行吗？不新增。`run_sql` 的现有守卫对每个 run 各自生效。**不做**自动合并 / LLM 裁判。
5. 会改状态吗？只有「选择」改会话状态 → 走审计（`ai.compare.select`），不需要审批流。

### 接口契约

SSOT = `docs/ai-agent/02-contracts.md` §19–§24（该目录不进 git；worktree 里的 worker 读主工作区的绝对路径
`/opt/myproject/New-IT-System/docs/ai-agent/02-contracts.md`）。摘要：

- 请求体加可选 `compare_models`（2–3 个、互异、都在 `AiModel` 里，否则 422）；不带 = 现在的单模型行为。
- SSE：对比轮次里每个来自 agent 的事件多一个 `run`（模型名）；另有整轮的 `compare` 事件和不带 `run` 的终止 `done`。
- 待选择时再提问 → `409 compare pending`（与是否带 `compare_models` 无关）。
- `POST /ai/sessions/{id}/select {compare_id, model, reason?}`：一个事务里写回 blob、写 `ai_messages` 两行、清所有候选 blob。幂等，可事后补理由。
- 存储：两张新表（`CREATE TABLE IF NOT EXISTS`）+ `ai_messages.model` / `ai_messages.compare_id` 两个可空列（`_MIGRATIONS`）。
- 审计：`ai.query.submit` 仍一行，带 `models` 与精简的 `runs[]`；新 action `ai.compare.select`。
- 配额：`turns += N`，`turns + N > 上限` 就拒；成本相加。全服并发对比轮次上限 `AI_COMPARE_MAX_CONCURRENT`（缺省 2）。

### 实施时由我定的几件小事（不改变已拍板的六项）

- 「上次选的另一个」第一次没有历史值时，第二个模型取 `grok-4.7`。
- 开关与模型组合存 `AI_ASSISTANT_MAIN_FILTERS_V1`（`useFilterPersist`），**不进** View Profiles 的 `FILTER_STATE_KEYS`：
  档案可以被别人认领，认领一个档案就把对方的对比开关打开、配额加倍消耗，不合适。
- 单模型模式唯一看得见的变化：每条回答下面标出模型名（item 第 9 点，历史回看同样有）。
- 全部 run 失败时不进入待选择（没有可选的东西），按整轮失败显示，可以直接再问。
- 只有一个 run 成功时仍要用户点一下（不自动选）。

### 改动文件

后端（worker A）：

| 文件 | 改动 |
|---|---|
| `backend/app/schemas/ai.py` | `TurnRequest.compare_models`；`SelectRequest`；`SessionMessage.model/compare`；`SessionDetail.pending_compare`；`SessionSummary.pending_compare` |
| `backend/app/core/ai_usage_db.py` | 两张新表、两个新列；`increment_turn(n)`；`begin_compare` / `finish_compare` / `select_candidate` / `get_pending_compare`；`claim_turn` 加待选择条件；`append_turn_messages(model=)`；详情与列表查询；`purge_ai_sessions` 连带删；`TURN_CLAIM_STALE_SECONDS` 720 |
| `backend/app/api/v1/routes/ai.py` | 把「消费一条 agent 流」抽成一个函数，单模型与对比共用；对比的扇出 / 收尾 / 审计；`select` 路由 |
| `backend/app/core/config.py` | `AI_COMPARE_MAX_CONCURRENT`（缺省 2） |
| `backend/app/core/data_scope.py` | `ROUTE_SCOPE` 加 `/ai/sessions/{session_id}/select`: OPEN |

`ai_gateway_service.py`、`app/ai_agent/**`、`frontend/nginx.conf`、`api_key_middleware`、`AUDIT_EXEMPT_ROUTES` 都不改。

后端测试（worker B，只按契约写，不看 A 的实现）：新文件 `backend/tests/test_ai_compare.py`；`test_data_scope.py` / `test_app_assembly.py` 若有字面清单则补一行。

前端（worker C）：

| 文件 | 改动 |
|---|---|
| `frontend/src/hooks/useAiTurn.ts` | 请求带 `compare_models`；按 `run` 分发事件；`AiMessage.compare`；`select()`；`pendingCompare`；Stop / 刷新后的轮询 |
| `frontend/src/lib/ai-compare.ts`（新）+ `.test.ts` | 纯函数：事件归并、显示顺序、缺省模型组合、可选判定 |
| `frontend/src/lib/ai-session.ts` + `.test.ts` | 新字段的类型与映射 |
| `frontend/src/components/ai/CompareBlock.tsx` / `CompareColumn.tsx` / `CompareModelPicker.tsx`（新） | 并排块、单列、模型多选 |
| `frontend/src/pages/AiAssistant.tsx` | 开关、待选择条、历史栏在对比模式下收成图标、回答下的模型名 |
| `frontend/src/components/ai/AiStatusBar.tsx` / `SessionList.tsx` | 对比时显示合计与「计 N 轮」；列表上的待选择标记 |
| `frontend/src/i18n/locales/{zh-CN,en-US}.ts` | 文案与新错误码（`compare_pending` / `compare_busy` / `compare_failed` / `compare_stale` / `incomplete`） |

复用 `MarkdownMessage.tsx` 与 `SourceBadge.tsx`，不引入新的 Markdown 库，不加新依赖。

主会话：`docs/ai-agent/`（02 已写；实施后补 03 §3 / §7 / §11、05 上线记录、index 状态表）、`docs/features/ai-assistant.md`、
`docs/architecture/audit-log-design.md` 的 action 表、`CLAUDE.md` 的 AI agent 段、verify、冷审。

### 测试清单

后端 `test_ai_compare.py`：

- `compare_models` 校验：1 个 / 4 个 / 重复 / 未知模型 → 422；缺省与 `null` 走单模型且事件里没有 `run`。
- 两个模型：agent 被调两次，payload 只有 `model` 不同；受限调用者的 `scope` 两次都原样是列表。
- 每个 agent 事件带对的 `run`；`session_state` / `skill_loaded` 不外泄；最后是 `compare` + 不带 `run` 的 `done`。
- 一个 run 报错，另一个正常 → `state: pending`，`selectable` 只有正常的那个；全部失败 → `void`、`ai_messages` 两行、不待选择。
- 轮次结束后：`ai_sessions.blob` 没变，`ai_messages` 没新增，`GET /ai/sessions/{id}` 有 `pending_compare`，列表行 `pending_compare: true`。
- 待选择时再 `POST /ai/turn`（带与不带 `compare_models` 各一次）→ `409 compare pending`，agent 未被调用。
- 配额：`turns` 增加 N；`turns + N > 上限` → `quota_exceeded` 且 agent 未被调用；成本 = 各 run 之和且按各自模型定价。
- 并发上限：已有 2 个 `running` → `compare_busy`，不计轮次；过期的 `running` 行不占名额。
- `select`：blob 换成选中者的、`ai_messages` 多两行且带 `model` / `compare_id`、所有候选 blob 为 NULL、之后可以正常再问。
- `select` 的拒绝：别人的会话 404；未知 `compare_id` 404；不可选的模型 422；已选别的模型 409；`base_seq` 不符 409 且轮次变 `void`。
- `select` 幂等；事后补理由只更新 `reason`。
- 审计：一次对比恰好一行 `ai.query.submit`（成功 / 部分失败 / 全失败 / 配额拒绝 / 断线各一），`runs[]` 每模型一项；
  选择一行 `ai.compare.select`；3 个 run × 6 次工具 + 500 字问题的行不超过 2000 字符且可解析。
- 断线：drain 期内跑完的进候选，会话进入待选择；审计 `error_code: client_disconnected`。
- 迁移：在「旧 schema 的库」上跑 `init_ai_usage_db()` 后，用**旧版本的 SQL 文本**（当前 `append_turn_messages` / `get_session_detail` 的语句）仍能读写。
- 旧代码可见性：待选择期间 `ai_messages` 里没有这轮的任何行。
- 认领：`TURN_CLAIM_STALE_SECONDS > TURN_TOTAL_SECONDS` 的断言。
- 单模型回归：`test_ai_route.py`（31 个）与 `test_ai_sessions.py`（30 个）**不改一行**仍全绿；`append_turn_messages` 写入了 `model`。

前端 vitest：事件按 `run` 归并；`run` 缺失时走单模型路径；显示顺序与「放得下几列」的纯函数；缺省模型组合；历史映射（`model`、`compare.alternatives`、`pending_compare`）。

### 验收标准

1. 开关关着时：请求体没有 `compare_models`，事件、落库、审计行形状与现在一致（上面的回归测试 + dev 上实际问一轮）。
2. 开关开着问一句：N 列各自流式、各自显示工具调用；一个模型失败不影响其他列；Stop 一个按钮停全部。
3. 没选就离开 / 刷新 / 换会话再回来：候选仍在，输入框不可用并说明原因；此时关掉开关也不能提问。
4. 选一个之后：对话以该模型的上下文继续（追问「刚才第 2 条」能答上）；其余回答可在「查看其他回答」里展开；候选 blob 已清。
5. 历史回看：每条回答标着模型名；对比轮次标「从 N 个中选出」。
6. 配额条：一次 2 模型对比后「今日已用」加 2，成本为两者之和。
7. **看得清**：任何情况下一列不窄于 480px；放不下就少显示几列，其余在模型条上切换；正文 14px 不缩小；模型名与工具条常驻。
   在浏览器里按 1280 / 1440 / 1920 × 侧栏开 / 收逐格看过（这次不接受只过类型检查）。
8. 受限用户（scope 非空）可用对比；`run_sql` 与 risk 三工具的有无与他单模型时一致。
9. `./verify.sh` 绿。

活体测试只用不含客户数据的问题：经济日历（「10 月有哪些重要数据发布」）、读 skill（「解释一下 MT 服务器的冬令时规则」）、对话内追问（「把上面第二条再说详细点」）。

### 第二步的做法

- 契约已冻结（02 §19–§24）。三个 worker 各自一个 worktree（放 `/opt/myproject/.worktrees/`，该目录在 750 的 `/opt/myproject` 下），
  分支 `opt/ai-agent-compare-mode-{be,test,fe}`，在第四个 worktree 里合到 `opt/ai-agent-compare-mode`。主工作区始终停在 main。
- dev 容器挂载的是主工作区，所以看界面时从集成 worktree 另起一套 api + vite（不同端口、不同 compose project 名），
  ai-agent 容器没改、沿用现有 dev 的。这套 api 启动时会迁移共享的 `ai_agent.db`——迁移对当前 prod 代码兼容，是设计前提。

### 上线与回滚

上线（第三步，用户点头后）：

1. 集成分支 `./verify.sh` 绿 → 在浏览器里看过 → 问是否 outsider-review。
2. `git merge --no-ff` 进 main（close 信息进 merge commit）→ push（被权限分类器拦就停下，由用户自己推）。
3. 打回滚标签：`new-it-system-{api,web,ai-agent}:pre-ai-compare-20261007`（日期按实际上线日）。
4. `./deploy.sh`。api 与 web 镜像都要重建；ai-agent 镜像没有代码变化，但 `deploy.sh` 会一并重建，标签照打三个。
5. 上线后检查：`ai_agent.db` 有两张新表与两个新列；单模型问一轮；对比问一轮并选择；`audit_log` 各有一行；
   `grep AUDIT_MISSING` / `AUDIT_WRITE_FAILED` 为空；03 §12.1 的常规检查。

回滚：把三个镜像切回 `pre-ai-compare-<日期>` 再 `up -d`。**不需要回滚数据**：旧代码不读新表，新列可空；
回滚时还处在待选择的轮次对旧代码不可见，会话可以照常继续；之后若再升级，这些轮次在选择时回 `409 compare stale` 并作废。

### 已知但本单不处理

- 待选择的轮次没有过期时间，N 份候选 blob 会一直留着（会话存储增长问题 2026-09-29 已拍板暂缓）。
- 模型多选框沿用硬编码的 `AI_MODELS`；OPT-0077 的 `GET /ai/models` 上线后再换，不是前置。
- 带标签的选择记录存在偏向（长答案、知名模型），做评测结论时要注明。

## 布局方案（2026-10-07，待用户确认）

Refactoring UI 建议：

- **不靠缩小来塞下内容**：正文保持 14px、行长 50–75 字符；放不下时减少同时显示的列数，不压缩列宽。
- **一屏一个主操作**：只有「用这个回答继续」是实心主按钮；复制、展开工具详情都是图标或文字链接。
- **少用边框，用间距和底色分区**：列与列之间靠 16px 间距和列头的浅底色区分，不画粗分隔线。

### 一条规则

对比块用容器查询量自己的宽度 `A`，同时显示的列数 `k = min(N, ⌊(A + 16) / 496⌋)`，即一列至少 480px、列间距 16px。
块上方始终有一条**模型条**（每个模型一个标签：模型名 + 状态点 + 工具调用数）。`N ≤ k` 时全部显示；`N > k` 时模型条用来选看哪 `k` 个。
`k = 1` 就是普通标签页。两列时每列封顶 720px，块居中。显示几列由 CSS 容器查询决定，JS 只记显示顺序。

对比开关打开时，或打开的会话处于待选择时，历史栏收成一个图标（点开是现在窄屏用的那个抽屉）。不这样的话 1280 和 1440 基本都只能看一列。

### 实测可用宽度与结果

`A = 视口 − 应用侧栏(288 或 0) − 页面内边距 48 − 滚动条约 15`（历史栏已收起）。实际值以浏览器里量到的为准，可能差十几像素。

| 视口 | 应用侧栏 | A | 2 个答案 | 3 个答案 |
|---|---|---|---|---|
| 1280 | 开 | 929 | 标签页，一列 768 | 标签页，一列 768 |
| 1280 | 收起 | 1217 | 两列 × 600 | 两列 × 600，第三个在模型条上切换 |
| 1440 | 开 | 1089 | 两列 × 536 | 两列 × 536，第三个切换 |
| 1440 | 收起 | 1377 | 两列 × 680 | 两列 × 680，第三个切换 |
| 1920 | 开 | 1569 | 两列 × 720（封顶） | 三列 × 512 |
| 1920 | 收起 | 1857 | 两列 × 720（封顶） | 三列 × 608 |

1280 侧栏开时两列各 456px，差 24px 不到线，所以是标签页。若历史栏不收起，六格依次是：一列 / 一列 / 一列 / 两列 552 / 两列 648 / 三列 523。

### 草图

三列（1920，3 个答案；侧栏开每列 512，收起每列 608）：

```
│应用侧栏│ [历史]                                    10 月有哪些重要数据发布？ ┐(用户)
│ 288   │
│ 或 0  │ ┌ gpt-5.6-terra ──────────┐ ┌ grok-4.7 ───────────────┐ ┌ DeepSeek-V4-Pro ────────┐
│       │ │ ✓ 完成 12s · 8.1k tok ⧉ │ │ ● 生成中 9s           ⧉ │ │ ✕ 失败                  │ ← 列头吸顶
│       │ │ [✓ get_economic_calendar]│ │ [✓ get_economic_cal…]   │ │ [✓ get_economic_cal…]   │ ← 工具条常驻
│       │ │                          │ │ [◌ load_skill(mt-dst)]  │ │                         │   可换行
│       │ │ 10 月的重点是 …          │ │ 本月有三项 …            │ │ 模型返回错误            │
│       │ │ | 日期 | 事件 | …        │ │ 1. 10-03 非农 …         │ │ rate_limited · trace …  │
│       │ │ （表格在列内横向滚动）   │ │ ▍                       │ │                         │
│       │ │                          │ │                         │ │                         │
│       │ │ [ 用 gpt-5.6-terra 继续 ]│ │ [ 生成中… ]（禁用）     │ │ [ 不可选 ]（禁用）      │ ← 按钮同一行
│       │ └──────── 512 / 608 ──────┘ └──────── 512 / 608 ──────┘ └──────── 512 / 608 ──────┘
│       │ ┌─────────────────────────────────────────────────────────────────────────────────┐
│       │ │ 输入框（生成中禁用）            [模型 terra + grok + DeepSeek ▾] [对比 ●━] [■ 停止]│
│       │ └─────────────────────────────────────────────────────────────────────────────────┘
│       │   对比 3 个模型 · 本次计 3 轮 · 今日已用 7/100 轮 · $0.28/$20.00
```

两列放两个答案（1280 侧栏收起 600；1440 侧栏开 536 / 收起 680；1920 封顶 720 并居中）：

```
│       │ [gpt-5.6-terra ✓ 1 个工具]  [grok-4.7 ✓ 2 个工具]            ← 模型条（全部可见时只是索引）
│       │ ┌ gpt-5.6-terra ─────────────────┐ ┌ grok-4.7 ──────────────────────┐
│       │ │ ✓ 完成 12s · 8.1k tok        ⧉ │ │ ✓ 完成 19s · 11.4k tok       ⧉ │
│       │ │ [✓ get_economic_calendar]      │ │ [✓ get_economic_calendar]      │
│       │ │                                │ │ [✓ load_skill(mt-dst)] ← 只有它调用过：描边
│       │ │ 正文 …                         │ │ 正文 …                         │
│       │ │ [ 用 gpt-5.6-terra 的回答继续 ]│ │ [ 用 grok-4.7 的回答继续 ]     │
│       │ └────────── 536–720 ─────────────┘ └────────── 536–720 ─────────────┘
```

两列放三个答案（1280 侧栏收起；1440 两种状态）：模型条上实心的两个正在显示，点第三个就换掉较早点开的那一个。

```
│       │ [gpt-5.6-terra ✓]  [grok-4.7 ✓]  ( DeepSeek-V4-Pro ✓ 3 个工具 )   ← 实心 = 正在显示
│       │ ┌ gpt-5.6-terra ─────────────────┐ ┌ grok-4.7 ──────────────────────┐
│       │ │ …                              │ │ …                              │
│       │ └────────── 536–680 ─────────────┘ └────────── 536–680 ─────────────┘
```

标签页（1280 侧栏开，2 个或 3 个答案）：

```
│应用侧栏│ [历史]                              10 月有哪些重要数据发布？ ┐
│ 288   │ [gpt-5.6-terra ✓ 1]  ( grok-4.7 ● 生成中 )  ( DeepSeek-V4-Pro ✕ )
│       │ ┌ gpt-5.6-terra ───────────────────────────────────────┐
│       │ │ ✓ 完成 12s · 8.1k tok                              ⧉ │
│       │ │ [✓ get_economic_calendar]                            │
│       │ │ 正文 …                                               │
│       │ │ [ 用 gpt-5.6-terra 的回答继续 ]                      │
│       │ └───────────────────── 768 ────────────────────────────┘
```

待选择态（刚跑完、刷新后、从别的会话回来都一样）：输入框的位置换成一条提示，候选块在上方原样显示。

```
│       │ ┌─────────────────────────────────────────────────────────────────────┐
│       │ │ 这个问题有 2 个回答待选择。选一个之后才能继续提问。                 │
│       │ │ 继续用： [ gpt-5.6-terra ]  [ grok-4.7 ]                            │
│       │ └─────────────────────────────────────────────────────────────────────┘
│       │   对比开关此时可以拨动，但不解除上面的限制。
```

历史列表里该会话标题前有一个小圆点，悬停提示「有回答待选择」。

选完之后与回看历史：选中的回答变回普通消息（普通对话宽度 768），其余折叠。

```
│       │                                     10 月有哪些重要数据发布？ ┐
│       │ 🤖 10 月的重点是 … （正文）
│       │    [✓ get_economic_calendar]
│       │    grok-4.7 · 从 2 个回答中选出    为什么选它？ (数字更可信) (更清楚) (更快) (其他)
│       │    ▸ 查看其他回答（1）
│       │
│       │ 展开后：未选中的回答按同一套「k 列」规则排在下面，列头灰色、没有选择按钮。
```

理由那一行选完才出现，点一下就记下，不点也不影响继续提问。单模型的回答下面同样有一行小字的模型名。

### 开关与模型选择

- 位置：输入框底部那一行，模型选择器右边，一个带「对比」字样的 Switch；缺省关。
- 关着：模型选择器是现在的单选，页面与现在一样。
- 开着：模型选择器变成多选（勾 2–3 个，少于 2 个时发送按钮禁用并提示），状态行写「对比 N 个模型 · 本次计 N 轮」。
- 开关和所选模型组合记在本机（`useFilterPersist`），下次打开页面保持。

## 结果

2026-10-07 完成并合入 main。契约 SSOT 与全部实施出入在 `docs/ai-agent/02-contracts.md` §19–§24 及其后的实施注记。

**交付**

- 后端：`POST /ai/turn` 可选 `compare_models`（2–3 个），主 API 并发调 N 次 agent、事件带 `run`；两张新表 `ai_compare_turns` / `ai_turn_candidates`，
  `ai_messages` 加 `model` / `compare_id`；`POST /ai/sessions/{id}/select`；待选择时 `409 compare pending`；配额按模型数计；
  全服并发对比轮次上限 `AI_COMPARE_MAX_CONCURRENT`（缺省 2）；`TURN_CLAIM_STALE_SECONDS` 360 → 720。agent 容器零改动。
- 前端：对比开关（缺省关，`useFilterPersist` 键 `AI_ASSISTANT_MAIN_FILTERS_V1`，不进 View Profiles）、容器查询决定 1 / 2 / 3 列（一列不窄于 480px）、
  模型条、待选择提示条、选后折叠「查看其他回答」、可选理由、每条回答标出模型名、对比模式下历史栏收成图标。
- 测试：`backend/tests/test_ai_compare.py` 95 个（另一个 worker 只看契约写成）；`test_ai_route.py` / `test_ai_sessions.py` 零改动全过；
  `./verify.sh` PASS（后端 2998 过、前端 vitest 377 过）。

**与 plan 的出入**

- §23 的新返回字段为空时不出现（不是 `null` / `false`），因为现有测试钉死了键集合。
- 全部失败时流里没有不带 `run` 的 `error`；对已作废的轮次 select 回 422。
- 前端：输入框与待选择提示条保持 768px 居中；工具条沿用现有徽章文案；待选择提示条里重复放了对比开关；对比块流式时不自动滚到底。

**验证情况**

- 用户在 dev（独立端口，集成 worktree）浏览器里看过并确认：三模型可选、Grok 可用。
- 用户要求直接上线，**未跑 outsider-review**。
- 没有人工逐格核对 1280 / 1440 / 1920 × 侧栏开收的列宽；列头吸顶未专门确认。

**Follow-up（未立单）**

- 审计行余量小：3 模型 × 6 次工具 + 500 字问题约 1817 / 2000 字符，subject 多或带 `run_sql` 时会被截断成不可解析的 JSON（单模型行今天也会）。
- 待选择的轮次没有过期时间，候选 blob 一直留到选择或会话被删。
- 同一模型无变化的重复 select 不写审计行，`AuditMissing` 会记一条 WARNING。
- 没有测试覆盖：N 个 run 共用整轮时限、`AUTH_ENABLED=false` 下的对比、多 worker 真并发抢对比名额。
- 模型多选框仍是硬编码的 `AI_MODELS`，等 OPT-0077 的 `GET /ai/models`。
- dev 的后端镜像缺 `openpyxl`（与本单无关，8001 起不来），需要重建 dev 镜像。
