---
id: OPT-0063
title: 交易 IP 盈利归因 —— /login-ips 第 5 个 tab（risk-only）：逐单下单 IP 落库 + 按「账户组 × 时段」找同 IP 多客户/多 IB 的人头账户集群
status: wip
priority: P1
area: mixed
effort: L
created: 2026-09-21
claimed: 2026-09-22
related: [[OPT-0062]] [[OPT-0020]]
---

> **v2（2026-09-22）**：独立冷审 15 条 finding（实测 9-18 工作日 journal + 从库）已全部吸收：MT5 开仓判定改为「全收 + 夜间按 `Order == PositionID` 判开/平」、补 MT5 挂单 `order placed`、量级修正 4 倍、P&L 改 08:30 预计算、结果表按 deal 建键、私有 IP 定义统一、回填改走 `backfill_login_ip.py`。冷审原文浓缩在 §冷审对照表。
> **本文件自洽，实施 worker 只读这一份即可。** 分析、口径验证与一个月回测都已做完（§背景），不要重做。
> ⚠ 按 tracker 规则这是 net-new feature，本应走 `feat/` 分支；用户 2026-09-21 明确要求按 OPT 管理（同 OPT-0062 先例），照办。
> ⚠ **Phase 1（落库）越早上线越好**：逐单 IP 只在 MT journal 里，本机 `backend/data/login_ip/tmp/` 只留 7 天、MT5 FTP 只留 5 天，**功能上线日 = 数据起点，历史补不回来**。Phase 1 可以先于 Phase 2/3 单独部署。

## 问题

老板需求（2026-09-21 转述）：

> 新增一个分类，按照客户的交易 IP 做统计。每个盈利都统计到当时下单的 IP，如果有特定 IP 盈利特别高，再看看是否来自不同客户和 IB，是否故意在用人头账户用同一个方法刷单。

用户决定：放在 `/login-ips` 页面做第 5 个 tab；页面归 `cs` 模块不变，**这个 tab 只有 `risk` 模块的人能看**。

## 背景（已做的分析，勿重做）

### 1. 现有系统里有什么、没什么

| 有 | 位置 | 说明 |
|---|---|---|
| 每日全量 journal 流式扫描 | `backend/app/core/login_ip_scheduler.py:174` `_download_job`（05:10 HKT）→ `backend/app/services/login_ip_analyzer_service.py:126` `_parse_one_log` | 三台服务器（MT4 / MT4_Live2 / MT5）每天已经逐行扫一遍，边际成本≈0 |
| 「当日最后平仓 IP」提取器 | `login_ip_analyzer_service.py:97` `_is_mt4_close` / `:104` `_is_mt5_close`、循环体 `:162-215`、落库 `login_ip_db.py:523` `upsert_last_trade_ips`、表 `login_ip_db.py:170` `last_trade_ip` | **本 OPT 的开仓提取器照这个模式再加一个**；demo/manager 过滤、IPv4 门槛、`no money` 排除都现成 |
| 登录 IP 每日快照（04-08 起永不清理） | `backend/data/login_ip/YYYYMMDD/analysis_account_logins.json` / `analysis_ip_to_accounts.json` / `analysis_last_trade_ip.json`（07-13 起） | 第 3 tab 搜索 120 天靠它；本 OPT 的近似回测也靠它 |
| geo 缓存 | `login_ip_db.py:741` `get_cached_countries`（cache-only，不计费） | 展示国家用这个，别在列表页调 MaxMind |
| **没有**：逐单下单 IP | — | `fxbackoffice.mt4_trades` 不存 IP；只在 journal 第 4 列（MT5）/ 第 3 列（MT4） |

### 2. journal 开仓行格式（2026-09-18 / 09-20 实测采样，`backend/data/login_ip/tmp/`）

**MT4 / MT4_Live2**（UTF-8，`\t` 分列：`[0]` 短码 `[1]` 时间 `[2]` IP `[3]` `'LOGIN': msg`）。请求行与确认行成对，**确认行带 ticket**：

```
1	01:01:16.424	39.144.59.59	'8520962': order #23106530, buy 0.60 XAUUSD at 4344.62000
1	01:03:31.314	49.93.23.191	'8510072': order buy limit 0.11 XAUUSD at 4305.00000 sl: ... exp: never     ← 挂单请求
1	01:03:31.714	49.93.23.191	'8510072': order #23106533, buy limit 0.11 XAUUSD at 4305.00000            ← 挂单确认
```

