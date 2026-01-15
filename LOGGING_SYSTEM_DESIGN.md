# 全栈项目日志系统设计方案 (React + FastAPI)

## 1. 概述与目标
本方案旨在将现有的零散 `print()` 调试信息升级为专业、标准化、结构化的全链路日志系统。通过统一的日志管理，实现问题的快速定位、性能监控以及生产环境的异常追踪。

### 核心目标
- **标准化**：替换所有 `print()`，统一日志格式。
- **可配置**：支持不同环境（开发/生产）的日志级别动态切换。
- **全链路追踪**：引入 Trace ID，打通前端请求到后端服务的完整链路。
- **易排查**：日志包含文件名、行号及完整错误堆栈。

---

## 2. 后端日志架构 (FastAPI)

### 2.1 日志等级规范
| 等级 | 场景 | 生产环境 |
| :--- | :--- | :--- |
| **DEBUG** | 详细的开发调试信息、SQL 参数、Redis Key 等 | 默认关闭 |
| **INFO** | 关键业务节点（API 请求进入、任务完成、用户登录） | 开启 |
| **WARNING** | 非致命异常（请求超时重试、配置缺失默认值） | 开启 |
| **ERROR** | 业务逻辑错误、数据库连接失败（需记录堆栈） | 开启 |
| **CRITICAL** | 系统级崩溃、服务不可用 | 开启 |

### 2.2 日志格式设计 (标准非 Emoji 风格)
```text
[时间戳] [日志级别] [请求ID] [文件名:行号] - 消息内容
```
**示例：**
`[2026-01-15 08:30:12.456] [ERROR] [req-550e8400] [clickhouse_service:184] - 字段 'ut.tagid' 无法解析`

### 2.3 关键实施步骤

#### 第一步：全局配置 (`backend/app/core/logging_config.py`)
创建一个中央配置文件，利用 Python 标准库 `logging` 的 `DictConfig` 或 `loguru` 进行初始化。
- 配置 `StreamHandler` 输出到控制台（Docker 收集）。
- 配置 `RotatingFileHandler`（可选）输出到本地文件。
- 根据环境变量 `LOG_LEVEL` 自动过滤。

#### 第二步：项目入口初始化
在 `backend/app/main.py` 中，在 `create_app` 启动前调用初始化函数。

#### 第三步：业务代码规范
- **禁止使用 `print()`**。
- 每个模块顶部声明：`logger = logging.getLogger(__name__)`。
- 异常捕获必须包含堆栈：使用 `logger.exception("详细描述")`。

---

## 3. 全链路追踪 (Trace ID)

### 3.1 后端中间件实现
1. 在 FastAPI 中编写中间件，为每个 incoming 请求生成唯一 UUID。
2. 将该 UUID 存入 `contextvars`（线程安全）。
3. 在日志 Formatter 中提取该 UUID 并打印。
4. 在 Response Header 中返回 `X-Trace-ID` 给前端。

### 3.2 价值
当用户反馈报错时，只需提供前端收到的 Trace ID，开发者可以在后端日志中一键搜索出该请求涉及的所有数据库查询和逻辑步骤。

---

## 4. 前端日志策略 (React)

### 4.1 异常捕获
- **React Error Boundary**：捕获组件层面的崩溃，并在渲染降级 UI 的同时上报错误。
- **Axios 拦截器**：自动记录所有非 2xx 的响应，并提取 Header 中的 `X-Trace-ID`。

### 4.2 日志回传 (可选)
建立 `/api/v1/logs/client` 接口，接收前端捕获的关键错误。
**上报内容：**
- 浏览器版本/操作系统
- 错误消息与堆栈
- 关联的后端 Trace ID
- 发生错误时的前端状态（Store Snapshot）

---

## 5. 快速排查指南 (Ops)

1. **定位特定错误**：`grep "ERROR" backend.log`
2. **追踪完整流程**：`grep "TRACE_ID_HERE" backend.log`
3. **实时监控**：使用 `tail -f` 观察生产环境关键节点日志。
4. **性能分析**：通过 DEBUG 级别的 SQL 时间戳差异计算查询耗时。

