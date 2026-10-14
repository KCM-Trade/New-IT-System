---
id: OPT-0079
title: KCM 风控管道守卫 hardening —— K13 冷审 #2/#3/#5：lag 告警双份 + 从库语句超时 + J13 告警去重
status: wip
priority: P2
area: backend
effort: M
created: 2026-10-14
related: [[OPT-0066]]
---

> ⚠ **代码在另一个仓库**：`/opt/myproject/KCM_Risk_Control_System`（独立 Docker 容器 `kcm-risk-pipeline`，
> compose 在该仓库根目录，部署 = `docker compose build && docker compose up -d`，部署前先
> `docker tag kcm_risk_control_system-kcm-pipeline kcm_risk_control_system-kcm-pipeline:pre-<阶段>-<日期>`）。
> 本 tracker 只放在 New-IT 这边记账。实施 branch 开在 KCM 仓库：`opt/kcm-guard-hardening`。
> 测试：`cd /opt/myproject/KCM_Risk_Control_System && .venv/bin/python -m pytest -q`（当前 155 绿）。
> 项目约定先读该仓库 `CLAUDE.md` + `docs/decisions.compact.md`（K 号台账，本 OPT 落地后应追加 K14）。

## 问题

2026-10-14 K13（删除 `'7%'` demo 条款）上线时的独立冷审指出三条守卫层缺陷，都是「实测发生过、不是假设」：

1. **复制延迟告警每次发两封。** `src/main.py:50`（`self.guard = ReplicationGuard()`）和 `src/hot_path/positions.py:167`
   （`guard = ReplicationGuard()`）各自 new 了一个守卫，J1 与 J2 各自计数、各自发信。10-13 / 10-14 两天用户
   每天收到两封内容完全相同的「hot path skipped 5 consecutive rounds」。`guard.py` 文件头明明写着「J1/J2 可共享同一实例」。
   另：告警只在进入 skip 状态时发一次（`_alerted` 去重），之后**无论 skip 多久都不再提醒** —— 2026-09-05 MySQL 8.4
   升级后 `SHOW SLAVE STATUS` 1064、lag=None、J1/J2 连续 skip **5 天**才被人发现（见 `mysql_src.py:replication_lag` 注释）。
2. **从库常驻连接没有服务端语句超时。** `grep -rn MAX_EXECUTION_TIME src/` 为空；只有 `scripts/backfill.py:98`
   的 `GuardedMySQLSource` 设了 `SET SESSION MAX_EXECUTION_TIME`。`src/connectors/mysql_src.py:26 connect()` 只有
   `read_timeout`（客户端断开，**服务端查询继续跑并持有 MDL**）。这个从库有 2026-08-09 MDL 锁事故史（15h 冻结），
   且被多个团队共用。J13 三条 `mt4_users × mt4_users_snapshot` 聚合、J15 每 5 分钟拉全部 ~271k `user_tags` id 都走这条裸连接。
3. **J13 快照守卫的告警既漏又吵。** `src/cold_path/snapshot_guard.py:37` 阈值 `MISKILL_ALERT_THRESHOLD=5`：
   G-B 1–5 个活跃真实客户被误杀只打 WARNING 日志（没人看）；超过 5 之后**每天发同样的名单**（08-13 → 10-14
   连发 63 天、同样的 6–9 个名字），直到今天才有人处理。同类：J11 reconcile 的「对拍不符」邮件也连发过 40 天。

## 背景

- 三条都在 `/opt/myproject/KCM_Risk_Control_System`，Python 3.12 + APScheduler，15 个 job，一个容器。
- `send_alert()` 在 `src/ops/runs.py:79`，纯文本邮件，样式按 alert-email-style skill。
- PG（`risk_cases` 库 `kcm` schema）是唯一持久化落点；游标表 `kcm.pipeline_cursors`（`job_name, cursor_ts, cursor_id, cursor_date, updated_at`）。
  新表 DDL 放 `sql/00N_*.sql` 并在启动时 `CREATE TABLE IF NOT EXISTS`（看 `sql/002_tables.sql` 的现有做法）。
- 配置走 `src/config.py` 的 `settings`（`_env_int` / `_env_bool`，`.env` 在仓库根，**dev/prod 共用**）。
- backfill 侧 `scripts/backfill.py:119 wait_for_replication()`：lag=None 时 **放行**（fail-open），与热路径守卫（fail-closed）相反——冷审点名「选一个」。

## 假设 / 待验证

