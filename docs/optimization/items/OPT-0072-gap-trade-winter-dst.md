---
id: OPT-0072
title: Gap Trade 扫描冬令时漏扫 —— cron 按 HKT 固定 07:20、now_mt 固定 +3，11 月起早于 MT 窗口结束
status: wip
priority: P0
area: backend
effort: S
created: 2026-09-30
deadline: 2026-10-31（冬令时首个扫描 = 2026-11-02 周一 07:20 HKT；11-01 是周日不扫）
related: [[OPT-0032]] [[OPT-0062]] [[OPT-0069]]
---

## 问题

Gap Trade（rule 71–80 SO+AB · 81–90 缺口超额盈利）扫描 MT 当日 `[00:00, 02:00)` 窗口。MT 服务器按**美国 DST**
走：夏令 UTC+3、冬令 UTC+2（2nd Sun Mar → 1st Sun Nov；CLAUDE.md「Timezones」、`rule_intraday_return_service.MT_SERVER_TZ`）。
该功能 2026-05-12 上线，**从未在冬令时跑过**。代码两处写死夏令假设：

1. **cron 按 HKT 固定时刻**（`backend/app/core/burst_open_scheduler.py` ~1405–1430）：
   final scan `CronTrigger(day_of_week="mon-sat", hour=7, minute=20, timezone="Asia/Hong_Kong")`。
   夏令 HKT 07:20 = MT 02:20（窗口结束后 20 分钟 ✅）；**冬令 HKT 07:20 = MT 01:20 —— 窗口还剩 40 分钟**。
2. **`now_mt` 固定 +3**：`_run_gap_trade_scan`（~946）与 `_run_gap_trade_intraday_scan`（~1132）
   `now_mt = now_utc + timedelta(hours=3)`，注释还写着「MT is UTC+3 with no DST」（已被 CLAUDE.md 实测推翻）。
   冬令时它算出的 02:20 是假的（真 MT 01:20），所以不会发现「窗口未结束」。
3. **intraday tier 的 HKT 窗口**（`GAP_TRADE_INTRADAY_START_HKT=(5,55)` / `END=(7,5)`，~38–43 + cron hour 5-7）
   是按「黄金 MT 01:00 开盘 = HKT 06:00」配的。冬令时黄金开盘 = HKT 07:00，intraday 窗口只覆盖开盘后 5 分钟。

### 后果（冬令时每个交易日）

- MT 01:20–02:00 平仓的缺口单**永久漏扫**：final scan 只跑一次、不回补；这 40 分钟恰在黄金开盘（MT 01:00）后的活跃期。
- rule 81 触发的 CRM 风控标签（禁止出金(風控) / Withdrawal Notice，`gap_trade_crm_tag_service`）随之漏打 —— **涉及资金**。
- intraday tier 几乎失效，实时打标签退化为只靠 final scan（而 final scan 本身也漏）。
- `window_day` 在冬令时仍是对的日期（HKT 07:20 = UTC 23:20，+3 → 次日 02:20，真 MT 01:20 同一天），所以**不报错、不告警**，
  只是静默少数据 —— 这是最危险的形态。

## 方案

**把调度锚到 MT 时钟，而不是 HKT。** MT 时钟下市场时刻全年不变（黄金 MT 01:00 开、窗口 MT 00–02），DST 只影响它映射到 HKT 的时刻。

1. `now_mt` 两处改用 `datetime.now(timezone.utc).astimezone(MT_SERVER_TZ)`（`app.services.rule_intraday_return_service.MT_SERVER_TZ`，
   即日高收益已在用的 DST 版）。删掉「UTC+3 no DST」注释。