---

## 6. 后续扩展方向
- **日志聚合**：引入 **Grafana Loki** 或 **ELK (Elasticsearch, Logstash, Kibana)**。
- **主动告警**：对日志中的 `ERROR` 级别设置钉钉或邮件告警规则。
- **审计日志**：对敏感操作（如删除数据、修改权限）记录独立的审计日志。

---

## 7. 技术专项：IB 报表组别动态管理方案

### 7.1 需求背景
为了解决前端硬编码组别导致的维护困难，并提供实时的组别用户量统计，设计此动态加载与缓存方案。

### 7.2 后端设计 (ClickHouse + Python Cache)
- **数据源**：
    - 组别定义：`"KCM_fxbackoffice"."fxbackoffice_tags"` (categoryId = 6)
    - 用户关联：`"KCM_fxbackoffice"."fxbackoffice_user_tags"`
- **缓存策略**：
    - 使用 Python 内存对象缓存查询结果。
    - **有效期**：7 天。
    - **数据结构**：包含 `tag_id`, `tag_name`, `user_count`, `last_update_time`, `previous_update_time`。
- **性能优化**：通过 `GROUP BY tagId` 一次性完成所有组别的人数统计，避免 N+1 查询。

### 7.3 前端设计 (React + shadcn/ui)
- **交互方式**：
    - Popover 底部新增“查看所有组别”按钮。
    - 弹出 Dialog 展示所有组别的详细列表（名称、人数）。
- **持久化**：
    - “常用组别”存储于浏览器的 `localStorage` 中。
    - 用户可以在 Dialog 中通过“星标”快速切换常用状态。

### 7.4 交互逻辑详解 (Filtering Logic)

#### 1. 快捷选择器 (Popover Dropdown)
- **展示内容**：显示“常用组别”与“当前已选中组别”的**并集**。这意味着任何在全量弹窗中勾选的组别，都会自动出现在快捷菜单中。
- **视觉标识**：
    - **金星图标**：标识该组别为“常用”，通过点击组别名旁的星标切换。
    - **蓝色高亮**：标识该组别当前已被选中参与报表计算。
- **按钮逻辑**：
    - **清空**：一键清空所有已选组别（`selectedGroups = []`）。
    - **全选常用**：快速选中所有被标记为“常用”的组别。
    - **查看所有组别**：打开全量详情弹窗。

#### 2. 全量详情弹窗 (Dialog Overview)
- **实时同步**：弹窗内的选择状态与主页面报表状态实时联动。在弹窗中勾选 `CheckSquare`，报表数据会同步变化。
- **元数据展示**：
    - **MT Server Time**：显示数据最后一次从 MetaTrader 服务器同步的时间（数据源更新时间）。
    - **数据状态**：显示“数据更新于：时间 (上一次：时间)”，若无历史记录则显示 N/A。
- **搜索过滤**：支持对 60+ 个组别进行前端实时文本检索。
- **大小写兼容**：所有匹配逻辑（选中、收藏、过滤）均采用 `toLowerCase()` 处理，自动兼容数据库与前端可能存在的大小写差异（如 `HZL` vs `hzl`）。

### 7.5 待办事项：结束 Mock 阶段 (Next Steps)
目前报表主体数据处于 Mock 阶段（读取本地 `ib_report_mock.csv`），后续需执行以下步骤实现生产切换：

1.  **后端 SQL 补全**：在 `clickhouse_service.py` 中编写真实的报表聚合 SQL，替代现有的模拟逻辑。
2.  **API 联调**：将前端 `handleSearch` 函数中的 `fetch` 地址由 `.csv` 路径更改为正式的后端 API 接口。
3.  **参数传递**：确保前端将 `date_range` (开始/结束日期) 和 `selectedGroups` (已选组别列表) 作为请求参数发送至后端。

### 7.4 安全与规范
- **连接安全**：使用生产环境专用的环境变量 `CLICKHOUSE_prod_*`。
- **大小写敏感**：SQL 语句中表名必须使用双引号包裹，如 `"fxbackoffice_tags"`。