- [ ] 热路径每轮各 job 的最长查询实际多少秒（从 `logs/pipeline.log*` 的每轮耗时行估）——决定 #2 的默认超时值；别把正常的 J15/J13 查询杀了。
- [ ] `MAX_EXECUTION_TIME` 只对 SELECT 生效（MySQL 语义），本管道 MySQL 侧只读，所以够用；确认 `SHOW REPLICA STATUS` 不受影响。
- [ ] 去重状态放 PG 新表 `kcm.alert_dedup`（`alert_key PK, fingerprint, last_sent_at, first_seen_at`）还是复用 `pipeline_cursors`——倾向新表，语义清楚。

## 验收标准

**#1 lag 守卫**
- [ ] 整个进程只有**一个** `ReplicationGuard` 实例，J1/J2 共用（`main.py` 建一次、注入 `positions` 热路径；删掉 `positions.py:167` 那个局部 new）。
- [ ] 一次 lag 事件只发**一封**邮件；仍处于 skip 状态时**每 60 分钟**再发一封提醒（主题带 `still skipping, Nh`），恢复时发一封「recovered」（或至少 INFO 日志 + 下一封提醒不再发）。
- [ ] `lag=None` 的处理两侧统一为 **fail-closed**：`backfill.wait_for_replication()` 拿不到 lag 也等待（循环 sleep 60，日志 WARNING），不再放行。
- [ ] 单测：共享实例计数、60 分钟重提醒、恢复重置；`tests/test_guard*.py` 现有用例不退化。

**#2 语句超时**
- [ ] `MySQLSource.conn()` 每建一条新连接（含断线重连）都 `SET SESSION MAX_EXECUTION_TIME = <settings.mysql_statement_timeout_sec * 1000>`，
  `read_timeout` 对齐为超时 + 5；新配置 `MYSQL_STATEMENT_TIMEOUT_SEC`（`.env` 加注释行，默认值按「假设」第 1 条实测定，建议 60）。
- [ ] backfill 的 `GuardedMySQLSource` 改为复用基础层能力（只覆盖超时值为 `sql_statement_timeout_sec`），不再两套。
- [ ] 超时触发时（MySQL 错误 3024 `Query execution was interrupted, maximum statement execution time exceeded`）该 job 本轮记 `pipeline_runs` 失败 + ERROR 日志，**不**让调度器死掉。
- [ ] 单测：新连接执行了 SET SESSION（mock cursor 断言）；重连后再次执行。

**#3 J13 告警去重 + 单人误杀可见**
- [ ] G-B 触发条件改为 **任何 > 0**（去掉「>5」门槛）——但配合去重：按「误杀 loginSid 集合」算指纹，**指纹不变且 7 天内发过 → 不发**；出现新 loginSid → 立即发；指纹不变满 7 天 → 再发一封提醒（主题带 `unchanged for N days`）。
- [ ] 去重状态持久化在 PG（容器重建不丢）；写失败只 WARNING、不阻止发信（宁可多发不可漏发）。
- [ ] 邮件正文保留现有格式，加一行「vs last alert: +N new / −M resolved」。
- [ ] 把同一个去重 helper 套到 J11 reconcile 的「对拍不符」邮件（指纹 = 超差 (userId,date) 集合）。
- [ ] `SNAPSHOT_GUARD_MISKILL_THRESHOLD` 配置废弃（删除或改成「≤ 此数只记日志」默认 0），`.env`/文档同步；decisions.compact.md 追加 **K14**，architecture.md §E5 告警段同步。
- [ ] 单测：指纹相同 7 天内不发 / 新名字立即发 / 7 天到期提醒 / PG 写失败仍发。

**通用**
- [ ] `pytest` 全绿；不改 MySQL 侧任何口径 SQL（norms.py 不动）。
- [ ] 部署：先打回滚 tag，再 build + up -d；观察一轮 J1/J2 日志有 `MAX_EXECUTION_TIME` 已设的 INFO 行。

## 笔记

- 冷审原文（2026-10-14，subagent）：lag 守卫 J1/J2 各一份实例；`MySQLSource` 无服务端超时，`read_timeout=120` 只断客户端；
  J13 G-B 63 天同样名单；E5 文档声称的「>5 min stale 告警」未实现（grep 无）——这条**不在本 OPT 范围**，另立。
- 05:06–05:12 HKT 的每日 lag 尖峰 = MT 日切（master 日终写入），是预期内的，**别**为它加特殊窗口豁免——60 分钟重提醒机制下它只会在首次发一封，几分钟后 recovered。
- 不要动 `norms.py` / 口径 SQL；不要动 New-IT 侧。

## 结果

<done 时填>