取 **`order #N, <buy|sell>[ limit| stop] <lots> <SYMBOL> at <price>`** 这一形态：`N` = MT4 TICKET，直接对 `mt4_trades.ticketSid = '{sid}-{N}'`（sid 1 / 6）。挂单的 IP 是「挂单那一刻」的 IP，激活是服务端行为，仍归给挂单 IP（符合「当时下单的 IP」）。09-18 单日 MT4 verb 计数：`order` 14,089 / `close` 13,469 / `open` 7,831 / `market` 7,497 —— 冷审已核：`open` = `open order #N modified by API`（改单，IP 空），`market` = 无单号的请求行，都不与 `order #N,` 重复；另有 397 条 `order #N, buy ... is opened at`（API 账号 100001333，IP 空）自然被 IPv4 门槛挡掉。⚠ **MT4 部分平仓会生成新 ticket**（9-18 平仓行 2%，`COMMENT = 'from #原单号'`，可链式、可带 `[Expiration]` 后缀）——对账时沿 `from #` 链回溯到原 ticket 的 IP，否则剩余单落无 IP 桶。

**MT5**（UTF-16-LE，`[3]` 时间 `[4]` IP `[5]` `'LOGIN': msg`）：

```
HR	0	6	00:05:09.218	58.10.224.247	'67043240': order performed buy 0.02 at 81169.06 [#40659198 buy 0.02 BTCUSD at market], time 324.37 ms
JQ	0	6	00:05:09.218	58.10.224.247	'67043240': deal performed [#36371477 buy 0.02 BTCUSD at 81169.06]
CL	0	6	00:08:23.965	58.10.224.247	'67043240': market buy 0.07 BTCUSD (81106.36 / 81121.36)        ← 请求行，无单号
JO	0	6	00:08:50.786		'60002140': market sell 0.01 BTCUSD (81100.80 / 81115.80)          ← IP 列为空 = 非终端来源
```

🔴 **`order performed` 行本身分不出开仓还是平仓**（冷审实证：9-18 全天 137,376 条 `order performed` 里 **0 条**含 `, close #`——那个标记只在请求行 `market buy 0.02 XAUUSD.cent, close #40498093` 上；平仓的确认行 `order performed buy 0.02 at 4344.31 [#40498822 buy 0.02 XAUUSD.cent at market]` 与开仓长得一样，`#40498822` 是**平仓单号**不是 PositionID）。所以 journal 侧规则是：

- 收**所有**带 IPv4 的 `order performed ... [#N <buy|sell> <lots> <SYMBOL> at market]`，落 `order_ip.event_kind = 'performed'`；**排除** `[#N close by M ...]`（close-by 平仓，日均 1,212 条，带 IP）。
- 收 **`order placed [#N buy limit|buy stop|sell limit|sell stop <lots> <SYMBOL> at <price>]`**（挂单下达，带 IP，日均 ≈7,700 条），`event_kind = 'placed'`。⚠ 挂单**激活**时的 `order performed` IP 列为空——不加 `placed` 形态，所有挂单来源的仓位全落无 IP 桶，而 EA 刷单最常用挂单。
- **开/平判定放到夜间对账**：`mt5_live.mt5_orders_history`（PK `Order`）里 `Order == PositionID` ⇒ 这条是开仓单（9-18 当日 70,430 条 `Entry=0` deal **全部**满足 `Order = PositionID`；抽样 12 个 journal 单号里 2 个 `Order ≠ PositionID` 的都是 `Entry=1` 平仓单）。平仓单的 IP **保留**为第二信号（`close_ip`），不丢。
- 盈亏走 `mt5_live.mt5_deals WHERE (Login, PositionID) IN (...) AND Entry IN (1,2,3)`（索引 `IDX_POSITION`），`Profit + Storage + Commission`，`Volume / 10000` 手数，🔴 CEN 组的 `Profit` 也是 cents，币种查 `mt4_users` `loginSid = '5-{Login}'`。🔴 **MT5 单绝不能拿 `#N` 去查 `mt4_trades` sid=5**（TICKET 是另一套编号，会 100% 查无此单）。
- 顺带存 `mt5_deals.Reason`（0=客户端 / 1=EA / 2=dealer / 16=手机；9-18 开仓 deal 分布 45,344 / 18,916 / 3,179 / 2,983），无 IP 桶按它拆原因。

**量级（9-18 工作日实测，⚠ v1 用的是周日样本，低估 4 倍）**：MT5 带 IP 的 `order performed` **100,443** 条 + 空 IP 16,669 条（14%，集中在桥接 / `KCM\5LS_*` 组——**系统性盲区**，这类组里的团伙看不见）+ `order placed` ≈7,700；MT4 家族 `order #N,` 带 IP 9,604 条。去掉平仓后 ≈ **5.5–6 万行 / 交易日**。

