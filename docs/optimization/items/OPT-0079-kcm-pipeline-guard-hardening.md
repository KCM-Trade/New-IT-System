---
id: OPT-0079
title: KCM 风控管道守卫 hardening —— K13 冷审 #2/#3/#5：lag 告警双份 + 从库语句超时 + J13 告警去重
status: done
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

- **实施记录(2026-10-14,branch `opt/kcm-guard-hardening`,未部署未 merge)**:
  - **超时默认值实测**:`logs/pipeline.log*` 没有「每轮耗时」行(各 job 只打行数),改用 `pipeline_runs.started_at/finished_at`
    (45 天,只读 PG 探针)+ 日志相邻行时间差推单条语句:
    | job | 单次运行 p50 / p99 / max(秒) | 单条语句量级 |
    |---|---|---|
    | J1 open_positions(43,590 次) | 1.7 / 2.2 / 165.3(单次孤例) | 1 条全量 + 回查分批 |
    | J2 account_state(43,592 次) | 1.5 / 1.7 / 12.1 | 1 条增量 |
    | J15 crm_tags(8,726 次) | 2.7 / 5.8 / 9.1 | 拉 ~29 万 id |
    | J9 intraday(8,726 次) | 3.6 / 12.9 / 26.6 | 2 天各 1 条,单日 ≤~6s |
    | J4 closed_orders | 8.6 / 12.4 / 12.5 | 同上 |
    | J13 snapshot_guard | 7.9 / 9.4 / 9.6 | 4–5 条 + 3–4s sleep |
    | J16 hold_stats | 191 / 356 / 396 | **60 条逐日,单日 max 7.0s**(日志相邻行差) |
    | J6w rebate_full | 181 / 183 / 183 | **按年 1 条,max 39.3s(2025 块,含 PG 写 29.4 万行)** |
    | J11 reconcile | 158 / 169 / 169 | 几百条小查询 + sleep |
    | J6 / J7 / J8 / J14 / J12 | max 5.2 / 6.1 / 8.2 / 9.1 / 3.1 | — |
    ⇒ **`MYSQL_STATEMENT_TIMEOUT_SEC` 默认 60**(read_timeout 65):比最长单条(39s,且含 PG 写)留 1.5× 余量,
    热路径 p99 留 30×。⚠ j6w 的 2026 年块随年增长会逼近 39s;若 prod 出现 3024,给 c6w 的 `MySQLSource`
    传更大的 `statement_timeout_sec`,别全局调高。`MAX_EXECUTION_TIME` 只作用 SELECT,`SHOW REPLICA STATUS` 不受影响(MySQL 语义)。
  - **去重状态**:新表 `kcm.alert_dedup`(`sql/004_alert_dedup.sql`),没复用 `pipeline_cursors`;启动 `ensure_table()` 幂等建表,
    **未对活库手建**,部署后首轮 J13/J11 读表失败 → WARNING + 自动建表 + 当「从没发过」发信。
  - **与验收标准的出入**:①recovered 邮件只在**发过至少一封提醒(事故 ≥1h)**时补发,短事故(MT 日切 5–7 轮)只 INFO
    —— AC 允许「或至少 INFO 日志」,这样每日日切只有首封一封;②J11 K4 的指纹成员是 `userId:metric` 而非 (userId,date)
    —— K4 的 check_date 恒为 today,按日期算等于天天新指纹;R4 按 AC 用 (userId,stat_date);③G-B 指纹「变了」含成员消失
    (只少不多也发),按 AC「指纹不变才不发」的字面;④`SNAPSHOT_GUARD_MISKILL_THRESHOLD` 直接删除(未留「≤N 只记日志」),
    `.env.example` 留废弃说明;⑤`MySQLSource.conn()` 由 `ping(reconnect=True)` 改为 `ping(reconnect=False)` + 显式重连,
    为的是重连走同一条 `connect()`(init_command + INFO 行),行为等价。
  - 旧 `GuardedMySQLSource` 用对象属性 `_kcm_backfill_guard` 标记「已设」,但 pymysql `ping(reconnect=True)` 在**同一对象**上
    重建会话,标志不会清 ⇒ 重连后守卫静默失效。新实现用 `init_command` 由 pymysql 在每次 connect 重放,没有这个洞。
  - 测试 155 → **200 绿**(新增 `test_guard.py` 11 / `test_mysql_src_timeout.py` 10 / `test_alert_dedup.py` 17 / `test_scripts.py` +7)。
  - **部署时看**:J1/J2 启动后日志有 `mysql session guard set: MAX_EXECUTION_TIME=60s read_timeout=65s`(每个 JobCtx 一行,15 条连接);
    次日 04:40 J13 日志 `alert_dedup[j13_gb_miskill]: first ...`;06:20 J11 两个 key 同理。回滚 tag 建议 `pre-guard-hardening-<日期>`;
    回滚**不需要**回滚数据(`alert_dedup` 表旧代码不读)。

