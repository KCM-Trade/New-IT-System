---
id: OPT-0062
title: 即日高收益率自動偵測與郵件警報 —— risk-monitor 新規則 Intraday Return（band 131-140）+ detail 表 + 郵件源 + 回測腳本
status: done
priority: P1
area: mixed
effort: L
created: 2026-09-17
related: [[OPT-0046]] [[OPT-0033]] [[OPT-0043]] [[OPT-0021]]
---

> **v2（2026-09-18）**：吸收獨立冷審 21 條 finding + 風控 Sammy 第二封回覆（加「近 7 日淨利 ≥ 0」過濾 +
> 兩項驗證），並用修訂公式實跑了 9/14–9/17 回測與 9/17 全盤清單（結果見 §驗證）。v1 的「方案」段已整體替換；
> 冷審原文濃縮在 §冷審對照表。**本文件自洽，實施 worker 只讀這一份即可。**

## 問題

風控（Sammy，2026-09-17 郵件）反映越南客戶用小本金 + 鎖倉 + 階梯加碼在震盪市裡刷出當日數倍收益，
要求系統實時監控所有活躍帳戶，**當日收益率 ≥ 閾值即發郵件到風控與 CS**。

用戶（Kieran）拍板：100% 這檔也要收郵件，按檔分級發。
Sammy 2026-09-18 回覆：四點修改建議照做；**加過濾「近 7 日累計淨利 ≥ 0」**（排除「當天僅反彈、整體仍虧」的客戶）；
要求兩項驗證：① 9/14–9/17 在 300% 下必須命中 67044208 / 60006521 / 60011522；② 用新條件跑昨天全盤並匯出清單。

Sammy 原公式：`Intraday Profit / Initial Equity >= 300%`，
`Initial Equity = 當天開盤 Balance + 開盤時未平倉 Floating`，`Intraday Profit = 當日已平倉盈虧 + 當前浮動盈虧`。

## 背景（已做的分析，勿重做）

### 原公式的問題（近 30 天真數據，8/16–9/16，三台服務器）

1. **分母為零**：當天才入金的帳戶開盤權益 = 0。MT5 近 30 天 532 個「日初權益 ≤ 0 但當日盈利」帳戶日，
   Sammy 舉的 67044208 就是（9/16 06:58 註冊、07:03 入金 50、當日 88 筆 XAUUSD 賺 478.27）。
2. **沒有最低門檻**：300% 檔不設門檻 MT5 30 天 288 帳戶日，「初始權益 ≥ 50 USD」後剩 59。
3. **量**：分母含當日入金、門檻 50 時（日終口徑，MT5+MT4）≥100% 357 帳戶日/30 天（日均 12）、≥200% 124、≥300% 79。
   ⚠ 這張表是**日終、淨額口徑**，只能當下界（冷審 F3）。
4. **原公式對跨日持倉重複計分**（v2 新發現）：昨天開的倉、昨日終浮盈 +100 已在 Initial Equity 裡，今天平在 +150，
   「當日已平倉盈虧」記 150 而真實當日增量是 50。v2 改用權益增量口徑消除了這一條，但引入「浮虧回補算盈利」的新問題，最終口徑見 §公式 v3。

### 數據源（已探明，全部現成）

| 項 | MT5 (sid 5, `mt5_live`) | MT4 Live / Live2 (sid 1 / 6) |
|---|---|---|
| 昨日日終權益 | `mt5_users.EquityPrevDay`（✅ 冷審抽 14 帳戶與 `mt5_daily.ProfitEquity` 全等） | `mt4_daily` 主鍵 (LOGIN, TIME) 點查 `TIME < 今日日初` 最近一行 `EQUITY`（無 PREVEQUITY 列，只有 PREVBALANCE）；週末無行，「最近一行」自然覆蓋 |
| 7 日前日終權益 | `mt5_daily` 按 `Datetime`（PK 首列，int 秒）範圍取 | 同上點查 `TIME = D-7 23:59:59`（無行往前找） |
| 當前權益 | `mt5_users.Balance + Credit` + `mt5_positions` Σ(Profit+Storage) | `mt4_users.BALANCE + CREDIT` + `mt4_trades CLOSE_TIME='1970' Σ(PROFIT+SWAPS)`（現成 `_query_mt4_floating`） |
| 當日 / 7 日餘額操作 | `mt5_deals Action IN (2,3)`，**按 `Timestamp`（FILETIME，有索引）切日，別用 `Time`**（19.3s vs 0.25s） | `mt4_trades CMD IN (6,7)` 按 `CLOSE_TIME` |
| 當日成交（行為特徵） | `mt5_deals Action IN (0,1)` 按 `Timestamp` | `mt4_trades CMD IN (0,1)` 按 `OPEN_TIME` |
| 幣種權威 | `account_enrichment.build_currency_map()`（**檢測前**調） | 同左 |

🔴 `mt4_daily` 不能按 TIME 範圍掃（全表 > 300s 被殺），只能先圈候選 login 再主鍵點查；實測 425 login 點查 5.37s，
真實候選 MT4 側 ~800 → 分母**按 (login, trading_day) 進程內緩存**，每天每 login 只查一次。
🔴 `mt5_daily` 按 `Login` 點查直接超時（PK 首列是 `Datetime`）。
MT5 `Datetime` = 服務器本地日終 23:59:59 當 UTC 存的 unix 秒；從庫 session tz = `Indian/Antananarivo`（+03:00）。

