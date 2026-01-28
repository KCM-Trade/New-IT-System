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
1. 在 FastAPI 中编写中间件，为每个 incoming 请求生成 unique UUID。
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
