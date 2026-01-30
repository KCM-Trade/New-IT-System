# 筛选框架对接后端 API 完整指南

## 一、前端当前状态检查 ✅

### 已完成的前端组件

#### 1. **筛选状态管理** (CustomerPnLMonitorV2.tsx)
```typescript
// Line 175-177: 筛选状态
const [filterBuilderOpen, setFilterBuilderOpen] = useState(false)  // 筛选器弹窗开关
const [appliedFilters, setAppliedFilters] = useState<FilterGroup | null>(null)  // 已应用的筛选条件
```

#### 2. **筛选器 UI 组件** ✅
- `FilterBuilder` 组件：Dialog/Drawer 响应式弹窗
- `FilterRuleRow` 子组件：单条规则编辑器
- `ValueInput` 适配组件：根据列类型显示不同输入控件

#### 3. **筛选条件展示** ✅
- 筛选按钮显示激活规则数量的 Badge
- 状态栏下方显示蓝色 Badge 列表
- 单个移除与清空所有功能

#### 4. **持久化** ✅
- localStorage key: `pnl_v2_filters:${server}`
- 按服务器隔离存储
- 切换服务器时自动恢复

#### 5. **回调函数** ✅
```typescript
// Line 1293-1346: 筛选相关回调
handleApplyFilters(filters: FilterGroup)    // 应用筛选
handleRemoveFilter(ruleIndex: number)       // 移除单个规则
handleClearFilters()                        // 清空所有规则
```

### 🔴 **缺失的部分**：未对接到 `fetchData`

目前 `appliedFilters` 状态存在，但 `fetchData` 函数**没有使用它**。

---

## 二、对接后端 API 实现方案

### 方案概览

```
用户点击"应用" 
  → handleApplyFilters(filters) 
  → setAppliedFilters(filters) + setPageIndex(0)
  → useEffect 监听 appliedFilters 变化
  → 调用 fetchData()
  → fetchData 将 appliedFilters 序列化为 filters_json 参数
  → 后端接收并解析
  → 返回筛选后的数据
```

### 前端改动

#### 改动 1: `fetchData` 添加 `appliedFilters` 依赖

**位置**: `CustomerPnLMonitorV2.tsx` 约 1101-1207 行

**当前 useEffect 依赖**:
```typescript
}, [pageIndex, pageSize, sortModel, server, userGroups, searchDebounced, groupsReady])
```

**需要添加**:
```typescript
}, [pageIndex, pageSize, sortModel, server, userGroups, searchDebounced, groupsReady, appliedFilters])
//                                                                                      ^^^^^^^^^^^^^^^ 新增
```

#### 改动 2: `fetchData` 函数发送 `filters_json` 参数

**位置**: `CustomerPnLMonitorV2.tsx` 约 1120-1165 行

**在构建 URLSearchParams 时添加**:

```typescript
// 添加统一搜索参数（客户ID精确或客户名称模糊，由后端实现）
if (searchDebounced) {
  params.set('search', searchDebounced)
}

// ✨ 新增：添加筛选条件参数
if (appliedFilters && appliedFilters.rules.length > 0) {
  params.set('filters_json', encodeURIComponent(JSON.stringify(appliedFilters)))
}

// 切换为新的 ETL API（直查 PostgreSQL 的 pnl_user_summary）
const url = `/api/v1/etl/pnl-user-summary/paginated?${params.toString()}`
```