### 「入金」白名單（冷審 F1，實測 9/11–9/17 MT5 Action=2 Comment 分布）

正向 `Action=2` 裡混著 **`Balance Adjustment Zero`（64 筆 / $4.02M，單筆可達 $779k，運維批量調帳）**、`Adjustment - *`、
`Initial balance`（$9.85M，開戶初始）。真入金形態：`DEPOSIT` / `Deposit` / `D- …` / `D-#-…` / `XTHB-Deposit-` / `IT-D #`（內轉入）/
`IT-Third Party-D` / `IB Wallet Transfer` / `OnefinVA#`。MT4 `CMD=6` 同型（`D-…` / `IT-D` / `IB Wallet Transfer` / `Adjustment - #` / `Balance Adjustment Zero`）。
**口徑：黑名單排除 `Comment` 以 `Balance Adjustment` / `Adjustment` / `Initial` 開頭者，其餘正向 = 入金**（含內轉入，那是客戶自己的錢）。
Credit / Bonus 走 `Action=3` / `CMD=7`（7 天 Credit In $92.9k、Bonus In $33.2k；`mt5_users` 1,059 帳戶持 credit $3.2M、`mt4_live` 31 帳戶 $6.31M）。

### 行為特徵（MT5 近 30 天 ≥100% 的 208 帳戶日）

鎖倉占比 ≥30% 的 31、開倉 ≥30 筆且中位持倉 ≤15 分鐘的 91、兩者都滿足 14。
67044208：88 筆 / 中位 10.5 分鐘 / 鎖倉 88%；60006521（9/15，8021%）：258 筆 / 82%。
現有對沖規則（91-100，3 秒內同開多空且完美 1:1）抓不到「先空、虧了再逐筆補多」。

## 公式 v3（實施口徑，SSOT；2026-09-18 用戶認可，取代 v2）

> **v2 → v3 的原因**：v2 用「權益增量」當分子，會把**隔夜倉的浮虧回補**算成當日盈利（9/17 清單實例：8611807 一張 3 手 XAUUSD 多單自 8/28 扛到現在，
> 浮虧 −62,471 → −39,763，當天零成交零出入金，被記成「盈利 22,708 / 112%」；8521502 五張隔夜 XAGUSD 多單白銀大漲，−403 → +645，記成 205%），
> 而分母又恰被同一批隔夜浮虧壓小，比率被放大。Sammy 原式（當日已平 + **當前全部浮動**）反過來會讓盈利的隔夜倉**每天重複命中**且重複計分。
> 「只算當天開的倉」又會漏掉「昨天 50 美元開倉、今天浮盈 500」這種最典型的小本金暴利。v3 三者都避開。

以 MT 服務器交易日 D 為單位，對每個候選帳戶：

```
prev_eq        = 昨日（D-1，週末往前找）日終權益
prev_bal       = 昨日日終餘額；prev_credit = 昨日日終 credit
carried_float0 = prev_eq − prev_bal − prev_credit        （隔夜倉在昨日日終的總浮動，可正可負）
dep_in         = D 內真入金（Action=2/CMD=6，Profit>0，Comment 不在黑名單）
cred_in        = D 內 credit/bonus 入（Action=3/CMD=7，Profit>0）
initial_equity = prev_eq + dep_in + cred_in              （include_deposits_in_base=false 時 = prev_eq）

倉位分兩類（按開倉時間 < 今日日初 判定）：
  same_day_pnl   = 當天開的倉：已平（PROFIT+SWAPS+COMMISSION）+ 仍持有的當前浮動（PROFIT+SWAPS），全算
  carried_now    = 隔夜倉：今天平掉的已實現 + 仍持有的當前浮動（帳戶級合計）
  carried_gain   = max(carried_now, 0) − max(carried_float0, 0)   （只算今天在「盈利區」新增的部分；虧損回補 = 0；不重複計昨天已進 initial_equity 的浮盈）

intraday_profit= same_day_pnl + carried_gain
return_pct     = 100 × intraday_profit / initial_equity
net_7d         = 近 net_window_days 日已平倉 PnL 合計 + 當前全部持倉浮動  （「整體仍虧」的人過不了：8611807 = 0 + (−24,350)）
觸發 = initial_equity ≥ min_initial_equity_usd
     ∧ intraday_profit ≥ min_profit_usd
     ∧ return_pct ≥ min_return_pct
     ∧ net_7d ≥ min_net_7d_usd
     ∧ [可選] lock_pct ≥ min_lock_pct ∧ median_hold_min ≤ max_median_hold_min
```

**v3 在典型場景下的表現**（單測要逐條釘住）：

