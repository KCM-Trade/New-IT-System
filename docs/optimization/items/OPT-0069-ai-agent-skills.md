---
id: OPT-0069
title: AI agent 接入 Agent Skills —— 按需加载的领域知识目录，替代单一 system prompt
status: done
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

> 2026-09-30 首轮实施（分支 `opt/ai-agent-skills`，worktree `../New-IT-System-wt-0069`）：**接线 + 过滤 + 测试 + 无 TODO 的 skill**。
> 用户**尚未审草稿**，所以是 build-and-test，不是上线：未 merge、未 push、未部署、未 rebuild。status 保持 `wip`。

### 做了什么

- **接线**（`harness.py`，仍是唯一 import MAF 的模块）：`KcmSkillsProvider(SkillsProvider)` 挂进 `context_providers`；
  源 = 进程级 `CachingSkillsSource(FileSkillsSource(skills/, resource_extensions=(".md",), script_extensions=(), resource_filter=排除 SOURCES.md))`，
  每轮外包一层 `FilteringSkillsSource`（缓存在过滤之下，不会串人）。
- **MAF 1.19.0 实际行为**（读源码 `agent_framework/_skills.py` 确认）：
  - `_create_tools` **总是**造三个工具（含 `run_skill_script`，哪怕没有脚本）→ 子类覆写删掉；脚本发现关掉；无 `script_runner`。
  - 三个工具默认 `approval_mode="always_require"`；plain `Agent` 没有 `ToolApprovalMiddleware`，要审批 = 本轮以审批请求结束、没人能批。
    用构造参数 `disable_load_skill_approval` / `disable_read_skill_resource_approval` = True → `never_require`（没走 `read_only_tools_auto_approval_rule`，那条只对仍需审批的工具生效、需要中间件）。
  - 默认指令模板无条件带 `run_skill_script` 说明 → 换成自定义 `SKILLS_INSTRUCTION_TEMPLATE`（写明 skill 不替代工具取数、与 prompt 冲突时 prompt 优先）。
  - `SkillsProvider(source=调用方给的 SkillsSource)` **不自动缓存**（框架注释：怕按人过滤的结果被缓存串给别人），所以缓存放在叶子上。
  - ⚠ frontmatter YAML 解析失败的 skill **只打一行日志就跳过**：起草稿 `trading-patterns-and-events` 的 description 里有 `Also:` 就被静默丢了。已修，测试钉「目录数 = 加载数」。
- **按人过滤**：`SKILL_VISIBILITY`（all / run_sql / risk），与 `build_tools` / `system_prompt` 用**同两个布尔值**；表外的 skill 一律隐藏（fail closed，anti-drift 测试钉目录 = 表）。
- **审计**：覆写 `_load_skill` / `_read_skill_resource`，**找到**才回调 → `skill_loaded` 事件 → 主 API 记 `ai.query.submit.new_value.skills_loaded`（去重、按序、恒有，没用就 `[]`），**不转发浏览器**；agent 侧 INFO 行加 `skills=`。
- **prompt**：规则 1 加例外（skill 里的文档样本数字可引用，须标 `(documented, <skill name>)`，永不当作所谈客户/账户/期间的数）；`rebate_all` 注明是「这个客户的交易产生的全链返佣、不是其作为 IB 的收入」；run_sql 的 CEN 坑改精确；schema card 加 `MARGIN_LEVEL`（0 = 无持仓）与 skill 指针；Risk 块的脚本指针改指 skill。**gap-trade 扫描时刻那句（`prompt.py` 原 216 行）一字未动**，`tools/risk_alerts.py` 未碰（OPT-0072 所有）。
- **item 文件修复**：认领 commit `ed4ddb7` 把本文件截成 0 字节，已从 `edc18b4` 恢复（`7b1c821`）。

### skill 上线情况（8 上 / 3 不上）

