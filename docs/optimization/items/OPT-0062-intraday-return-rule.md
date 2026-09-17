---
id: OPT-0062
title: 即日高收益率自動偵測與郵件警報 —— risk-monitor 新規則 Intraday Return（band 131-140）+ detail 表 + 郵件源
status: ready
priority: P1
area: mixed
effort: L
created: 2026-09-17
related: [[OPT-0046]] [[OPT-0033]] [[OPT-0043]] [[OPT-0021]]
---

## 問題

風控（Sammy，2026-09-17 郵件）反映越南客戶用小本金 + 鎖倉 + 階梯加碼在震盪市裡刷出當日
數倍收益，要求系統實時監控所有活躍帳戶，**當日收益率 ≥ 閾值即發郵件到風控與 CS**。

用戶（Kieran）拍板：100% 這檔也要收郵件，但按檔分級發。

Sammy 原公式：`Intraday Profit / Initial Equity >= 300%`，
`Initial Equity = 當天開盤 Balance + 開盤時未平倉 Floating`，
`Intraday Profit = 當日已平倉盈虧 + 當前浮動盈虧`。

## 背景（本 OPT 之前已做的分析，勿重做）

### 公式核過的三個問題（近 30 天真數據，8/16–9/16，三台服務器）

1. **分母為零**。當天才入金的帳戶「開盤權益」= 0，公式算不出。MT5 近 30 天有 **532** 個
   「日初權益 ≤ 0 但當日盈利」的帳戶日；**Sammy 自己舉的例子 67044208 就是其中之一**
   （9/16 06:58 註冊、07:03 入金 50 USD、當日 88 筆 XAUUSD 賺 478.27）。
   → 分母改為 **昨日日終權益 + 當日入金**，例子帳戶 = 478/50 = 957%。
2. **沒地板**。1 USD 賺 3 USD 也是 300%。300% 檔不設地板 MT5 30 天 288 帳戶日，設
   「初始權益 ≥ 50 USD」後剩 59。
3. **量**。分母 = 昨日日終權益 + 當日入金、地板 50 USD 時（日終口徑，MT5 + MT4 兩台合計）：

   | 閾值 | 帳戶日 / 30 天 | 日均 | 單日最多 |
   |---|---|---|---|
   | ≥100% | 357 | 12 | ~40 |
   | ≥200% | 124 | 4 | ~18 |
   | ≥300% | 79 | 2.6 | ~15 |

   盤中衝到閾值又回吐的沒算在內，實時量只會更高 → 100% 檔不能逐條發。

### 數據源（已探明，全部現成，無需重建歷史）

| 項 | MT5 (sid 5, `mt5_live`) | MT4 Live / Live2 (sid 1 / 6, `mt4_live` / `mt4_live2`) |
|---|---|---|
| 日初權益 | `mt5_users.EquityPrevDay`（現成列，服務器日切自動歸零） | `mt4_daily` 主鍵 (LOGIN, TIME)，取 `TIME < 今日日初` 最近一行的 `EQUITY`；無行 → 0 |
| 當日已平倉盈虧 | `mt5_deals` 當日 `Entry IN (1,3)`，Profit+Storage+Commission | `mt4_trades` 當日 `CLOSE_TIME`，PROFIT+SWAPS+COMMISSION |
| 當前浮動 | `mt5_positions` Profit+Storage（現成 `rule_quick_profit_service._query_mt5_floating` / `rule_martingale_service._query_mt5_open_positions`） | `mt4_trades CLOSE_TIME='1970-01-01'`（現成 `_query_mt4_floating` / `_query_mt4_open_positions`） |
| 當日入金（只算正向） | `mt5_deals Action=2 AND Profit>0` | `mt4_trades CMD=6 AND PROFIT>0` |

🔴 **`mt4_daily` 不能按 TIME 範圍掃**：主鍵是 (LOGIN, TIME)，全表掃實測 > 300s 被
`MAX_EXECUTION_TIME` 殺。必須先圈候選 login 再按主鍵點查。
`mt5_daily` 主鍵 (Datetime, Login)，按日範圍掃 OK（回測用），但線上直接讀 `mt5_users.EquityPrevDay` 更省。

MT5 `Datetime` 是「服務器本地時間當 UTC 存」的 unix 秒（日終 23:59:59）；
`mt5_deals.Time` 在從庫是 DATETIME（服務器時間）。從庫 session tz = `Indian/Antananarivo`（+03:00）。

### 行為特徵（同一批數據上驗過，給 CS 判斷「鎖倉 + 高頻」用）