**共同规则**（沿用 `_parse_one_log`）：IP 列必须是 IPv4（空 / `StopOut.All` / `DealerLogic 776` 直接跳过 —— 约两成下单行没有客户 IP，`KCM\5LS_*` 组一半客户拿不到，**这是数据性质不是 bug**，报表里单列「无 IP」桶）；账号 < 5 位跳过；MT5 `3` 开头 / MT4 `7` 开头 demo 跳过；`no money` / `invalid` 被拒行不算。

### 3. 一个月近似回测（2026-09-21，`backend/scripts/ip_profit_backtest.py`，已发 kieran）

用「当天登录 IP（优先当天最后平仓 IP）」近似下单 IP，平仓日 08-21 → 09-20：

| 项 | 数字 |
|---|---|
| 已平仓单 | 1,281,161（98.9% 能归到某个 IP） |
| 出现的 IP | 16,031；**中位数只活跃 1 天**（手机流量天天换 IP） |
| 盈利前 100 的 IP | 42 个只活跃 1 天（占前 100 盈利 49%）；**88 个是单客户单账户** |
| 多客户 IP（≥2 客户，<10 客户） | 741 个，合计 **−615k USD**（绝大多数是家人朋友一起亏） |
| ≥10 客户的 IP | 18 个，全是尼日利亚 MTN 运营商出口（`102.91.x`），一天 25 个互不相干客户 |

**决定性发现**：同一批客户反复出现在不同 IP 上。IB「Cheng Qian」名下 5 个 CN 客户（162939 / 164487 / 165032 / 165695 / 166069）一个月共用 **20 个 IP**，合计 +38,364 USD / 959 单，持仓形态一致（2–5 天）—— **正是老板描述的形态，但任何一个 IP 单看都进不了前 20**。另外自动抓到了 9-17「被黑账户」案（45.32.124.94 新加坡 VPS，客户 148784 + 155079，两个 IB）。

### 4. 由此定下的口径（用户 2026-09-21 认可）

1. **事实按「IP × 日」记，排名按「账户组 × 时段」算。** 按日排榜首是当天运气最好的散户；按时段但以 IP 为单位排会把一个团伙切成 20 份。
2. **共享出口 / 私有 IP**（v2 统一为按**窗口**判）：窗口内该 IP 上的 distinct CRM 客户数 ≥ `public_ip_clients`（默认 10）= 共享出口，不参与连边；其余为私有 IP。**按客户数不按账户数** —— 1 客户 17 账户同 IP（182.46.13.58）是发现不是 NAT。
3. **账户组** = 窗口内通过私有 IP 相连的账户的连通分量（union-find），**连边要求共用 ≥ 2 个 IP 日或 ≥ 2 个不同私有 IP**（✅ 用户 2026-09-22 拍板采用此默认值；单次共现连边在 90 天窗口会把换过租客的家庭宽带串成一组）。IP 降级为证据列（「共用 N 个 IP」+ 桥接 IP）。
4. **盈亏归开仓日的 IP**（老板要「下单时」的 IP），窗口按 **平仓日** 切（盈亏在平仓日实现）。
5. 组的指标：盈利、单数、手数、账户数、客户数、直属 IB 数、共用 IP 数、活跃日、赚钱日 / 活跃日、主品种占比、平均持仓、按日盈利序列。
6. 「一人多户」（1 客户 ≥ 2 账户同 IP）单独一类，开关默认关。

### 5. 权限：页面 cs、tab risk —— 机制现成

- 后端闸 `backend/app/core/auth_deps.py:249` `("login-ip",): "cs"`；匹配是**最长元组胜出**，加一行 `("login-ip", "trade-profit"): "risk"` 即可（同 `("ib-data", "query")` 那三处 carve-out）。闸挂在 API 层，cs 同事手输 URL 也是 403。
- 前端 `frontend/src/lib/modules.ts:101` `PAGE_POLICIES["/login-ips"] = "cs"` **不动**；`LoginIPs.tsx` 里第 5 个 `TabsTrigger`/`TabsContent` 用 `hasModule(access, "risk")`（`modules.ts:176`，access 来自 `providers/auth-provider.tsx:254` `useAuth`）条件渲染；manager 恒可见（`canAccess` 已处理）。`?tab=` 深链对无 risk 者回落 tab 1。
- 数据范围：新路由归 risk 模块 ⇒ 不在 cs 路由集合 ⇒ **不进 `data_scope.py:816` `ROUTE_SCOPE`**，`tests/test_data_scope.py:215` 与孤儿测试都不用动（往 ROUTE_SCOPE 加了反而会红）。受限名单两位是 cs-only，碰不到。
- 活库现状（2026-09-21）：持 risk 的 3 人 + 4 位 manager（其中 1 位模块为 `[]` 但 manager 恒过）都同时有 cs，**没人被锁在外**；6 位 cs-only 看到页面看不到 tab。将来出现「只有 risk 没有 cs」的人 → 给他勾 cs，或给 `PAGE_POLICIES` 补 any-of（后端已支持、前端没有，约半天）。
- 护栏：`tests/test_app_assembly.py` 两条 MODULE_MAP anti-drift（每 key ≥1 活路由、每路由唯一最长匹配），新 key 与新路由同一 commit 加。