| skill | 结果 | 处理 |
|---|---|---|
| kcm-metrics-definitions | ✅ | profit factor 的 TODO 改为「无内部定义，不要自造」；cent 段加「lots ×100 是品种属性」 |
| fxbackoffice-schema（仅 run_sql） | ✅ | margin 段重写为「SQL 侧」、概念归 margin-and-stopout；新增 P2b / P4b / P6；黄金命名 TODO 删（改「告诉用户匹配到哪些 symbol」）；stats_ib_commissions lots 重复 TODO 用数据证实 |
| ib-and-rebate | ✅ | 层级 tag id TODO → 「未记录，不要按 tag id 判层级」；partnerId TODO → 「只一层，链路看 /cs/ib-tree」 |
| risk-monitor-rules（仅 risk 工具） | ✅ | 扫描间隔 / 分档 / 杠杆档 TODO → 「页面设置，工具不返回」；**gap-trade 所有具体扫描时刻删掉**，改为「引用 get_risk_alerts caveat」；早间 rule 81 pass 的 HK 时间也删（DST 会漂） |
| trading-patterns-and-events | ✅ | 事件 AB 月份 TODO → 「问 IT」；Risk 工具行标「仅当工具在列表里」；修 YAML |
| system-pages-guide | ✅ | swap-free zipcode TODO → 「未记录，不要解释」；删 mt-manager-howto 引用 |
| execution-and-slippage | ✅ | 两条 TODO 保留为保守规则（客户文本不写内部机制并告知；A-book 执行策略只讲原因、转交 dealing）；删两个未上 skill 的引用 |
| margin-and-stopout | ✅ | MC/SO 水平 TODO → 「系统未记录，绝不陈述，用用户给的阈值」；§A.2 / §D 的 SQL 删掉改指 fxbackoffice-schema P2b / P1（**去重：单边黄金 + margin level 只剩一处**） |
| a-book-b-book | ❌ | A-book 适配标准 / AKCM 是否等于 A-book 等核心问题是 TODO(business)，删了就没内容 |
| mt-manager-howto | ❌ | 骨架，待 dealing |
| client-reply-drafting | ❌ | 骨架，待 CS / 合规（审批人、红线） |

`SOURCES.md` 随 skill 进了 git（给审阅人看），每份顶部加了「Port notes」记录删改与核实；`resource_filter` 保证模型读不到。

### SQL 模式核实（`validate_sql` + `prepare_sql` + 从库 EXPLAIN，`connect_readonly`，2026-09-30）

| 模式 | 守卫 | 计划 | 实跑 |
|---|---|---|---|
| P1 单边黄金 + margin level | ✅（HAVING 别名、`` `GROUP` ``、GROUP_CONCAT 均接受） | `mt4_trades` ref `INDEX_CLOSEDATE` ~42k 行 | 0.27 s |
| P2 低 margin level（任意品种） | ✅ | `mt4_users` 全表 ~216k 行（无 MARGIN_LEVEL 索引） | 0.35 s，保留并注明「别把这形状搬到 mt4_trades」 |
| P2b 按 login_sid 查 margin | ✅ | LOGIN_SID range | — |
| P3 单账户平仓单 | ✅ | loginSid 系索引 range | — |
| P4 某 IB 按客户返佣 | ✅ | IDX_REF range | — |
| P4b 某客户按 IB 返佣 | ✅ | PRIMARY 日期 range（28 天 ~203k 行） | 0.05 s；注明「约一个月以内」 |
| P5 CRM tags | ✅ | user_tags 索引 | — |
| P6 isIb / partnerId | ✅ | PRIMARY | — |
| margin §D（已删，并入 P1） | ✅（`SUM(t.CMD = 1)` 接受） | 同 P1 | 0.23 s |
| execution 逐单成交 | ✅ | loginSid range | — |

没有任何模式全表扫 `mt4_trades`。测试把 skill 里**每个** SQL 块跑一遍 `validate_sql`，碰 `mt4_trades` 的必须过滤 `closeDate/openDate/loginSid` 且带 LIMIT。
顺带核实：`mt4_users.MARGIN_LEVEL > 0` 的活账户 1,244 个（MT5 790 个 → MT5 有值）；`IB-WALLET%` 13,656 行里 13,516 的 userId 是 isIb 用户；`OPEN_TIME/CLOSE_TIME` 是 `datetime`（整秒）；`stats_ib_commissions` 09-22~26 每客户日平均 3.83 条 IB 行、96% 多行 lots 相同 → 加总 lots 会放大约 3.8 倍。

