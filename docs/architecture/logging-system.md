# 后端日志系统设计文档

> **状态**: 已实施 ✅  
> **最后更新**: 2026-01-29  
> **版本**: v2.0 (含文件持久化)

---

## 1. 概述

本项目采用标准化的全链路日志系统，支持：

- **统一日志格式**：`[时间] [级别] [trace_id] [模块:行号] - 消息`
- **请求追踪**：每个请求自动生成 Trace ID，便于问题排查
- **文件持久化**：日志保存 30 天，容器重启不丢失
- **Docker 友好**：同时输出到 stdout 和文件

---

## 2. 日志架构

### 2.1 核心文件

| 文件 | 说明 |
|------|------|
| `backend/app/core/logging_config.py` | 日志配置中心 |
| `backend/app/core/trace_middleware.py` | Trace ID 中间件 |
| `backend/logs/` | 日志文件存储目录 |

### 2.2 日志等级规范

| 等级 | 场景 | 生产环境 |
|------|------|----------|
| **DEBUG** | 详细调试信息、SQL 参数 | 默认关闭 |
| **INFO** | 关键业务节点、请求进入/完成 | 开启 |
| **WARNING** | 非致命异常、缓存失败 | 开启 |
| **ERROR** | 业务错误、数据库连接失败 | 开启 |
| **CRITICAL** | 系统级崩溃 | 开启 |

### 2.3 日志格式

```
[2026-01-29 10:30:15] [INFO] [req-a1b2c3d4] [app.services.clickhouse_service:165] - PnL analysis request: start=2026-01-01, end=2026-01-29
```

组成部分：
- `[时间戳]` - 精确到秒
- `[级别]` - DEBUG/INFO/WARNING/ERROR/CRITICAL
- `[trace_id]` - 请求追踪 ID (格式: `req-xxxxxxxx`)
- `[模块:行号]` - 代码位置
- `消息` - 日志内容

---

## 3. 文件持久化配置

### 3.1 轮转策略

| 配置项 | 值 | 说明 |
|--------|-----|------|
| 轮转周期 | 每天午夜 | `when="midnight"` |
| 保留天数 | 30 天 | `backupCount=30` |
| 文件命名 | `backend.log.YYYY-MM-DD` | 按日期后缀 |
| 存储位置 | `backend/logs/` | 挂载到宿主机 |

### 3.2 目录结构

```
backend/
├── logs/
│   ├── .gitkeep              # Git 保留空目录
│   ├── backend.log           # 当天日志
│   ├── backend.log.2026-01-28
│   ├── backend.log.2026-01-27
│   └── ...                   # 最多 30 天
```

### 3.3 Docker 挂载配置

```yaml
# docker-compose.dev.yml
services:
  api:
    volumes:
      - ./logs:/app/logs  # 日志持久化
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "10"
```

---

## 4. Trace ID 追踪

### 4.1 工作原理

1. 请求进入时，中间件生成唯一 UUID：`req-xxxxxxxx`
2. 存入 `contextvars`（线程安全）
3. 所有日志自动携带此 ID
4. 响应头返回 `X-Trace-ID`

### 4.2 使用场景

当用户报告错误时：
1. 获取前端显示的 Trace ID
2. 在日志中搜索该 ID
3. 找到完整的请求链路日志

```bash
# 搜索特定请求的所有日志
grep "req-a1b2c3d4" backend/logs/backend.log*
```

---

## 5. 代码使用示例

### 5.1 在 Service 中使用

```python
from app.core.logging_config import get_logger

logger = get_logger(__name__)

class MyService:
    def do_something(self):
        logger.info("Starting operation")
        try:
            # ... business logic ...
            logger.debug(f"Processing data: {data}")
        except Exception as e:
            # exception() 自动包含堆栈信息
            logger.exception("Operation failed")
            raise
```

### 5.2 在 Router 中使用

```python
from app.core.logging_config import get_logger

logger = get_logger(__name__)

@router.get("/data")
def get_data():
    logger.info("Received request for data")
    # ...
```

### 5.3 日志级别指南

```python
# DEBUG: 详细调试信息 (仅开发环境)
logger.debug(f"SQL params: {params}")

# INFO: 关键业务节点
logger.info(f"User {user_id} logged in")

# WARNING: 非致命异常
logger.warning(f"Redis cache miss, falling back to DB")

# ERROR: 业务错误 (需要关注)
logger.error(f"Failed to process order: {order_id}")

# EXCEPTION: 错误 + 堆栈信息 (用于 except 块)
logger.exception("Unexpected error occurred")
```

---

## 6. 配置管理

### 6.1 环境变量

```bash
# backend/.env
LOG_LEVEL=INFO    # 可选: DEBUG, INFO, WARNING, ERROR, CRITICAL
```

### 6.2 动态调整日志级别

```bash
# 1. 修改 .env 文件
LOG_LEVEL=DEBUG

# 2. 重启容器
docker compose -f docker-compose.dev.yml restart api
```

---

## 7. 运维指南

详见 [后端LOG.md](./后端LOG.md)

---

## 8. 注意事项

### 8.1 禁止事项

- ❌ 不要使用 `print()` 输出日志
- ❌ 不要在代码中调用 `logging.basicConfig()`
- ❌ 不要在日志中使用 emoji
- ❌ 不要记录敏感信息（密码、Token）

### 8.2 最佳实践

- ✅ 使用 `get_logger(__name__)` 获取模块级 logger
- ✅ 异常使用 `logger.exception()` 自动记录堆栈
- ✅ 关键操作前后记录 INFO 日志
- ✅ 性能敏感的详细日志使用 DEBUG 级别

---

## 9. 故障排查

### 9.1 日志未生成

1. 检查 `LOG_LEVEL` 是否正确
2. 检查 `/app/logs` 目录权限
3. 检查 Docker 挂载是否正确

### 9.2 日志格式异常

1. 确认未调用 `logging.basicConfig()`
2. 确认 `main.py` 中 `setup_logging()` 在其他导入之前

### 9.3 Trace ID 为 "-"

- 启动日志正常（无请求上下文）
- 非 HTTP 请求触发的日志
