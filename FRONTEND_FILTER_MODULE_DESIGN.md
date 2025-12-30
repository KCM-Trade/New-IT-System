# 前端筛选模块设计说明（Filter Module Design）

## 目标

在多个数据表页面复用统一的“高级筛选”能力，实现：

- **一致的交互**：移动端 Drawer、桌面端 Dialog
- **一致的数据模型**：用结构化 JSON 表达筛选（`FilterGroup`）
- **安全/可控**：只允许在白名单列（`ColumnMeta[]`）上筛选
- **可演进**：同一套筛选模型既可用于“前端本地筛选”，也可平滑迁移到“后端服务端筛选”
- **统一英文操作符**：筛选操作符文本（contains/equals/starts with 等）固定显示英文，避免中文翻译不自然

## 核心模块与文件

- `frontend/src/types/filter.ts`
  - `FilterGroup` / `FilterRule` 数据结构
  - `ColumnMeta` 列元信息（筛选白名单）
  - `OPERATOR_LABELS` 操作符展示文本（**固定英文**）
- `frontend/src/components/FilterBuilder.tsx`
  - 通用筛选 UI 组件（按 `ColumnMeta` 渲染字段/操作符/值输入）

页面接入示例：

- `frontend/src/pages/CustomerPnLMonitorV2.tsx`：服务端筛选（透传 `filters_json`）
- `frontend/src/pages/ClientPnLAnalysis.tsx`：前端本地筛选（本地执行规则）

## 数据模型

### 1) FilterGroup / FilterRule

- `FilterGroup.join`: `'AND' | 'OR'`
- `FilterGroup.rules`: 规则数组
- `FilterRule.field`: 字段名（必须在 `ColumnMeta[]` 白名单内）
- `FilterRule.op`: 操作符（按列类型与白名单限制）
- `FilterRule.value/value2`: 操作数（`between` 用 value2）

### 2) ColumnMeta（白名单列定义）

每个页面需要提供自己的 `ColumnMeta[]`（字段、类型、可筛选与否、可用操作符等），用于驱动 FilterBuilder：

- `id`: 字段名（与 row key 对齐；服务端筛选时与后端字段对齐）
- `label`: UI 展示名
- `type`: `'text' | 'number' | 'date' | 'percent'`
- `filterable`: 是否可筛选
- `operators?`: 可用操作符白名单（用于禁用 blank/not_blank 等不需要的能力）
- `options?`: 枚举下拉选项（用于把“用户看到的 label”映射为“实际筛选的 value”）

典型例子：server（sid）筛选

- 用户看到：MT4 / MT5 / MT4Live2
- 实际筛选值：1 / 5 / 6（number）
- 使用 `options` 渲染下拉选择，保存 value 为 number

## UI 交互约定

### 1) 打开/应用

- 点击页面“筛选”按钮：`setFilterBuilderOpen(true)`
- FilterBuilder 点“应用”：输出 `FilterGroup` 给页面 `onApply`
- 页面存入 `appliedFilters`（可持久化 localStorage）

### 2) 生效与展示

通用建议：

- “应用”后立刻生效（本地筛选：立即更新表格；服务端筛选：触发重新请求）
- 在筛选按钮上显示已应用规则数量（Badge）
- 页面上方展示已应用规则列表，并支持：
  - 删除单条规则
  - 清空全部规则

### 3) 操作符显示固定英文

本模块明确约束：

- **操作符永远显示英文**（不随语言切换）
- 统一由 `OPERATOR_LABELS` 提供文本

原因：避免中文翻译生硬与多处不一致。

## 本地筛选 vs 服务端筛选：统一接入方式

### 1) 本地筛选（Local Filtering）

适用：后端一次返回数据集规模可控（如 5k~1w）。

页面侧建议拆三层状态：

- `rawRows`：后端返回的原始 rows
- `appliedFilters`：结构化筛选条件
- `viewRows = useMemo(applyFilters(rawRows, appliedFilters))`：最终喂给表格的数据

并配套：

- computed 字段：维护 `computedGetters[field] = (row) => value`
- `getValue(row, field)`：优先 computed，否则 row[field]

### 2) 服务端筛选（Server Filtering）

适用：数据量大/需要后端分页/需要统一口径。

页面侧保持同一套 `appliedFilters`，只是把它序列化透传：

- `filters_json = JSON.stringify(appliedFilters)`

后端解析 `FilterGroup` 并拼接 where 条件（必须做字段白名单与类型校验）。

## 约束与注意事项

- **字段必须白名单**：FilterBuilder 只能选择 `ColumnMeta[]` 中 filterable=true 的字段
- **操作符必须可控**：通过 `ColumnMeta.operators` 限制，避免出现页面不支持/不需要的操作符
- **枚举字段优先用 options**：避免用户输入数字造成理解成本（如 server id）