| 場景 | intraday_profit | 結果 |
|---|---|---|
| 當天開當天平（三個重點帳戶） | same_day_pnl 全算 | 67044208 957% / 60006521 8021% / 60011522 617%、942%，與 v2 完全一致 |
| 昨天 50 開倉，昨日終浮盈 +20，今天浮盈 +500 | 500 − 20 = 480，/70 = 686% | 命中；第二天浮盈不再漲則分子 0，**不重複命中** |
| 扛單浮虧 −62k 回到 −40k（8611807） | max(−40k,0) − max(−62k,0) = 0 | 不命中；net_7d 也是負 |
| 隔夜倉由虧轉盈（8521502：−403 → 平 74.64 + 浮 645.60） | 720 − 0 = 720，/548 = 131% | 100% 檔命中、300% 不命中（與 Sammy 原式同值，扛單轉盈讓風控看一眼是合理的） |
| 隔夜倉浮盈 +100 今天平在 +150 | 150 − 100 = 50 | 只算今天增量，不重複計分 |

- `carried_float0` 全部現成：MT5 `mt5_users.EquityPrevDay − BalancePrevDay − Credit`（credit 用昨日行 `mt5_daily.Credit`）；MT4 `mt4_daily` 昨日行 `EQUITY − BALANCE − CREDIT`。
  不需要逐倉的日初價格。隔夜倉今天的盈虧按 `TimeCreate` / `OPEN_TIME` < 今日日初 分組即可。
- `carried_gain` 是**帳戶級**截零，不是逐倉截零（逐倉需要日初價格，做不到；帳戶級已足夠排除扛單回補）。
- 出金：不進分子也不進分母（分子按倉位算，天然與出入金無關）；當日出金 > 入金 × 50% 仍打 `flag_withdraw_gt_half_deposit`。
- CEN：比率免換算；三個 USD 門檻與手數 ÷100；幣種在檢測前取。
- `initial_equity ≤ 0` 一律跳過（不再有 ÷0）。
- 「當天」= MT 服務器交易日。⚠ MT 日界隨 DST（夏 GMT+3 / 冬 GMT+2），`BROKER_TZ_OFFSET` 硬編碼 +03:00 —— 本規則是唯一以日界為口徑的規則，
  **「今日日初」從 `mt5_daily` 最新 `Datetime` + 1s 推導**（跟隨服務器實際歸零點），MT4 沿用同一時刻；不用 `CURDATE()`。上線前實測一次。
- 排除：demo/test 組（`sql_helpers.demo_test_filter_sql()`）、MT4 login 7 開頭。⚠ risk-monitor SKILL.md 寫的 `RISK_MONITOR_EXCLUDED_LOGINSIDS` /
  `excluded_login_sql` **在代碼裡不存在**，只有反向的 `RISK_MONITOR_FORCE_INCLUDE_LOGINSIDS`；那段 skill 文檔順手修掉。

## 方案 v2（公式部分以上方 v3 為準）

### 規則定位

- 名稱：即日高收益 · Intraday Return。新 tab 放「馬丁」與「Gap Trade」之間。
- band **131-140**：`INTRADAY_RETURN_RULE_ID_BASE = 131` / `_MAX = 140`，rule_id = 131 + 列表位置，常量放 `routes/risk_monitor.py`。
- 粒度：帳戶級，每 MT 交易日每規則一條；`symbol` 填當日主力品種。
- **獨立 job**（照 `_run_rebate_arb_scan` / `_locked_rebate_arb_scan`：同一個 `_scheduler.add_job()`、**自己的 lock 不占 `_scan_lock`**、
  `INTRADAY_RETURN_INTERVAL_MIN` 默認 5，env 可調、`INTRADAY_RETURN_SCAN_ENABLED` 默認 true、dev compose 關）。
  不進 slow tier：slow tier 週期是 `scan_interval_min`（默認 10、5–60 UI 可改），且 `BURST_FAST_TIER_ENABLED` off 時只有 `tier="all"`。
- 仍要把 `(131, 140)` 加進 `_SLOW_TIER_RULE_BANDS` 並把 `_MAX_ALLOCATED_RULE_ID` 130→140（`burst_open_scheduler.py:277-284`），
  否則 `test_scheduler_tiers.py:488` 的 anti-drift 循環根本不覆蓋新 band。
- 「立即掃描」按鈕：有，觸發本 job 的 `_locked_*` 入口（不是共享 scan-now）。
- 分檔靠多條規則（Rule 1 = 100%、Rule 2 = 300%），**高檔命中抑制低檔**（同帳戶同日 132 命中則 131 不再發、已發的 131 在郵件裡標「已升檔」），不另做 severity。

### 規則參數 `IntradayReturnRule`（最多 10 條）

| 參數 | 類型 / 範圍 | 默認 | 含義 |
|---|---|---|---|
| `name` | str 1-100 | — | 快照進 `rule_label` |
| `enabled` | bool | true | 單條停車 |
| `min_return_pct` | float 10–100000 | 100 / 300 | 收益率閾值 |
| `min_initial_equity_usd` | float 0–1e6 | 50 | 分母門檻 |
| `min_profit_usd` | float 0–1e7 | **30** | 分子門檻。⚠ 不能是 100：與門檻 50 疊加 = 50 USD 帳戶要 200% 才觸發，100% 檔對目標人群失效 |
| `min_net_7d_usd` | float −1e7–1e7 | 0 | Sammy 新增：近 N 日淨利 ≥ 此值 |
| `net_window_days` | int 1–30 | 7 | 上一條的窗口 |
| `include_deposits_in_base` | bool | true | 分母是否加當日入金 + credit（冷審要求可關） |
| `min_lock_pct` | float 0–100, nullable | null | 可選：鎖倉占比 ≥（Sammy 的第二個需求「鎖倉 + 高頻」自動篩） |
| `max_median_hold_min` | float 0–1440, nullable | null | 可選：中位持倉 ≤ |
| `lock_ratio_min` | float 0–1 | 0.5 | 鎖倉判定「小邊 ≥ 大邊 × 此值」（v1 拍的 0.5，做成參數） |

