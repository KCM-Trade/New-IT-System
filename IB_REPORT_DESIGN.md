# IB 报表页面设计文档 (IB Report Design)

## 1. 概述
IB 报表主要用于展示各业务组别（Group）及其下属用户的资金往来、交易量及各项成本明细。该页面支持高度定制的筛选条件、月度数据比对以及高性能的表格展示。

## 2. 页面排布 (UI Layout)

页面遵循 `ClientPnLAnalysis.tsx` 的经典布局，分为两大部分：

### 2.1 顶部筛选卡片 (Filter Card)
- **简洁布局**: 移除冗余标签，参考 `ClientPnLAnalysis.tsx` 保持单行对齐。
- **时间范围筛选 (Date Range)**: 宽度 260px，支持选择起始和结束日期。
- **组别筛选 (Group Filter)**: 宽度 260px，多选下拉框。
    - 预定义组别：`HZL`, `CCX`, `JSA`, `SZS`, `SZU`, `SHY`, `SHT037`, `SHT042`, `SHT049`, `SHS`, `SHP`, `CS/Company`, `SP01`。
- **展示当月数据开关 (Monthly Data Checkbox)**: 
    - 勾选后，表格在展示指定时间段数据的基础上，额外展示该时间段所属月份的全月数据。
- **操作按钮组**:
    - **查询按钮 (Search)**: 宽度 140px，触发后端数据抓取。
    - **图表按钮 (Chart)**: 宽度 140px，预留接口用于展示趋势分析。

### 2.2 下方数据表格 (AG-Grid Table)
- **汇总行 (Totals Row)**: 位于表格顶部（Pinned Top），实时展示所有组别的各项指标总和。
- **双行展示逻辑 (Double-Row Display)**:
    - 每个单元格（Cell）内部展示两行数据：
        - 第一行：选定时间范围（Selected Range）内的统计值。
        - 第二行：当月全月（Monthly Total）的统计值。
    - **排序规则**: 点击表头排序时，系统应**仅以第一行（时间范围内的数据）**作为排序依据。
- **核心列定义 (Columns)**:
    1. **组别 (Group)**
    2. **用户名称 (User Name)**
    3. **时间段 (Time Range)**
    4. **入金 (Deposit USD)**
    5. **出金 (Withdrawal USD)**
    6. **IB 出金 (IB Withdrawal USD)**
    7. **净入金 (Net Deposit USD)**
    8. **平仓交易量 (Closed Volume lots)**
    9. **交易调整 (Trade Adjustments)**: 目前统一设置为 `0`（待团队确定）。
    - *待扩展列*: 佣金 (Commission)、IB 佣金 (IB Commission)、平仓利息 (Swap)、平仓盈亏 (Profit)、当天新开客户、当天新开代理。
- **交互特性**:
    - **排序**: 所有列均支持点击表头排序（基于时间段数据）。
    - **对齐**: 数值列右对齐，文本列左对齐。

## 3. 技术实现细节 (Technical Implementation)

### 3.1 数据逻辑 (Data Logic)
- **多维度聚合**: 后端需要根据 `Group` 和 `User` 进行聚合。
- **月度数据叠加**: 
    - 如果开启“月度累计”，API 需要返回一个包含两个时间窗口数值的对象，例如：`{ "range_val": 100, "month_val": 500 }`。
- **汇总计算**: 前端利用 AG-Grid 的 `pinnedTopRowData` 展示所有记录的合计。

### 3.2 接口设计 (API Design)
- **Endpoint**: `POST /api/v1/ib-report/query`
- **Payload**:
  ```json
  {
    "start_date": "2026-01-04",
    "end_date": "2026-01-08",
    "groups": ["HZL", "CCX"],
    "include_monthly": true
  }
  ```
- **Response Item 结构**:
  ```json
  {
    "group": "HZL",
    "user_name": "Test User",
    "time_range": "2026-01-04 ~ 2026-01-08",
    "deposit": { "range_val": 1000, "month_val": 5000 },
    "withdrawal": { "range_val": -200, "month_val": -1000 },
    "ib_withdrawal": { "range_val": 0, "month_val": 0 },
    "net_deposit": { "range_val": 800, "month_val": 4000 },
    "volume": { "range_val": 10.5, "month_val": 45.2 },
    "adjustments": { "range_val": 0, "month_val": 0 }
  }
  ```

### 3.3 前端组件结构
- 引用 `AgGridReact` 实现核心表格。
- 使用 `shadcn/ui` 的 `Card`, `Button`, `Checkbox`, `Popover+Calendar` 构建筛选器。
- 状态管理使用 React `useState` 和 `useMemo`。
- 自定义 `DoubleValueRenderer` 用于单元格内的双行数据展示。

## 4. 待明确事项 (Pending Questions)
1. **组别对应关系**: 数据库中的字段名是 `group` 还是需要通过其他逻辑映射？
2. **交易调整逻辑**: “交易调整”的具体计算公式或 ClickHouse 中的原始字段名是什么？
3. **图表细节**: 图表页面是原地切换还是新页面跳转？（建议先做原地切换或 Modal 展示）。

## 5. 开发路线图 (Roadmap)
- [x] 前端：构建筛选卡片 UI（已参考 ClientPnLAnalysis 优化，保持简洁）。
- [x] 前端：集成 AG-Grid 并配置汇总行（Pinned Top Row）。
- [x] 前端：实现双行展示逻辑（Selected Range vs Monthly Total）。
- [ ] 后端：编写 ClickHouse 查询逻辑，实现基于组别的 SQL 映射。
- [ ] 后端：实现月度累计数据的聚合查询。
- [ ] 联调：前后端 API 对接。
- [ ] 联调：测试大数据量下的排序与筛选性能。
- [ ] 扩展：增加预留的趋势图表功能。