### CEN 冲突结论

数据（平仓 2025-12-01..03、2026-06-01..03、2026-09-21..28 + 当前持仓，sid 1/5/6，CMD 0/1）：**CEN 账户只交易 .cent/.kcmc 品种**（交叉格 0 单）；非 CEN 账户持有 cent 品种的有 2 张未平仓单。
→ `trade_activity.py`（只对 cent 品种除手数、CEN 账户只除金额）是**精确**的；`run_sql.FIXED_CAVEATS`「CEN 账户金额和手数都 ×100」是**不精确**（实践中碰巧重合，但规则归属错了）。已改 caveat（`35ae662`）+ prompt 坑位措辞 + 测试钉三处一致。

### 每轮上下文开销（诚实版：prompt 没变短）

| 调用者 | prompt 前→后（字符） | skills 列表 | 合计增量 |
|---|---|---|---|
| 不受限 + risk | 17,172 → 17,785 | 4,143 | +4,756（≈1.5k tokens） |
| 不受限、无 risk | 13,348 → 13,977 | 3,738 | +4,367 |
| 受限 | 10,354 → 10,758 | 3,278 | +3,682 |

原因：守卫依赖、或缺了会产生静默错误的事实（join 路径、`users.cid`、开仓哨兵、15s）**刻意留在 prompt**——skill 只有模型决定 load 才会读到；已被测试钉住的 Risk tab 表也留着。skill 是「加深」不是「替代」。
是否把 schema card / Risk control 表整体搬进 skill（能省 ~3–7k 字符），建议等下面的真实问题前后对比再决定。
另：load 过的 skill 正文作为工具结果进会话历史（直到 compaction 折叠），会让 `ai_sessions` blob 变大——与「AI 会话存储增长」待办相关。

### 测试

- 新 `tests/test_ai_agent_skills.py`（89 个用例）：目录 = 加载数 = 可见性表；三个被扣的草稿不在也不被引用；无 TODO；按工具矩阵 × 真实 CallerCtx（受限 / 受限持 risk / 空 scope）过滤；隐藏 skill 按名 load/read 都「not found」且不记审计；只两个工具且 `never_require`、无脚本；SOURCES.md 三种拼法都读不到、不在任何清单；load/read 回调；`run_turn` 装配了过滤后的 provider；每个 SQL 块过守卫；关键事实；skill 不写 gap-trade 扫描时刻；禁用词只作禁令出现；**OPT-0069 之前被钉住的 prompt 事实全部仍可在 prompt 或 skill 中找到**；守卫依赖的事实仍在 prompt；规则 1 例外措辞；CEN 三处一致；打包（.dockerignore 不排 skills、`COPY . /app`、dev ro 挂载）。
- `tests/test_ai_route.py` +1（`skill_loaded` → `skills_loaded`、不转发浏览器）+ 契约用例断言 `skills_loaded == []`。
- 变异自检：去掉 resource_filter / 去掉删 run_skill_script / 读工具改回需审批 / run_sql skill 恒可见，各至少 1 个用例红。
- `./verify.sh`（worktree 根）：**PASS** — pytest 2810 passed / 1 deselected（slow），tsc 0，vitest 339，eslint advisory。AI 子集 592 passed。

### commits

`7b1c821` docs(opt) 恢复 item · `35ae662` fix(ai-agent) run_sql CEN caveat · `715042a` feat(ai-agent) Agent Skills（+ 本节所在 docs commit）

### 镜像

`backend/.dockerignore` 不排 `app/ai_agent/skills/**`，`Dockerfile.ai-agent` 的 `COPY . /app` 会烤进去；dev compose 已 `./app/ai_agent:/app/app/ai_agent:ro`。**未 build**。上线时照常 rebuild ai-agent（回滚标签 `pre-ai-skills-<date>`）；无新 env / 表 / 依赖。