**固定不做參數**：分子含浮動（權益增量口徑天然含，改成可關會讓規則在事件型/快照型之間變性、歷史不可比）；交易日邊界；入金黑名單（代碼常量）。
`IntradayReturnConfig = {enabled, rules}`，與 `MartingaleConfig` 同形。

### 檢測流程（每 tick，三台服務器各一遍）

1. 交易日邊界：`mt5_daily` 最新 `Datetime` + 1s（PK 倒序 LIMIT 1）。跨日時清空當日緩存。
2. 候選 = 當日有成交（開或平）∪ 當前有持倉 ∪ 當日有餘額操作 的 login（實測 9/17：MT5 成交 912 / 持倉 630、MT4_Live 537 / 419、Live2 44 → 約 1,500–1,800）。
3. 幣種：對候選調 `build_currency_map()`。
4. 分母：按 (server, login, trading_day) 進程內緩存 `prev_eq` 與 `eq_d7`；首 tick 全量、後續只補新 login。MT5 讀 `EquityPrevDay`（D-7 走 `mt5_daily`），MT4 主鍵點查 `mt4_daily`。
5. 流水：當日 + 7 日餘額/credit 操作（小表），算 `dep_in / cred_in / net_flow / adj_excluded / withdrawals_out`。
6. 當前權益：Balance + Credit + Σfloating（現成浮動查詢）。
7. 先過三個門檻（含 7 日淨利），再算比率，再逐條規則比閾值；命中且規則帶可選行為條件時，**只對命中帳戶**拉當日成交算 `trades_today / median_hold / lock_pct / top_symbol`。
8. 去重：`(rule_id, server, login, trading_day)`。**每 tick 從 SQLite 回種當日已發**（照 `get_rebate_arb_alerted_userids` 寫 `get_intraday_return_alerted_keys(trading_day)`）——
   `alert_events` 無唯一約束、slow tick 會替換自己的告警段（`burst_open_scheduler.py:669-678`），只靠內存 = 一天 288 封。
9. 已命中的帳戶後續 tick **UPSERT detail 行**（最新 `return_pct` / 行為特徵 / `peak_return_pct` 取最大），不新增 alert 行、不重發。
10. 連接自己開，`SET SESSION MAX_EXECUTION_TIME`（db-timeout-guard；`rule_quick_profit_service._get_connection()` 沒釘）。tick 耗時 DEBUG、命中才 INFO（OPT-0058）。

### 落庫 `alert_intraday_return_detail`（主表 23 列不動，OPT-0008 五步 LEFT JOIN）

`trading_day` · `prev_day_equity` · `deposits_in` · `credit_in` · `withdrawals_out` · `adj_excluded` · `initial_equity` ·
`equity_now` · `same_day_pnl` · `carried_float0` · `carried_now` · `carried_gain` · `intraday_profit` · `return_pct` · `peak_return_pct` · `net_7d` · `realized_7d` · `floating_all_now` · `flag_withdraw_gt_half_deposit`（當日出金 > 入金 × 50%，冷審 F6 規避路徑）·
`trades_today` · `lots_today` · `median_hold_sec` · `lock_pct` · `top_symbol` · `updated_at`。

主表映射：`order_count` = 當日筆數、`total_lots` = 當日手數、`first_open`/`last_open` = 當日首末成交、enrichment 走 `get_account_info_map()`。
`return_pct` / `net_7d` 可排序 → 後端 `SORTABLE_ALERT_COLS` + `_SORT_COL_DB_NAME`、前端 `SORTABLE_COL_IDS`。
`_ALERT_FROM_CLAUSE` 將是第 9 個 LEFT JOIN，可接受；順手在 follow-up 記「按 band 動態選 JOIN」。

### 郵件

`MAIL_SOURCES["intraday_return"]`，band (131,140)，realtime（realtime 本來就是每 tick 每訂閱一封 digest，命中合併，配按日去重後 100% 檔一天最多十幾封，不另做匯總層）。
照 `services/alert_mail/rebate_arb.py`（4 個 fetch_* + template_builder + registry entry）。可過濾字段：`return_pct` / `intraday_profit` / `initial_equity` / `net_7d` / `trades_today` / `lock_pct`。
模版按 alert-email-style：帳戶信息 + 初始權益拆解（昨日日終 / 入金 / credit）+ 當日盈虧拆解 + 收益率（含峰值）+ 7 日淨利 + 三個行為特徵 + 出金標記 + CRM 連結。
**收件人（用戶拍板 2026-09-18）**：上線即發、**不做 shadow 期**，100% 與 300% 兩檔都發；先只發風控組，**CS 暫不收**；
兩條規則各 seed 一條 realtime 訂閱：`to = risk@kcmtrade.com`，`cc = kieran.xiang@kohleservices.com, lawrence.li@kohleservices.com`。
上線後人工觀察量級再調（門檻 / 是否給 CS / 是否關 100% 檔），都是郵件中心 UI 操作，不改代碼。

