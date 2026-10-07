---
id: OPT-0075
title: AI agent 接入 grok-4.7 + DeepSeek-V4-Pro 作为可选模型
status: done
priority: P2
area: mixed
effort: M
created: 2026-10-07
related: [[OPT-0064]], [[OPT-0069]], [[OPT-0076]]
---

## 问题

用户（2026-10-07）要在 AI 分析 agent 里用 OpenAI 之外的模型，指定加 xAI `grok-4.7` 和 `DeepSeek-V4-Pro`；
后续还要做多模型对比模式（OPT-0076），本单是它的前置：先让两个模型能被单独选用。

现状：可选模型是三个 OpenAI 部署（`gpt-5.6-terra` 缺省 / `gpt-5.6-sol` / `gpt-6.1-sol`），加模型的模板是
commit `b2791e3`（2026-10-05 加 `gpt-6.1-sol`，先 `git show b2791e3` 看它动了哪些文件）。

## 已实测的事实（2026-10-07，dev ai-agent 容器内，合成问题、无客户数据）

Azure 资源 `kcm-ai-agent-east-us`（RG `AI_AGENT_EAST_US`）上**已建好**两个部署，GlobalStandard：
`grok-4.7`（format xAI，version 1，容量 200）、`DeepSeek-V4-Pro`（format DeepSeek，version 2026-04-23，容量 300）。
CSP 订阅 + HK 账单没有挡。**不要重建、不要删。**

| 项 | grok-4.7 | DeepSeek-V4-Pro |
|---|---|---|
| Responses API（`/openai/v1/responses`，同一个 endpoint + key）含 function tool | ✅ | ✅ |
| 现有 `OpenAIChatClient` + 真实 harness 跑通（10 工具 + 2 skill 工具） | ✅（需展开 `$ref`，见下） | ✅ |
| 一次回复并行两个 tool call | ✅ | ✅ |
| 每次模型调用都有 usage（input / output / cached） | ✅ | ✅ |
| `store: false` 被接受 | ✅ | ✅ |
| `load_skill` | ✅ | ✅ |
| 会话 blob 里产生 `text_reasoning` 条目 | 无 | 无 |
| 同一会话跨模型（GPT→Grok→DeepSeek→GPT，及 Grok→GPT→DeepSeek→Grok） | ✅ 每步都读到历史并正确续答 | ✅ |

结论：**不需要 Chat Completions client，不需要按接口类型锁会话**（`list-models` 的 capabilities 字段说「只支持
chatCompletion」是错的）。

🔴 **Grok 拒绝带 `$defs` / `$ref` 的工具 schema**：`get_client_overview` / `get_trade_activity` / `get_risk_signals`
三个工具的参数用了 `SubjectArg` TypedDict（`harness.py` 的 `SubjectArg` / `Subject` / `Subjects`），pydantic 生成
`$defs` + `$ref`，Grok 对整个请求回 400 `There was an issue with your request. Please check your inputs and try again`
（无 param、无 code）。把 `$ref` 内联展开后 12 个工具全部通过。`rank_accounts` 等不含 `$ref` 的工具原样可用
（`anyOf`+null、整数 enum、`additionalProperties: true`、`default`、`title` 都没问题）。

🟡 **限流早于名义额度**：两个模型都在同一会话的第三轮（一分钟内累计约 5 万 input token）撞
`have exceeded token rate limit`，而部署额度是 200K / 300K TPM。原因未查清（额度刚调高未生效？计量按 max
output 预留？）。

探针脚本（本机资产，不在 git）：`docs/ai-agent/probes/2026-10-07-*.py`，用法
`docker exec -i -w /app new-it-ai-agent-dev python - < <script>`。`harness-probe` 里有一段「probe-only shim」
就是 `$ref` 展开的参考实现。

## 要做的