**完整改动示例**:
```typescript
const fetchData = useCallback(async (
  page?: number, 
  newPageSize?: number, 
  sortBy?: string, 
  sortOrder?: string
) => {
  // 暂不接入 MT4Live：前端直接显示空并跳过请求
  if (server === "MT4Live") {
    setTotalCount(0)
    setTotalPages(0)
    setLastUpdated(null)
    return []
  }

  const currentPage = page ?? pageIndex + 1
  const currentPageSize = newPageSize ?? pageSize
  const currentSortBy = sortBy ?? (sortModel.length > 0 ? sortModel[0].colId : undefined)
  const currentSortOrder = sortOrder ?? (sortModel.length > 0 ? sortModel[0].sort : 'asc')
  
  const params = new URLSearchParams({
    page: currentPage.toString(),
    page_size: currentPageSize.toString(),
  })
  // 追加 server 参数
  params.set('server', server)
  
  if (currentSortBy) {
    params.set('sort_by', currentSortBy)
    params.set('sort_order', currentSortOrder)
  }
  
  // 添加用户组别筛选参数（使用重复键，保留内部标识符，除 __ALL__ 外）
  if (userGroups && userGroups.length > 0) {
    if (userGroups.includes("__ALL__")) {
      // 全部：不传 user_groups（表示查询所有）
    } else {
      const tokensToSend = userGroups.filter(g => g !== "__ALL__")
      // 可见项定义：真实组别或特殊包含项 __USER_NAME_TEST__
      const hasVisible = tokensToSend.some(g => !g.startsWith("__") || g === "__USER_NAME_TEST__")
      if (hasVisible) {
        tokensToSend.forEach(g => params.append('user_groups', g))
      } else {
        // 仅剩排除型标识符时，视为无选择
        params.append('user_groups', '__NONE__')
      }
    }
  } else {
    // 没有任何选择：明确请求空集
    params.append('user_groups', '__NONE__')
  }

  // 添加统一搜索参数（客户ID精确或客户名称模糊，由后端实现）
  if (searchDebounced) {
    params.set('search', searchDebounced)
  }

  // ✨ 新增：添加筛选条件参数
  if (appliedFilters && appliedFilters.rules.length > 0) {
    params.set('filters_json', encodeURIComponent(JSON.stringify(appliedFilters)))
  }

  // 切换为新的 ETL API（直查 PostgreSQL 的 pnl_user_summary）
  const url = `/api/v1/etl/pnl-user-summary/paginated?${params.toString()}`
  const res = await fetchWithTimeout(url, { headers: { accept: "application/json" } }, 20000)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const payload = (await res.json()) as PaginatedPnlSummaryResponse
  if (!payload?.ok) throw new Error(payload?.error || "加载失败")
  
  // ... 后续处理逻辑保持不变
}, [server, pageIndex, pageSize, sortModel, userGroups, searchDebounced, appliedFilters])
//                                                                        ^^^^^^^^^^^^^^^ 新增依赖
```

---

## 三、后端 API 改动

### 改动 1: API 路由添加 `filters_json` 参数

**文件**: `backend/app/api/v1/routes/etl.py`

**位置**: 第 26-35 行

**添加新参数**:
```python
@router.get("/pnl-user-summary/paginated", response_model=PaginatedPnlUserSummaryResponse)
def get_pnl_user_summary(
    server: str = Query("MT5", description="服务器名称：MT5 或 MT4Live2"),
    page: int = Query(1, ge=1, description="页码，从1开始"),
    page_size: int = Query(100, ge=1, le=1000, description="每页记录数"),
    sort_by: Optional[str] = Query(None, description="排序字段"),
    sort_order: str = Query("asc", description="排序方向: asc/desc"),
    user_groups: Optional[List[str]] = Query(None, description="用户组别筛选，使用重复键传递；例如 user_groups=G1&user_groups=G2"),
    search: Optional[str] = Query(None, description="统一搜索：支持 login/user_id(精确) 或 user_name(模糊)"),
    # ✨ 新增参数
    filters_json: Optional[str] = Query(None, description="筛选条件 JSON，格式：{join:'AND'|'OR', rules:[{field,op,value,value2?}]}"),
) -> PaginatedPnlUserSummaryResponse:
```

**解析 JSON 并传递给服务层**:
```python
try:
    source_table, dataset = resolve_table_and_dataset(server)
    
    # 解析组别参数（现有逻辑保持不变）
    groups_list: Optional[List[str]] = None
    if user_groups:
        flat: List[str] = []
        for g in user_groups:
            if g and "," in g:
                flat.extend([x.strip() for x in g.split(",") if x.strip()])
            elif g and g.strip():
                flat.append(g.strip())
        groups_list = flat or None

    # 内部标识白名单校验
    if groups_list:
        allowed_internal = {
            "__ALL__", "__NONE__", "__USER_NAME_TEST__",
            "__EXCLUDE_USER_NAME_TEST__", "__EXCLUDE_GROUP_NAME_TEST__",
        }
        for token in groups_list:
            if token.startswith("__") and token not in allowed_internal:
                raise HTTPException(status_code=422, detail=f"Invalid internal token: {token}")

    # ✨ 新增：解析筛选条件 JSON
    filters_dict = None
    if filters_json:
        try:
            import json
            filters_dict = json.loads(filters_json)
            # 基本校验：必须有 join 和 rules
            if not isinstance(filters_dict, dict):
                raise ValueError("filters_json must be a JSON object")
            if "join" not in filters_dict or "rules" not in filters_dict:
                raise ValueError("filters_json must contain 'join' and 'rules' fields")
            if filters_dict["join"] not in ["AND", "OR"]:
                raise ValueError("join must be 'AND' or 'OR'")
            if not isinstance(filters_dict["rules"], list):
                raise ValueError("rules must be an array")
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=422, detail=f"Invalid filters_json: {str(e)}")
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    rows, total_count, total_pages = get_pnl_user_summary_paginated(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        user_groups=groups_list,
        search=search,
        source_table=source_table,
        filters=filters_dict,  # ✨ 传递给服务层
    )

    # ... 后续逻辑不变
```