MT5 近 30 天 ≥100%（地板 50）的 208 個帳戶日：鎖倉占比 ≥30% 的 31 個、
開倉 ≥30 筆且中位持倉 ≤15 分鐘的 91 個、兩者都滿足 14 個。
例子帳戶 67044208：88 筆 / 中位持倉 10.5 分鐘 / 多空同時持有時間占 88%。
9/15 收益 8021% 的 60006521（被黑帳戶案）同族：258 筆 / 82% 鎖倉。
現有對沖規則（91-100，3 秒內同開多空且完美 1:1）**抓不到**這種「先空、虧了再逐筆補多」，
所以要新加「鎖倉時長占比」維度。

回測腳本（scratchpad，未入庫，寫進 item 供重跑參考）：`bt_mt5.py` 讀 `mt5_daily`
`DailyProfit / (EquityPrevDay + max(DailyBalance,0))`；`bt_fxbo.py` 讀
`fxbackoffice.stats_balances` 相鄰日 `endingEquity` 差 − `stats_transactions` 淨流；
`lock_profile.py` 讀 `mt5_deals` 掃事件算鎖倉占比。

## 方案（已與用戶對齊 2026-09-17）

### 規則定位

- 名稱：即日高收益 · Intraday Return。新 tab 放「馬丁」與「Gap Trade」之間。
- band **131-140**：`INTRADAY_RETURN_RULE_ID_BASE = 131` / `INTRADAY_RETURN_RULE_ID_MAX = 140`，
  rule_id = 131 + 列表位置。常量放 `routes/risk_monitor.py`（與其他 band 同處）。
- 粒度：**帳戶級，每 MT 交易日一條**；`symbol` 填當日主力品種。
- tier：**slow tier**（5 分鐘，與快速獲利同組）。狀態型不是事件型，不走 event-gated。
  `_is_fast_tier_rule_id` 不動（131-140 自然落 slow 側），但要加測試釘住。
- 有「立即掃描」（共享 scan-now）。
- **分檔靠多條規則**（Rule 1 = 100%、Rule 2 = 300%），各自去重、各自觸發，郵件中心按 rule_id 訂閱；
  不另做 severity。

### 規則參數 `IntradayReturnRule`（最多 10 條，`MAX_RULES`）

| 參數 | 類型 / 範圍 | 默認 | 含義 |
|---|---|---|---|
| `name` | str 1-100 | — | 快照進 `rule_label` |
| `enabled` | bool | true | 單條停車 |
| `min_return_pct` | float 10–100000 | 100 | `intraday_profit / initial_equity × 100 ≥ 此值` |
| `min_initial_equity_usd` | float 0–1e6 | 50 | 分母地板（CEN ÷100 後比） |
| `min_profit_usd` | float 0–1e7 | 100 | 分子地板 |
| `include_floating` | bool | true | 分子是否含當前浮動（同快速獲利） |

`IntradayReturnConfig = {enabled: bool, rules: [...]}`，與 `MartingaleConfig` 同形。

**固定口徑（不做參數，寫進 InfoHeader tooltip + docs）**：
- 交易日 = MT 服務器日（與 `EquityPrevDay` 歸零時點對齊）。
- 初始權益 = 昨日日終權益 + 當日入金（正向 balance 操作，含內轉入；**出金不減分母**）。
  分母 ≤ 0 一律跳過不報（不再有 ÷0）。
  > 待用戶拍板：`include_deposits_in_base` 做參數還是固定 true。建議固定。
- 當日盈利 = 當日已平倉 profit+swap+commission + 當前浮動 profit+swap。
- CEN：比率免換算，兩個 USD 地板 ÷100，手數 ÷100；貨幣權威 `get_account_info_map()`。
- 排除：demo/test 組、MT4 login 7 開頭、`RISK_MONITOR_EXCLUDED_LOGINSIDS`（`excluded_login_sql`）。

### 檢測流程（每 tick，三台服務器各一遍）

1. 候選 = 當日有成交（開或平）的 login ∪ 當前有持倉的 login。
2. 分母：MT5 讀 `mt5_users.EquityPrevDay`；MT4 對候選按主鍵點查 `mt4_daily`。
3. 分子：當日已平倉聚合 + 浮動聚合，按 login，只查候選。
4. 先過兩個地板，再算比率，再逐條規則比閾值。
5. **只對命中的帳戶**再拉當日成交明細算三個行為特徵（筆數、中位持倉秒、鎖倉占比）。
   鎖倉占比定義：同品種多空同時持有且小邊 ≥ 大邊一半的時間 ÷ 有持倉的時間。
6. 去重鍵 `(rule_id, server, login, trading_day)`：每規則每帳戶每交易日一條；
   重啟從 SQLite 回種當日已發（照 `get_recent_quick_profit_alerts` 的模式），否則重啟整批重發。