- **冷审处理(2026-10-14 第二轮,10 MUST + 3 nit,同 branch 追加 commit,218 绿)**:
  1. **J11 去重形同虚设** → R4 指纹改 `userId:metric`(去掉 stat_date);K4 **只对确定性的 R1 行取指纹**(`userId:metric`),
     随机抽样的不符**不进指纹**、在正文单独报个数并**每次都发**(抽样每天换人,按定义是新发现)。**选的是「R1 only + 抽样计数在外」**,
     没改成按 ISO 周定种——那会把 K4「每天换人」的覆盖面砍成 1/7,属于口径变化,不在冷审授权内。
  2. **`sent=True` 虚记** → `send_alert()` 返回 bool(SMTP 真收下才 True;禁用 / 失败 False),三处 `commit(sent=...)` 都传它;
     `AlertDedup.commit()` 在 `ALERT_ENABLED=false` 时不落库;`reconcile.py --no-dedup`(`persist=False`)供手工跑。
  3. **共享计数砍半门槛** → 首封改**墙钟 5 分钟**(`alert_after_sec=300`,用已有的 `_skip_started_at`),次数只进正文;
     `account_state.py` docstring 改为「config 默认 30s、prod .env 60s」;测试按 J1+J2 每分钟两次 check 重写。
     ⚠ 每日 05:06–05:12 日切尖峰约 6 分钟,**≥5 分钟的那几天仍会发首封一封**(item 要求不加时间窗豁免,未加)。
  4. **60s vs 39s 年块 / 165s 孤例** → `JobCtx(statement_timeout_sec=180)` 给 c6w;其余默认 60。**失败邮件**:`RunRecorder` 在
     status=failed 时经 `alert_dedup`(key `run_failed:<job>`,成员 = HKT 日期)每 job 每天一封,正文含 error_text 与 3024 指引;
     告警路径自身异常只 `log.exception`,绝不盖掉 job 原异常(有测试)。
  5. **K4 崩掉 R4 陪葬** → `_j11_reconcile` 两段各自 try/except + `log.exception`,各自 `recorder.record`(各自 pipeline_runs 行 + 失败邮件)。
  6. **回填无声死等** → `wait_for_replication(max_wait_sec=3600, clock=…)`:等满 10 分钟 `send_alert` 一封(只一次),
     满上限抛 `ReplicationWaitTimeout` → main ERROR 退出 1;CLI `--max-wait-min`(默认 60);`backfill_hold_stats` 走默认。
  7. **提醒落在第 8 天** → `decide()` 改**日历日**比较(`(now.date() - last_sent_at.date()).days >= 7`),`unchanged_days` 同。
  8. **G-B 名单抖动** → 只少不多(`added` 空、`resolved` 非空)= `shrunk`:不发、更新存储集合、保留 `first_seen_at`/`last_sent_at`;
     下一个真正新名字仍立即发;缩小后名单不变满 7 天照常提醒。
  9. **装配无测试** → `tests/test_main_wiring.py`:替换 MySQLSource/PgSink/PositionSyncer/j2 后 `build_scheduler()`,断言
     J1 syncer 与 J2 闭包拿到**同一个** `ReplicationGuard` 对象、15 jobs、只有 c6w 是 180s、K4 崩不拖 R4。
  10. **DDL 的 `SET search_path`** → 从 `sql/004` 删掉(sink 连接已 `search_path=kcm,public`),留注释;测试断言文件无 `SET` 语句。
  - nit:architecture J11 05:00 → 06:20;`scripts/conn.py`(`source()` / `conn()` / 兼容 `cfg()`)与 `repl_lag_probe.py` 改走
    `MySQLSource`(探测脚本也带服务端上限,`lag_probe.py` 经 `conn()` 自动受益);`guard.check` 加注释说明 send 失败时
    `_last_alert_at` 已记、下次尝试在 60 分钟后(刻意)。
  - 测试 200 → **218 绿**(新 `test_runs.py` 7 / `test_main_wiring.py` 4 / dedup +5 / scripts +2)。

## 结果

**2026-10-14 已 merge + 部署**（KCM 仓库 merge `a51d10f`，回滚镜像 `kcm_risk_control_system-kcm-pipeline:pre-opt0079-20261014`，回滚不需要回滚数据——`kcm.alert_dedup` 是新表，旧代码不读）。

交付 vs AC：三部分全部交付；偏差 ①recovered 邮件只在至少发过一次重提醒后才发（日切尖峰不产生第二封）；②首封告警改为**墙钟 5 分钟**而非 5 轮（共享实例后 J1+J2 一分钟两轮）；③K4 指纹只取确定性 R1 行，抽样不符在正文计数、每次都发；④`SNAPSHOT_GUARD_MISKILL_THRESHOLD` 直接删除。

冷审处理：第一轮（K13 上线时）3 条 = 本 OPT；第二轮（本分支）10 条全部当场修（见 笔记 §冷审处理），无 live-with。

Follow-up（未立单）：E5 文档声称的「T1 >5 min stale 告警」仍未实现；j6w 年块 180s 上限若将来仍超时需改成按年分块。

部署后首轮：J1/J2 日志出现 `mysql session guard set: MAX_EXECUTION_TIME=60s read_timeout=65s`，15 jobs 注册，J1 7042 行正常。