### 改动 2: 服务层解析筛选条件并生成 SQL WHERE

**文件**: `backend/app/services/etl_pg_service.py`

**函数**: `get_pnl_user_summary_paginated`

**添加 filters 参数**:
```python
def get_pnl_user_summary_paginated(
    page: int = 1,
    page_size: int = 100,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    user_groups: Optional[List[str]] = None,
    search: Optional[str] = None,
    source_table: str = "public.pnl_user_summary",
    filters: Optional[Dict[str, Any]] = None,  # ✨ 新增参数
) -> Tuple[List[dict], int, int]:
```

**解析筛选条件并拼接 WHERE**:
```python
# 现有 where_conditions 继续追加组别、搜索等条件
where_conditions: List[str] = []
params: List[object] = []

# ... 组别筛选逻辑（现有代码）
# ... 统一搜索逻辑（现有代码）

# ✨ 新增：解析筛选条件
if filters and isinstance(filters, dict):
    join_type = filters.get("join", "AND")
    rules = filters.get("rules", [])
    
    if rules:
        filter_conditions = []
        for rule in rules:
            field = rule.get("field")
            op = rule.get("op")
            value = rule.get("value")
            value2 = rule.get("value2")
            
            # 白名单校验（防注入）
            allowed_filter_fields = {
                "login", "symbol", "user_name", "user_group", "country", "zipcode", "user_id",
                "user_balance", "user_credit", "positions_floating_pnl", "equity",
                "closed_sell_volume_lots", "closed_sell_count", "closed_sell_profit", "closed_sell_swap",
                "closed_sell_overnight_count", "closed_sell_overnight_volume_lots",
                "closed_buy_volume_lots", "closed_buy_count", "closed_buy_profit", "closed_buy_swap",
                "closed_buy_overnight_count", "closed_buy_overnight_volume_lots",
                "total_commission", "deposit_count", "deposit_amount", "withdrawal_count",
                "withdrawal_amount", "net_deposit", "closed_total_profit", "overnight_volume_ratio", "last_updated",
            }
            allowed_operators = {
                # 文本
                "contains", "not_contains", "equals", "not_equals", "starts_with", "ends_with", "blank", "not_blank",
                # 数字/日期
                "=", "!=", ">", ">=", "<", "<=", "between", "on", "before", "after",
            }
            
            if field not in allowed_filter_fields:
                continue  # 跳过非法字段
            if op not in allowed_operators:
                continue  # 跳过非法操作符
            
            # 映射 closed_total_profit 到数据库列
            db_field = "closed_total_profit_with_swap" if field == "closed_total_profit" else field
            
            # 生成 SQL 条件
            condition = _build_filter_condition(db_field, op, value, value2, params)
            if condition:
                filter_conditions.append(condition)
        
        # 组合所有筛选条件
        if filter_conditions:
            combined = f" ({f' {join_type} '.join(filter_conditions)})"
            where_conditions.append(combined)

where_clause = " WHERE " + " AND ".join(where_conditions) if where_conditions else ""
```

