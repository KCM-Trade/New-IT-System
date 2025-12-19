# Demo 盈亏监控 (Preview) 功能集成总结

本文档汇总了 "Demo 盈亏监控 (Preview)" 功能的开发背景、技术实现、文件变动及配置说明。该功能旨在提供一个基于 ClickHouse 数据库的即席查询（Ad-hoc Query）页面，用于分析指定时间范围内的客户交易盈亏情况。

## 1. 功能概述

*   **目标**：快速集成 ClickHouse 数据库，提供实时的客户盈亏分析报表。
*   **特性**：
    *   **即席查询**：用户可选择任意时间范围（如过去1周、1月）。
    *   **直连 ClickHouse**：后端绕过 ETL 流程，直接查询 ClickHouse 聚合数据。
    *   **预览版限制**：为了演示目的，查询结束日期被强制锁定（如 2024-12-13），以匹配数据库中的静态/测试数据。
    *   **UI 一致性**：复用现有系统的 AG Grid 表格与筛选组件风格。

## 2. 文件清单

### 后端 (Backend - FastAPI)

| 文件路径 | 类型 | 用途 |
| :--- | :--- | :--- |
| `backend/app/services/clickhouse_service.py` | **新增** | 核心服务层。负责连接 ClickHouse，执行 SQL 查询，处理 DataFrame。 |
| `backend/app/api/v1/routes/client_pnl_analysis.py` | **新增** | API 路由层。定义 HTTP 接口，接收参数并调用 Service。 |
| `backend/app/api/v1/routers.py` | 修改 | 注册新的 API 路由模块。 |
| `backend/requirements.txt` | 修改 | 添加 `clickhouse-connect` 依赖。 |

### 前端 (Frontend - React)

| 文件路径 | 类型 | 用途 |
| :--- | :--- | :--- |
| `frontend/src/pages/ClientPnLAnalysis.tsx` | **新增** | 页面组件。包含时间筛选、查询按钮、Banner 提示及 AG Grid 表格。 |
| `frontend/src/components/app-sidebar.tsx` | 修改 | 添加侧边栏菜单项 "Demo 盈亏监控 (Preview)"。 |
| `frontend/src/App.tsx` | 修改 | 注册前端路由 `/client-pnl-analysis`。 |

## 3. 技术架构与数据流

```mermaid
graph LR
    User[用户] -->|1. 选择时间 & 点击查询| FE[前端页面 (React)]
    FE -->|2. GET /api/v1/client-pnl-analysis/query| BE[后端 API (FastAPI)]
    BE -->|3. 调用 ClickHouseService| Service[Service 层]
    Service -->|4. SQL Query (SSL/8443)| DB[(ClickHouse Cloud)]
    DB -->|5. 返回聚合数据| Service
    Service -->|6. Pandas DataFrame 处理| BE
    BE -->|7. JSON Response| FE
    FE -->|8. 渲染 AG Grid| User
```

## 4. 环境配置

需要在 `backend/.env` 中配置 ClickHouse 连接信息：

```ini
# ClickHouse Database Configuration
CLICKHOUSE_HOST=your-clickhouse-host.clickhouse.cloud
CLICKHOUSE_PORT=8443
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=your_password
CLICKHOUSE_DB=Fxbo_Trades
```

**依赖安装**：
确保后端环境安装了驱动：
```bash
pip install clickhouse-connect
```

## 5. 前端逻辑详解

### 页面入口
*   **路由**：`/client-pnl-analysis`
*   **菜单**：Sidebar -> Risk Control -> Demo 盈亏监控 (Preview)

### 交互逻辑
1.  **初始状态**：进入页面时不自动加载数据，显示占位提示。
2.  **Banner 提示**：顶部黄色警告条，提示“快速预览版 — 当前演示数据截止至 2024-12-13”。
3.  **时间计算**：
    *   用户选择“过去 1 个月”。
    *   前端逻辑强制设定 `end_date = 2024-12-13` (Demo 限制)。
    *   推算 `start_date = 2024-11-13`。
4.  **表格展示**：
    *   列：Client ID, Client Name, Total Trades, Total Volume, Total PnL, Commission, Swap。
    *   样式：正负值颜色区分（红/绿），货币格式化。

## 6. 后端逻辑详解

### Service 层 (`clickhouse_service.py`)
*   **连接管理**：使用 `clickhouse_connect` 建立连接，强制开启 `secure=True` (TLS)。
*   **SQL 逻辑**：
    *   使用 `WITH` 子句预计算 IB 佣金。
    *   关联 `mt4_trades`, `mt4_users`, `users` 表。
    *   过滤条件：排除测试号 (`userId=0`) 和员工号 (`isEmployee=1`)。
    *   时间过滤：基于前端传入的 `start_date` 和 `end_date`。
*   **数据清洗**：
    *   使用 Pandas `fillna(0)` 处理空值。
    *   格式化 `client_name` 为 "Name (Account)" 格式。

### API 层 (`client_pnl_analysis.py`)
*   **接口**：`GET /query`
*   **参数**：
    *   `start_date` (YYYY-MM-DD)
    *   `end_date` (YYYY-MM-DD)
    *   `search` (可选，模糊匹配 ID 或 Name)

## 7. 数据库 SQL 逻辑摘要

```sql
WITH ib_costs AS (...)
SELECT
    t.LOGIN AS Account,
    m.userId AS client_id,
    any(m.NAME) AS client_name,
    countIf(t.CMD IN (0, 1)) AS total_trades,
    sumIf(t.lots, t.CMD IN (0, 1)) AS total_volume_lots,
    sumIf(t.PROFIT + t.SWAPS + t.COMMISSION, t.CMD IN (0, 1)) AS total_profit_usd,
    sumIf(t.SWAPS, t.CMD IN (0, 1)) AS total_swap_usd,
    COALESCE(sum(ib.total_ib_cost), 0) AS total_commission_usd
FROM fxbackoffice_mt4_trades AS t
-- ... JOINS ...
WHERE 
    t.CLOSE_TIME >= %(start_date)s 
    AND t.CLOSE_TIME <= %(end_date)s
    AND t.CMD IN (0, 1, 6)
-- ... GROUP BY & ORDER BY ...
```