### 回測 / 清單腳本（交付物，Sammy 會反覆要）

`backend/scripts/intraday_return_backtest.py`：`--from --to --threshold --floors --net-window` → CSV + 可選郵件。
原型已在 scratchpad `verify.py`（日終口徑：`mt5_daily` + `mt5_deals` 流水；MT4 走 `mt4_trades` 圈候選 + `mt4_daily` 主鍵點查），
輸出列 = detail 表列。⚠ 日終口徑是下界，跟線上 tick 口徑（盤中峰值）不同，腳本 docstring 要寫明。

### 前端

新 tab，`useGridColumnPersist` + `ColumnVisibilityMenu` + `useFilterPersist`（key 命名須匹配 `^[A-Z0-9_]+_(GRID_STATE|FILTERS|AGGREGATED|ACTIVE_TAB)_V\d+$`），
列全顯式 `colId`，`InfoHeader` 解釋公式 v3。匯總卡三張：今日命中帳戶數 / 峰值收益率最高 / 命中帳戶當日盈利合計。config drawer 復用 §9 每規則卡片。
`SSE` / `/stats` / CSV 三個端點契約同其他 tab（客戶端 `exportGridAsCsv()`）。

## 驗證（2026-09-18 用 `backend/scripts/intraday_return_backtest.py` 以公式 v3 實跑，門檻 50/30、7 日淨利 ≥ 0，日終口徑）

> 腳本 = 日終口徑 = **下界**（線上 tick 記盤中峰值）；日終浮動在「當天開 + 隔夜」同時持倉時按手數比例拆分（近似，日內全平的目標人群無影響）。
> 命令：`cd backend && .venv/bin/python scripts/intraday_return_backtest.py --from 2026-09-14 --to 2026-09-17 --threshold 300 --servers mt5`
> / `… --from 2026-09-17 --to 2026-09-17 --threshold 100`。CSV 落在 `backend/data/tmp/intraday_return_backtest_<from>_<to>.csv`。

**① 9/14–9/17 MT5 回測，閾值 300%**：三個重點帳戶全部精準命中，四行數字與 v2 完全相同（全是當天開當天平，`carried_now = carried_float0 = 0`）；7 日淨利過濾對它們零影響。

| 帳戶 | 日期 | 昨日日終 | 入金 | 初始權益 | 當日盈利 | 收益率 | 7 日淨利（v3 = 7 日已平 + 當前浮動） |
|---|---|---|---|---|---|---|---|
| 60011522 | 9/14 | 0.19 | 50 | 50.19 | 309.60 | 617% | +309.60 |
| 60006521 | 9/15 | 0.79 | 75 | 75.79 | 6,079.22 | 8021% | +6,079.22 |
| 60011522 | 9/15 | 359.79 | 0 | 359.79 | 3,388.25 | 942% | +3,697.85 |
| 67044208 | 9/16 | 0.00 | 50 | 50.00 | 478.27 | 957% | +478.27 |

四天 300% 命中 **2 / 4 / 3 / 0**（7 日過濾後不變）—— ⚠ 與 v2 相同，**不是 v3 改稿時預估的 1 / 4 / 3 / 0**：9/14 的第二個命中 67043694
（init 59.33 = 7.66 + 入金 51.67，同日盈利 190.76，322%）是純當天開當天平，v2/v3 同值，預估時誤以為它是隔夜倉案例。
9 個 300% 命中裡只有 67034358（9/16）帶 4.10 的 carried_gain，其餘 carried 全 0。
100% 檔：11 / 7 / 11 / 4 → 7 日過濾後 11 / 7 / 8 / 3（v2 為 11 / 7 / 12 / 5 → 11 / 7 / 10 / 4；差異來自 9/16、9/17 各一兩個隔夜倉回補在 v3 下分子歸零）。

**② 9/17 全盤（三台）**：≥100% 12 帳戶 → 7 日過濾後 **9**（MT5 3 / MT4 6）；≥200% 3 → 1；≥300% 2 → 1（9815866，423%）。
v2 是 17 → 13 / 5 → 3 / 2 → 1：少掉的全是隔夜倉浮虧回補被 v3 截零的 MT4 帳戶。7 日過濾砍掉的 3 個仍是「本週已虧、當天反彈」：
8612530（當日 +382 / 7 日 −314）、8515329（+280 / −723）、67043240（+103 / −327）。

v3 專門修的兩個案例，按驗收標準逐條核對：
- **8611807 不出現**：same_day 0、carried_now −39,762.93、carried_float0 −62,470.77 → carried_gain 0、分子 0；net_7d = 0 + (−39,762.93)（v3 改稿時寫的 −24,350 是盤中某刻的浮動，日終是 −39,763）。
- **8521502 只在 100% 檔**：carried_now = 平倉 74.64 + 浮動 645.60 = 720.24、carried_float0 −402.76 → carried_gain 720.24，/548.50 = **131.3%**（v2 記 205%）。

