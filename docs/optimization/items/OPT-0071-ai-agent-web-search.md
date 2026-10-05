---
id: OPT-0071
title: AI 助手联网搜索 —— 受控的 search_web 工具（查询词出境需合规拍板）
status: dropped
priority: P3
area: backend
effort: M
created: 2026-09-30
related: [[OPT-0069]] [[OPT-0070]]
---

## 问题

同事会问「最近某某新闻 / 事件对金价有什么影响」这类需要实时外部信息的问题，agent 目前无联网能力
（prompt.py 明写 "no web capability"）。

## 调研结论（2026-09-30）

- Azure OpenAI Responses API 自带 `web_search` 工具（Grounding with Bing），`tools=[{"type":"web_search"}]`，
  支持 `allowed_domains`（≤100）/ `blocked_domains`、`user_location`、返回 `url_citation`。
  **订阅级开关**：`az feature register/unregister --name OpenAI.BlockedTools.web_search`。
- 🔴 **合规**：微软文档明写「使用 Grounding with Bing 时数据流出你的合规与地理边界，Microsoft DPA 不适用」，
  且按 Bing 请求次数另计费（`tool_usage.web_search.num_requests`）。
- 本项目的问题常含客户姓名 / login / 邮箱 —— 原生 web_search 让模型自己拼查询词，可能把这些送进 Bing。
- 与 OPT-0065 的「经济日历不接 web search」不冲突：那是为确定性数据；本单是通用新闻 / 事件问题。

来源：learn.microsoft.com/azure/foundry/openai/how-to/web-search

## 方案（待拍板后细化）

**不**把原生 `web_search` 直接挂主 agent。做一个受信工具 `search_web(query)`：
1. 查询词先过我们的守卫：含 login / loginSid / 客户 id（纯数字串）/ 邮箱 / 已知客户姓名 → 拒绝。
2. 守卫通过后由工具内部单独发一次 Responses 调用（`web_search` + `allowed_domains` 白名单：央行、
   官方统计、主流财经媒体），只把摘要 + 引用返回主 agent。
3. 每次搜索的查询词写进审计（`ai.query.submit.new_value.web_queries`）。
4. 独立开关 env（缺省关），并可按模块 / 人限制。

## 验收标准（草拟）

- [ ] 合规 / 老板书面拍板：允许经守卫过滤后的查询词出境 Bing
- [ ] 订阅开启 web_search
- [ ] 守卫单测：含客户标识的查询被拒
- [ ] 回答带来源链接；审计含查询词
- [ ] 费用监控：`num_requests` 计入配额

## 开放问题（阻塞，升 Ready 前必须回答）

- 谁拍板数据出境（老板 / Lawrence / 合规）？
- 域名白名单清单
- 是否只对部分人 / 模块开放

## 结果

**2026-10-05 放弃**（用户决定），依据 2026-09-30 评估：
1. **需求为零**：audit_log 截至 09-30 共 111 条 `ai.query.submit` 提问，按新闻/央行/美联储/非农/金价/行情/关税等关键词筛，真实命中 0 条；提问全是内部数据。数据发布日程已由 `get_economic_calendar`（Fed + FRED）覆盖。
2. **合规成本高、守卫不牢**：Grounding with Bing 数据出境且 Microsoft DPA 不适用；查询词守卫挡得住 login / 邮箱 / 数字 ID，挡不住客户姓名（需对约 7 万客户名模糊匹配，漏一个即出境）。
3. **将来若真有需求的替代方案**：另开新 OPT 做 `get_market_news`——只读固定公开来源（Fed / ECB 新闻稿、官方统计 RSS），不向外发送任何查询词，无数据出境问题，路子同 `get_economic_calendar`，effort S。
