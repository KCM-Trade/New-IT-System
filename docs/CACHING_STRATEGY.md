# 前后端缓存与持久化优化建议 (Caching Strategy)

本文档总结了针对 IT 系统（特别是数据分析类页面）的缓存与持久化优化方案。旨在提升用户体验（UX）并减轻数据库（ClickHouse/PostgreSQL）的负载。

## 1. 后端缓存 (Backend Caching)

**目标**：减少重复的数据库查询，降低延迟，节省计算资源。

### 推荐方案：Redis Cache
Redis 是一个高性能的键值对内存数据库，非常适合存储查询结果。

### 实现逻辑
在 FastAPI 的 Service 层（如 `ClickHouseService` 或 `ClientPnLService`）中实现：

1.  **定义 Key (Cache Key)**：
    *   Key 必须包含所有影响查询结果的参数。
    *   格式示例：`app:pnl:{start_date}:{end_date}:{search_hash}`
2.  **查询流程**：
    *   **Check**: 收到请求时，先用 Key 查 Redis。
    *   **Hit**: 如果存在且未过期，直接反序列化（JSON）返回，**跳过数据库查询**。
    *   **Miss**: 如果不存在，执行 SQL 查询 ClickHouse，将结果序列化后存入 Redis，并设置 **TTL (过期时间)**（目前设置为 1800秒 / 30分钟）。
4.  **UX 标识 (UX Indication)**：
    *   API 响应的 `statistics` 中包含 `from_cache` 布尔值。
    *   前端 UI 在统计栏显示 “⚡ 已缓存 (Cached)” 标识，提升用户对响应速度的感知。

---

## 2. 前端持久化 (Frontend Persistence)

**目标**：用户刷新页面、关闭浏览器再重新打开时，能够恢复上次的筛选条件（日期范围、搜索词等）。

### 推荐方案：LocalStorage
浏览器提供的本地键值存储，数据一直保存在用户浏览器中，直到被显式清除。

### 实现逻辑
在 React 组件（如 `ClientPnLAnalysis.tsx`）中实现：

1.  **保存 (Save)**：
    *   使用 `useEffect` 监听状态变化（`timeRange`, `searchInput`, `date`）。
    *   当状态改变时，将它们组合成一个对象，`JSON.stringify` 后写入 `localStorage`。
2.  **恢复 (Hydrate)**：
    *   在组件初始化的 `useState` 中，尝试读取 `localStorage`。
    *   如果有存档，使用 `JSON.parse` 解析。
    *   **特殊处理**：日期字符串（ISO format）需要转换回 JavaScript `Date` 对象。

### 替代方案：URL Query Params
*   将状态同步到 URL（如 `?range=1m&q=client_1`）。
*   **优点**：便于分享链接，用户复制 URL 给同事可以看到相同视图。
*   **缺点**：实现稍微复杂（需要处理路由更新），URL 长度有限制。
*   *建议作为进阶优化。*

---

## 3. 浏览器缓存 (HTTP Caching)

**目标**：利用浏览器自身的缓存机制，减少网络请求。

### 实现
*   后端 API 响应头添加 `Cache-Control: private, max-age=60`。
*   这告诉浏览器：这个接口返回的数据在 60 秒内是新鲜的，不用再次请求服务器。
*   适用于数据变化频率不高的分析报表。