清單 CSV：`backend/data/tmp/intraday_return_backtest_2026-09-17_2026-09-17.csv`（2,355 個帳戶日）、
`…_2026-09-14_2026-09-17.csv`（MT5 5,719 個帳戶日）。四天 MT5 跑 32s、9/17 三台跑 8s。

## 假設 / 待驗證

- [x] 郵件收件人：risk@kcmtrade.com，cc kieran.xiang@kohleservices.com + lawrence.li@kohleservices.com；CS 暫不發（2026-09-18 拍板）
- [ ] Sammy 對「公式 v3（隔夜倉只算盈利區新增）」與「門檻 50/30」的確認（v1 回信寫的是 50/100，**已發 Kieran 的草稿轉發前要改**）
- [ ] 「今日日初」從 `mt5_daily` 推導在 DST 切換日的實測（下一次切換 2026-10-25 前後）
- [ ] MT4 分母緩存後每 tick 實測耗時（目標 < 10s 三台合計）
- [x] 不做 shadow，兩檔上線即發，人工觀察後再調（2026-09-18 拍板）

## 驗收標準

- [ ] 獨立 job + 自有 lock + env 開關與週期；`_SLOW_TIER_RULE_BANDS` 含 (131,140) 且 `_MAX_ALLOCATED_RULE_ID=140`，tier anti-drift 測試覆蓋
- [ ] 公式 v3 逐項單測：÷0 跳過、三門檻、CEN ÷100（檢測前取幣種）、入金黑名單、credit 進分母、**§公式 v3 表格五個場景逐一釘住**（同日全算 / 隔夜爆發命中且次日不重複 / 扛單回補 = 0 / 由虧轉盈只算過零段 / 隔夜浮盈只算增量）、net_7d = 已平 7 日 + 當前全部浮動、可選行為條件
- [ ] 去重每 tick 回種 + UPSERT detail + `peak_return_pct`；高檔抑制低檔
- [ ] MT5 切日用 `Timestamp`；MT4 只主鍵點查 `mt4_daily`；分母按日緩存；`MAX_EXECUTION_TIME` 釘住
- [ ] `alert_intraday_return_detail` 落庫 + `/alerts` 拍扁 + `return_pct` / `net_7d` 服務端排序
- [ ] 郵件源註冊 + anti-drift + test-send；seed 兩條訂閱（131 / 132 → risk@，cc kieran + lawrence）
- [ ] 前端 tab 四個 hook 齊全，tsc/vitest 綠；drawer 可增刪規則（含可選行為條件與 nullable 顯示）
- [ ] `backend/scripts/intraday_return_backtest.py` 落地（v3 口徑），重跑 §驗證 ① 得到同樣四行，且 9/17 清單裡 8611807 不出現、8521502 只在 100% 檔
- [ ] 回放測試：三個重點帳戶各自日期在 300% 命中（種子時間戳相對 `datetime.now()`，OPT-0041）
- [ ] 回寫 risk-monitor skill（Rule ID 表 131-140、references、修掉不存在的 `excluded_login_sql`）+ docs/features/risk-monitor.md + alert-mail-center skill

## 冷審對照表（2026-09-18 獨立 Opus reviewer，21 條；本會話逐條核對）

| # | finding | 處置（在 v2 哪裡） |
|---|---|---|
| F1 🔴 | Action=2 正向含調帳 / Initial balance | §入金白名單（黑名單口徑）+ `adj_excluded` 列 |
| F2 🔴 | credit / bonus 漏掉 | 分母加 `cred_in`，detail 單列 |
| F3 🔴 | 回測 ≠ 上線口徑 | 用戶拍板不做 shadow、上線後人工觀察；回測腳本 docstring 標「下界」 |
| F4 🟡 | EquityPrevDay 語義（已核實為真） | — |
| F5 🟡 | 日界隨 DST | 從 `mt5_daily` 推導日初，不用 CURDATE |
| F6 🟡 | 出金不減分母可被利用 | `flag_withdraw_gt_half_deposit` 標記 |
| F7 🔴 | 去重只在重啟回種會每 tick 重發 | 每 tick 回種 + UPSERT |
| F8 🔴 | `excluded_login_sql` 不存在 | 改 `demo_test_filter_sql()`，修 skill 文檔 |
| F9 🟡 | tier 常量不動則護欄失效 | 加 (131,140) + 140 |
| F10 🟡 | slow tier 不是 5 分鐘 | 獨立 job |
| F11 🟡 | 缺 MAX_EXECUTION_TIME | 自開連接釘超時 |
| F12 ⚪ | realtime 已是 digest | 不另做匯總層 |
| F13 🔴 | MT5 切日用 Timestamp | 數據源表 |
| F14 🟡 | MT4 點查 5.37s/425 + 占 `_scan_lock` | 分母按日緩存 + 自有 lock |
| F15 🟡 | 行為特徵只算一次 | UPSERT + `peak_return_pct` |
| F16 ⚪ | 9 個 LEFT JOIN | follow-up |
| F17 🔴 | CEN 門檻差 100 倍 | 檢測前 `build_currency_map()` |
| F18 🔴 | 兩個門檻互相抵消 | `min_profit_usd` 默認 30 |
| F19 🟡 | 分檔無抑制 | 高檔抑制低檔 |
| F20 🟡 | 參數/固定反了 | `include_deposits_in_base` / `lock_ratio_min` 做參數；含浮動固定 |
| F21 🟡 | AC 缺 shadow / mt5_daily 點查陷阱 / SSE-stats-CSV / 快照型決策 | 已補；快照型漂移對本規則不成立（只關心比率往上、按日去重、回落不重報） |