## 方案

### Phase 1 — 逐单开仓 IP 落库（backend，**先部署**）

1. 新库文件 `backend/data/login_ip_orders.db`，新模块 `backend/app/core/login_ip_orders_db.py`（照 `login_ip_db.py` 的 `get_connection` / `_SCHEMA` / `init_*` 模式；`order_ip`、`order_ip_parse_runs` 与 Phase 2 的 `trade_ip_pnl` 都放这里，与六个 tab 共用的 `login_ip.db` 隔离）。`order_ip` 表：
   ```sql
   CREATE TABLE IF NOT EXISTS order_ip (
     id            INTEGER PRIMARY KEY AUTOINCREMENT,
     trade_date    TEXT NOT NULL,          -- YYYYMMDD (MT day of the journal file)
     server_name   TEXT NOT NULL,          -- MT4 | MT5 | MT4_Live2
     account_id    INTEGER NOT NULL,
     order_ref     INTEGER NOT NULL,       -- MT4 TICKET / MT5 Order ticket (open OR close order; resolved nightly)
     ip_address    TEXT NOT NULL,
     event_time_mt TEXT NOT NULL,          -- HH:MM:SS.mmm
     event_kind    TEXT NOT NULL,          -- MT4: 'order' | MT5: 'performed' | 'placed'
     cmd           TEXT NOT NULL,          -- buy | sell | buy limit | sell stop ...
     lots          REAL,
     symbol        TEXT,
     UNIQUE (server_name, order_ref)       -- one row per order ticket; re-run of the same day overwrites
   );
   CREATE INDEX IF NOT EXISTS idx_order_ip_date ON order_ip(trade_date);
   -- NO ip_address index here: this table is only ever joined by (server_name, order_ref);
   -- the IP index lives on trade_ip_pnl (Phase 2), which is what the rankings read.
   ```
   另加一张解析审计表 `order_ip_parse_runs(trade_date, server_name, lines_scanned, rows_written, parsed_at)`，让 `coverage` 能列出「哪天哪台服务器日志不完整」——`_download_job` 当天会告警，但几周后查窗口时那一天只会表现为「盈利偏低」，没人看得出来。
   ⚠ `_SCHEMA` 里的索引只能引用建表时就有的列（CLAUDE.md `users_db._SCHEMA` 那条同样适用于本库）。
2. `_parse_one_log` 的非 login 分支里，在 `is_close(...)` 之前加 `_match_order_event(server, msg)`，按 §背景 2 的 v2 规则匹配（MT4：`order #N, <buy|sell>[ limit| stop] ...`；MT5：`order performed ... [#N ... at market]` **排除** `close by`，以及 `order placed [#N ...]`），解析 ticket / event_kind / cmd / lots / symbol，收进 `order_events: list[dict]`（⚠ MT5 这里**不判开平**，夜间对账才判）；返回值多一项（**改签名要同步 `:289` 的 return、调用方 `:456` 附近、`scripts/backfill_login_ip.py:129`，以及单测**）。
3. `upsert_order_ips()`（`INSERT OR REPLACE`，参考 `login_ip_db.py:523`）+ `record_parse_run()`；`cleanup_old_order_ip(days=DEFAULT_ORDER_IP_RETENTION_DAYS)` 挂进 `_report_job` 的 `_daily_housekeeping`（`login_ip_scheduler.py:339`），**保留期 ✅ 用户 2026-09-22 拍板：`order_ip` 原始表 120 天、`trade_ip_pnl` 对账结果表 400 天**（排名只读后者；原始证据 120 天后清除）。⚠ 量级按 9-18 工作日实测 ≈ **5.5–6 万行 / 交易日**（v1 写的 350 万行 / 300 MB 是周日样本，错了 4 倍）——这就是单独库文件的原因（VACUUM / 备份 / WAL 增长不牵连主库）。保留期写进 `docs/features/login-ip.md` §5.1 表。
4. 日 JSON 同步落一份 `analysis_order_ip.json`（与 `analysis_last_trade_ip.json` 同款，方便脚本/回放）。
5. 回填：上线当天用 `backend/data/login_ip/tmp/` 里还在的 7 天 log 回填，🔴 **必须走 `backend/scripts/backfill_login_ip.py`（直接调 `analyze_date`，不推 CRM），不能用 `/login-ip/scheduler/run-now`**——`_download_job` 会顺带跑 `push_last_close_ips_to_crm(target_date)`，diff 基准是推送日志里的 `MAX(trade_date)`，回填老日期会把 CRM 里较新的「最后平仓 IP」覆盖成旧值。`analyze_date` 返回值/签名一改，三个调用方（`login_ip_scheduler.py:214`、`backfill_login_ip.py:129`、单测）同步。

