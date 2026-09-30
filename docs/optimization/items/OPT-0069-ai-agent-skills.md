---
id: OPT-0069
title: AI agent 接入 Agent Skills —— 按需加载的领域知识目录，替代单一 system prompt
status: ready
priority: P1
area: backend
effort: M
created: 2026-09-30
related: [[OPT-0064]] [[OPT-0065]] [[OPT-0066]] [[OPT-0070]] [[OPT-0071]]
---

## 问题

AI 分析助手（`/ai/assistant`，`backend/app/ai_agent/`）的全部领域知识只有一份静态 system prompt
`backend/app/ai_agent/prompt.py`（366 行：口径、run_sql schema card、Risk control 块）。后果：

- 口径 / 表结构 / 坑只有 prompt 里写到的那部分；项目里大量知识（`docs/`、`.cursor/skills/`）agent 看不到。
- 每加一类知识 prompt 就更长，每轮全量发送（token 成本 + 注意力稀释）。
- 真实提问（`audit_log` 的 `ai.query.submit.new_value.question`，09-27~09-29 共 36 条非 Kieran 提问）
  大量落在 prompt 没覆盖的领域：A-book 适合度、oneZero 执行延迟、margin level / 快 SO 账户、
  MT4/MT5 Manager 操作、给客户草拟回复。

## 方案

用 Microsoft Agent Framework 内置的 **Agent Skills**（开放格式 agentskills.io，与 Claude Code skill 同格式）：
`SkillsProvider`（context provider）每轮只把 skill 名字 + 描述放进 prompt，模型用 `load_skill` 取 `SKILL.md`
正文、用 `read_skill_resource` 取 `references/*`。**已确认 prod ai-agent 容器装的 agent-framework-core 1.19.0
自带 `SkillsProvider` / `FileSkillsSource` / `FilteringSkillsSource`**，无需升级。

1. **目录**：`backend/app/ai_agent/skills/<name>/{SKILL.md, references/*.md}`（进 git、`COPY . /app` 烤进镜像；
   dev compose 已只读挂载 `app/ai_agent`）。`SOURCES.md`（每条事实 → 出处）**不给模型看**：放在同目录但
   `FileSkillsSource` 配置里排除，或搬到 `docs/ai-agent/skills-sources/`。
2. **接线**（`harness.py`）：`SkillsProvider.from_paths(...)` 加进 context providers；**不传 `script_runner`**
   （不注册 `run_skill_script`）。确认 plain `Agent` 下三个 skill 工具是否需要 approval —— MAF 文档说 harness
   agent 默认装 `ToolApprovalMiddleware`，plain agent 需自行组合；要保证 `load_skill` / `read_skill_resource`
   无需审批直接可用（`SkillsProvider.read_only_tools_auto_approval_rule`）。
3. **按人过滤**：`FilteringSkillsSource` 按调用者三轴决定可见 skill，和现有工具注册矩阵一致：
   `fxbackoffice-schema` 只给 `run_sql_enabled(ctx)`；`risk-monitor-rules` 只给 `risk_tools_enabled`。
4. **prompt 瘦身**：prompt.py 只保留「不可协商规则」+ 工具路由；口径细节、schema card、Risk control 表
   搬进对应 skill（逐条对照，**不能丢**——schema card 里有 run_sql 守卫依赖的事实）。
5. **事实即代码**：沿用 CLAUDE.md「prompt / schema card 是代码」——skill 里的 schema / 索引 / 口径断言要有
   测试钉住（扩展 `test_ai_agent_prompt.py` 或新 `test_ai_agent_skills.py`：frontmatter 合法、name=目录名、
   关键事实字符串存在、被过滤的 skill 对受限者不可见、SOURCES 不进模型上下文）。
6. **审计**：`ai.query.submit.new_value` 增加 `skills_loaded: [...]`，便于看哪些 skill 被用、哪些从不触发。

## 草稿（2026-09-30 已起草，待用户 / 业务审）

`docs/ai-agent/skills-draft/`（本地，gitignored）—— 11 个 skill：

| skill | 状态 | 可见范围 |
|---|---|---|
| kcm-metrics-definitions | 草稿 | 全员 |
| fxbackoffice-schema | 草稿 | 仅 run_sql |
| ib-and-rebate | 草稿 | 全员 |
| risk-monitor-rules | 草稿 | 仅 risk 工具持有者 |
| trading-patterns-and-events | 草稿 | 全员 |
| system-pages-guide | 草稿 | 全员 |
| execution-and-slippage | 草稿 | 全员 |
| margin-and-stopout | 草稿 | 全员 |
| a-book-b-book | 草稿（含 TODO(business)） | 全员 |
| mt-manager-howto | 骨架，待 dealing 团队填 | 全员 |
| client-reply-drafting | 骨架，待 CS / 合规填 | 全员 |

