# IB 报表页面设计文档 (IB Report Design)

## 1. 概述
IB 报表主要用于展示各业务组别（Group）及其下属用户的资金往来、交易量及各项成本明细。该页面支持高度定制的筛选条件、月度数据比对以及高性能的表格展示。

## 2. 页面排布 (UI Layout)

页面遵循 `ClientPnLAnalysis.tsx` 的经典布局，分为两大部分：

### 2.1 顶部筛选卡片 (Filter Card)
- **简洁布局**: 移除冗余标签，参考 `ClientPnLAnalysis.tsx` 保持单行对齐。
- **时间范围筛选 (Date Range)**: 宽度 260px，支持选择起始和结束日期。
- **组别筛选 (Group Filter)**: 宽度 260px，支持从数据库动态加载的组别多选。
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

---

## 3. 技术专项：IB 报表组别动态管理方案

### 3.1 需求背景
为了解决前端硬编码组别导致的维护困难，并提供实时的组别用户量统计，设计此动态加载与缓存方案。

### 3.2 后端设计 (ClickHouse + Python Cache)
- **数据源**：
    - 组别定义：`"KCM_fxbackoffice"."fxbackoffice_tags"` (categoryId = 6)
    - 用户关联：`"KCM_fxbackoffice"."fxbackoffice_user_tags"`
- **缓存策略**：
    - 使用 Python 内存对象缓存查询结果。
    - **有效期**：7 天。
    - **数据结构**：包含 `tag_id`, `tag_name`, `user_count`, `last_update_time`, `previous_update_time`。
- **性能优化**：通过 `GROUP BY tagId` 一次性完成所有组别的人数统计，避免 N+1 查询。

### 3.3 前端设计 (React + shadcn/ui)
- **交互方式**：
    - Popover 底部新增“查看所有组别”按钮。
    - 弹出 Dialog 展示所有组别的详细列表（名称、人数）。
- **持久化**：
    - “常用组别”存储于浏览器的 `localStorage` 中。
    - 用户可以在 Dialog 中通过“星标”快速切换常用状态。

### 3.4 交互逻辑详解 (Filtering Logic)

#### 1. 快捷选择器 (Popover Dropdown)
- **展示内容**：显示“常用组别”与“当前已选中组别”的**并集**。这意味着任何在全量弹窗中勾选的组别，都会自动出现在快捷菜单中。
- **视觉标识**：
    - **金星图标**：标识该组别为“常用”，通过点击组别名旁的星标切换。
    - **蓝色高亮**：标识该组别当前已被选中参与报表计算。
- **按钮逻辑**：
    - **清空**：一键清空所有已选组别（`selectedGroups = []`）。
    - **全选常用**：快速选中所有被标记为“常用”的组别。
    - **查看所有组别**：打开全量详情弹窗。

#### 2. 全量详情弹窗 (Dialog Overview)
- **实时同步**：弹窗内的选择状态与主页面报表状态实时联动。在弹窗中勾选 `CheckSquare`，报表数据会同步变化。
- **元数据展示**：
    - **MT Server Time**：显示数据最后一次从 MetaTrader 服务器同步的时间（数据源更新时间）。
    - **数据状态**：显示“数据更新于：时间 (上一次：时间)”，若无历史记录则显示 N/A。
- **搜索过滤**：支持对 60+ 个组别进行前端实时文本检索。
- **大小写兼容**：所有匹配逻辑（选中、收藏、过滤）均采用 `toLowerCase()` 处理，自动兼容数据库与前端可能存在的大小写差异（如 `HZL` vs `hzl`）。

### 3.5 待办事项：结束 Mock 阶段 (Next Steps)
目前报表主体数据处于 Mock 阶段（读取本地 `ib_report_mock.csv`），后续需执行以下步骤实现生产切换：

1.  **后端 SQL 补全**：在 `clickhouse_service.py` 中编写真实的报表聚合 SQL，替代现有的模拟逻辑。
2.  **API 联调**：将前端 `handleSearch` 函数中的 `fetch` 地址由 `.csv` 路径更改为正式的后端 API 接口。
3.  **参数传递**：确保前端将 `date_range` (开始/结束日期) 和 `selectedGroups` (已选组别列表) 作为请求参数发送至后端。

### 3.6 安全与规范
- **连接安全**：使用生产环境专用的环境变量 `CLICKHOUSE_prod_*`。
- **大小写敏感**：SQL 语句中表名必须使用双引号包裹，如 `"fxbackoffice_tags"`。

---

## 4. 待明确事项 (Pending Questions)
1. **组别对应关系**: 数据库中的字段名是 `group` 还是需要通过其他逻辑映射？
2. **交易调整逻辑**: “交易调整”的具体计算公式或 ClickHouse 中的原始字段名是什么？
3. **图表细节**: 图表页面是原地切换还是新页面跳转？（建议先做原地切换或 Modal 展示）。

## 5. 开发路线图 (Roadmap)
- [x] 前端：构建筛选卡片 UI（已参考 ClientPnLAnalysis 优化，保持简洁）。
- [x] 前端：集成 AG-Grid 并配置汇总行（Pinned Top Row）。
- [x] 前端：实现双行展示逻辑（Selected Range vs Monthly Total）。
- [x] 前端：实现动态组别加载与“查看所有组别”弹窗。
- [ ] 后端：编写 ClickHouse 查询逻辑，实现基于组别的 SQL 映射。
- [ ] 后端：实现月度累计数据的聚合查询。
- [ ] 联调：前后端 API 对接。
- [ ] 联调：测试大数据量下的排序与筛选性能。
- [ ] 扩展：增加预留的趋势图表功能。
