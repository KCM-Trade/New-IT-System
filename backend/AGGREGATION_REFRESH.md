## 背景与目标

- **需求**: 聚合刷新无需命令行；前端在“时区”旁新增“刷新”按钮，一键触发后端计算并写入最新数据；所有用户共享“上次刷新时间”显示。默认日期范围为“上次刷新日期当日 - 一周前”，避免一次性加载过多数据。

## 后端变更摘要

- **新增服务函数**
  - 文件: `backend/app/services/aggregation_service.py`
    - `refresh_aggregations(settings, symbol="XAUUSD")`:
      - 从公共导出目录读取两份 NDJSON：`profit_xauusd_hourly.json`（open）与 `profit_xauusd_hourly_close.json`（close），解析出最新 `(date, hour)`（UTC+3）。
      - 计算 `start = 最新小时 + 1h（UTC+3）`；`end = 当前UTC时间转换为UTC+3`。
      - 先后调用已有 `aggregate_to_json(settings, symbol, start, end, basis='open'/'close')` 写入增量数据；合并策略保持不变（新窗口覆盖旧数据）。
      - 将 `end` 写入公共标记文件 `frontend/public/profit_last_refresh.txt`，供前端与所有用户共享显示。
      - 返回结构包含 `ok`、`open`、`close`、`refreshed_at`。
    - 时区实现：使用 `ZoneInfo("Etc/GMT-3")` 表示 UTC+3；先在 UTC 取 “现在”，再转 UTC+3，确保与 JSON 的时区口径一致。

- **新增/调整 API**
  - 文件: `backend/app/api/v1/routes/aggregations.py`
    - 已有: `POST /api/v1/aggregate/to-json`（无改动）
      - 入参 `AggregateRequest`：`symbol`、`start`、`end`、`basis: 'open'|'close'`
      - 出参 `AggregateResponse`：`ok`、`json`、`rows`、`error`
    - 新增: `POST /api/v1/aggregate/refresh`
      - 作用：自动从 JSON 取起点、以“当前UTC→UTC+3”为终点，刷新 open/close 两份聚合。
      - 出参 `RefreshResponse`：`ok`、`open{start,end,result}`、`close{start,end,result}`、`refreshed_at`
    - 新增: `GET /api/v1/aggregate/last-refresh`
      - 作用：读取 `profit_last_refresh.txt`，返回 `LastRefreshResponse{refreshed_at}`

- **新增/调整 Schemas**（`backend/app/schemas/aggregation.py`）
  - 新增 `RefreshInnerResult`、`RefreshResponse`（含 `refreshed_at`）、`LastRefreshResponse`。

## 后端使用方法

- **自动刷新（推荐）**

```bash
curl -X POST http://<host>:<port>/api/v1/aggregate/refresh
```

- **查询上次刷新时间**

```bash
curl http://<host>:<port>/api/v1/aggregate/last-refresh
```

- **手动精确窗口（如需）**

```bash
curl -X POST http://<host>:<port>/api/v1/aggregate/to-json \
  -H "Content-Type: application/json" \
  -d '{"symbol":"XAUUSD","start":"2025-08-01 00:00:00","end":"2025-08-07 23:59:59","basis":"open"}'
```

## 前端变更摘要（`frontend/src/pages/Profit.tsx`）

- **刷新按钮**
  - 在“时区”右侧新增“刷新”按钮：点击后 `POST /api/v1/aggregate/refresh`，完成后重新拉取 NDJSON 并更新页面。

- **上次刷新时间标签**
  - 新增状态 `lastRefreshed`；页面挂载与刷新后调用 `GET /api/v1/aggregate/last-refresh`，在刷新按钮右侧显示 “上次刷新(UTC+3)：{时间}”。

- **默认时间范围逻辑**
  - 页面初次进入或返回时：
    - 先获取 `lastRefreshed`，将 `range.to` 设为“上次刷新日期（UTC+3 的日粒度）”，`range.from = range.to - 7 天`。
    - 仅当 `range` 有效（from/to 均存在）时，才触发首轮数据加载，避免落到“全量数据”。
  - 刷新后亦会按最新 `lastRefreshed` 同步重设 `range`（一周窗口）。

- **数据加载与渲染**
  - 依据 `aggType` 拉取 NDJSON：
    - open: `/profit_xauusd_hourly.json`
    - close: `/profit_xauusd_hourly_close.json`
  - NDJSON 每行对象形如：`{date: 'YYYY-MM-DD', hour: 0-23, profit: number}`；均以 UTC+3 口径写入。
  - 转换规则：将源数据（UTC+3）换算成 UTC 时间戳，再按用户所选时区（UTC+3/UTC+8）渲染。
  - 页面标题更新为：筛选与视图（XAU-CNH）。

## 数据与时区约定

- 后端聚合输出（两份 NDJSON）字段：
  - `date`: ‘YYYY-MM-DD’、UTC+3 口径
  - `hour`: 0-23、UTC+3 口径
  - `profit`: 数值
- 合并策略：DuckDB 合并时以 `(date, hour)` 作为键，新聚合覆盖旧值，最终写回 NDJSON。
- 存储位置由 `Settings.public_export_dir` 决定，默认为 `frontend/public/`：
  - `profit_xauusd_hourly.json`（open）
  - `profit_xauusd_hourly_close.json`（close）
  - `profit_last_refresh.txt`（刷新标记）

## 端到端流程（刷新按钮）

1) 用户点击“刷新” →
2) 后端自动计算窗口（`start=上次小时+1h@UTC+3`，`end=当前UTC转UTC+3`）并增量聚合 open/close →
3) 写入 NDJSON 与 `profit_last_refresh.txt` →
4) 前端读取 `last-refresh` 并更新“上次刷新时间”，重设一周日期范围 →
5) 拉取对应 NDJSON，渲染图表与汇总。

## 注意事项

- 所有时间计算在后端统一按 UTC → UTC+3 转换；JSON 内存储口径固定为 UTC+3。
- 前端首次获取数据前，务必等待 `range` 初始化完成（避免加载全量数据）。
- 若需要支持更多品种或公共标记多品种区分，可将 `profit_last_refresh.txt` 扩展为每品种独立文件或 JSON 索引。


