# 头寸查询系统功能增强：跨服汇总与订单明细下钻

本手册整合了实时头寸页面的两个核心增强功能：**跨服务器品种汇总分析**以及**订单明细下钻查询**。

---

## 1. 跨服务器品种汇总分析 (Cross-Server Symbol Summary)

### 1.1 功能设计
风控团队可以从全局视角监控核心品种（如 XAUUSD）在所有服务器（mt4_live, mt4_live2, mt5）上的总敞口。

*   **入口**: 页面顶部“选择产品 (跨服汇总)”区域。
*   **功能**:
    *   **模糊匹配**: 选择“XAUUSD相关”可自动包含 `.cent`, `.kcm`, `.kcmc` 等变体。
    *   **多库并发**: 后端并行查询三个数据库，确保实时性。
    *   **汇总对比**: 展示各服务器明细及全球总计。

### 1.2 如何新增产品品种
如需向下拉框添加新产品（例如 BTCUSD），请按以下步骤操作：

1.  **修改文件**: `frontend/src/pages/Position.tsx`
2.  **添加选项**: 在 `SelectContent` 中增加：
    ```tsx
    <SelectItem value="BTCUSD">BTCUSD</SelectItem>
    <SelectItem value="BTCUSD (Related)">BTCUSD相关 (模糊匹配)</SelectItem>
    ```
3.  **注意**: 
    *   带 ` (Related)` 后缀会自动触发后端的 `LIKE` 匹配。
    *   后端已内置美分账户换算逻辑，无需额外配置。

---

## 2. 订单明细下钻查询 (Order Drill-down) - 规划中

### 2.1 需求背景
当发现某个品种盈亏异常时，风控人员需点击数值直接查看底层订单明细，以定位异常账户。

### 2.2 交互流程
1.  **触发**: 点击主表中的 `Profit Buy/Sell/Total` 数值。
2.  **展示**: 从右侧弹出 `Sheet` (Drawer) 展示订单列表。
3.  **关键信息**: 订单号 (Ticket)、交易账号 (Login)、客户姓名 (Name)、手数、开仓价、实时盈亏、开仓时间。

### 2.3 后端待开发要点
*   **API**: `GET /api/v1/open-positions/details`
*   **SQL**: 增加 `JOIN mt4_users` 获取姓名，按 `ABS(totalProfit)` 倒序排列。

---

## 3. 开发待办清单 (Progress Tracking)

### 3.1 跨服务器汇总 (已完成)
- [x] 后端并行查询服务层实现。
- [x] API 路由及 Schema 定义。
- [x] 前端筛选卡片及汇总对比表 UI。
- [x] 品种变体模糊匹配逻辑。

### 3.2 订单明细下钻 (待执行)
- [ ] `open_positions_service.py` 新增 `get_open_position_details`。
- [ ] 封装前端 `OrderDetailsDrawer` 组件。
- [ ] 修改 `Position.tsx` 单元格为可点击状态并联调。

---
*如有任何维护需求，请联系 Kieran。*

