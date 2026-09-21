---
id: OPT-0063
title: 交易 IP 盈利归因 —— /login-ips 第 5 个 tab（risk-only）：逐单下单 IP 落库 + 按「账户组 × 时段」找同 IP 多客户/多 IB 的人头账户集群
status: ready
priority: P1
area: mixed
effort: L
created: 2026-09-21
related: [[OPT-0062]] [[OPT-0020]]
---

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

取 **`order #N, <buy|sell>[ limit| stop] <lots> <SYMBOL> at <price>`** 这一形态：`N` = MT4 TICKET，直接对 `mt4_trades.ticketSid = '{sid}-{N}'`（sid 1 / 6）。挂单的 IP 是「挂单那一刻」的 IP，激活是服务端行为，仍归给挂单 IP（符合「当时下单的 IP」）。09-18 单日 MT4 verb 计数：`order` 14,089 / `close` 13,469 / `open` 7,831 / `market` 7,497 —— ⚠ `open` / `market` 两个 verb 未采样，**实施前先 grep 看它们是不是同一笔单的另一条行**（若是，去重按 ticket）。

**MT5**（UTF-16-LE，`[3]` 时间 `[4]` IP `[5]` `'LOGIN': msg`）：

```
HR	0	6	00:05:09.218	58.10.224.247	'67043240': order performed buy 0.02 at 81169.06 [#40659198 buy 0.02 BTCUSD at market], time 324.37 ms
JQ	0	6	00:05:09.218	58.10.224.247	'67043240': deal performed [#36371477 buy 0.02 BTCUSD at 81169.06]
CL	0	6	00:08:23.965	58.10.224.247	'67043240': market buy 0.07 BTCUSD (81106.36 / 81121.36)        ← 请求行，无单号
JO	0	6	00:08:50.786		'60002140': market sell 0.01 BTCUSD (81100.80 / 81115.80)          ← IP 列为空 = 非终端来源
```

取 **`order performed ... [#N ...]`** 且消息**不含** `, close #`：`N` = MT5 Order ticket，开仓单 Order ticket **== PositionID**（`docs/features/login-ip.md` §3.4.1 已验证），盈亏走 `mt5_live.mt5_deals WHERE (Login, PositionID) IN (...) AND Entry IN (1,2,3)`（索引 `IDX_POSITION`），`Profit + Storage + Commission`，`Volume / 10000` 手数。🔴 **MT5 单绝不能拿 `#N` 去查 `mt4_trades` sid=5**（TICKET 是另一套编号，会 100% 查无此单）。09-20 单日 MT5 带客户 IP 的行：`order` 11,753 / `deal` 11,636 / `market` 11,637 / `invalid` 12,737 / `request` 12,128。

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
2. **私有 IP**：当天该 IP 上的 CRM 客户数 < `public_ip_clients`（默认 10）。**按客户数不按账户数** —— 1 客户 17 账户同 IP（182.46.13.58）是发现不是 NAT。
3. **账户组** = 时间窗内共用过任一私有 IP 的账户的连通分量（union-find）。IP 降级为证据列（「共用 N 个 IP」）。
4. **盈亏归开仓日的 IP**（老板要「下单时」的 IP），窗口按 **平仓日** 切（盈亏在平仓日实现）。
5. 组的指标：盈利、单数、手数、账户数、客户数、直属 IB 数、共用 IP 数、活跃日、赚钱日 / 活跃日、主品种占比、平均持仓、按日盈利序列。
6. 「一人多户」（1 客户 ≥ 2 账户同 IP）单独一类，开关默认关。

### 5. 权限：页面 cs、tab risk —— 机制现成

