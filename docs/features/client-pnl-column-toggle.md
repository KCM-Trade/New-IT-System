# Client PnL Analysis：列显示切换（AG Grid Community）说明

本文档说明页面 `frontend/src/pages/ClientPnLAnalysis.tsx` 新增的 **“列显示切换”** 功能，包括：交互位置、默认行为、localStorage 持久化策略，以及维护/扩展注意事项。

---

## 功能目标

- **用户可自行选择显示/隐藏哪些列**
- **不同浏览器（或同浏览器不同 Profile）分别记住偏好**（使用 localStorage）
- **默认隐藏部分列**：`client_name`（客户名称）、`total_trades`（总交易数）默认隐藏
- **Server 快速筛选**：支持 MT4/MT5/MT4Live2 多选筛选，默认全选，状态保存到 localStorage

---

## UI 交互与布局

- **位置**：位于顶部筛选区的“搜索框”右侧（同一行）
- **宽度策略**：
  - 搜索框更宽（桌面端 `sm:w-[320px]`）
  - “列显示”按钮与“查询”按钮同宽（桌面端 `sm:w-[140px]`）
- **内容**：
  - 下拉菜单列出所有可切换列（基于 `columnDefs` 自动生成）
  - 提供快捷操作：
    - **全选**：显示所有列
    - **重置**：清除 localStorage 并恢复默认列状态

---

## 持久化策略（localStorage）

### Key

- 使用固定 key：`CLIENT_PNL_ANALYSIS_GRID_STATE_V1`

> 说明：localStorage 天然按浏览器隔离，所以“不同用户浏览器”会各自保存自己的偏好。

### 存储内容

- 直接存 AG Grid 的 **Column State** 数组（`api.getColumnState()` 返回值）
- 该 state 通常包含：
  - `colId`
  - `hide`（是否隐藏）
  - 以及宽度、顺序、pinned、sort 等（取决于用户操作）

### 默认全显示如何实现？

- **只有当 localStorage 中存在有效 state 时才恢复**
- 如果 key 不存在或内容无效：不调用 `applyColumnState`，AG Grid 会按 `columnDefs` 默认渲染 ⇒ **默认全部显示**

---

## 何时保存（事件监听 + 节流）

为了让“拖动/调整列宽”这种高频操作不频繁写 localStorage，本页采用节流策略：

- 监听事件：
  - `onColumnResized`（仅在 `finished === true` 时保存）
  - `onColumnMoved`
  - `onColumnVisible`
  - `onColumnPinned`
- 保存函数做了 **300ms throttle**（减少 localStorage 写入次数）

---

## 关键维护点（新列越来越多时）

### 1) 计算列必须有稳定的 `colId`

AG Grid 的列持久化依赖 `colId` 匹配。对于没有 `field` 的“计算列”（只写了 `valueGetter`），必须显式设置 `colId`，否则：

- 用户保存的“隐藏/显示偏好”可能无法正确恢复
- 列增删后可能出现错位

本页已为“净盈亏(含佣金)”计算列增加了 `colId: "net_pnl_with_comm_usd"`。

### 2) 不维护第二份 `columnVisibility` 映射

为了避免 “React state vs Grid internal state” 双来源不一致，本页：

- 用 `columnState` 仅做 UI 快照（展示勾选状态）
- 以 AG Grid 自身 Column State 为准（单一事实来源）

---

## Server 快速筛选

### 功能说明

- **位置**：位于时间快速选择右侧
- **选项**：MT4 (sid=1)、MT5 (sid=5)、MT4Live2 (sid=6)
- **交互**：多选下拉菜单，支持"全选"和"清空"快捷操作
- **默认值**：全选（显示所有服务器数据）
- **持久化**：选择状态保存到 `CLIENT_PNL_SETTINGS_V1` localStorage key

### 筛选逻辑

- 筛选在前端本地执行，作用于后端返回的结果集
- 与其他本地筛选（FilterBuilder）联动：先应用 Server 筛选，再应用 FilterBuilder 条件

---

## 默认隐藏列

以下列通过 `hide: true` 设置为默认隐藏：

- `client_name`（客户名称）
- `total_trades`（总交易数）

用户可通过"列显示"菜单手动显示这些列。

---

## 常见问题（FAQ）

### Q1：我新增了一列，但用户说“列显示菜单里没有”

A：菜单列表来自 `columnDefs`。只要把新列加入 `columnDefs`，并确保它有稳定 `field` 或 `colId`，菜单会自动出现。

### Q2：我想让某些列“禁止隐藏”

A：可以在生成 `toggleColumns` 时过滤掉这些列，或在菜单里禁用对应 Checkbox（并在 Grid 层面也忽略隐藏操作）。

---

## 相关代码位置

- 页面实现：`frontend/src/pages/ClientPnLAnalysis.tsx`
- localStorage key：`CLIENT_PNL_ANALYSIS_GRID_STATE_V1`