2. final scan cron：首选 `CronTrigger(..., hour=2, minute=20, timezone=MT_SERVER_TZ)` —— **先验证** APScheduler 能否接受
   `_MTServerTZ` 这个自定义 tzinfo（它不是 `ZoneInfo`；APScheduler 3.x 需要 pytz 或能 `localize`/`normalize` 的 tz，
   可能不兼容）。不兼容时的备选：
   - 用 `Europe/Athens` 等 IANA 时区？**不行** —— 欧盟 DST 与美国差 2–3 周（CLAUDE.md 实测 03-10 已 +3、10-28 仍 +3）。
   - 备选 A：cron 同时在 HKT 07:20 与 08:20 各注册一次，函数内按真 MT 时钟判断「窗口是否已结束 ≥15 分钟且今天还没跑过」，
     只执行一次（用 `scan_history` 或内存标记幂等）。
   - 备选 B：`IntervalTrigger` 每 10 分钟 + 同样的「窗口已结束且今日未跑」守卫。
3. intraday tier：窗口守卫从 HKT `(5,55)–(7,5)` 改为 **MT** `(00:55)–(02:05)`，cron 的 hour fence 放宽为 HKT 5–8
   （两层防线保留：trigger 粗框 + 函数内按 MT 精确判断）。
4. 日志文案里的「07:20 HKT」改成「MT 02:20（夏 HKT 07:20 / 冬 HKT 08:20）」；`gap_trade_crm_tag_service` 的
   `scan_label="final 07:20 HKT"` 同改。

### ⚠ 不要动的东西

- **告警表里存的时间固定 +03:00**（`rule_gap_trade_*_service` 的 `timedelta(hours=3)` ~361 / ~528，检测器 `broker_time_to_utc_iso`），
  还原靠 `alert_orders_service.stored_alert_time_to_mt`。这是存储约定，改了会让历史告警与新告警混用两种偏移、下钻全空
  （CLAUDE.md AI agent 第三刀条目的冷审教训）。本单**只改调度 / 窗口判断，不改存储**。页面按 UTC+3 显示的 1h 偏差另开单。
- 同类固定 +3 还出现在 `services/rule_rebate_arb_service.py`（已退役）、`services/exec_comp/source.py`、`api/v1/routes/honeypot.py` —— 不在本单范围，
  实施者顺手 grep 评估一句是否受 DST 影响，写进结果段即可。

## 验收标准

- [ ] 单测：冻结时钟在 **冬令**（如 2026-11-02 23:20 UTC）与 **夏令**（2026-10-05 23:20 UTC）两个时刻，final scan
      计算出的 `now_mt` / `window_day` / 窗口结束判断正确；冬令 HKT 07:20 **不**执行扫描（或等到 MT 02:xx 才执行），HKT 08:20 执行
- [ ] 单测：DST 切换日边界（2026-11-01 周日 → 11-02 周一；2027-03-14）
- [ ] 单测：intraday 窗口守卫按 MT 判断，冬令 HKT 06:55–08:05 内触发、夏令 05:55–07:05 内触发
- [ ] final scan 每个 MT 交易日**恰好执行一次**（若用备选 A/B，要有幂等测试）
- [ ] APScheduler 与 `_MTServerTZ` 兼容性结论写进结果段
- [ ] 现有 `tests/test_gap_trade_crm_tag.py`、`test_rule_gap_trade_*` 全绿；`./verify.sh` 绿
- [ ] 部署前打回滚标签 `pre-gap-dst-<date>`；**2026-10-31 前上 prod**
- [ ] 11-02（周一）早上人工确认：scan_history 有一行、窗口 = MT 11-02 00:00–02:00、执行时刻 ≥ MT 02:00
- [ ] 更新 docs/features/risk-monitor.md 的 gap-trade 扫描时刻；`prompt.py:216` 与 `ai_agent/tools/risk_alerts.py:547`
      的「次日 05:20 HKT 扫前一 MT 日」改为「MT 02:20 扫当日 00:00–02:00」（同一事实，一起改）

## 结果

实施 2026-09-30，分支 `opt/gap-trade-winter-dst`，commit `247a9e4`（调度）+ `2128e33`（AI prompt 事实）。**未 merge / 未部署**。

### 采用的方案：首选方案（cron 直接挂 MT 时钟），没有走备选 A/B