- 后端闸 `backend/app/core/auth_deps.py:249` `("login-ip",): "cs"`；匹配是**最长元组胜出**，加一行 `("login-ip", "trade-profit"): "risk"` 即可（同 `("ib-data", "query")` 那三处 carve-out）。闸挂在 API 层，cs 同事手输 URL 也是 403。
- 前端 `frontend/src/lib/modules.ts:101` `PAGE_POLICIES["/login-ips"] = "cs"` **不动**；`LoginIPs.tsx` 里第 5 个 `TabsTrigger`/`TabsContent` 用 `hasModule(access, "risk")`（`modules.ts:176`，access 来自 `providers/auth-provider.tsx:254` `useAuth`）条件渲染；manager 恒可见（`canAccess` 已处理）。`?tab=` 深链对无 risk 者回落 tab 1。
- 数据范围：新路由归 risk 模块 ⇒ 不在 cs 路由集合 ⇒ **不进 `data_scope.py:816` `ROUTE_SCOPE`**，`tests/test_data_scope.py:215` 与孤儿测试都不用动（往 ROUTE_SCOPE 加了反而会红）。受限名单两位是 cs-only，碰不到。
- 活库现状（2026-09-21）：持 risk 的 3 人 + 3 位 manager 都同时有 cs，**没人被锁在外**；6 位 cs-only 看到页面看不到 tab。将来出现「只有 risk 没有 cs」的人 → 给他勾 cs，或给 `PAGE_POLICIES` 补 any-of（后端已支持、前端没有，约半天）。
- 护栏：`tests/test_app_assembly.py` 两条 MODULE_MAP anti-drift（每 key ≥1 活路由、每路由唯一最长匹配），新 key 与新路由同一 commit 加。

## 方案

### Phase 1 — 逐单开仓 IP 落库（backend，**先部署**）

1. `login_ip_db.py` 新表：
   ```sql
   CREATE TABLE IF NOT EXISTS order_ip (
     id            INTEGER PRIMARY KEY AUTOINCREMENT,
     trade_date    TEXT NOT NULL,          -- YYYYMMDD (MT day of the journal file)
     server_name   TEXT NOT NULL,          -- MT4 | MT5 | MT4_Live2
     account_id    INTEGER NOT NULL,
     order_ref     INTEGER NOT NULL,       -- MT4 TICKET / MT5 Order ticket (== PositionID)
     ip_address    TEXT NOT NULL,
     event_time_mt TEXT NOT NULL,          -- HH:MM:SS.mmm
     cmd           TEXT NOT NULL,          -- buy | sell | buy limit | sell stop ...
     lots          REAL,
     symbol        TEXT,
     UNIQUE (server_name, order_ref)       -- one row per order; re-run of the same day overwrites
   );
   CREATE INDEX IF NOT EXISTS idx_order_ip_date ON order_ip(trade_date);
   CREATE INDEX IF NOT EXISTS idx_order_ip_ip   ON order_ip(ip_address);
   ```
   ⚠ `_SCHEMA` 里的索引只能引用建表时就有的列（CLAUDE.md `users_db._SCHEMA` 那条同样适用于本库）。
2. `_parse_one_log` 的非 login 分支里，在 `is_close(...)` 之前加 `is_open(msg)`（MT4：`order #` 开头且逗号后是 `buy|sell`；MT5：`order performed` 且不含 `, close #`），解析 ticket / cmd / lots / symbol，收进 `open_orders: list[dict]`；返回值多一项（**改签名要同步 `:289` 的 return 与调用方 `:456` 附近**，以及单测）。
3. `upsert_order_ips()`（`INSERT OR REPLACE`，参考 `:523`）；`cleanup_old_order_ip(days=DEFAULT_ORDER_IP_RETENTION_DAYS)` 挂进 `_report_job` 的 `_daily_housekeeping`（`login_ip_scheduler.py:339`），**保留 400 天**（老板要长窗口；量级 ≈ 1.2–1.5 万行/交易日 × 250 ≈ 350 万行/年，SQLite 带索引无压力，`login_ip.db` 增长 ≈ 300 MB/年，写进 `docs/features/login-ip.md` §5.1 表）。
4. 日 JSON 同步落一份 `analysis_order_ip.json`（与 `analysis_last_trade_ip.json` 同款，方便脚本/回放）。
5. 回填：上线当天用 `backend/data/login_ip/tmp/` 里还在的 7 天 log 手动 `run-now` 回填（`/login-ip/scheduler/run-now` 支持 `target_date`）。