1. **`backend/app/ai_agent/harness.py`**
   - 工具 schema 发送前内联展开 `$ref` / 去掉 `$defs`，对**所有**模型生效（GPT / DeepSeek 不受影响，少一条按模型分支）。
     落点自选：改参数类型让 pydantic 不产生 `$ref`，或在工具构造后改写 schema，或包一层 client——要求是
     `harness.py` 仍是唯一 import MAF 的模块，并有单测钉住「build_tools 产出的每个工具 schema 里没有 `$ref`」。
   - 模型清单从三个 env getter（`default_model` / `deep_model` / `frontier_model`，`harness.py:104-135`）扩成能容纳
     5 个模型的结构（小注册表即可；保留现有三个 env 覆盖名，新两个也给 env 覆盖名）。`allowed_models()` 是
     `server.py:97` 白名单的来源。`summary_model()`（compaction 摘要用 luna）**不动**。
2. **价格**：`backend/app/core/config.py` `_DEFAULT_MODEL_PRICES`（约 :33-40）加两行；未知模型按 $0 计费 = 配额被绕过，
   所以**价格必须和模型一起上**。DeepSeek-V4-Pro Global：input 1.74 / cached 0.145 / output 3.48（Azure Retail
   Prices API，2026-10-07）。grok-4.7 Azure 没有 meter：暂用 xAI 官方价 input 2.00 / output 6.00（二手来源，注释写明
   待核，同 `gpt-6.1-sol` 的做法）。`ai_gateway_service.py:90-95` 把 cached input 固定按 10% 计——DeepSeek 实际
   ≈8.3%、Grok 4.6 是 25%：看 `compute_cost_usd` 现状决定是否加 per-model cached 比例（小改；不做也要在结果段写明偏差方向）。
3. **三处模型清单必须一致**：`backend/app/schemas/ai.py` `AiModel`（:19）、`harness.allowed_models()`、前端
   （`frontend/src/hooks/useAiTurn.ts:28-30` 附近的 `AI_MODELS`、`frontend/src/pages/AiAssistant.tsx:303-311` 硬编码的
   `SelectItem`、两个 locale 的 i18n 文案）。`test_selectable_models_agree_across_layers` 是护栏，它现在按三个具体 env
   名写，要一起改。
4. **测试**：schema 无 `$ref`；5 个模型三层一致；新模型有价格（没有价格的可选模型 = 测试红）；
   既有 `backend/tests/test_ai_*.py` 全绿。
5. **限流**：查清上面 🟡（`az cognitiveservices account deployment show` 的 rateLimits、Azure 文档、必要时再跑探针），
   结论写进结果段；需要调容量就调（`deployment create` 同名即 upsert）。
6. **dev 活体**：改完后在 dev 跑 harness 探针（去掉 shim），两个模型各至少：日历工具一轮 + load_skill 一轮 + 续问一轮，
   再加一轮跨模型续问。⚠ 活体问题**只用不含客户数据的**（经济日历、skill），真实客户问题留给用户手测。
7. **文档**（`docs/**` 不进 git，直接改 `/opt/myproject/New-IT-System/docs/` 下的文件；**由主会话做，worker 不用管**）。

## 不在本单

- 多模型对比模式 → OPT-0076。
- Claude（CSP 订阅试建 4 次均 500，未再试）。
- 把新模型设为缺省；按人限制可选模型。

## 用户拍板（2026-10-07）

- **处理地**：维持 Global Standard（与现有 GPT 部署同姿态），不改 Data Zone Standard。
- **受限用户**（anson / rose）也能选这两个模型；不做按人限制模型。
- **不标「实验」**：UI 上两个新模型与现有模型同等展示。

仍未做的：新模型对 prompt 规则的遵守程度（数字只来自工具 / 写 SQL 前先 load_skill / closeDate 哨兵）没有评测，
靠用户拿真实问题手测。

## 验收标准

