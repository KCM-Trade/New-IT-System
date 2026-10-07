---
id: OPT-0075
title: AI agent 接入 grok-4.7 + DeepSeek-V4-Pro 作为可选模型
status: wip
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

## 开放问题（不阻塞开发，上 prod 前要用户答）

- 处理地：现在两个部署是 Global Standard（与现有 GPT 部署同姿态：提示词可能在任何有该模型的地区处理，静态数据留美国）。
  是否改 Data Zone Standard（美国境内处理，DeepSeek 贵约 10%）？
- 受限用户（anson / rose）是否也能选这两个模型？缺省 = 能（现在没有按人限制模型的机制）。
- 新模型对 prompt 规则的遵守程度（数字只来自工具 / 写 SQL 前先 load_skill / closeDate 哨兵）没有评测，
  需要用户拿真实问题手测后再决定是否在 UI 上标「实验」。

## 验收标准

- [ ] dev：UI 模型选择器出现 `grok-4.7` 和 `DeepSeek-V4-Pro`，各自能完成带工具调用的一轮，状态条显示非零成本
- [ ] `build_tools` 产出的工具 schema 无 `$ref`（单测）；Grok 对 12 个工具全集不再 400（活体）
- [ ] 三处模型清单 + 价格表一致性测试覆盖 5 个模型
- [ ] 同一会话跨模型续问在 dev 活体通过
- [ ] 限流问题有结论
- [ ] `./verify.sh` 绿（⚠ 必须 `--ignore` case_metrics 集成测试那一个，见 verify.sh 现状；不要自己加跑它）
- [ ] 上线：rebuild ai-agent + api + web，回滚标签 `new-it-system-{api,web,ai-agent}:pre-ai-grok-deepseek-<date>`（由主会话 / 用户做）

## 结果

（待实施）