### 前后对比用的真实问题（待用户审完草稿后由主会话跑，需 Azure 调用）

取自 09-27~09-29 `ai.query.submit` 真实提问（Sammy / Rebecca 等），看回答是否 load 了对的 skill、引用口径是否正确、是否诚实声明工具缺口：

1. 列出上週盈利最多的10位客戶 → 追问「以上10位客戶自開戶以來淨盈利為正的有哪些」「淨入金為負的有哪些」（kcm-metrics-definitions；rank_accounts 是账户不是客户；net_gain vs 净入金）
2. 幫我找只持倉多單黃金的account，剔除cent account，快被SO或保證金水平低於300%（margin-and-stopout + fxbackoffice-schema P1；受限账号应说无 margin 能力）
3. 同上「低於500%，同時需要是浮動虧損的」（P1 + `floating_pl < 0`）
4. 目前持倉多單黃金最多的account，剔除cent account，只有單邊buy，同時marginal level 要高於3000%
5. 幫我找MT4 account，只做單邊做多黃金單，快被SO（sid 1/6；不得声称公司 SO 水平）
6. 幫我添加這些account的目前盈虧損狀況（追问；rank_open_positions floating_pl vs get_client_overview）
7. MT5 manager/admin 如何找到快被so的account？（应说无 Manager 文档——mt-manager-howto 未上）
8. 這個帳戶5-67040168適合a-book嗎？（a-book-b-book 未上：应只给事实、不给 A-book 结论）
9. 5-67043472 等五个账户同 EA、其中三个转了 A-book，2026.09.28 03:31 执行结果不同（execution-and-slippage：两条执行路径、秒级时间看不到亚秒延迟、不能算补偿）
10. 從公司利益角度 a-book 有什麼好方法？（应只讲原因、转交 dealing，不推荐策略）
11. 請幫忙查看以上對客戶回覆，是否有反駁 / 幫我用英文草擬（client-reply-drafting 未上：不得写内部插件机制，并告知）
12. SID-67040168（非法 login_sid 形态 → 应要求 `{sid}-{login}`）
13. 以风控角色：最近 30 天哪些账户值得关注（risk-monitor-rules + trading-patterns；不下结论、不用禁用词）

### 开放问题（给用户）

1. 草稿审阅：8 个已上的 skill 里被我改写的 TODO 段（见上表）是否接受「未记录 → 不要说」这种处理，还是要等业务答复补真内容。
2. 3 个未上的 skill（a-book-b-book / mt-manager-howto / client-reply-drafting）找谁答复。
3. prompt 是否进一步瘦身（把 schema card / Risk tab 表搬进 skill），前后对比后再定？
4. 工具缺口另开 OPT：`rank_open_positions` 加 margin_level + 过滤前置（Sammy 快 SO 系列）、exec-compensation 摘要工具、`get_ib_chain`。
5. 起草期发现的文档过期项（risk-monitor.md rule 81 门槛、martingale tab、blowup/news-event「未落地」、Athens 日界、ib-financial-monitor 归属）本单未改。
6. 仓库有 post-commit hook：commit 碰到 `docs/**` 会重启 `new-it-mkdocs-prod` 重建文档站——本次提交 item 文件时触发了一次（非本单主动操作）。

### 冷审处理（2026-09-30，用户拍板后修；本节优先于上文冲突处）