### Phase 2 — 归因 + 分组服务与 API（backend）

`backend/app/services/login_ip_trade_profit_service.py`（新）：

1. **盈亏对账**（按平仓日增量，每天 08:30 report job 顺带跑，或 API 首次请求按需算 + Redis 缓存 30 min，键带 `scope_cache_suffix()`）：
   - MT4 家族：`mt4_trades WHERE closeDate = :day AND sid IN (1,6) AND CMD IN (0,1)` → `ticketSid` join `order_ip`（`server_name`+`order_ref`）。
   - MT5：`mt5_deals WHERE Entry IN (1,2,3) AND Timestamp 落在 :day`（🔴 用 `Timestamp`（FILETIME，有索引）切日别用 `Time`，OPT-0062 实测 19.3s vs 0.25s）→ `(Login, PositionID)` join `order_ip`。
   - 结果表 `trade_ip_pnl(close_date, server, account_id, order_ref, ip_address, user_id, ib_id, symbol, lots, profit_usd, hold_sec, open_date)`，CEN 用 `mt4_users.CURRENCY` 折算，剔 demo/test/员工（口径同 `ip_profit_backtest.py` `DAY_SQL`）。`user_id` 来自 `mt4_users.userId`，`ib_id` 来自 `ib_tree WHERE level = 1`（已验 62,171/62,171 一客户一行）。
   - 未匹配到 `order_ip` 的单落 `ip_address = NULL`（= 无 IP 桶），**不丢**。
2. **分组**：给定窗口 `[from, to]`（平仓日）：私有 IP = 窗口内该 IP 的 distinct `user_id` < `public_ip_clients`；账户在私有 IP 上连边，union-find 出组；组指标见 §背景 4.5。一人多户与跨客户用 `clients ≥ 2` 区分。
3. **API**（`routes/login_ip.py` 同文件或新 `routes/login_ip_trade_profit.py`，前缀必须是 `/login-ip/trade-profit/...`，`def` 不要 `async def`）：
   - `GET /login-ip/trade-profit/groups?from&to&min_clients=2&public_ip_clients=10&include_same_client=false&page&page_size` → 标准分页响应（`data/total/page/page_size/total_pages/statistics`）。
   - `GET /login-ip/trade-profit/groups/{group_id}` → 账户明细 + IP 列表（每 IP：国家（cache-only geo）、窗口内客户数、活跃日）+ 按日盈利序列。
   - `GET /login-ip/trade-profit/coverage?from&to` → 已平仓单数 / 有 IP 单数 / 无 IP 单数与盈亏，给页面顶部说明「这段时间多少单没有 IP」。
   - 查询型 GET 不记审计（口诀：人做的 + 改了状态 + 做成了）。
   - `MODULE_MAP` 加 `("login-ip", "trade-profit"): "risk"`。
4. `group_id` 用窗口 + 排序后账户列表的 sha1 前 12 位（无状态，同窗口稳定）。

### Phase 3 — 前端第 5 tab（frontend）

`frontend/src/pages/login-ip/TradeProfitTab.tsx`（新），挂进 `LoginIPs.tsx`（`:36-60` 那组 Tabs），仅 `hasModule(access, "risk")` 渲染：