**辅助函数 `_build_filter_condition`**:
```python
def _build_filter_condition(field: str, op: str, value: Any, value2: Any, params: List) -> Optional[str]:
    """根据操作符构建 SQL WHERE 条件片段
    
    Args:
        field: 列名（已通过白名单校验）
        op: 操作符（已通过白名单校验）
        value: 主值
        value2: 副值（between 使用）
        params: 参数列表（用于 psycopg2 的 %s 占位符）
    
    Returns:
        SQL 条件字符串，如 "user_name ILIKE %s"；返回 None 表示跳过该条件
    """
    # 文本操作符
    if op == "contains":
        params.append(f"%{value}%")
        return f"{field} ILIKE %s"
    elif op == "not_contains":
        params.append(f"%{value}%")
        return f"{field} NOT ILIKE %s"
    elif op == "equals":
        params.append(value)
        return f"{field} = %s"
    elif op == "not_equals":
        params.append(value)
        return f"{field} != %s"
    elif op == "starts_with":
        params.append(f"{value}%")
        return f"{field} ILIKE %s"
    elif op == "ends_with":
        params.append(f"%{value}")
        return f"{field} ILIKE %s"
    elif op == "blank":
        return f"({field} IS NULL OR {field} = '')"
    elif op == "not_blank":
        return f"({field} IS NOT NULL AND {field} != '')"
    
    # 数字/日期操作符
    elif op == "=":
        params.append(value)
        return f"{field} = %s"
    elif op == "!=":
        params.append(value)
        return f"{field} != %s"
    elif op == ">":
        params.append(value)
        return f"{field} > %s"
    elif op == ">=":
        params.append(value)
        return f"{field} >= %s"
    elif op == "<":
        params.append(value)
        return f"{field} < %s"
    elif op == "<=":
        params.append(value)
        return f"{field} <= %s"
    elif op == "between":
        if value is None or value2 is None:
            return None  # 跳过无效区间
        params.append(value)
        params.append(value2)
        return f"{field} BETWEEN %s AND %s"
    
    # 日期特殊操作符
    elif op == "on":
        # 匹配整个日期（DATE(field) = value）
        params.append(value)
        return f"DATE({field}) = %s"
    elif op == "before":
        params.append(value)
        return f"DATE({field}) < %s"
    elif op == "after":
        params.append(value)
        return f"DATE({field}) > %s"
    
    return None
```

---

## 四、完整数据流图

