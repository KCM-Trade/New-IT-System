### 页面与组件风格（可复用模板）

- **总体布局**
  - 上下两个 `Card`：上卡片“筛选与视图”，下卡片“数据展示（表格/图表）”
  - 外层容器：`class="px-4 pb-6 lg:px-6"`
  - 表格容器：`mx-auto w-full max-w-[1280px]`；表格本体：`min-w-[960px]`，超出可横向滚动

### 上卡片：筛选与视图（与 Profit 页一致）

- **结构**
  - `CardHeader > CardTitle("筛选与视图")`
  - `CardContent` 使用 `flex`：`class="flex flex-col gap-3 md:flex-row md:items-center md:gap-6"`
- **控件**
  - **产品**: 原生 `select`，`h-9 rounded-md border bg-background px-2`
  - **日期**: `Popover + Button(variant="outline") + Calendar(mode="single")`
    - 触发按钮内容：`<CalendarIcon /> + yyyy-MM-dd`
    - `onSelect` 后格式化为 `yyyy-MM-dd`
  - **刷新**: `Button class="h-9"`
- **交互建议**
  - Loading/Disabled/错误提示（后续接入 API 时添加）
  - 统一图标大小与间距，按钮使用 `gap-2 font-normal`

### 下卡片：表格（产品报仓专用）

- **列头**
  - 报仓｜Type｜即日(Volume/Profit)｜过夜(Volume/Profit)
- **行结构**
  - 分组顺序：`正在持仓` → `当日已平（选中日期）` → `昨日已平（选中日期-1）`
  - 每组下含 3 行：`Buy`、`Sell`、`Total`
- **数值规则**
  - `Sell.Volume` 显示为负值
  - `Total.Volume = Buy.Volume - Sell.Volume`
  - `Total.Profit = Buy.Profit + Sell.Profit`
  - 所有数值右对齐，`tabular-nums`，保留 2 位小数
  - Profit 颜色：正值绿色，负值红色
- **可读性**
  - 报仓分组使用 `rowSpan` 合并
  - 表头两行：第一行“即日/过夜”分组，第二行“Volume/Profit”

### API 与数据

- **接口形态**
  - POST `http://<后端IP>:8001/api/v1/trade-summary/query`，Body: `{ date: "yyyy-MM-dd", symbol: "XAU-CNH" }`
- **前端透视整形**
  - 按 `grp`(正在持仓/当日已平/昨日已平) × `settlement`(当天/过夜) × `direction`(buy/sell) 汇总
  - 缺失组合补 0，生成 `Buy/Sell/Total` 三行展示

### 样式规范

- **宽度**
  - 页面最大宽度：`max-w-[1280px]`；表格最小宽度：`min-w-[960px]`
- **间距**
  - 卡片与控件使用 `gap-3`/`gap-6`，移动端优先单列，`md` 起横向对齐
- **对齐**
  - 数字列统一右对齐；标题与文本列左对齐
- **色彩**
  - 正盈利：绿色；亏损：红色；弱化文案用 `text-muted-foreground`

### 交互状态

- **加载**: 顶部按钮 `disabled`、下卡片显示 Loading 占位
- **空数据**: 表格显示 “No results.”
- **错误**: 顶部 toast 或下卡片内文案提示；可重试按钮

### 可复用与一致性

- **可抽离组件**
  - `FiltersBar`（产品/日期/刷新）
  - `SingleDatePicker`（Popover + Calendar + Button 包装）
  - `WarehouseTable`（接收透视后的数据）
- **主题一致**
  - 优先使用项目现有的 shadcn 组件与 Token；不要使用原生 `<input type="date">`（视觉与交互与官网不一致）

### 示例骨架（用于新页面起步）

```tsx
// 上卡片：筛选
<Card>
  <CardHeader>
    <CardTitle className="text-2xl font-bold">筛选与视图</CardTitle>
  </CardHeader>
  <CardContent className="flex flex-col gap-3 md:flex-row md:items-center md:gap-6">
    {/* 产品选择 */}
    {/* 单日日期选择：Popover + Button + Calendar(mode='single') */}
    {/* 刷新按钮 */}
  </CardContent>
</Card>

// 下卡片：数据展示
<Card>
  <CardContent className="pt-6">
    <div className="mx-auto w-full max-w-[1280px]">
      <div className="overflow-hidden rounded-md border">
        <Table className="min-w-[960px]">{/* ...rows... */}</Table>
      </div>
    </div>
  </CardContent>
</Card>
```

### 说明：为什么日历效果和官网不一样

- 使用原生 `<input type="date">` 会与 shadcn 的样式体系脱节（系统控件外观），需改用 `Popover + Calendar` 组合
- 确保引入 shadcn 的组件样式与 Tailwind 配置一致；按钮使用 `variant="outline"` 与项目 Token，从而与官网示例一致