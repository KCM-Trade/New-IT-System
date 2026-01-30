# ClientPnLAnalysis - 前端本地筛选（Local Filtering）方案说明

## 背景与目标

`ClientPnLAnalysis` 页面当前的查询流程是：

- 前端按日期范围（以及可选 search）调用后端接口
- 后端在 ClickHouse 侧按时间范围过滤聚合后返回结果集
- 前端将结果集渲染到 AG Grid（当前量级通常约 5k，最多不超过 1w）

本方案目标是在 **不改后端接口** 的前提下，为该页面增加“筛选（Filter）”能力：

- 筛选作用于 **当前查询结果集**（后端返回的 rows 数组）
- 筛选条件由统一的结构化模型 `FilterGroup` 表达
- 使用 `FilterBuilder` 组件提供一致的交互（Dialog/Drawer）
- 默认不提供“空值筛选”（blank / not_blank）以简化逻辑与口径

## 为什么选择“前端本地筛选”

在结果集稳定落在 5k~1w 的情况下，前端本地筛选更优：

- **交互更快**：用户应用筛选后无需等待网络/数据库，表格即时刷新
- **后端零改动**：无需实现 filters_json 解析与 ClickHouse where 拼接
- **研发成本低**：仅需要在前端增加规则执行器与少量 UI 组件能力

风险与边界：

- 本地筛选只对 **当前结果集** 生效，并非全库过滤；需要在产品文案/交互上达成一致
- 若未来数据量显著增长或改为后端分页，本地筛选需要评估是否迁移为服务端筛选

## 端到端数据流

### 1) 后端（现状）

- API：`GET /api/v1/client-pnl-analysis/query`
- Query 参数：`start_date`, `end_date`, `search`
- 后端将 `date` 转为 `datetime`（起始 00:00:00，结束 23:59:59）
- 在 ClickHouse SQL 中通过 `t.CLOSE_TIME` / `close_time` 做范围过滤
- 返回 JSON `data: Array<Record<string, any>>`（后端内部会临时用 Pandas 做 `fillna(0)` 后再转回 records）

### 2) 前端（新增）

新增三层状态（单一事实来源）：

- **rawRows**：后端返回的原始 rows（查询按钮触发更新）
- **appliedFilters**：`FilterGroup | null`（由 FilterBuilder “应用”产生，可持久化）
- **viewRows**：`useMemo` 基于 `rawRows + appliedFilters` 计算得到的筛选结果集，作为 AG Grid 的 `rowData`

## 筛选条件模型

复用项目已有 `FilterGroup` / `FilterRule`：

- `FilterGroup.join`: `'AND' | 'OR'`
- `FilterRule.field`: 字段名（来自该页面的 ColumnMeta 白名单）
- `FilterRule.op`: 操作符（text / number）
- `FilterRule.value / value2`: 操作数（between 使用 value2）

## 列白名单（ColumnMeta）

为 `ClientPnLAnalysis` 单独维护 `ColumnMeta[]` 白名单（字段必须能从 row 中取值或由 computed getter 计算得到）。

### server（sid）筛选

用户看到的是服务器名，但筛选值是数字：

- 1 → MT4
- 5 → MT5
- 6 → MT4Live2

实现方式：

- ColumnMeta.type 仍为 `number`
- ColumnMeta.options 提供 `{ label, value }[]` 用于在筛选器里渲染下拉选择
- 本地规则执行器按 number 比较 `sid`

## 操作符约束（本方案口径）

为避免空值口径与不必要复杂度：

- text：`contains`, `equals`, `starts_with`, `ends_with`
- number：`=`, `!=`, `>`, `>=`, `<`, `<=`, `between`
- 不提供：`blank`, `not_blank`

通过 ColumnMeta.operators 对每列限制可用 operator，保证 UI 与执行逻辑一致。

## computed 字段

该页面当前已有 computed 列：

- `net_pnl_with_comm_usd = trade_profit_usd + ib_commission_usd`

实现方式：

- 在前端维护 `computedGetters[field] = (row) => number`
- 本地规则执行时，`getValue(row, field)` 优先命中 computed getter，否则直接取 row[field]

## UI/交互建议

- 点击“筛选”打开 FilterBuilder
- “应用”后立即更新表格（无需重新请求后端）
- 在筛选按钮上显示已应用规则数量（Badge）
- 在表格上方显示已应用条件（Badge 列表），支持：
  - 删除单条规则
  - 清空全部规则
- 筛选条件变化后：
  - 表格回到第一页（避免用户停在后面页导致空白）
  - 统计展示（总记录数）基于 viewRows.length

## 持久化

建议将 `appliedFilters` 与现有页面 settings 一起持久化（localStorage）：

- 页面刷新后自动恢复 filters
- 用户重新查询时间范围时不丢筛选条件（筛选仍作用于新结果集）

## 未来演进（可选）

若后续引入服务端筛选：

- `appliedFilters` 保持不变
- 将 `FilterGroup` 序列化为 `filters_json` 透传到后端
- 后端按白名单字段与类型执行 where 条件
- 前端 viewRows 可直接等于 rawRows（或保留本地二次过滤作为兜底）


