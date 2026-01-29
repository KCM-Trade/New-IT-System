# 后端日志运维指南

> **最后更新**: 2026-01-29

---

## 1. 快速命令

### 1.1 实时查看日志

```bash
# 方式1: 通过 Docker (推荐)
docker logs -f new-it-backend-dev

# 方式2: 通过文件
tail -f /opt/myproject/New-IT-System/backend/logs/backend.log
```

### 1.2 查看最近日志

```bash
# 最近 100 行
docker logs --tail 100 new-it-backend-dev

# 最近 1 小时
docker logs --since "1h" new-it-backend-dev

# 最近 24 小时
docker logs --since "24h" new-it-backend-dev
```

### 1.3 查看历史日志

```bash
# 进入日志目录
cd /opt/myproject/New-IT-System/backend/logs

# 查看特定日期
cat backend.log.2026-01-28

# 查看所有日志文件
ls -lh backend.log*
```

---

## 2. 搜索过滤

### 2.1 按级别过滤

```bash
# 只看 ERROR
grep "\[ERROR\]" backend/logs/backend.log

# 只看 WARNING 和 ERROR
grep -E "\[(WARNING|ERROR)\]" backend/logs/backend.log*

# 排除 DEBUG (生产环境一般不会有)
grep -v "\[DEBUG\]" backend/logs/backend.log
```

### 2.2 按 Trace ID 追踪

```bash
# 追踪特定请求的完整链路
grep "req-a1b2c3d4" backend/logs/backend.log*

# 统计某个请求的日志行数
grep -c "req-a1b2c3d4" backend/logs/backend.log
```

### 2.3 按模块过滤

```bash
# 只看 ClickHouse 服务日志
grep "clickhouse_service" backend/logs/backend.log

# 只看 API 路由日志
grep "routes" backend/logs/backend.log
```

### 2.4 按时间过滤

```bash
# 查看今天 10:00-11:00 的日志
grep "2026-01-29 10:" backend/logs/backend.log

# 查看最近 30 分钟内的错误
grep "\[ERROR\]" backend/logs/backend.log | tail -50
```

---

## 3. 统计分析

### 3.1 错误统计

```bash
# 每天错误数
for f in backend/logs/backend.log*; do 
  echo "$(basename $f): $(grep -c '\[ERROR\]' $f 2>/dev/null || echo 0)"; 
done

# 错误类型分布
grep "\[ERROR\]" backend/logs/backend.log | cut -d'-' -f2- | sort | uniq -c | sort -rn | head -20
```

### 3.2 请求统计

```bash
# 统计请求数 (每行 "Request started" 代表一个请求)
grep -c "Request started" backend/logs/backend.log

# 统计各状态码分布
grep "Request completed" backend/logs/backend.log | grep -oP "status=\d+" | sort | uniq -c
```

### 3.3 性能分析

```bash
# 查看慢请求 (>1000ms)
grep "Request completed" backend/logs/backend.log | grep -E "duration=[0-9]{4,}\."
```

---

## 4. 日志管理

### 4.1 查看磁盘占用

```bash
# 日志目录大小
du -sh /opt/myproject/New-IT-System/backend/logs/

# 各文件大小
ls -lh /opt/myproject/New-IT-System/backend/logs/
```

### 4.2 手动清理 (一般不需要)

日志会自动轮转保留 30 天。如需手动清理：

```bash
# 删除 30 天前的日志
find backend/logs/ -name "backend.log.*" -mtime +30 -delete

# 清空当前日志 (不推荐，会丢失数据)
# > backend/logs/backend.log
```

### 4.3 调整日志级别

```bash
# 编辑环境变量
vim /opt/myproject/New-IT-System/backend/.env

# 修改 LOG_LEVEL=DEBUG (或 INFO/WARNING/ERROR)

# 重启容器生效
cd /opt/myproject/New-IT-System/backend
docker compose -f docker-compose.dev.yml restart api
```

---

## 5. 日志格式说明

### 5.1 标准格式

```
[2026-01-29 10:30:15] [INFO] [req-a1b2c3d4] [app.services.clickhouse_service:165] - Message here
```

| 字段 | 说明 |
|------|------|
| `[2026-01-29 10:30:15]` | 时间戳 |
| `[INFO]` | 日志级别 |
| `[req-a1b2c3d4]` | 请求 Trace ID |
| `[app.services.clickhouse_service:165]` | 模块名:行号 |
| `Message here` | 日志内容 |

### 5.2 Trace ID 说明

- 格式：`req-xxxxxxxx` (8位十六进制)
- 同一请求的所有日志共享相同的 Trace ID
- 启动日志的 Trace ID 为 `-`（无请求上下文）
- 响应头 `X-Trace-ID` 可用于前端关联

---

## 6. 常见问题

### Q1: 日志文件在哪里？

```bash
/opt/myproject/New-IT-System/backend/logs/backend.log
```

### Q2: 如何查看实时日志？

```bash
docker logs -f new-it-backend-dev
# 或
tail -f /opt/myproject/New-IT-System/backend/logs/backend.log
```

### Q3: 如何找到某个用户的问题日志？

1. 获取用户操作时的 Trace ID（前端可显示）
2. `grep "req-xxxxx" backend/logs/backend.log*`

### Q4: 日志太多怎么办？

- 生产环境使用 `LOG_LEVEL=INFO`（默认）
- 只在排查问题时临时改为 `DEBUG`
- 日志自动保留 30 天

### Q5: 容器重启后日志丢失？

不会丢失。日志挂载到宿主机 `backend/logs/` 目录。

---

## 7. 告警建议 (未来扩展)

建议配置监控告警：

| 场景 | 阈值 | 动作 |
|------|------|------|
| ERROR 日志 | >10/分钟 | 钉钉/邮件告警 |
| 请求延迟 | >5秒 | 记录慢查询 |
| 日志磁盘 | >80% | 检查轮转配置 |

可使用 Grafana Loki 或 ELK 实现集中日志管理。