### Phase 2 — 归因 + 分组服务与 API（backend）

`backend/app/services/login_ip_trade_profit_service.py`（新）：

1. **盈亏对账 = 每天 08:30 report job 的固定一步，结果落 SQLite `trade_ip_pnl`；API 只读 SQLite，不碰从库**（冷审实测：`mt5_deals` 一个 MT 日按 `Timestamp` 切 0.55 s / 69,207 行，`mt4_trades` 一个 closeDate 0.59 s；「首次请求现算」冷启动 30 天 ≈ 35 s、90 天 ≈ 100 s 从库时间，每次改阈值都是 cache miss，达不到 P95 < 2 s）。Redis 只缓存分组结果（键 = 全部阈值参数），`scope_cache_suffix()` 对 risk 路由无意义，不用带：
   - MT4 家族：`mt4_trades WHERE closeDate = :day AND sid IN (1,6) AND CMD IN (0,1)` → `ticketSid` join `order_ip`（`server_name`+`order_ref`；`COMMENT LIKE 'from #%'` 先沿链回溯到原 ticket）。
   - MT5：`mt5_deals WHERE Entry IN (1,2,3) AND Timestamp 落在 :day`（🔴 用 `Timestamp`（FILETIME，有索引）切日别用 `Time`，OPT-0062 实测 19.3s vs 0.25s）→ `order_ip WHERE server_name='MT5' AND order_ref = PositionID`（开仓单 `Order == PositionID`，所以直接拿 PositionID 当 `order_ref` 查即可命中开仓行；同一 PositionID 的平仓单号另查得 `close_ip`）。
   - 结果表 `trade_ip_pnl` **按平仓 deal 建键**：`(server, deal_ref)`（MT5 `Deal`，MT4 `ticketSid`）+ `close_date, account_id, position_ref, open_ip, close_ip, user_id, ib_id, symbol, lots, profit_usd, hold_sec, open_date, reason, no_ip_cause`。🔴 不能按 PositionID 建键——MT5 一个仓位常分多笔、跨多天平掉（9-18：69,207 笔平仓 deal 对 68,709 个仓位）。索引 `(close_date)`、`(open_ip)`、`(user_id)`。CEN 用 `mt4_users.CURRENCY` 折算，剔 demo/test/员工（口径同 `ip_profit_backtest.py` `DAY_SQL`）。`user_id` 来自 `mt4_users.userId`，`ib_id` 来自 `ib_tree WHERE level = 1`（已验 62,200/62,200 一客户一行）。MT4 `COMMENT LIKE 'from #%'` 沿链回溯原 ticket 取 IP。
   - 未匹配到开仓 IP 的单 `open_ip = NULL` 且 `no_ip_cause ∈ {server_initiated, bridge_group, pre_golive, partial_remainder, journal_incomplete}`（= 无 IP 桶，**不丢**，按原因可拆）。长持仓很少（9-18 平仓里开仓 > 7 天的 MT5 40 / 69,207、MT4 39 / 9,560），上线前开的仓两周左右自然清掉。
2. **分组**：给定窗口 `[from, to]`（平仓日）：
   - **共享出口**按窗口判：窗口内该 IP 的 distinct `user_id` ≥ `public_ip_clients`（默认 10）的 IP 不参与连边（抓运营商 NAT / VPN）。
   - **连边条件（✅ 用户 2026-09-22 拍板）**：两个账户之间要成边，必须在窗口内共用 **≥ 2 个 IP 日**或 **≥ 2 个不同私有 IP**——只靠一次共现连边，90 天窗口里一条家庭宽带三个月换过三户人就会把三个无关家庭串成一组（union-find 的传递性放大误合并）。每次合并记下**桥接 IP**，详情里可见。
   - union-find 出组；组指标见 §背景 4.5。一人多户与跨客户用 `clients ≥ 2` 区分。