### 落庫 `alert_intraday_return_detail`（主表 23 列不動，OPT-0008 五步 LEFT JOIN）

`trading_day` · `prev_day_equity` · `deposits_today` · `initial_equity` ·
`closed_pnl_today` · `floating_pnl` · `intraday_profit` · `return_pct`（觸發時快照，不覆蓋）·
`trades_today` · `median_hold_sec` · `lock_pct` · `top_symbol`。

主表映射：`order_count` = 當日筆數、`total_lots` = 當日手數、`first_open`/`last_open` = 當日首末成交、
enrichment 走 `get_account_info_map()`（equity/balance/group/currency/zipcode/net_deposit_hist）。
`return_pct` 要可排序 → 後端 `SORTABLE_ALERT_COLS` + `_SORT_COL_DB_NAME`、前端 `SORTABLE_COL_IDS`。

### 郵件

`MAIL_SOURCES["intraday_return"]`，band (131,140)，realtime。照最新範例 `services/alert_mail/rebate_arb.py`
（4 個 fetch_* + template_builder + registry entry）。可過濾字段：`return_pct` / `intraday_profit` /
`initial_equity` / `trades_today` / `lock_pct`。模版按 alert-email-style（英文正文、雙語標題、無 emoji、
MT/HK 雙時間、CRM 連結）：帳戶信息 + 初始權益拆解 + 當日盈虧拆解 + 收益率 + 三個行為特徵。
上線 seed 兩條訂閱：風控郵箱訂 131+132、CS 郵箱只訂 132（收件人待用戶給）。

### 前端

新 tab，`useGridColumnPersist` + `ColumnVisibilityMenu` + `useFilterPersist`（key 命名須匹配
`^[A-Z0-9_]+_(GRID_STATE|FILTERS|AGGREGATED|ACTIVE_TAB)_V\d+$`），列全顯式 `colId`，
`InfoHeader` 解釋公式。匯總卡三張：今日命中帳戶數 / 最高收益率 / 命中帳戶當日盈利合計。
config drawer 復用 page-style-conventions §9 每規則卡片。

## 假設 / 待驗證

- [ ] `include_deposits_in_base` 固定 true（建議）還是做參數 —— 用戶拍板
- [ ] 郵件收件人：風控 + CS 具體地址
- [ ] Sammy 對「分母加當日入金 + 50/100 USD 地板 + 100% 匯總、300% 即發」的回覆（回信草稿已發 Kieran 審）
- [ ] MT4 候選帳戶 `mt4_daily` 主鍵點查在 slow tick 內的實測耗時（候選預估數百 login）
- [ ] `mt5_users.EquityPrevDay` 在週一（跨週末）的值是否為週五日終（預期是）

## 驗收標準

- [ ] 三台服務器 slow tick 內完成，tick 耗時記 DEBUG、命中才 INFO（OPT-0058 口徑）
- [ ] 分母 ≤ 0 跳過；兩個地板生效；CEN ÷100；rule_id override guard；同日去重 + 跨日重報；重啟回種
- [ ] `alert_intraday_return_detail` 落庫 + `/alerts` 拍扁返回 + `return_pct` 服務端排序
- [ ] 郵件源註冊 + anti-drift 測試 + test-send 可用；realtime 模式下命中即發
- [ ] 前端 tab 四個 hook 齊全，tsc/vitest 綠；配置 drawer 可增刪規則
- [ ] 例子帳戶 67044208 的 9/16 數據回放能命中 100% 與 300% 兩條（回放測試以相對時間種子）
- [ ] 測試種子時間戳全部相對 `datetime.now()`（OPT-0041）
- [ ] 回寫 risk-monitor skill（references 各文件 + Rule ID 表 131-140）+ docs/features/risk-monitor.md + alert-mail-center skill

## 筆記

- 工期估：後端檢測 + detail + 去重 2 天、郵件源 1 天、前端 2 天、測試部署 1 天 ≈ 6 個工作日；
  只上郵件不上 tab ≈ 3 天。
- 走 OPT 是用戶明示（2026-09-17）；按 tracker README 這本屬 net-new feature，先例 OPT-0030/0033/0046 同樣以 OPT 立項。
- 快速獲利（61-70）形狀最近但它是分鐘窗口 + 絕對金額，不硬改，新開一條。
- 現有馬丁規則（111）把 `lot_multiplier=1.0`、`min_add_count=3` 即可覆蓋「浮虧下同手數連加 ≥3 次」，
  不需要在本規則裡重做。

## 結果

（未開始）
