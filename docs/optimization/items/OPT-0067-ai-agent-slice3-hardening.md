---
id: OPT-0067
title: AI agent 第三刀 hardening —— agent 内 SQLite 读的语句级截止
status: ready
priority: P2
area: backend
effort: S
created: 2026-09-28
related: [[OPT-0066]] [[OPT-0065]]
---

## 问题

1. **SQLite 读没有语句级超时**（OPT-0066 冷审 ⚪#8）。agent 容器里的工具经 `tools/common.run_sync_with_timeout` 跑同步 DB 调用：25s 到了只是**等待方**放弃（`abandon_on_cancel=True`），工作线程继续跑完。MySQL / PG 有服务端 `MAX_EXECUTION_TIME` / `statement_timeout` 兜底，SQLite 没有——`risk_monitor_db.open_readonly()`（`backend/app/core/risk_monitor_db.py:1411`）只有 `busy_timeout`。一次 31 天聚合是四条语句（`ai_agent/tools/risk_alerts.py` 受限预扫 + counts + aggregate），库再长大后可能在后台线程里持续占 CPU / 读锁。实测现在 0.2s，现实风险低。
2. ~~**「每工具每轮 ≤2 次」只对第三刀三个工具硬性执行**……把老工具的上限也做成硬性~~ —— **作废（2026-10-07 划掉）**：每工具每轮上限已于 2026-09-28 12:53 按用户要求整体取消（`0545418`，`harness.py` 里 `MAX_CALLS_PER_TOOL_PER_TURN` 已删），一轮现在只受 40 次迭代 / 520s 约束，没有东西可「硬性化」。

## 方案

1. agent 侧打开的 SQLite 只读连接挂 `conn.set_progress_handler(cb, N)`，`cb` 在超过截止时间（与工具 25s 对齐，或更短如 20s）时返回非零 → 语句以 `sqlite3.OperationalError: interrupted` 结束 → 映射 `upstream_timeout`。只在 agent 调用路径上挂（`open_readonly(deadline=…)` 参数或工具侧包装），**不改**主 API 页面路径。
2. ~~先查审计表里老工具一轮 >2 次的真实频率，再决定上限方案~~ —— 作废，同上。

## 验收标准

- [ ] 慢查询 stub（progress handler 触发）→ 工具返回 `upstream_timeout`，线程在截止后不再运行（单测）
- [ ] 主 API 页面的 `risk_monitor_db` 调用行为不变
- ~~老工具上限：先出审计频率数据，再定方案~~ —— 作废（上限已取消）

## 结果

（待实施）
