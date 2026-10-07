---
id: OPT-0077
title: AI agent 模型注册表 hardening —— OPT-0075 冷审 #2 / #5 / #6 / #7 / #9
status: ready
priority: P2
area: mixed
effort: M
created: 2026-10-07
related: [[OPT-0075]], [[OPT-0076]], [[OPT-0073]]
---

## 问题

OPT-0075（接入 grok-4.7 + DeepSeek-V4-Pro，2026-10-07）merge 前冷审里「不阻塞上线」的几条，用户拍板另立本单。
行号是 `opt/ai-agent-grok-deepseek` 合入时的。

1. **env 覆盖模型名会改掉对外的名字**（冷审 #2）：`harness.SELECTABLE_MODELS` 的 env 覆盖直接替换 `allowed_models()` 里的
   名字。设 `AI_AGENT_MODEL_GROK=grok-prod` 后，浏览器仍发 `grok-4.7`、`schemas/ai.py::AiModel` 放行，agent 侧
   `server.py:97` 回 400 → 用户看到「agent unavailable」且已 `increment_turn`（白花一轮）；价格表按部署名查也会落空。
   两个条目指向同一部署时静默少一个选项。`test_model_env_overrides_keep_their_names` 把这个行为钉住了。
   修：一张 `逻辑名 → {部署名, 价格, 文案 key}` 注册表；agent 接受逻辑名、在 `get_client` 里解析部署名；价格与审计按逻辑名。
   现状：prod / dev 都没设新模型的覆盖，所以今天不会触发（2026-10-07 实查）。
2. **模型清单七处手工同步**（#7）：harness 表、`AiModel` Literal、价格表、前端 `AI_MODELS`、`AiAssistant.tsx` 的
   `<SelectItem>`、两个 locale。一致性测试用正则扫整页的 `<SelectItem value=`（页面再加一个 Select 就误报），且不查 i18n key。
   修：主 API 出 `GET /ai/models`（来自上面的注册表），前端渲染它；测试改成对注册表断言。
3. **schema 展开依赖框架对象复用**（#5）：`inline_tool_schemas` 就地改 `tool.parameters()` 返回的缓存 dict。1.19.0 上实测
   请求体确实读同一个对象（含 `anyOf` / `items` 里的 ref），pydantic 入参校验不受影响；但框架改成直接从 input model
   生成请求体时现有测试抓不到。修：测试改为断言 client 实际组出的请求体（`_prepare_tools_for_openai` 的输出）里没有
   `$ref`；并覆盖「将来别的 context provider 加进来的工具」——在请求构造处统一展开（client 子类）比逐个来源展开稳。
4. **`inline_schema_refs` 边界**（#6）：属性名恰好叫 `$ref`、`#/definitions/…`、JSON pointer 转义（`~1`）、enum 值里含
   `$ref` 键 → 抛 ValueError，且抛在 `drive()` 之外，表现为所有模型都「agent failed unexpectedly」。静态工具由 CI 兜住；
   修：不要把 `properties` 的键名和 `enum` / `const` / `default` 的值当 schema 遍历。递归 schema 不能上（记为约束）。
5. **厂商故障时的体验**（#9）：`OpenAIChatClient` 没设单次请求超时，厂商卡住时用户等满 520s（按 SDK 缺省推断，未实测）；
   `model_error` 文案不带模型名、不提示换模型。修：给 client 设请求级超时；错误文案带模型名 +「可换一个模型重试」。
6. **测试质量**：`test_skill_tools_carry_no_ref_either` 现在是空转（基础 skill 工具本来就没有 `$ref`）；
   `pytest.raises(Exception)` 太宽；i18n 里「newly added」和注释里的探针日期会过期。

顺带（既有问题，冷审提到）：compaction 的 3 字符/token 估算是按 GPT 校准的；摘要器（luna）的调用从不计费。

## 已核对、不用做的（2026-10-07 实测）

- 三家（Grok / DeepSeek / GPT）的 Responses usage 都满足 input + output = total、cached ≤ input、reasoning ⊂ output，
  现有计费公式正确。
- GPT 写下的带加密推理条目的会话，Grok / DeepSeek 都能续答；两个新模型自己不产生推理条目。
- `AI_MODEL_PRICES` 整表替换 → 已在 OPT-0075 当场修（`535f219`，改为叠加 + 行校验）。

## 验收标准

- [ ] 上述 1–6 逐条修复或在结果段写明 live with 原因
- [ ] 新增/改写的测试能被对应的破坏性改动打红
- [ ] `./verify.sh` 绿；rebuild ai-agent + api + web 部署（回滚标签 `pre-ai-model-registry-<date>`）

## 结果

（待实施）
