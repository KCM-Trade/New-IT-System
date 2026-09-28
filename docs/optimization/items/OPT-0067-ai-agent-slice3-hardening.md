---
id: OPT-0067
title: AI agent 第三刀 hardening —— agent 内 SQLite 读的语句级截止 + 老工具每轮调用上限硬性化
status: ready
priority: P2
area: backend
effort: S
created: 2026-09-28
related: [[OPT-0066]] [[OPT-0065]]
---

## 问题

1. **SQLite 读没有语句级超时**（OPT-0066 冷审 ⚪#8）。agent 容器里的工具经 `tools/common.run_sync_with_timeout` 跑同步 DB 调用：25s 到了只是**等待方**放弃（`abandon_on_cancel=True`），工作线程继续跑完。MySQL / PG 有服务端 `MAX_EXECUTION_TIME` / `statement_timeout` 兜底，SQLite 没有——`risk_monitor_db.open_readonly()`（`backend/app/core/risk_monitor_db.py:1411`）只有 `busy_timeout`。一次 31 天聚合是四条语句（`ai_agent/tools/risk_alerts.py` 受限预扫 + counts + aggregate），库再长大后可能在后台线程里持续占 CPU / 读锁。实测现在 0.2s，现实风险低。
2. **「每工具每轮 ≤2 次」只对第三刀三个工具硬性执行**（`ai_agent/harness.py` `MAX_CALLS_PER_TOOL_PER_TURN`，`RISK_TOOL_NAMES`）。老工具（`get_client_overview` 等五个 + `run_sql`）仍只靠 prompt 一句话（`prompt.py`「Use each tool at most twice per turn」），而 03 §3 把它写成硬上限。

## 方案

1. agent 侧打开的 SQLite 只读连接挂 `conn.set_progress_handler(cb, N)`，`cb` 在超过截止时间（与工具 25s 对齐，或更短如 20s）时返回非零 → 语句以 `sqlite3.OperationalError: interrupted` 结束 → 映射 `upstream_timeout`。只在 agent 调用路径上挂（`open_readonly(deadline=…)` 参数或工具侧包装），**不改**主 API 页面路径。
2. 先查审计表 `ai.query.submit.tools_called` 里老工具一轮 >2 次的真实频率（比较客户场景是否合理需要 3 次），再决定是全量硬性还是按工具设不同上限。

## 验收标准

- [ ] 慢查询 stub（progress handler 触发）→ 工具返回 `upstream_timeout`，线程在截止后不再运行（单测）
- [ ] 主 API 页面的 `risk_monitor_db` 调用行为不变
- [ ] 老工具上限：先出审计频率数据，再定方案（用户拍板）

## 结果

（待实施）