## 冷審二輪（2026-09-18 實施後，獨立 reviewer 12 條；同會話處置）

| # | finding | 處置 |
|---|---|---|
| R1 🔴 | 132 在冊後回落到 150% 會新開 131 行 + 發「≥100%」郵件 | **當場修**：只有比在冊最高檔更高的檔才算新命中（`test_decay_from_higher_tier_does_not_open_lower_tier_row`） |
| R2 🔴 | 昨日行缺失被當 (0,0,0) 緩存一整天 | **當場修**：MT5 回落 `mt5_users.EquityPrevDay/BalancePrevDay`；日初 60 分鐘內不緩存舊行；單台缺失 > 20% → 該服務器跳過 + ERROR |
| R3 🔴 | `_filetime` 硬編碼 +03:00，MT 墻鐘冬季是 +2 | **當場修**：實測 1 月 deals 差 +3600 → 先改 `Europe/Athens`，文檔回寫時再探 03-10 / 10-28 / 11-04 發現切換點是**美國 DST 日程**（與 KCM `mt_server_utc_offset_hours` 同）→ 自定義 tzinfo `_MTServerTZ`（`test_filetime_follows_mt_wall_clock_dst` 釘四個日期）。⚠ 回測腳本 SQL 側仍用 session +3（follow-up） |
| R4 🔴 | scan-now 落在別的 uvicorn worker，與 scheduled tick 各插一行各發一封 | **當場修**：`persist_intraday_return_tick()` 在 `BEGIN IMMEDIATE` 內再查當日鍵、撞上的降級成 update（`test_persist_tick_demotes_duplicate_alert_to_update`）。rebate-arb 同型洞未修（follow-up） |
| R5 🟡 | `mt5_daily` 停更 → 「今天」變成兩天 | **當場修**：日初比現在舊 > 26h → tick `skipped` + ERROR |
| R6 🟡 | 帳戶不再匹配時行凍在最後匹配值 | **當場修**：在冊行不論匹配與否都刷新 |
| R7 🟡 | 「最近 4 小時」按首次 `scanned_at` 篩，盤中持續上漲的行會消失 | **當場修**：`/alerts` `/stats` `/export` 改 `time_field="trading_day"`（MT 日期，含端點）+ 前端加「更新時間」列 |
| R8 🟡 | 郵件 sibling 用 `first_open` 的 UTC 日期查 `trading_day` | **當場修**：dispatcher `_alert_day_utc` 優先 `trading_day` / `window_date`（順帶修 rebate-arb 同型） |
| R9 🟡 | MT5 部分平倉在 positions 出現兩次 → 筆數/手數虛高 | **當場修**：快照裡還有餘量的倉不再加平倉條目。MT4 部分平倉（新 ticket）與 Entry=2 反手仍會多算一筆（live with） |
| R10 🟡 | `get_account_info_map` fail-open `{}` → 全按 USD | **當場修**：候選非空但 map 為空 → tick `skipped` |
| R11 🟡 | 單台採集失敗仍回 200 success | **當場修**：結果 `status: partial` + `servers_failed`，scan-now 回顯，scheduler 打 ERROR |
| R12 🟡 | 測試缺口（rollover / 部分平倉 / 原子落庫 / 單台失敗） | **當場補** `tests/test_intraday_return_review_fixes.py` 10 條；`_prepare_server` 的 SQL 組裝仍無測（live with） |
| ⚪ | 位置型 rule id 中途刪規則會錯位；`RiskMonitor.tsx` 已 12k 行；顏色閾值 100/300 硬編碼；`flag_withdraw` 在零入金時任何出金都打；回測 `open_eod` 掃 `mt4_trades` 240s | live with，記 follow-up |

## 筆記

- 工期：後端檢測 + detail + 去重 + 回測腳本 3 天、郵件源 1 天、前端 2 天、測試部署 1 天 ≈ **7 個工作日**；只上郵件 + 回測腳本 ≈ 3.5 天。
- 走 OPT 是用戶明示（2026-09-17）；按 tracker README 這本屬 net-new feature，先例 OPT-0030/0033/0046。
- 快速獲利（61-70）形狀最近但它是分鐘窗口 + 絕對金額，不硬改。
- 「浮虧下同手數連加 ≥3 次」由現有馬丁規則（111）`lot_multiplier=1.0` + `min_add_count=3` 覆蓋，本規則不重做。
- 回測腳本原型：scratchpad `verify.py`（本會話）；`bt_mt5.py` / `bt_fxbo.py` / `lock_profile.py` 是 v1 的 30 天回測。

