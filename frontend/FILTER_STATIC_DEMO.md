# 筛选器静态 UI 演示文档

## 已实现功能

### 1. 数据模型
- **列元数据** (`/frontend/src/config/filterColumns.ts`): 定义了可筛选列的白名单与类型
- **筛选规则** (`/frontend/src/types/filter.ts`): FilterRule, FilterGroup, 操作符定义

### 2. Filter Builder 组件 (`/frontend/src/components/FilterBuilder.tsx`)
- **响应式布局**: 
  - 桌面端 (≥640px): Dialog 弹窗，宽度 720px-860px
  - 移动端 (<640px): Drawer 全屏抽屉
- **AND/OR 切换**: 顶部切换逻辑联结方式
- **规则编辑器**: 每条规则包含列选择、操作符选择、值输入、删除按钮
- **值输入适配**: 根据列类型自动切换输入控件
  - 文本: `<Input type="text">`
  - 数字/百分比: `<Input type="number">`
  - 日期: `<Popover>` + `<Calendar>` 日期选择器
  - between 操作符: 显示两个值输入框
  - blank/not_blank: 隐藏值输入
- **操作按钮**: 重置、取消、应用

### 3. 主页面集成 (`/frontend/src/pages/CustomerPnLMonitorV2.tsx`)
- **筛选按钮**: 
  - 位置: 状态栏右侧，列显示切换按钮左侧
  - 样式: 黑色主题 (dark mode 为白色)，h-9 高度与现有控件一致
  - Badge 计数: 显示当前激活规则数量
- **激活规则展示**: 
  - 状态栏下方（有筛选条件时显示）
  - 蓝色 Badge 显示每个规则，格式：`{列名} {操作符} {值}`
  - 每个 Badge 可单独移除
  - "清空所有" 按钮快速清空
- **持久化**: 
  - localStorage key: `pnl_v2_filters:{server}` (按服务器隔离)
  - 切换服务器时自动恢复对应筛选条件

## 静态 JSON 输出

### 触发方式
1. 点击状态栏的"筛选"按钮，打开 Filter Builder
2. 添加/编辑筛选规则
3. 点击"应用"按钮

### 输出位置
- **浏览器控制台**: `console.log('✅ 已应用筛选条件（静态 JSON）:', ...)`
- **UI Badge**: 状态栏下方显示激活规则的可视化展示

### JSON 结构示例

```json
{
  "join": "AND",
  "rules": [
    {
      "field": "net_deposit",
      "op": "<",
      "value": 0
    },
    {
      "field": "closed_total_profit",
      "op": ">",
      "value": 0
    },
    {
      "field": "user_group",
      "op": "contains",
      "value": "KCM"
    },
    {
      "field": "last_updated",
      "op": "between",
      "value": "2025-10-01",
      "value2": "2025-10-22"
    },
    {
      "field": "zipcode",
      "op": "blank"
    }
  ]
}
```

### 操作符清单

**文本类型** (text):
- `contains`: 包含
- `not_contains`: 不包含
- `equals`: 等于
- `not_equals`: 不等于
- `starts_with`: 开头是
- `ends_with`: 结尾是
- `blank`: 为空
- `not_blank`: 不为空

**数字/百分比类型** (number, percent):
- `=`: 等于
- `!=`: 不等于
- `>`: 大于
- `>=`: 大于等于
- `<`: 小于
- `<=`: 小于等于
- `between`: 区间 (需要 value 和 value2)
- `blank`: 为空
- `not_blank`: 不为空

**日期类型** (date):
- `on`: 等于
- `before`: 早于
- `after`: 晚于
- `between`: 区间 (需要 value 和 value2)
- `blank`: 为空
- `not_blank`: 不为空

## 可筛选列白名单

当前支持筛选的列（参见 `/frontend/src/config/filterColumns.ts`）：

### 用户与账户信息 (文本)
- `login`: 账户ID
- `user_name`: 客户名称
- `user_group`: Group
- `country`: 国家/地区
- `zipcode`: ZipCode
- `user_id`: ClientID
- `symbol`: Symbol

### 账户余额与浮盈 (数字)
- `user_balance`: Balance
- `positions_floating_pnl`: 持仓浮动盈亏
- `equity`: Equity