1. **工具栏**（`useFilterPersist`，key `LOGIN_IP_TRADE_PROFIT_FILTERS_V1`，并手列进 `view-profiles/manifest.ts` 的 `FILTER_STATE_KEYS`）：窗口预设 7 / 30 / 90 天 + 自定义（自定义绝对区间不持久化）、最少客户数（默认 2）、排除共享出口（默认开，阈值 10）、含一人多户（默认关）。
2. **顶部一行覆盖率说明**（非持久化）：`本窗口 N 单，M 单无 IP（P%），盈亏 X` —— 让读者知道无 IP 桶有多大。
3. **主表 AG-Grid（一组一行）**：组盈利（红绿）、客户数、IB 数、账户数、共用 IP 数、活跃日、赚钱日/活跃日、主品种、平均持仓、按日盈利 sparkline（`agSparklineCellRenderer` 是 enterprise，社区版用一个 40×16 的内联 SVG cell renderer）。`useGridColumnPersist` key `LOGIN_IP_TRADE_PROFIT_GRID_STATE_V1` + `<ColumnVisibilityMenu>`；计算列显式 `colId`；列头解释用 `InfoHeader`；zebra 别用 `hsl(var(--primary))`。
4. **展开区**：点行后在表下方渲染 Card（不用 master/detail，社区版没有）：左侧账户明细（账户、客户 CRM 链接 `https://mt4.kohleglobal.com/crm/users/{uid}`、国家、直属 IB、单数、手数、盈亏、主品种、平均持仓），右侧该组用过的 IP（国家、窗口内客户数、活跃日），**点 IP 切到 Search tab 并预填该 IP**（`SearchTab` 需暴露一个受控入口，或走 URL `?tab=search&q=<ip>`）。
5. i18n：`frontend/src/i18n/locales/{en-US,zh-CN}.ts` 的 `loginIpsPage.tabs.tradeProfit` 及各列名；`locales.test.ts` 会比对两边 key。
6. 空状态：过滤后无集群时显示「本窗口无满足条件的多客户集群」+ 当前阈值，不是空白表。

### 明确不做（本 OPT 范围外）

- 不做告警/邮件（集群定义和阈值至少要老板看两周再定；将来可作为 risk-monitor 新 band 或 alert-mail 新 source）。
- 不做 IP 类型（机房/住宅）识别 —— geo 缓存只有国家；先靠客户数阈值。
- 不做前端 `PAGE_POLICIES` any-of；risk-only 用户出现时再做。
- 不回填 04-08 以来的登录 IP 近似数据进正式表（口径不同，混在一起会误导）；历史问题继续用 `ip_profit_backtest.py` 离线回答。

## 假设 / 待验证（实施前）

- [ ] MT4 `open` / `market` 两个 verb 的行是否与 `order #N,` 是同一笔单（grep `backend/data/login_ip/tmp/` 最近一个工作日）；若是，去重按 ticket 取**最早**带 IP 的那条。
- [ ] MT5 部分平仓 / close-by 产生的新 position 是否会出现 `order performed` 且不含 `, close #`（会被当成开仓）；抽 1 天与 `mt5_deals Entry=0` 对账，精确率目标 ≥ 99%（§3.4.1 last-close 是 99.91%）。
- [ ] `mt5_deals` 单日 `Timestamp` 切日 + `Entry IN (1,2,3)` 全量查询耗时（预期 < 1 s，OPT-0062 已用同一索引）。
- [ ] union-find 在 90 天窗口的规模：预计 ≤ 5,000 账户 / ≤ 2 万条边，纯 Python 毫秒级；若 API 按需算超过 2 s 就改成 08:30 预计算落 SQLite。

## 验收标准

**Phase 1**
- [ ] 05:10 job 跑完后 `order_ip` 有当日三台服务器的行；抽 1 个工作日：MT4 家族按 `ticketSid` 对 `mt4_trades`、MT5 按 `(Login, PositionID)` 对 `mt5_deals Entry=0`，**精确率 ≥ 99%**；召回缺口全部能在原始 log 里证明是「IP 列非 IPv4」（同 §3.4.1 的复核方法，脚本可仿 `backend/scripts/verify_last_close_ip.py`）。
- [ ] 0 条 demo 前缀 / <5 位账号 / 空 IP 行。
- [ ] `_daily_housekeeping` 表里多一行 `order_ip` 400 天；`docs/features/login-ip.md` 新增 §3.6 写清行格式、口径、保留期。
- [ ] 重跑同一天不产生重复行（UNIQUE 生效）。

**Phase 2**
- [ ] `GET /login-ip/trade-profit/groups` 30 天窗口 P95 < 2 s（缓存命中 < 200 ms）。
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
- 工期估计：Phase 1 1 天（含对账脚本）、Phase 2 1.5 天、Phase 3 1.5 天、文档与冷审 0.5 天，≈ 4.5 天。Phase 1 单独先上线，回滚镜像标签 `pre-order-ip-<日期>`。

## 结果

<done 时填>
