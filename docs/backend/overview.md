后端项目速览（FastAPI v1）

这份文档用于让 AI 与新同学快速理解本后端的结构、运行方式与主要功能。阅读完可立即在本地/容器启动并调用关键 API（尤其是聚合 Aggregate）。

一、目录结构（What lives where）
- app/
  - main.py：创建并返回 FastAPI 应用，挂载 /api/v1 路由与 /static 静态资源。
  - api/v1/routers.py：集中挂载版本内子路由（统一纳入 /api/v1）。
  - api/v1/routes/：按领域拆分的接口文件
    - health.py：健康检查 GET /api/v1/health
    - aggregations.py：聚合接口（见下文详细说明）
    - trade_summary.py、open_positions.py：其他领域接口（结构与 aggregations 类似）
  - schemas/：请求/响应的数据模型（Pydantic）
    - aggregation.py：AggregateRequest / AggregateResponse
  - services/：业务逻辑与数据访问（DB 查询、DuckDB 聚合、文件导出等）
    - aggregation_service.py：聚合逻辑实现（MySQL -> Parquet -> DuckDB -> JSON）
  - core/config.py：配置加载（Settings 读取 .env，路径计算，CORS 等）
- public/：静态资源目录（由 app/main.py 以 /static 挂载），例如 Favicon-01.svg
- data/：默认的中间数据目录（Parquet 等），可由环境变量覆盖
- main.py（仓库根下）：ASGI 入口（uvicorn main:app），兼容部署/容器命令
- Dockerfile、docker-compose.dev.yml：容器化与开发环境（端口 8001）
- aggregate_close.py：独立脚本示例（基于 CLOSE_TIME 的聚合，非 API）

二、如何运行（Run）
- 开发（Docker 推荐）：
  1) 在仓库 backend 目录下：docker compose -f docker-compose.dev.yml up --build -d
  2) 访问健康检查：curl http://localhost:8001/api/v1/health
  3) Swagger 文档：打开浏览器 http://localhost:8001/docs
- 直接运行（本地 Python 环境）：
  1) 确保已配置 .env（见下文 Settings）
  2) uvicorn main:app --reload --host 0.0.0.0 --port 8001

三、配置（Settings & .env）
- 由 app/core/config.py 的 Settings 统一读取：
  - DB_HOST、DB_USER、DB_PASSWORD、DB_NAME、DB_PORT、DB_CHARSET：MySQL 连接信息
  - PARQUET_DIR：Parquet 输出目录（默认 <repo_root>/backend/data）
  - PUBLIC_EXPORT_DIR：导出 JSON 的目录（默认 <repo_root>/frontend/public）
  - CORS_ORIGINS：逗号分隔的来源白名单（默认 *）
- .env 在开发模式由 dotenv 自动加载（容器亦支持 env_file 注入）。

四、API 一览（Overview）
- 健康检查：
  - GET /api/v1/health -> {"status":"ok"}
- 聚合（Aggregate）：
  - POST /api/v1/aggregate/to-json
  - 请求模型（schemas/aggregation.py）：
    - AggregateRequest：{ symbol: str = "XAUUSD", start: str, end: str }
      - start/end 形如 "2025-05-01 00:00:00"
  - 响应模型：AggregateResponse：{ ok: bool, json?: str, rows?: int, error?: str }
  - 示例：
    curl -X POST "http://localhost:8001/api/v1/aggregate/to-json" \
      -H "Content-Type: application/json" \
      -d '{"symbol":"XAUUSD","start":"2025-05-01 00:00:00","end":"2025-08-31 23:59:59"}'

五、Aggregate 数据流（How it works）
1) 路由层 app/api/v1/routes/aggregations.py 接收请求，注入 Settings。
2) 调用 services/aggregation_service.py 的 aggregate_to_json(settings, symbol, start, end)：
   - 使用 Settings 中的 DB_* 连接 MySQL，按 symbol 与 OPEN_TIME BETWEEN start ~ end 拉取交易记录。
   - 将结果写入 Parquet 文件（默认 backend/data/orders.parquet）。
   - 使用 DuckDB 对 Parquet 聚合：按 OPEN_TIME 的日期与小时求 SUM(profit)。
   - 导出 JSON 到 PUBLIC_EXPORT_DIR（默认 frontend/public/profit_xauusd_hourly.json）。
3) 返回 { ok, json, rows }；若异常，返回 { ok: false, error }。

备注：仓库还提供 aggregate_close.py（脚本），它演示了“按 CLOSE_TIME 过滤且聚合”的变体，输出为 frontend/public/profit_xauusd_hourly_close.json，便于对比 OPEN_TIME 与 CLOSE_TIME 两种口径。该脚本不通过 API 暴露，直接运行：python backend/aggregate_close.py。

六、代码职责约定（Mental model）
- 路由（routes）：只做“收参数/回响应”，不写业务逻辑。
- 模型（schemas）：定义请求/响应“长什么样”。
- 服务（services）：做“怎么查/怎么算/怎么输出”。
- 配置（core）：集中化读取 .env、路径、CORS 等。

七、扩展指南（Add a new API）
1) 在 schemas/ 添加请求/响应模型。
2) 在 services/ 实现业务逻辑（数据库、DuckDB、外部服务等）。
3) 在 api/v1/routes/ 新建路由文件或在现有文件中新增端点。
4) 在 api/v1/routers.py 注册该路由（include_router）。
5) 通过 /docs 验证交互式文档与入参与返回。

八、常见问题（FAQ）
- 连接失败：检查 .env 中 DB_HOST/USER/PASSWORD/NAME/PORT 是否正确，容器网络是否可达。
- 无法写文件：确保 PARQUET_DIR 与 PUBLIC_EXPORT_DIR 可写，或在 .env 中显式覆盖到可写目录。
- 前端读取不到 JSON：确认导出的 JSON 位于 frontend/public 下（或前端所用的静态目录），并检查文件名与路径。
- CORS 问题：在 .env 里设置 CORS_ORIGINS（如 http://localhost:3000,http://127.0.0.1:3000）。

九、快速定位（Jump to code）
- ASGI 入口：main.py（仓库根）
- 应用工厂：app/main.py（create_app -> app）
- 版本路由：app/api/v1/routers.py
- 聚合端点：app/api/v1/routes/aggregations.py
- 聚合参数与返回：app/schemas/aggregation.py
- 聚合实现：app/services/aggregation_service.py
- 配置：app/core/config.py

（Note for juniors: keep business logic in services; keep request/response shapes in schemas; keep routes thin.）