3. **API**（`routes/login_ip.py` 同文件或新 `routes/login_ip_trade_profit.py`，前缀必须是 `/login-ip/trade-profit/...`，`def` 不要 `async def`）：
   - `GET /login-ip/trade-profit/groups?from&to&min_clients=2&public_ip_clients=10&include_same_client=false&page&page_size` → 标准分页响应（`data/total/page/page_size/total_pages/statistics`）。
   - `GET /login-ip/trade-profit/groups/{group_id}?from&to&min_clients&public_ip_clients&include_same_client` → 账户明细 + IP 列表（每 IP：国家（cache-only geo）、窗口内客户数、活跃日、是否桥接 IP）+ 按日盈利序列。⚠ `group_id` 是窗口 + 参数 + 账户列表的哈希，**详情必须带同一套参数才能还原**；将来做告警需要稳定键时另起（不在本 OPT）。
   - `GET /login-ip/trade-profit/ips?from&to` → **按 IP 的榜单保留**（组视图会把「哪台机器」藏掉；45.32.124.94 那种 VPS 是 IP 级发现）。
   - `GET /login-ip/trade-profit/coverage?from&to` → 已平仓单数 / 有 IP 单数 / 无 IP 单数（按 `no_ip_cause` 拆）与盈亏 + **日志不完整的 (日期, 服务器) 列表**（来自 `order_ip_parse_runs`）。
   - 查询型 GET 不记审计（口诀：人做的 + 改了状态 + 做成了）。
   - `MODULE_MAP` 加 `("login-ip", "trade-profit"): "risk"`。
4. `group_id` = sha1(窗口 + 全部阈值参数 + 排序后账户列表) 前 12 位（无状态；同窗口同参数下稳定）。

### Phase 3 — 前端第 5 tab（frontend）

`frontend/src/pages/login-ip/TradeProfitTab.tsx`（新），挂进 `LoginIPs.tsx`（`:36-60` 那组 Tabs），仅 `hasModule(access, "risk")` 渲染：

1. **工具栏**（`useFilterPersist`，key `LOGIN_IP_TRADE_PROFIT_FILTERS_V1`，并手列进 `view-profiles/manifest.ts` 的 `FILTER_STATE_KEYS`）：窗口预设 7 / 30 / 90 天 + 自定义（自定义绝对区间不持久化）、最少客户数（默认 2）、排除共享出口（默认开，阈值 10）、含一人多户（默认关）。
2. **顶部一行覆盖率说明**（非持久化）：`本窗口 N 单，M 单无 IP（P%），盈亏 X` —— 让读者知道无 IP 桶有多大。
3. **主表 AG-Grid（一组一行）**：组盈利（红绿）、客户数、IB 数、账户数、共用 IP 数、活跃日、赚钱日/活跃日、主品种、平均持仓、按日盈利 sparkline（`agSparklineCellRenderer` 是 enterprise，社区版用一个 40×16 的内联 SVG cell renderer）。`useGridColumnPersist` key `LOGIN_IP_TRADE_PROFIT_GRID_STATE_V1` + `<ColumnVisibilityMenu>`；计算列显式 `colId`；列头解释用 `InfoHeader`；zebra 别用 `hsl(var(--primary))`。
4. **展开区**：点行后在表下方渲染 Card（不用 master/detail，社区版没有）：左侧账户明细（账户、客户 CRM 链接 `https://mt4.kohleglobal.com/crm/users/{uid}`、国家、直属 IB、单数、手数、盈亏、主品种、平均持仓），右侧该组用过的 IP（国家、窗口内客户数、活跃日、桥接标记），**点 IP 切到 Search tab 并预填该 IP**。⚠ 深链底座**现在不存在**：`LoginIPs.tsx` 是非受控 `<Tabs defaultValue="report">`、`grid-cols-4 max-w-2xl` 写死；`SearchTab` 不收 props、不读 URL。要照 RiskMonitor / WindowScan 的 `useSearchParams` 模式把 tab 改受控（`?tab=` 无权限时回落 `report`），`SearchTab` 接受初始搜索词（`?tab=search&q=<ip>`），`grid-cols` 按可见 tab 数算；若 tab 持久化则 `LOGIN_IP_ACTIVE_TAB_V1` 手列进 `UI_STATE_KEYS`。约 +0.5 天。
5. i18n：`frontend/src/i18n/locales/{en-US,zh-CN}.ts` 的 `loginIpsPage.tabs.tradeProfit` 及各列名；`locales.test.ts` 会比对两边 key。
6. 空状态：过滤后无集群时显示「本窗口无满足条件的多客户集群」+ 当前阈值，不是空白表。

