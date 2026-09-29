---
id: OPT-0068
title: 成交价差补偿 MT5 v1 —— Data Query 页面 + `/api/v1/exec-compensation/*`（请求价口径，本地预计算表 + Deal 游标同步）
status: done
priority: P1
area: mixed
effort: L
created: 2026-09-29
related: []
---

> ⚠ 按 tracker 规则这是 net-new feature，但同 0062–0066 先例，由用户 kickoff（`docs/exec-compensation/05-kickoff-prompt.md`）要求 file 成 OPT。分支 **`feat/exec-compensation`**（独立 worktree）。
> **本文件不是实施 SSOT。** 需求 / 决策 / 数据契约 / 实施方案 / 验收全在 `docs/exec-compensation/`（01–04，本机资产，docs/** 被 gitignore）。状态表在 01 §4。

## 问题

MT5 `DealerLogic` 插件把客户订单延迟约 300–400ms 再成交，成交价相对请求价只会变差或不变。需求方 Lawrence（2026-09-28）要一个内部查询：客户 / 账户 + 日期范围 → 延迟统计 + 按请求价逐笔算应补 USD。

## 范围（03 §7）

T0 补验 U1（`PriceCurrent` = 插件请求价）→ T1 classify/calc 纯函数 → T2 本地表 + 同步 + 回填 → T3 契约 + query.py + 入口 A 路由 → T4 前端（Data Query）→ T5 153034 对账 → T6 文档 / 冷审 / 部署。

## AC

见 `docs/exec-compensation/04-acceptance.md`；核心基准 153034（08-29~09-28，as_of=09-28）= 99,916 笔 / 6,354.84 手 / 正负相抵 208.2398 USD / 只补正数 208.6051 USD / 未计入 502 仓位 502 笔 5.02 手。

## 结果

交付（2026-09-29，分支 `feat/exec-compensation` → main）：
- T0 补验 U1：09-25 全天日志 vs 从库 11.1 万笔市价成交 → 普通组成立（99.3–99.8%），两处例外定为 01 D19（`Dealer = 1` oneZero 不补）/ D20（`KCM\5LS*@769` 不补）。
- 用户推翻预计算（01 D21）：请求时按需读从库，`Login + Deal 区间` 覆盖索引只取成交号（60009859 一个月 0.33s）。门槛 10s/条 · 60s/次 · 15 万笔（D22）· 全服 2 个 flock 槽 · Redis 1h（zlib 列数组）。
- 契约 `schemas/exec_compensation.py` + OpenAPI 快照；错误体 `{"error":{code,message}}`（仅本 API）；`MODULE_MAP` → `data`；不写审计。
- 前端 Data Query `/exec-compensation`：三条醒目说明、正负相抵为主、未计入卡片、逐笔 AG-Grid 服务端分页、xlsx 导出（第一行说明）。
- T5 `scripts/exec_comp_reconcile.py`：153034 全部 42 项与 04 §1 相等，逐笔与原型 CSV 99,916 行一致。04 §1.2 close_by 金额更正为 +0.0320（原型按 Type 判方向的缺陷）。
- 测试：exec_comp 203 个（不连库）；verify.sh PASS。

冷审（Stage 1）处理：
- #1/#2/#4 上限与内存：**当场修**——上限 50 万 → 15 万（01 D22，超过提示联系 IT，IT 用 `scripts/exec_comp_offline_export.py` 分段导出），slots dataclass / 提前释放 / 导出不建 pydantic 对象，10 万笔导出峰值 667MB → 300MB。
- #6 错误契约：**当场修**——`UPSTREAM_UNAVAILABLE` / `QUERY_BUDGET_EXCEEDED`（503 + Retry-After），`QUERY_TOO_LARGE` 只留给确定性上限。
- #5a CALC_VERSION：**当场修**——测试把 classify/calc 规则的 AST 哈希钉在版本号上。
- #7 #9 #12 #13：**当场修**（step-back 缓存键、DST 余量 3h、FORCE INDEX、舍入说明）。
- #8 周末就绪：实现后发现原提议有缺陷（分不清「安静」与「从库没追上」），**默认关闭**（`EXEC_COMP_READY_LAG_S=0`）；MT5 周末有加密货币成交，原规则已够。
- **Live with（follow-up）**：#3 缓存命中仍要解压整份结果（15 万上限下约 0.5s CPU）；#5b 账户组 / 币种取当前值，历史成交会按新组重新分类（入口 B 前阻塞，03 §4.4）；#10 外部调用翻页跨 MT 午夜（入口 B 前阻塞）；#11 时间预算非严格；source SQL 无单测（靠实时对账覆盖）。

其他 follow-up：04 §5 浏览器手测；Q7（Reason = 2 dealer 单来源）、D20 一跳差原因待问运维；入口 B（01 Q12）。
