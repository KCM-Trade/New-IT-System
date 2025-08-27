1) v1 与 schemas、services 各放什么
app/api/v1/routers.py
作用：集中挂载版本内的子路由（把功能路由统一纳入 /api/v1）。
app/api/v1/routes/
作用：具体的功能接口文件（按领域拆分），如 health.py、aggregations.py、trade_summary.py。
app/schemas/
作用：请求/响应的数据模型（Pydantic）。示例：trade_summary.py 中的 TradeSummaryQuery/TradeSummaryRow/TradeSummaryResponse。
app/services/
作用：业务逻辑与数据访问（如数据库查询、外部服务调用）。示例：trade_summary_service.py 中的 get_trade_summary。
app/core/
作用：配置与依赖注入（如 Settings 读取 .env、CORS、全局依赖等）。
小白提示：路由“只负责收/回数据”，schemas“定义数据长啥样”，services“怎么查/算/拿到数据”。