### SELL 平仓统计 (数字)
- `closed_sell_volume_lots`: Closed Sell Volume (Lots)
- `closed_sell_count`: Closed Sell Count
- `closed_sell_profit`: Closed Sell Profit
- `closed_sell_swap`: Closed Sell Swap
- `closed_sell_overnight_count`: Closed Sell Overnight Count
- `closed_sell_overnight_volume_lots`: Closed Sell Overnight Volume

### BUY 平仓统计 (数字)
- `closed_buy_volume_lots`: Closed Buy Volume (Lots)
- `closed_buy_count`: Closed Buy Count
- `closed_buy_profit`: Closed Buy Profit
- `closed_buy_swap`: Closed Buy Swap
- `closed_buy_overnight_count`: Closed Buy Overnight Count
- `closed_buy_overnight_volume_lots`: Closed Buy Overnight Volume

### 佣金与出入金 (数字)
- `total_commission`: Total Commission
- `deposit_count`: 入金笔数
- `deposit_amount`: 入金金额
- `withdrawal_count`: 出金笔数
- `withdrawal_amount`: 出金金额
- `net_deposit`: Net Deposit

### 日期
- `last_updated`: 更新时间

### 暂不支持筛选的列
以下列为前端计算列/聚合列，需要后端支持后才能开放筛选：
- `closed_total_profit`: 平仓总盈亏 (前端计算)
- `overnight_volume_ratio`: 过夜成交量占比 (前端计算)
- 所有 `overnight_*_all`、`total_*_all` 聚合列 (AG Grid valueGetter)

## 后续对接后端的步骤

### 1. 后端 API 扩展
在 `/api/v1/etl/pnl-user-summary/paginated` 接口添加参数：
```python
filters_json: Optional[str] = Query(None, description="URL-encoded JSON string of FilterGroup")
```

### 2. 前端发送参数
修改 `fetchData` 函数（约 1158 行）：
```typescript
if (appliedFilters && appliedFilters.rules.length > 0) {
  params.set('filters_json', encodeURIComponent(JSON.stringify(appliedFilters)))
}
```

### 3. 后端解析与 SQL 生成
- 白名单校验 field 与 op
- 文本操作: `ILIKE '%xxx%'` (contains), `=` (equals), `IS NULL` (blank)
- 数字操作: `>`, `>=`, `<`, `<=`, `BETWEEN`, `IS NULL`
- 日期操作: `DATE(field) = '2025-10-22'` (on), `<`, `>`, `BETWEEN`
- 组合: WHERE (rule1) AND/OR (rule2) AND/OR ...

### 4. 计算列支持
将前端计算下沉到 SQL SELECT：
```sql
SELECT 
  *,
  (closed_buy_profit + closed_sell_profit + closed_buy_swap + closed_sell_swap) AS closed_total_profit
FROM pnl_user_summary
```
然后允许对 `closed_total_profit` 筛选。

### 5. 性能优化
- 对高频筛选列建立索引
- 文本模糊搜索使用 trigram 索引
- 数值区间使用 B-Tree 索引

## 测试方式

1. 启动前端开发服务器
2. 打开浏览器开发者工具 Console 面板
3. 在 CustomerPnLMonitorV2 页面点击"筛选"按钮
4. 添加筛选规则，例如：
   - net_deposit < 0
   - closed_total_profit > 0
   - user_group contains "KCM"
5. 点击"应用"
6. 查看：
   - Console 输出的 JSON
   - 状态栏下方显示的 Badge
   - localStorage 存储（Application -> Local Storage -> `pnl_v2_filters:MT5`）

## UI 风格一致性

- **颜色**: 筛选按钮为黑色（dark mode 白色），与页面现有按钮风格一致
- **高度**: 所有控件统一 `h-9` (36px)
- **间距**: gap-2/gap-3 与现有卡片布局保持一致
- **响应式**: sm 断点 (640px) 切换移动端布局
- **Dark Mode**: 所有组件支持 dark mode 主题
- **图标**: 使用 lucide-react (Filter, X, Plus, Settings2, Search, CalendarIcon)

## 已知限制（静态阶段）

1. **不发送请求**: 应用筛选条件后不会触发后端 API 调用
2. **不影响数据**: 表格数据不会变化，仅生成 JSON 并展示 Badge
3. **计算列**: `closed_total_profit`、`overnight_volume_ratio` 等标记为不可筛选
4. **聚合列**: valueGetter 派生的列未列入选项（需后端先派生）

所有限制将在后端对接后解除。