**APScheduler × `_MTServerTZ` 兼容性结论：兼容，可直接用。** 本机 venv APScheduler 3.11.2、prod 容器 3.11.3（Python 3.11.13）。
3.11 已不走 pytz：`util.astimezone()` 对非 `ZoneInfo`、无 `.zone` 属性的 tzinfo **原样透传**；`localize()` 只在 tz 有 `localize` 方法时才调，
否则 `replace(tzinfo=)` + `fromtimestamp` 归一；`CronTrigger.get_next_fire_time` 全程是 aware datetime 运算，只用到 `utcoffset/dst/fromutc`。
实测 `CronTrigger(day_of_week="mon-sat", hour=2, minute=20, timezone=MT_SERVER_TZ)`：10-30 23:20Z（Sat 10-31 夏令）→ 11-02 00:20Z（Mon 冬令，HKT 08:20）→ 每天 00:20Z；
2027-03-13 00:20Z（Sat 冬令）→ 03-14 23:20Z（Mon 03-15 夏令）。⚠ 已知限制：①`requirements.txt` 里 apscheduler **未钉版本**，升到 4.x 会整套 API 变——`test_gap_trade_dst.py` 的 trigger 测试会红，就是护栏；
②`_MTServerTZ` 按「本地日期」整天切换（不是凌晨 2 点），切换发生在周日，而 cron 周日不跑，所以无影响；③只用 MemoryJobStore，自定义 tz 不需要 pickle。

### 逐条 AC

- [x] **冬 / 夏冻结时钟单测**（`tests/test_gap_trade_dst.py`）：夏 2026-10-05 23:20Z → MT 10-06 02:20，扫 10-06 00:00–02:00，label `final MT 02:20 (HKT 07:20)`；
      冬 2026-11-02 23:20Z（HKT 07:20 = MT 01:20）→ **拒扫**（不 detect、不写 alert_events、不打 CRM tag）；冬 2026-11-03 00:20Z（HKT 08:20）→ 扫满窗口，label `final MT 02:20 (HKT 08:20)`。
      另：trigger 在冬令 HKT 07:20 求下一次触发 = HKT 08:20。
- [x] **DST 切换日边界**：`_mt_now` 六个时刻（含 10-31 / 11-02 / 2027-03-13 / 03-15）+ trigger 精确触发时刻两段（2026-10-30→11-03、2027-03-12→03-15）+ 首个冬令周一 11-02 整扫。
- [x] **intraday 守卫按 MT**：常量改为 `GAP_TRADE_INTRADAY_START_MT=(0,55)` / `END_MT=(2,5)`，函数 `_in_intraday_window_mt`；cron 粗框放宽为 HKT 5–8（**故意不挂 MT tz**，作为独立于 MT tz 逻辑的第二层）。
      冻结时钟 10 个参数：夏 HKT 05:54✗ 05:55✓ 07:05✓(end 夹到 02:00) 07:06✗；冬 HKT 05:55✗(MT 周一 23:55) 06:54✗ 06:55✓ 07:30✓ 08:05✓ 08:06✗。另测 HKT 粗框在两季都完整覆盖 MT 00:55…02:05 共 15 个 tick、且都落在同一 MT 日。
      原 `test_gap_trade_crm_tag.py` 的边界测试同步改成 MT 分钟。
- [x] **每个 MT 交易日恰好一次**：首选方案下由 cron 本身保证（不需要幂等标记）；测试枚举 2026-10-25→11-15、2027-03-07→03-21 的全部触发：每次都是 MT 02:20、每个 MT 周一–周六恰一次、周日零次。
- [x] APScheduler 兼容性结论：见上。
- [x] 现有 gap-trade 测试全绿（`test_gap_trade_crm_tag` / `test_rule_gap_trade_gap_service` / `test_rule_gap_trade_so_service` / `test_scheduler_tiers` / `test_alert_mail_subject` + 新文件 = 115 passed）；
      `./verify.sh` **PASS**：pytest 2748 passed / 1 deselected（slow）· tsc 0 · vitest 339 passed（eslint advisory 316 problems 为既有，不阻塞）。