### 明确不做（本 OPT 范围外）

- 不做告警/邮件（集群定义和阈值至少要老板看两周再定；将来可作为 risk-monitor 新 band 或 alert-mail 新 source）。
- 不做 IP 类型（机房/住宅）识别 —— geo 缓存只有国家；先靠客户数阈值。
- 不做前端 `PAGE_POLICIES` any-of；risk-only 用户出现时再做。
- 不回填 04-08 以来的登录 IP 近似数据进正式表（口径不同，混在一起会误导）；历史问题继续用 `ip_profit_backtest.py` 离线回答。
- **已知盲区（写进 tab 的说明文案）**：① 天天换手机 IP、从不共用的团伙抓不到（Cheng Qian 组被抓是因为他们确实共用了）；② 桥接 / `KCM\5LS_*` 组约 14% 下单行没有 IP；③ 只有 IPv4，MT journal 没有 IPv6。

## 假设 / 待验证（实施前）

- [x] ~~MT4 `open` / `market` verb 是否重复~~ 冷审已核：不重复（改单 / 无单号请求行）。
- [x] ~~MT5 `order performed` 能否区分开平~~ 冷审已核：**不能**，改为夜间按 `mt5_orders_history.Order == PositionID` 判定（见 §背景 2）。
- [x] ~~`mt5_deals` 单日切日耗时~~ 实测 0.55 s / 69,207 行（`IDX_mt5_deals_Stamp`）。
- [ ] `UNIQUE(server_name, order_ref)` 在 400 天内是否安全：MT4 与 Live2 各自 ticket 空间、`server_name` 区分，单日核过，长期未核——上线后第一周看 `INSERT OR REPLACE` 是否出现跨日覆盖。
- [ ] 连边条件（≥ 2 IP 日 / ≥ 2 私有 IP）在 08-21 → 09-20 近似数据上是否仍能把 Cheng Qian 组和 45.32.124.94 组各聚成一组、且不把它们与无关账户串起来（这是回归样本）。
- [ ] union-find 在 90 天窗口的规模：预计 ≤ 5,000 账户 / ≤ 2 万条边，纯 Python 毫秒级（输入已是 SQLite 预计算结果，不再有从库成本）。

## 验收标准

**Phase 1**
- [ ] 05:10 job 跑完后 `order_ip` 有当日三台服务器的行；抽 1 个**工作日**：MT4 家族按 `ticketSid` 对 `mt4_trades`；MT5 先按 `mt5_orders_history` 判开/平，开仓单再对 `mt5_deals Entry=0` `(Login, PositionID)`，**开仓判定精确率 ≥ 99%**；召回缺口全部能在原始 log 里证明是「IP 列非 IPv4」或「挂单激活行」（同 §3.4.1 的复核方法，脚本可仿 `backend/scripts/verify_last_close_ip.py`）。
- [ ] `order_ip_parse_runs` 有当日三行；人为截断一份 log 重跑，`coverage` 能列出该 (日期, 服务器)。
- [ ] 用 `backfill_login_ip.py` 回填后 `crm_push_log` **没有**新增该日期的推送行。
- [ ] 0 条 demo 前缀 / <5 位账号 / 空 IP 行。
- [ ] `_daily_housekeeping` 表里多一行 `order_ip` 400 天；`docs/features/login-ip.md` 新增 §3.6 写清行格式、口径、保留期。
- [ ] 重跑同一天不产生重复行（UNIQUE 生效）。

**Phase 2**
- [ ] `GET /login-ip/trade-profit/groups` 90 天窗口 P95 < 2 s（只读 SQLite + union-find；缓存命中 < 200 ms）；08:30 对账步骤单日 < 30 s。
- [ ] MT5 分多笔平掉的仓位，各笔 deal 各自一行、盈亏合计等于 `mt5_deals` 合计（不重复、不丢）。
- [ ] 对 08-21 → 09-20 用近似数据集跑同一分组算法，能把 Cheng Qian 5 客户组和 45.32.124.94 组各聚成一组（算法正确性的回归样本；不入正式表）。
- [ ] cs-only 用户调该接口 403（不是 401）；`AUTH_ENABLED=false` 时恒过；`test_app_assembly` 两条 anti-drift 绿；`test_data_scope` 全绿且 ROUTE_SCOPE 未改。
- [ ] 无 IP 桶在 coverage 里可见，且 groups 的合计 + 无 IP 桶 = 窗口内全部已平仓单。

