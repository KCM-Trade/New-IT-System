## ag-Grid v34 在本项目中的集成实践

### 依赖与版本
- ag-grid-community: ^34.2.0
- ag-grid-react: ^34.2.0

### 全局配置（main.tsx）
1) 全局注册模块（修复错误 #272: No AG Grid modules are registered）
```ts
import { ModuleRegistry, AllCommunityModule } from "ag-grid-community"
ModuleRegistry.registerModules([AllCommunityModule])
```

2) 全局引入主题样式（先引入 Tailwind，再引入 ag-Grid 样式，保证覆盖顺序）
```ts
import "./index.css"
import "ag-grid-community/styles/ag-grid.css"
import "ag-grid-community/styles/ag-theme-quartz.css"
```

说明：Quartz 暗色主题不需要单独的 CSS 文件，直接使用容器类 `ag-theme-quartz-dark` 即可，无需引入 `ag-theme-quartz-dark.css`（该文件不存在，误引会导致 Vite 导入错误）。

### 页面容器与主题
- 容器必须有显式高度，否则会“空白”。推荐：`h-[600px] min-h-[400px]`。
- 使用 CSS 主题（legacy），避免与 Theming API 冲突的警告 #239：
  - 在 `AgGridReact` 上传入 `gridOptions={{ theme: 'legacy' }}`。
  - 使用容器类切换主题：亮色 `ag-theme-quartz`，暗色 `ag-theme-quartz-dark`。

示例（根据全局主题动态切换容器类）：
```tsx
<div
  ref={gridContainerRef}
  className={`${isDark ? 'ag-theme-quartz-dark' : 'ag-theme-quartz'} h-[600px] w-full min-h-[400px]`}
>
  <AgGridReact gridOptions={{ theme: 'legacy' }} ... />
</div>
```

### 列定义与默认列配置
```ts
const columnDefs: ColDef<Row>[] = [
  { field: 'login', headerName: '客户ID', width: 120, sortable: true, filter: true },
  {
    field: 'balance', headerName: '余额', width: 140, sortable: true, filter: true,
    cellRenderer: (p) => formatCurrencySafe(p.value, productConfig)
  },
  // ... 其他列
]

const defaultColDef: ColDef = {
  sortable: true,
  filter: true,
  resizable: true,
  flex: 1,
  minWidth: 100,
}
```

### 事件与状态（排序/加载/列宽自适配）
- onGridReady：保存 api 引用，并调用 `api.sizeColumnsToFit()`。
- onSortChanged：从 `event.api.getColumnState()` 读取排序列与方向，更新到本地状态以触发后端查询。

```ts
const onGridReady = (e: GridReadyEvent) => {
  setGridApi(e.api)
  try { e.api.sizeColumnsToFit() } catch {}
}

const onSortChanged = (e: SortChangedEvent) => {
  const sortModel = e.api.getColumnState()
    .filter(c => c.sort !== null)
    .map(c => ({ colId: c.colId, sort: c.sort }))
  setSortModel(sortModel)
}
```

列宽自适配（容器尺寸变化时重算）：
```ts
useEffect(() => {
  if (!gridContainerRef.current || !gridApi) return
  const ro = new ResizeObserver(() => { try { gridApi.sizeColumnsToFit() } catch {} })
  ro.observe(gridContainerRef.current)
  return () => ro.disconnect()
}, [gridApi])
```

### 服务端分页（推荐）
- 关闭 ag-Grid 内置分页，自己维护 `pageIndex/pageSize/totalPages`，所有翻页与更改 pageSize 都通过后端接口返回数据。
- 翻页按钮只更新本地状态并触发 `fetchData()`，不调用 `gridApi.pagination*` API。

核心请求封装：
```ts
async function fetchData(pageIndex: number, pageSize: number, sort?: { colId: string; sort: 'asc'|'desc' }) {
  const params = new URLSearchParams({ page: String(pageIndex + 1), page_size: String(pageSize) })
  if (sort) { params.set('sort_by', sort.colId); params.set('sort_order', sort.sort) }
  const res = await fetch(`/api/v1/pnl/summary/paginated?${params.toString()}`)
  const payload = await res.json()
  // 设置 rows/total/total_pages/product_config 等
}
```

分页控制示例：
```tsx
<Button onClick={() => setPageIndex(0)} disabled={pageIndex === 0}>首页</Button>
<Button onClick={() => setPageIndex(Math.max(0, pageIndex - 1))} disabled={pageIndex === 0}>上一页</Button>
<span>第 {pageIndex + 1} 页 / 共 {totalPages} 页</span>
<Button onClick={() => setPageIndex(Math.min(totalPages - 1, pageIndex + 1))} disabled={pageIndex >= totalPages - 1}>下一页</Button>
```

### 数据与显示工具函数
```ts
function toNumber(v: unknown, fallback = 0): number { /* string/number to number */ }

function formatCurrency(value: number, productConfig?: ProductConfig) {
  const displayDivisor = productConfig?.display_divisor || 1
  const adjusted = value / displayDivisor
  const symbol = (productConfig?.currency || 'USD') === 'USD' ? '$' : (productConfig?.currency || 'USD')
  return `${adjusted >= 0 ? '' : '-'}${symbol}${Math.abs(adjusted).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
}
```

### 常见错误与排查
- 空白表格：
  - 容器未设置高度；给容器设置 `h-[600px]` 或其它固定高度。
  - 主题 CSS 未正确引入或被覆盖；保证在 `main.tsx` 中全局引入 `ag-grid.css` 与 `ag-theme-quartz.css`。

- 错误 #272（未注册模块）：
  - 在入口文件注册：`ModuleRegistry.registerModules([AllCommunityModule])`。

- 警告/错误 #239（Theming API 与 CSS 主题同时使用）：
  - 采用 CSS 主题时，在 `AgGridReact` 传 `gridOptions={{ theme: 'legacy' }}`，并用容器类控制主题；不要同时使用 Theming API。

- Vite 导入 `ag-theme-quartz-dark.css` 失败：
  - 该文件不存在；暗色主题通过类名 `ag-theme-quartz-dark` 生效，无需额外 CSS 导入。

### UI 一致性与暗色模式
- 与 shadcn/ui 一致：可在表格外包一层 `div.border.rounded-md.overflow-hidden`，保持边框/圆角统一风格。
- 暗色：使用全局 `ThemeProvider` 在 `<html>` 添加/移除 `dark` 类，表格容器用 `ag-theme-quartz(-dark)` 动态切换。

### 最小可运行片段（要点）
```tsx
<div className={`${isDark ? 'ag-theme-quartz-dark' : 'ag-theme-quartz'} h-[600px] w-full`}>
  <AgGridReact
    rowData={rows}
    columnDefs={columnDefs}
    gridOptions={{ theme: 'legacy' }}
    defaultColDef={{ sortable: true, filter: true, resizable: true, flex: 1, minWidth: 100 }}
    onGridReady={onGridReady}
    onSortChanged={onSortChanged}
  />
</div>
```

以上实践已在 `src/pages/CustomerPnLMonitor.tsx` 中落地，可直接参考复制结构与逻辑。