1. **`execution-and-slippage` 暂不上**（用户决定：DealerLogic 机制保密）。移出 `app/ai_agent/skills/`、从 `SKILL_VISIBILITY` 删除（不再进镜像）；改过的版本放回主工作区 `docs/ai-agent/skills-draft/execution-and-slippage/`（SOURCES 顶部注明 held back），未改的原稿在 `_execution-and-slippage.pre-port-original/`。其它 skill 里描述机制的内容一并删除：margin-and-stopout「MT4 强平不经插件、MT5 经插件」及其 SOURCES 行。测试：被扣 skill 名与机制 marker（DealerLogic / oneZero / plugin / request price or worse / 0.35 s / 220 ms）不得出现在任何已上 skill。⚠ 分支历史 `715042a` 里仍有该 skill 的文件；未 push，合并前如需可 squash。
2. **可见性按调用者上下文判定**：`AUDIENCES`（all = 持 `ai`；run_sql = 持 `ai` + `run_sql_enabled`；risk = 持 `ai` + `risk_tools_enabled`）+ `SKILL_VISIBILITY`（受众表留在代码里，不进 frontmatter）；`visible_skill_names(ctx)` 每轮算一次，`build_skills_provider(ctx)`。**消除 all 受众的门控事实**：margin-and-stopout 删杠杆规则号段 / 200·150·125% / 开仓时机 / 15 分钟新鲜度 / get_risk_alerts 用法；ib-and-rebate 删已退役规则号段、get_window_scan 行，run_sql 表/列配方挪到 fxbackoffice-schema「IB questions」（新增 P7 IB 钱包余额：守卫通过、EXPLAIN 走 `userId` 索引 2 行）；trading-patterns-and-events 的全部 Risk 工具内容（告警特征、rule 71 配对、window scan、事件 AB 扫描与爆仓审计的检测参数）挪进 risk-monitor-rules（`style-features.md` / `window-scan-howto.md` / `event-ab-scan.md` / `blowup-audit.md`）。测试：固定 marker 清单（风控规则号段与阈值参数名 42 项、run_sql 表/列 10 项、被扣机制 6 项）不得出现在任何 all skill，且风控 / run_sql marker 必须真实存在于其所属 skill（防清单过期）。
3. **启动自检**：ai-agent lifespan 调 `harness.skills_self_check()`，发现结果 ≠ 表 → CRITICAL 列出 missing / extra。**选择不拒绝启动**：extra 已对所有人隐藏、missing 只是不可用，都 fail closed；容器 `restart: unless-stopped` 且无 healthcheck，拒绝启动 = 重启循环、整个助手挂掉（与现有 token 缺失检查同一姿态）。测试覆盖：现树通过无 CRITICAL、缺/多各一时 CRITICAL 点名、lifespan 调用且进程照常起来。
4. **资源白名单**：`skill_resource_allowed` 只放行 `references/<file>.md`（一层；SOURCES.md、`references/sub/…`、非 .md、`..` 全拒）。测试：skills 下每个 .md 要么是 SKILL.md、要么是 SOURCES.md、要么确实被发现为资源。

**最终可见性**

| skill | 受众 | 不受限 + risk | 不受限、无 risk | 受限 | 无 `ai` |
|---|---|---|---|---|---|
| kcm-metrics-definitions / ib-and-rebate / trading-patterns-and-events / system-pages-guide / margin-and-stopout | all | ✓ | ✓ | ✓ | — |
| fxbackoffice-schema | run_sql | ✓ | ✓ | — | — |
| risk-monitor-rules | risk | ✓ | — | — | — |

skills 列表每轮约 3.7k / 3.3k / 2.8k 字符（三类调用者）。测试：`test_ai_agent_skills.py` 104 个；AI 子集 607 passed；`./verify.sh` PASS（pytest 2825 passed / 1 deselected，tsc 0，vitest 339）。commit `956e963`（代码）+ 本节 docs commit。
不在本单（另开 hardening OPT）：token 上限 / compaction、审计资源粒度与拒绝计数、sqlglot 版 SQL 测试加固、MAF 升级注意事项、CODEOWNERS / 指令覆盖禁令、错工具提示、ai.py 事件白名单。

### Close（2026-09-30）

- 以 squash 合并进 main（分支历史 `715042a` 含被扣的 execution-and-slippage，不推远端）。
- 冷审 #5–#11（token 上限/压缩、审计 resource 粒度 + denied 计数、SQL 测试改 sqlglot、MAF 升级说明、skill 审核规则/禁指令覆盖、错工具提示、ai.py 事件白名单）→ 另立 hardening OPT-0073。
- 未做：用 13 条真实提问做前后对比（上线后做）；暂缓 4 个 skill 待 dealing / CS / 合规补内容。
