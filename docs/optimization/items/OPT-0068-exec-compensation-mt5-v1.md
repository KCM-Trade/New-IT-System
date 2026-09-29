---
id: OPT-0068
title: 成交价差补偿 MT5 v1 —— Data Query 页面 + `/api/v1/exec-compensation/*`（请求价口径，本地预计算表 + Deal 游标同步）
status: wip
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

（未完成）