每个 skill 的 `SOURCES.md` 列出事实出处 + `TODO(business)` 待确认问题。**骨架类与带 TODO 的 skill 在业务答复前不上线**
（宁缺勿错）。

## 验收标准

- [ ] 用户审过草稿；TODO(business) 已答复或该段删除；只上线「无未决 TODO」的 skill
- [ ] `SkillsProvider` 接入 harness；无 `run_skill_script`；两个读工具无需审批
- [ ] `FilteringSkillsSource`：受限者 / 无 risk 者看不到对应 skill（单测）
- [ ] prompt.py 瘦身后，原有 `test_ai_agent_prompt.py` 钉住的事实全部仍可在 prompt 或 skill 中找到（单测）
- [ ] `SOURCES.md` 不进入模型上下文（单测）
- [ ] 审计 `skills_loaded` 字段
- [ ] 用 audit_log 里的真实问题（Sammy 的 A-book / 快 SO / 单边持仓系列、Rebecca 的上周盈利前 10）做前后对比：
      回答是否引用了正确口径、是否诚实声明工具缺口
- [ ] 更新 `docs/ai-agent/03-architecture.md` + `docs/features/ai-assistant.md` + CLAUDE.md AI agent 条目
- [ ] rebuild ai-agent 镜像部署（打回滚标签 `pre-ai-skills-<date>`）

## 开放问题

- 草稿中的 TODO(business)（A-book 路由标准、各组 SO/MC 水平、MT Manager 问答是否允许凭通用知识回答、
  客户回复的合规红线）—— 找 Sammy / dealing / CS 确认。
- 起草时发现的工具缺口（如 `rank_open_positions` 是否带 margin level、单边持仓筛选）是否另开 OPT 补工具。

## 起草期发现（2026-09-30，三个起草 agent 汇总，主线程已核实标 ✅）

**现有 prompt / 工具说明里的错误（与 skill 上线无关，应先修）**
- ✅ `prompt.py:216` + `tools/risk_alerts.py:547` caveat 写 gap-trade「次日 05:20 HKT 扫前一 MT 日」；实际
  `core/burst_open_scheduler.py:914` 是 **Mon–Sat 07:20 HKT 扫当日 MT 00:00–02:00 窗口**。
- ✅ `tools/run_sql.py:288` FIXED_CAVEATS 称 CEN 账户「金额和手数」都 ×100；受信工具 `trade_activity.py:90`
  只对 .cent/.kcmc 品种除手数、CEN 账户只除金额。需用真实数据判定哪个对。
- schema card 缺 `mt4_users.MARGIN` / `MARGIN_FREE`；未说明 `rebate_all` 是「客户自身交易产生的全链返佣」而非其作为 IB 的收入。
- 规则 1「每个数字必须来自本轮工具」字面上禁止引用 skill 里的文档样本数字 —— 需加例外（标注 documented）。

**疑似线上问题（另行处理，不属本单）**
- 🔴 冬令时（2026-11-01 起）07:20 HKT = MT 01:20，gap-trade 扫描早于 00:00–02:00 窗口结束 → 01:20–02:00 的单漏扫。
- Risk Rule Alerts 页面时间固定按 UTC+3 显示，11 月起与 MT 墙钟差 1h。

**文档过期**：risk-monitor.md rule 81 门槛写 $1,000（代码 $100）；rules-catalog 称 martingale tab 隐藏（已可见）；
blowup / news-event 文档仍写「代码尚未落地」；news-event 文档日界锚 Europe/Athens；ib-financial-monitor 归属写错。

**工具缺口（建议另开 OPT）**：`rank_open_positions` 加 margin_level + 在 top_n 之前做 exclude_cent / 单边 / margin 过滤
（Sammy 快 SO 系列提问直接受益）；exec-compensation 摘要工具；Client Return Rate 指标工具；`get_ib_chain`。

**去重**：`margin-and-stopout` 与 `fxbackoffice-schema` 的单边黄金 / margin level 部分重叠，上线前合并到一处。

**SQL 模式**：`fxbackoffice-schema` P1/P2/P4/P5 与 `margin-and-stopout` §D 均**未 EXPLAIN、未实跑**，上线前必须验证
（含守卫是否接受 HAVING 别名、`` `GROUP` ``、`SUM(CMD = 1)`）。

## 结果

（待实施）
