## SwapFree Control 前端说明（2×2 布局 + API 接入）

### 布局与模块
- 第一行：
  - 无边框卡（左）：Current Zipcode Distribution（表格）
  - 有边框卡（右）：Zipcode Change Logs（日期范围控件 + 表格）
- 第二行：
  - 有边框卡（左）：Exclude Group（交互栏 + 表格）
  - 有边框卡（右）：Client Change Frequency（表格）

### UI 要点
- 全部卡片固定高度：600px；桌面 2×2，窄屏自动纵向堆叠。
- 表格统一斑马纹（odd:bg-muted/50），默认英文文案。
- Zipcode Change Logs 使用 Shadcn Popover + Calendar 的日期范围选择（移动端 1 月，桌面端 2 月）。
- 标题对齐与栅格间距在移动端下收紧（px-3, gap-4），减少留白。
- Distribution 表第二列列头为“Client count”。

### 已接入 API
- GET `/api/v1/zipcode/distribution`
  - 说明：统计启用客户（is_enabled=1）的 zipcode 分布；当某 zipcode 的人数 < 10 时，额外返回 `client_ids`。
  - 返回：`{ ok: boolean, data: Array<{ zipcode: string, client_count: number, client_ids?: number[] }>, rows: number }`
  - 前端行为：
    - 若存在任意行带 `client_ids`，第三列表头显示为“Client IDs (<10)”并渲染客户ID列表；否则列头为“Notes”（留空）。
    - 页面加载时自动调用并刷新数据；失败优雅降级为空表/错误信息。
  - 示例：

```bash
curl -X GET "http://localhost:8001/api/v1/zipcode/distribution"
```

```json
{
  "ok": true,
  "rows": 3,
  "data": [
    { "zipcode": "111", "client_count": 8000 },
    { "zipcode": "222", "client_count": 500 },
    { "zipcode": "90",  "client_count": 7, "client_ids": [1001,1002,1003,1004,1005,1006,1007] }
  ]
}
```

### 预留/待接入 API（后续可逐块串联）
- Zipcode Change Logs（日志列表）
  - GET `/api/v1/zipcode/changes`
  - 参数：
    - `start`, `end`（可选，timestamptz 字符串；不传默认过去 25 小时窗口）
    - `page`, `page_size`（默认 1/50；最大 1000）
  - 返回：`{ ok, rows, page, page_size, data: [{ client_id, zipcode_before, zipcode_after, change_reason, change_time }] }`
  - 示例：
```bash
curl "http://localhost:8001/api/v1/zipcode/changes?start=2025-01-01%2000:00:00&end=2025-01-02%2000:00:00&page=1&page_size=50"
```

- Exclude Group（排除清单列表）
  - GET `/api/v1/zipcode/exclusions`
  - 参数：`is_active`（可选，true/false）
  - 返回：`{ ok, rows, data: [{ id, client_id, reason_code, added_by, added_at, expires_at, is_active }] }`
  - 示例：
```bash
curl "http://localhost:8001/api/v1/zipcode/exclusions?is_active=true"
```

- Client Change Frequency（变更频次）
  - GET `/api/v1/zipcode/change-frequency`
  - 参数：`window_days`（默认 30, 1–365）、`page`、`page_size`
  - 返回：`{ ok, rows, page, page_size, window_days, data: [{ client_id, changes, last_change }] }`
  - 示例：
```bash
curl "http://localhost:8001/api/v1/zipcode/change-frequency?window_days=30&page=1&page_size=50"
```

### 数据来源与库表
- Postgres 数据库（默认 `MT5_ETL.public`）：
  - `pnl_client_summary`：zipcode 分布读取源（启用客户）
  - `swapfree_zipcode_changes`：zipcode 变更日志（Change Logs、Frequency）
  - `swapfree_exclusions`：排除清单（Exclude Group）

### 接入与运行
- 确保后端已启动（默认端口 8001），前端页面 `SwapFreeControl.tsx` 将在挂载时请求 `/api/v1/zipcode/distribution`。
- 若需要串接三块卡片数据，按上述 API 逐块对接即可；失败时建议保底空状态（不影响其余模块）。

### 后续可选优化
- Distribution Top-N + OTHERS 聚合（长尾场景，便于可视化）。
- Change Logs 增量拉取与导出 CSV（加下载按钮）。
- Exclude Group 写接口（Add/Disable/Enable），含后端审计与权限校验。