**Phase 3**
- [ ] 6 位 cs-only 用户看不到 tab，risk 用户与 manager 看得到；`?tab=` 深链无权限时回落 tab 1。
- [ ] 过滤器与列状态刷新后保留；`manifest.ts` anti-drift 测试绿。
- [ ] 点 IP 能跳到 Search tab 并出结果。
- [ ] `./verify.sh` 绿（tsc / vitest / pytest）。

## 笔记

- 为什么不用 `mt4_trades` sid=5 而绕去 `mt5_deals`：镜像表 TICKET ≠ PositionID（`docs/features/login-ip.md` §3.4.1 实测），且已平仓行 CMD 反转；本 OPT 只需盈亏与手数，不需要方向，所以反转不影响，但 join 键必须走 `mt5_deals`。
- 为什么组而不是 IP：§背景 3。为什么按客户数判共享出口：§背景 4.2。
- 近似回测脚本与正式表口径不同（登录 IP vs 下单 IP），正式上线后回测脚本退役为「历史问题专用」，脚本头注释已写明。
- 工期估计（v2）：Phase 1 1.5 天（两种 MT5 形态 + 解析审计表 + 回填脚本改造 + 对账脚本）、Phase 2 2 天（预计算 + `from #` 链 + 分组 + 4 个端点）、Phase 3 2 天（含 tab 受控化 / 深链 0.5 天）、文档与冷审 0.5 天，≈ 6 天。Phase 1 单独先上线，回滚镜像标签 `pre-order-ip-<日期>`。

## 冷审对照表（2026-09-22，独立 agent，实测 9-18 journal + 从库）

| # | Finding | 处理 |
|---|---|---|
| 1 | MT5 `order performed` 不含 `, close #`（0/137,376），开平不可分 | ✅ 改：全收 + 夜间 `Order == PositionID` 判定，平仓 IP 留作 `close_ip` |
| 2 | MT5 挂单 `order placed` 带 IP、激活行无 IP，v1 未覆盖（≈7,700/天） | ✅ 改：加 `placed` 形态 |
| 3 | `close by` 带 IP 无 `close #` 标记（1,212/天） | ✅ 改：排除 |
| 4 | 量级按周日采样低估 4 倍（实际 5.5–6 万行/交易日） | ✅ 改：重估 + 独立库文件 + 保留期待拍板 |
| 5 | 按需现算达不到 P95 < 2 s（30 天冷启动 ≈35 s） | ✅ 改：08:30 预计算落 SQLite |
| 6 | 结果表按 PositionID 建键在 MT5 分笔平仓下会错 | ✅ 改：按 deal 建键 |
| 7 | 私有 IP 两处定义矛盾；单次共现连边在 90 天窗口会串无关家庭 | ✅ 改：共享出口按窗口判 + 连边 ≥ 2 IP 日 / ≥ 2 私有 IP（**默认值待拍板**） |
| 8 | MT4 部分平仓剩余单（`from #`，2%）落无 IP | ✅ 改：沿链回溯 |
| 9 | 无 IP 桶是系统性的（桥接组 14%）；`mt5_deals.Reason` 免费 | ✅ 改：存 `reason` + 按原因拆桶 + 盲区写进文案 |
| 10 | `run-now` 回填会误推 CRM 覆盖新值 | ✅ 改：回填只走 `backfill_login_ip.py` |
| 11 | 日志不完整几周后静默成「盈利低」 | ✅ 改：`order_ip_parse_runs` + coverage 列出 |
| 12 | `group_id` 详情需同一套参数 | ✅ 改：详情路由带参 |
| 13 | tab 非受控、SearchTab 无入参，深链底座不存在 | ✅ 改：Phase 3 +0.5 天 |
| 14 | `scope_cache_suffix()` 对 risk 路由无意义 | ✅ 改：缓存键改为全部阈值参数 |
| 15 | 保留按 IP 的榜单 | ✅ 改：加 `/ips` 端点 |
| ⚪ | 权限段全部核实（最长元组胜出 / ROUTE_SCOPE 不动 / search & export 不泄漏）；manager 4 人（rebecca `[]` 但 manager 绕过） | 已更正 §背景 5 |
| ⚪ | 天天换手机 IP 从不共用的团伙抓不到 | 接受，写进盲区 |

## 结果

<done 时填>