- [ ] 部署前打回滚标签 `pre-gap-dst-<date>`；2026-10-31 前上 prod —— **待主会话**。
- [ ] 11-02（周一）早上人工确认 scan_history 一行、窗口 MT 11-02 00:00–02:00、执行时刻 ≥ MT 02:00（= HKT 08:20 之后）—— **待上线后**。
- [x] 文档 / prompt：`docs/features/risk-monitor.md` §3.4 调度段、盘中 tier 段、架构框图、时区约定表都改了（⚠ 该文件被 gitignore，只落在本机磁盘，不在 commit 里）；
      `prompt.py` RISK_CONTROL_BLOCK 与 `risk_alerts.py` caveat 改为「MT 02:20（HKT 07:20 夏 / 08:20 冬）扫**同一** MT 日 00:00–02:00」——原文「次日 05:20 HKT 扫前一 MT 日 / scanned_at 比 window_date 晚一天」本来就是错的（现行代码夏令也是同日扫），一并更正。
      原先**没有**测试钉这条事实，新增 `test_ai_agent_prompt.py::test_gap_trade_scan_time_fact_matches_the_scheduler`（对齐 scheduler 常量 `GAP_TRADE_FINAL_HOUR_MT/MINUTE_MT`）。

### 其他改动

- `_run_gap_trade_scan` 新增守卫：MT 窗口未收盘就 WARNING 拒扫（防手动触发 / 时区 bug 把半截窗口写成当日权威记录）。**例外**：UI 配置把 `window_end_hour_mt` 拉到 02:20 之后（schema 允许 1–24）时，保持旧行为照扫部分窗口 + WARNING，避免变成天天拒扫。
- scan_label 变为动态：`final MT 02:20 (HKT 07:20|08:20)` / `intraday MT hh:mm (HKT hh:mm)`（邮件标题里能看出季节）。
- 顺手改掉陈旧注释：`routes/risk_monitor.py` 三处「HKT 05:20 扫 MT 昨天」、`gap_trade_crm_tag_service.py` / `rule_gap_trade_so_service.py` 的「07:20」。
- **未动**存储约定：`broker_time_to_utc_iso`、`rule_gap_trade_*` 的 `timedelta(hours=3)`、`stored_alert_time_to_mt` 原样。

### 其他固定 +3 位置评估（未改）

- `services/exec_comp/source.py:180` `_LOCATE_MARGIN = 3h`：**不受 DST 影响**——它是按 Timestamp 定位成交号的安全边距而非时区偏移，真正换算走 `calc.srv_to_utc_calendar`（已用 `MT_SERVER_TZ`），且逐笔按自身 `TimeMsc` 过滤。
- `api/v1/routes/honeypot.py:62` `_MT_TZ = UTC+3`：**受影响但仅是展示**——告警邮件里「Time (MT UTC+3)」冬令会比真 MT 快 1 小时，不影响任何判定；要改就换 `MT_SERVER_TZ` 并把标签改成「MT」，低优先级。
- `services/rule_rebate_arb_service.py:196` `_mt_now()` 固定 +3：**受影响但已退役**（`REBATE_ARB_SCAN_ENABLED=false`）——冬令时交易日 key 会早 1 小时翻日；若将来复活必须改成 `MT_SERVER_TZ`。

### Follow-ups

1. 部署（回滚标签 `pre-gap-dst-<date>`，deadline 10-31）+ 11-02 早上人工核对（注意：冬令首扫在 **HKT 08:20**，08:20 前 scan_history 没有当日行是正常的）。
2. 冬令时 CRM digest 邮件 / 分析师「早上看 gap」的时间点推迟 1 小时（HKT 08:20），需要告知 risk / CS。
3. 页面按固定 UTC+3 显示的 1h 偏差（存储约定）另开单，本单未碰。
4. `honeypot.py` 展示时区可顺手换 `MT_SERVER_TZ`（见上）。
5. 考虑在 `requirements.txt` 钉 `apscheduler<4`。