```
┌─────────────────────────────────────────────────────────────────┐
│ 前端 CustomerPnLMonitorV2.tsx                                   │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ 用户操作
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ FilterBuilder 组件                                              │
│ - 用户选择列、操作符、输入值                                     │
│ - 点击"应用"                                                     │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ onApply(filterGroup)
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ handleApplyFilters(filters)                                     │
│ 1. setAppliedFilters(filters)                                   │
│ 2. localStorage 持久化                                          │
│ 3. setPageIndex(0) - 重置到第一页                               │
│ 4. console.log(JSON) - 静态阶段输出                             │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ useEffect 监听 appliedFilters 变化
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ fetchData() - 构建请求参数                                       │
│ const params = new URLSearchParams(...)                        │
│ if (appliedFilters && appliedFilters.rules.length > 0) {       │
│   params.set('filters_json',                                   │
│     encodeURIComponent(JSON.stringify(appliedFilters)))        │
│ }                                                               │
│ GET /api/v1/etl/pnl-user-summary/paginated?...&filters_json=...│
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ HTTP Request
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ 后端 FastAPI routes/etl.py                                      │
│ @router.get("/pnl-user-summary/paginated")                     │
│ def get_pnl_user_summary(                                      │
│   filters_json: Optional[str] = Query(None)                    │
│ ):                                                              │
│   filters_dict = json.loads(filters_json) if filters_json      │
│   # 白名单校验                                                  │
│   rows = get_pnl_user_summary_paginated(..., filters=filters_dict) │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ 调用服务层
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ 后端 services/etl_pg_service.py                                 │
│ def get_pnl_user_summary_paginated(                            │
│   filters: Optional[Dict[str, Any]] = None                     │
│ ):                                                              │
│   # 解析 filters.join 和 filters.rules                         │
│   # 字段与操作符白名单校验                                       │
│   # 生成 WHERE 子句片段                                         │
│   where_conditions.append("(rule1 AND/OR rule2 AND/OR ...)")   │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ SQL 查询
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ PostgreSQL 数据库 (MT5_ETL)                                     │
│ SELECT ... FROM public.pnl_user_summary                        │
│ WHERE ... AND (net_deposit < %s AND closed_total_profit > %s)  │
│ ORDER BY ... LIMIT ... OFFSET ...                              │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ 返回结果
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ 前端接收数据                                                     │
│ setRows(data)                                                   │
│ setTotalCount(total)                                            │
│ setTotalPages(total_pages)                                      │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ 渲染
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ AG Grid 表格显示筛选后的数据                                     │
│ 状态栏显示 Badge：净入金 < 0 AND 平仓总盈亏 > 0 (2 条)          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 五、关键注意事项

### 1. **安全性（SQL 注入防护）**
- ✅ 字段白名单：只允许预定义的列
- ✅ 操作符白名单：只允许安全的操作符
- ✅ 参数化查询：使用 `%s` 占位符 + psycopg2 参数绑定
- ❌ 禁止直接字符串拼接 SQL

### 2. **性能优化**
- 对高频筛选列建立索引：
  - `user_group`, `zipcode`, `net_deposit`, `closed_total_profit` (即 `closed_total_profit_with_swap`)
  - 文本模糊搜索：创建 trigram 索引 (`CREATE EXTENSION pg_trgm;`)
  - 数值区间：B-Tree 索引（PostgreSQL 默认）

### 3. **分页一致性**
- 筛选变更时必须 `setPageIndex(0)` 重置到第一页
- `total_count` 应反映筛选后的总数，而非全量数据

### 4. **错误处理**
- 后端应返回友好的错误信息（422 参数错误，500 服务器错误）
- 前端应捕获并显示错误信息（已有 `setError` state）

### 5. **类型转换**
- 日期筛选：前端发送 `yyyy-MM-dd` 字符串，后端转换为 DATE
- 数值筛选：确保 value 为 number 类型（前端 `toNumber` 转换）
- 布尔筛选：暂不支持（可扩展）

### 6. **字段映射**
- `closed_total_profit` 在后端需映射为 `closed_total_profit_with_swap`（已在服务层处理）

---

## 六、测试清单

### 前端测试
- [ ] 打开筛选器，选择"平仓总盈亏"列（应能找到）
- [ ] 添加单个规则：平仓总盈亏 > 1000
- [ ] 添加多个规则：AND/OR 切换
- [ ] 点击"应用"，查看 console 输出 JSON
- [ ] 切换服务器，验证筛选条件是否恢复
- [ ] 清空筛选，验证 Badge 消失

### 后端测试（对接后）
- [ ] 单个筛选：`?filters_json={"join":"AND","rules":[{"field":"net_deposit","op":"<","value":0}]}`
- [ ] 多个筛选（AND）：`net_deposit < 0 AND closed_total_profit > 0`
- [ ] 多个筛选（OR）：`user_group contains "KCM" OR user_group contains "AKCM"`
- [ ] 区间筛选：`closed_total_profit between 1000 and 5000`
- [ ] 空值筛选：`zipcode blank`
- [ ] 日期筛选：`last_updated on 2025-10-22`
- [ ] 文本筛选：`user_name contains "Li"`
- [ ] 非法字段：应拒绝或跳过
- [ ] 非法操作符：应拒绝或跳过
- [ ] 分页准确性：筛选后总数 + 分页导航正确

### 集成测试
- [ ] 筛选 + 排序 + 分页 组合
- [ ] 筛选 + 组别筛选 + 搜索 组合
- [ ] 移除单个规则后重新请求
- [ ] 清空所有规则后恢复全量数据

---

## 七、实施步骤

### 第 1 步：前端改动（最小化）
1. 修改 `fetchData` 依赖数组，添加 `appliedFilters`
2. 在 `URLSearchParams` 构建时添加 `filters_json` 参数

### 第 2 步：后端路由改动
1. 在 `routes/etl.py` 添加 `filters_json` 参数
2. 解析 JSON 并校验格式

### 第 3 步：后端服务改动
1. 在 `etl_pg_service.py` 的 `get_pnl_user_summary_paginated` 添加 `filters` 参数
2. 实现 `_build_filter_condition` 辅助函数
3. 生成 WHERE 子句并拼接

### 第 4 步：测试与调优
1. 单元测试：各种操作符组合
2. 集成测试：前后端联调
3. 性能测试：复杂筛选 + 大数据量
4. 创建索引优化查询速度

### 第 5 步：文档与部署
1. 更新 API 文档（Swagger）
2. 编写用户手册（筛选器使用指南）
3. 部署到测试环境验证
4. 生产环境发布

---

## 八、未来扩展

### 1. 预设筛选模板
允许用户保存常用筛选条件：
- "高风险客户"：净入金 < 0 AND 浮盈 < -1000
- "盈利客户"：平仓总盈亏 > 5000 AND 净入金 > 0

### 2. 嵌套筛选组
支持复杂逻辑：
```json
{
  "join": "OR",
  "groups": [
    {
      "join": "AND",
      "rules": [
        {"field": "net_deposit", "op": "<", "value": 0},
        {"field": "closed_total_profit", "op": ">", "value": 0}
      ]
    },
    {
      "join": "AND",
      "rules": [
        {"field": "user_group", "op": "contains", "value": "KCM"},
        {"field": "deposit_count", "op": ">", "value": 10}
      ]
    }
  ]
}
```

### 3. 导出筛选结果
提供"导出当前筛选"按钮：
- 后端接收相同的 `filters_json`
- 流式生成 CSV/Excel
- 返回下载链接

### 4. 筛选历史
记录用户最近 10 次筛选条件，快速重新应用。

---

**所有准备已就绪，只需对接后端 API 即可完成整个筛选框架！** 🚀