## 結果（2026-09-18 合併，同日部署）

**交付 vs 驗收標準**：全部 ✅ ——獨立 job + 自有 lock + env 開關（`INTRADAY_RETURN_SCAN_ENABLED` / `_INTERVAL_MIN`）；tier 護欄 `(131,140)` + `_MAX_ALLOCATED_RULE_ID=140`；公式 v3 五場景 + 三門檻 + CEN + 黑名單 + credit + 7 日淨利 + 可選行為條件逐條單測；每 tick 回種 + UPSERT + `peak_return_pct` + 高檔抑制低檔（含回落抑制，二輪冷審 R1）；MT5 `Timestamp` 切日 / `mt4_daily` 只點查 / 分母按日緩存 / `MAX_EXECUTION_TIME`；detail 表 + 拍扁 + 服務端排序；郵件源 + seed 兩條訂閱；前端 tab 四 hook；`intraday_return_backtest.py` 重跑 §驗證 ① 四行全中、8611807 不出現、8521502 只到 100% 檔；回放測試；skill / docs 回寫（順帶修掉不存在的 `excluded_login_sql`）。

**與計畫的偏差**：
- 交易日換算改用美國 DST 日程的自定義 tzinfo `MT_SERVER_TZ`（+2/+3，3 月第 2 週日 → 11 月第 1 週日）而非 `BROKER_TZ_OFFSET`；CLAUDE.md 的「UTC+3 no DST」已改寫。合併時曾短暫用 `Europe/Athens`，同日文檔回寫階段探到 03-10 / 10-28 都已是 +3 才改正（hotfix 同日部署）。
- 落庫改成 `persist_intraday_return_tick()` 原子 check-and-insert（scan-now 跨 worker 競態），不是「append + update 兩步」。
- `/alerts` `/stats` `/export` 按 `trading_day` 篩選而非 `scanned_at`；前端多一列「更新時間」。
- 9/14 300% 命中數是 2 不是預估的 1（67043694 純當日交易，v2/v3 同值）。

**冷審處置**：一輪 21 條（設計期）見 §冷審對照表；二輪 12 條（實施後）見 §冷審二輪——4 🔴 7 🟡 全部當場修 + 10 條回歸測試，⚪ live with。

**Follow-up（不阻塞）**：
1. 回測腳本 SQL 側切日仍用 session +3（冬季日期需改 `MT_SERVER_TZ`）；`open_eod` 掃 `mt4_trades` 240s，建議預設 `--servers mt5`。
2. rebate-arb 的 `trigger_rebate_arb_scan_now` 有同型跨 worker 競態（threading.Lock 不跨進程）。
3. 位置型 rule id：中途刪第一條規則會把 132 的行錯位成 131（郵件訂閱 `[131]/[132]` 也跟著錯）；drawer 未提示。
4. `_ALERT_FROM_CLAUSE` 第 9 個 LEFT JOIN；按 band 動態選 JOIN（一輪 F16）。
5. `RiskMonitor.tsx` 12k 行，`IntradayReturnTab` 是 `MartingaleTab` 的複製；第 9 條規則前抽共用 hook。
6. MT4 部分平倉（新 ticket）與 MT5 Entry=2 反手仍會多算一筆 `trades_today`。
7. `_prepare_server` 的 SQL 組裝無單測；DST 切換日（**2026-11-01**，美國日程）前後各實測一次。
8. Sammy 對公式 v3 與門檻 50/30 的確認仍待；量級看一周後再決定 CS 是否收、100% 檔是否關（郵件中心 UI 操作）。
9. 課件（Hook 2）：`BEGIN IMMEDIATE` 跨進程去重 + DST 墻鐘 vs FILETIME 是新概念，可補。

**同日 hotfix**：`6c96344` MT 墻鐘改美國 DST 日程 tzinfo；`9606ece` 郵件 CRM 連結改 `/crm/accounts/{sid}-{login}`（首封真實 digest 裡用戶發現原來是 `/admin/accounts/{login}`）。

**同日第三個 hotfix（🔴 首日 12/12 告警全誤報）**：`_query_mt4_prev_day` / `_query_mt5_prev_day` 的候選日終用 `range(1, N+1)`，k=1 已是 `day_start − 1d − 1s` = **前天** 23:59:59，昨天從不在候選裡 → 每個賬戶的基準都是 D−2 EOD，昨日入金與浮盈全部消失（8613868：9/17 入 3,000 + 浮盈 1,350 沒進 base → 郵件 1,322%，正確 8.5%）。按正確 D−1 重算首日 12 條最高 71.8%，無一過 100% 檔；用戶拍板不補更正郵件。修法 `range(_PREV_DAY_LOOKBACK_DAYS)`（k=0 才是昨天）+ 兩個回歸測試（第一個候選必須是 `day_start − 1s`）。回測腳本用 `day − k` 再拼 `23:59:59`，沒有此 bug，四行回歸基準仍有效。附帶：舊代碼下 settle 邏輯的 `row_end == expected_end` 永遠不成立（expected 不在候選裡），修後才真正生效。教訓：`_prepare_server` 無單測（follow-up 7）正是這條漏網的地方——SQL 參數組裝本身也要有斷言。