- [ ] dev：UI 模型选择器出现 `grok-4.7` 和 `DeepSeek-V4-Pro`，各自能完成带工具调用的一轮，状态条显示非零成本
- [ ] `build_tools` 产出的工具 schema 无 `$ref`（单测）；Grok 对 12 个工具全集不再 400（活体）
- [ ] 三处模型清单 + 价格表一致性测试覆盖 5 个模型
- [ ] 同一会话跨模型续问在 dev 活体通过
- [ ] 限流问题有结论
- [ ] `./verify.sh` 绿（⚠ 必须 `--ignore` case_metrics 集成测试那一个，见 verify.sh 现状；不要自己加跑它）
- [ ] 上线：rebuild ai-agent + api + web，回滚标签 `new-it-system-{api,web,ai-agent}:pre-ai-grok-deepseek-<date>`（由主会话 / 用户做）

## 结果

**2026-10-07 完成**（分支 `opt/ai-agent-grok-deepseek`：`a437318` 实施 + `535f219` 冷审 #1 修复）。

交付（对照 AC）：
- 工具 schema 发送前内联展开 `$ref`、去掉 `$defs`（`harness.inline_schema_refs` / `inline_tool_schemas`，`build_tools` 与
  skill 的两个读工具都走），对所有模型生效。
- 模型清单改成 `harness.SELECTABLE_MODELS` 五行表（新 env 覆盖名 `AI_AGENT_MODEL_GROK` / `AI_AGENT_MODEL_DEEPSEEK`）；
  `AiModel`、前端 `AI_MODELS`、选择器、两个 locale 同步到五个。
- 价格：DeepSeek-V4-Pro `(1.74, 3.48, cached 0.145)`（Azure 实价）；grok-4.7 `(2.0, 6.0, cached 0.5)` **占位**
  （Azure 无 meter；cached 按 grok-4.6 的 25% 猜，猜错方向是多算配额）。价格行可带第三个值 = 缓存输入单价，没有则 10%。
- 测试：新增 10 + 12 个（schema 无 `$ref`、五模型各层一致、每个可选模型价格 > 0、价格 env 叠加与行校验）。
- 闸门：实施 commit 上 `./verify.sh` PASS（pytest 2896 / tsc 0 / vitest 339）；冷审修复后后端 pytest 2907 passed / 1 deselected。
- dev 活体（一次性容器挂分支代码，无 shim，合成问题）：两个模型各通过 日历工具 / `load_skill` / 同会话续问 / 12 工具全集；
  terra → Grok → DeepSeek → terra 跨模型会话四步全过。
- 限流：未复现。六轮并发（Grok 24s 内 17.1 万 token、DeepSeek 11s 内 14.1 万）无 429；上午的 429 最可能是容量刚从 50 调到
  200 / 300 还没生效（活动日志看不到调整前容量，属推断）。未改容量。

冷审（独立零上下文 agent，2026-10-07）处理记录：
- #1 `AI_MODEL_PRICES` 整表替换 → 漏写的模型按 $0 计费；解析器接受零 / 负数 / NaN：**当场修**（`535f219`，叠加 + 行校验）。
  prod / dev 当时都没设这个 env，属潜在问题。
- #3 推理 / 缓存 token 记账：**实测排除**——三家 Responses usage 都满足 in + out = total、cached ≤ in、reasoning ⊂ out。
- #4 跨模型加密推理条目：**实测排除**——GPT 的带推理条目会话被 Grok / DeepSeek 正常续答，新模型不产生推理条目。
- #2 env 覆盖改掉对外名字、#5 schema 展开依赖框架对象复用、#6 展开函数边界、#7 清单七处手工同步、#9 厂商卡住等满 520s
  + 测试质量：**立 OPT-0077**。
- #8 逐条消息没有模型名：**并入 OPT-0076**。

未验证 / follow-up：
- 浏览器里的选择器与状态条成本显示（只有单测 + tsc）。
- 三个带客户参数的工具（`get_client_overview` 等）在 Grok 上的真实调用——schema 被接受，但活体问题不含客户数据，未触发。
- 新模型对 prompt 规则的遵守程度没有评测，靠用户手测。
- grok-4.7 价格与缓存比例：Azure 出 meter 后核对。
