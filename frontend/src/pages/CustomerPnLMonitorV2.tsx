import { useMemo } from "react"
import { useState, useEffect, useCallback, useRef } from "react"
import { useTheme } from "@/components/theme-provider"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Settings2, Search } from "lucide-react"
import { AgGridReact } from 'ag-grid-react'
import { ColDef, GridReadyEvent, SortChangedEvent } from 'ag-grid-community'
import { MultiSelectCombobox } from "@/components/ui/multi-select-combobox"
 

// 产品配置接口
interface ProductConfig {
  account_type: 'standard' | 'cent'
  volume_divisor: number
  display_divisor: number
  currency: string
  description: string
}

// backend API response schema aligned with public.pnl_user_summary
interface PnlSummaryRow {
  // 主键与维度
  login: number | string
  symbol: string

  // 用户信息
  user_name?: string | null
  user_group?: string | null
  country?: string | null
  zipcode?: string | null
  user_id?: number | string | null

  // 账户与浮盈
  user_balance: number | string
  user_credit: number | string
  positions_floating_pnl: number | string
  equity: number | string

  // 平仓统计（SELL，平多）
  closed_sell_volume_lots: number | string
  closed_sell_count: number | string
  closed_sell_profit: number | string
  closed_sell_swap: number | string
  closed_sell_overnight_count: number | string
  closed_sell_overnight_volume_lots: number | string

  // 平仓统计（BUY，平空）
  closed_buy_volume_lots: number | string
  closed_buy_count: number | string
  closed_buy_profit: number | string
  closed_buy_swap: number | string
  closed_buy_overnight_count: number | string
  closed_buy_overnight_volume_lots: number | string

  // 佣金 & 资金
  total_commission: number | string
  deposit_count: number | string
  deposit_amount: number | string
  withdrawal_count: number | string
  withdrawal_amount: number | string
  net_deposit: number | string

  // 审计
  last_updated?: string | null
}

// 分页查询响应接口
interface PaginatedPnlSummaryResponse {
  ok: boolean
  data: PnlSummaryRow[]
  total: number
  page: number
  page_size: number
  total_pages: number
  error?: string
  product_config?: ProductConfig
}

function formatCurrency(value: number, productConfig?: ProductConfig) {
  // 根据产品配置调整显示值（美分账户需要/100）
  const displayDivisor = productConfig?.display_divisor || 1.0
  const adjustedValue = value / displayDivisor
  
  const sign = adjustedValue >= 0 ? "" : "-"
  const abs = Math.abs(adjustedValue)
  const currency = productConfig?.currency || 'USD'
  const symbol = currency === 'USD' ? '$' : currency
  
  return `${sign}${symbol}${abs.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
}

function toNumber(v: unknown, fallback = 0): number {
  if (typeof v === "number" && Number.isFinite(v)) return v
  if (typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v))) return Number(v)
  return fallback
}

function fetchWithTimeout(url: string, options: any = {}, timeout = 15000) {
  const controller = new AbortController()
  const id = setTimeout(() => controller.abort(), timeout)
  const opts = { ...options, signal: controller.signal }
  return fetch(url, opts).finally(() => clearTimeout(id))
}

export default function CustomerPnLMonitorV2() {
  const { theme } = useTheme()
  // server filter
  const [server, setServer] = useState<string>("MT5")
  
  // 用户组别筛选
  const [userGroups, setUserGroups] = useState<string[]>(["__ALL__"])
  const [availableGroups, setAvailableGroups] = useState<Array<{value: string, label: string}>>([])
  const [isLoadingGroups, setIsLoadingGroups] = useState(false)

  // 统一搜索：客户ID或客户名称（前端输入，后端检索）
  // fresh grad note: keep two states for debounce - immediate input and debounced value
  const [searchInput, setSearchInput] = useState("")
  const [searchDebounced, setSearchDebounced] = useState("")

  

  // data state and refresh
  const [rows, setRows] = useState<PnlSummaryRow[]>([])
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const [productConfig, setProductConfig] = useState<ProductConfig | null>(null)
  const AUTO_REFRESH_MS = 10 * 60 * 1000 // 10 minutes

  // 分页状态管理
  const [pageIndex, setPageIndex] = useState(0)
  const [pageSize, setPageSize] = useState(50)
  const [totalCount, setTotalCount] = useState(0)
  const [totalPages, setTotalPages] = useState(0)

  // AG Grid 状态管理
  const [sortModel, setSortModel] = useState<any[]>([])
  // 列可见性（供社区版列显示切换用）
  const [columnVisibility, setColumnVisibility] = useState<Record<string, boolean>>({
    // 默认显示的列：在这里调整 true/false 即可控制“默认显示”
    login: true,
    user_name: false,
    user_group: true,
    symbol: false,
    country: false,
    zipcode: true,
    user_id: false,

    user_balance: true,
    user_credit: false,
    positions_floating_pnl: true,
    equity: false,

    closed_sell_volume_lots: false,
    closed_sell_count: false,
    closed_sell_profit: false,
    closed_sell_swap: false,
    closed_sell_overnight_count: false,
    closed_sell_overnight_volume_lots: false,

    closed_buy_volume_lots: false,
    closed_buy_count: false,
    closed_buy_profit: false,
    closed_buy_swap: false,
    closed_buy_overnight_count: false,
    closed_buy_overnight_volume_lots: false,

    total_commission: true,
    deposit_count: false,
    deposit_amount: true,
    withdrawal_count: false,
    withdrawal_amount: true,
    net_deposit: true,

    last_updated: true,
  })
  const [gridApi, setGridApi] = useState<any>(null)
  const gridContainerRef = useRef<HTMLDivElement | null>(null)

  // AG Grid 列定义
  const columnDefs = useMemo<ColDef<PnlSummaryRow>[]>(() => [
    {
      field: "login",
      headerName: "账户ID",
      width: 120,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => {
        // 只有MT5服务器才显示为可点击链接
        if (server === "MT5") {
          return (
            <a 
              href={`https://mt4.kohleglobal.com/crm/accounts/5-${params.value}`}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 underline hover:no-underline transition-colors cursor-pointer"
              onClick={(e) => {
                // 防止触发AG Grid的行选择事件
                e.stopPropagation()
              }}
            >
              {params.value}
            </a>
          )
        } else {
          // 其他服务器显示为普通文本
          return (
            <span className="font-medium">{params.value}</span>
          )
        }
      },
      hide: !columnVisibility.login,
    },
    {
      field: "user_name",
      headerName: "客户名称",
      width: 180,
      minWidth: 150,
      maxWidth: 300,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="max-w-[180px] truncate">
          {params.value || `客户-${params.data.login}`}
        </span>
      ),
      hide: !columnVisibility.user_name,
    },
    {
      field: "user_group",
      headerName: "Group",
      width: 140,
      minWidth: 120,
      maxWidth: 220,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-muted-foreground">{params.value || ""}</span>
      ),
      hide: !columnVisibility.user_group,
    },
    {
      field: "country",
      headerName: "国家/地区",
      width: 120,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-muted-foreground">{params.value || ""}</span>
      ),
      hide: !columnVisibility.country,
    },
    {
      field: "zipcode",
      headerName: "ZipCode",
      width: 120,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-muted-foreground">{params.value || ""}</span>
      ),
      hide: !columnVisibility.zipcode,
    },
    {
      field: "user_id",
      headerName: "ClientID",
      width: 140,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-muted-foreground">{params.value || ""}</span>
      ),
      hide: !columnVisibility.user_id,
    },
    {
      field: "symbol",
      headerName: "Symbol",
      width: 140,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="font-mono text-sm">
          {params.value}
        </span>
      ),
      hide: !columnVisibility.symbol,
    },
    {
      field: "user_balance",
      headerName: "balance",
      width: 140,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => {
        const value = toNumber(params.value)
        return (
          <span 
            className={`text-right ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}
          >
            {formatCurrency(value, productConfig || undefined)}
          </span>
        )
      },
      hide: !columnVisibility.user_balance,
    },
    {
      field: "positions_floating_pnl",
      headerName: "持仓浮动盈亏",
      width: 150,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => {
        const value = toNumber(params.value)
        return (
          <span 
            className={`text-right ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}
          >
            {formatCurrency(value, productConfig || undefined)}
          </span>
        )
      },
      hide: !columnVisibility.positions_floating_pnl,
    },
    {
      field: "equity",
      headerName: "equity",
      width: 150,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => {
        const value = toNumber(params.value)
        return (
          <span 
            className={`text-right ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}
          >
            {formatCurrency(value, productConfig || undefined)}
          </span>
        )
      },
      hide: !columnVisibility.equity,
    },
    // SELL 平仓统计
    {
      field: "closed_sell_volume_lots",
      headerName: "closed_sell_volume_lots",
      width: 120,
      minWidth: 90,
      maxWidth: 150,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-right tabular-nums">
          {toNumber(params.value).toLocaleString()}
        </span>
      ),
      hide: !columnVisibility.closed_sell_volume_lots,
    },
    {
      field: "closed_sell_count",
      headerName: "closed_sell_count",
      width: 140,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-right tabular-nums">
          {toNumber(params.value).toLocaleString()}
        </span>
      ),
      hide: !columnVisibility.closed_sell_count,
    },
    {
      field: "closed_sell_profit",
      headerName: "closed_sell_profit",
      width: 140,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => {
        const value = toNumber(params.value)
        return (
          <span className={`text-right ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}>
            {formatCurrency(value, productConfig || undefined)}
          </span>
        )
      },
      hide: !columnVisibility.closed_sell_profit,
    },
    {
      field: "closed_sell_swap",
      headerName: "closed_sell_swap",
      width: 140,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => {
        const value = toNumber(params.value)
        return (
          <span className={`text-right ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}>
            {formatCurrency(value, productConfig || undefined)}
          </span>
        )
      },
      hide: !columnVisibility.closed_sell_swap,
    },
    {
      field: "closed_sell_overnight_count",
      headerName: "closed_sell_overnight_count",
      width: 140,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-right tabular-nums">
          {toNumber(params.value).toLocaleString()}
        </span>
      ),
      hide: !columnVisibility.closed_sell_overnight_count,
    },
    {
      field: "closed_sell_overnight_volume_lots",
      headerName: "closed_sell_overnight_volume_lots",
      width: 160,
      minWidth: 120,
      maxWidth: 220,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-right tabular-nums">
          {toNumber(params.value).toLocaleString()}
        </span>
      ),
      hide: !columnVisibility.closed_sell_overnight_volume_lots,
    },
    // BUY 平仓统计
    {
      field: "closed_buy_volume_lots",
      headerName: "closed_buy_volume_lots",
      width: 140,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-right tabular-nums">
          {toNumber(params.value).toLocaleString()}
        </span>
      ),
      hide: !columnVisibility.closed_buy_volume_lots,
    },
    {
      field: "closed_buy_count",
      headerName: "closed_buy_count",
      width: 140,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-right tabular-nums">
          {toNumber(params.value).toLocaleString()}
        </span>
      ),
      hide: !columnVisibility.closed_buy_count,
    },
    {
      field: "closed_buy_profit",
      headerName: "closed_buy_profit",
      width: 140,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => {
        const value = toNumber(params.value)
        return (
          <span className={`text-right ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}>
            {formatCurrency(value, productConfig || undefined)}
          </span>
        )
      },
      hide: !columnVisibility.closed_buy_profit,
    },
    {
      field: "closed_buy_swap",
      headerName: "closed_buy_swap",
      width: 140,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => {
        const value = toNumber(params.value)
        return (
          <span className={`text-right ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}>
            {formatCurrency(value, productConfig || undefined)}
          </span>
        )
      },
      hide: !columnVisibility.closed_buy_swap,
    },
    {
      field: "closed_buy_overnight_count",
      headerName: "closed_buy_overnight_count",
      width: 160,
      minWidth: 120,
      maxWidth: 220,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-right tabular-nums">
          {toNumber(params.value).toLocaleString()}
        </span>
      ),
      hide: !columnVisibility.closed_buy_overnight_count,
    },
    {
      field: "closed_buy_overnight_volume_lots",
      headerName: "closed_buy_overnight_volume_lots",
      width: 160,
      minWidth: 120,
      maxWidth: 220,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-right tabular-nums">
          {toNumber(params.value).toLocaleString()}
        </span>
      ),
      hide: !columnVisibility.closed_buy_overnight_volume_lots,
    },
    // 佣金 & 资金
    {
      field: "total_commission",
      headerName: "total_commission",
      width: 140,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => {
        const value = toNumber(params.value)
        return (
          <span className="text-right">{formatCurrency(value, productConfig || undefined)}</span>
        )
      },
      hide: !columnVisibility.total_commission,
    },
    {
      field: "deposit_count",
      headerName: "入金笔数",
      width: 120,
      minWidth: 100,
      maxWidth: 180,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-right tabular-nums">{toNumber(params.value).toLocaleString()}</span>
      ),
      hide: !columnVisibility.deposit_count,
    },
    {
      field: "deposit_amount",
      headerName: "入金金额",
      width: 140,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-right">{formatCurrency(toNumber(params.value), productConfig || undefined)}</span>
      ),
      hide: !columnVisibility.deposit_amount,
    },
    {
      field: "withdrawal_count",
      headerName: "出金笔数",
      width: 120,
      minWidth: 100,
      maxWidth: 180,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-right tabular-nums">{toNumber(params.value).toLocaleString()}</span>
      ),
      hide: !columnVisibility.withdrawal_count,
    },
    {
      field: "withdrawal_amount",
      headerName: "出金金额",
      width: 140,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-right">{formatCurrency(toNumber(params.value), productConfig || undefined)}</span>
      ),
      hide: !columnVisibility.withdrawal_amount,
    },
    {
      field: "net_deposit",
      headerName: "net_deposit",
      width: 140,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => {
        const value = toNumber(params.value)
        return (
          <span className={`text-right ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}>
            {formatCurrency(value, productConfig || undefined)}
          </span>
        )
      },
      hide: !columnVisibility.net_deposit,
    },
    {
      field: "last_updated",
      headerName: "更新时间",
      width: 180,
      minWidth: 160,
      maxWidth: 220,
      sortable: true,
      filter: false,
      cellRenderer: (params: any) => (
        <span className="whitespace-nowrap text-muted-foreground">
          {params.value ? new Date(params.value).toLocaleString() : ""}
        </span>
      ),
      hide: !columnVisibility.last_updated,
    },
  ], [productConfig, columnVisibility, server])

  // AG Grid 事件处理函数
  const onGridReady = useCallback((params: GridReadyEvent) => {
    setGridApi(params.api)
    // ensure columns fit container when grid is ready
    try { params.api.sizeColumnsToFit() } catch {}
  }, [])

  const onSortChanged = useCallback((event: SortChangedEvent) => {
    const sortModel = event.api.getColumnState()
      .filter(col => col.sort !== null)
      .map(col => ({ colId: col.colId, sort: col.sort }))
    setSortModel(sortModel)
    // 在这里可以触发后端排序请求
  }, [])

  // 持久化表格状态（已移除列可见性，仅保留排序如需扩展可在此）
  useEffect(() => {
    try {
      const tableState = {
        sortModel,
      }
      localStorage.setItem("pnl_table_state", JSON.stringify(tableState))
    } catch {}
  }, [sortModel])

  // 恢复表格状态（仅恢复排序）
  useEffect(() => {
    try {
      const saved = localStorage.getItem("pnl_table_state")
      if (saved) {
        const state = JSON.parse(saved)
        if (state.sortModel) setSortModel(state.sortModel)
      }
    } catch {}
  }, [])

  // 获取用户组别列表
  const fetchUserGroups = useCallback(async () => {
    if (server !== "MT5") {
      setAvailableGroups([])
      return
    }
    
    setIsLoadingGroups(true)
    try {
      const url = `/api/v1/pnl/groups?server=${server}`
      const res = await fetchWithTimeout(url, { headers: { accept: "application/json" } }, 10000)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const groups = await res.json()
      
      // 转换为选项格式
      const groupOptions = groups.map((group: string) => ({
        value: group,
        label: group
      }))
      setAvailableGroups(groupOptions)
    } catch (e) {
      console.error("获取用户组别失败:", e)
      setAvailableGroups([])
    } finally {
      setIsLoadingGroups(false)
    }
  }, [server])

  // 搜索输入防抖处理（300ms）
  useEffect(() => {
    const handler = setTimeout(() => {
      setSearchDebounced(searchInput.trim())
    }, 300)
    return () => clearTimeout(handler)
  }, [searchInput])

  // GET 拉取后端数据（分页查询）
  const fetchData = useCallback(async (
    page?: number, 
    newPageSize?: number, 
    sortBy?: string, 
    sortOrder?: string
  ) => {
    // 非 MT5 服务器：直接显示无数据并跳过请求
    if (server !== "MT5") {
      setTotalCount(0)
      setTotalPages(0)
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
    
    if (currentSortBy) {
      params.set('sort_by', currentSortBy)
      params.set('sort_order', currentSortOrder)
    }
    
    // 添加用户组别筛选参数
    if (userGroups && userGroups.length > 0) {
      if (userGroups.includes("__ALL__")) {
        // 选择了"全部组别"，不发送筛选参数（查询所有数据）
      } else {
        // 选择了具体组别，发送这些组别
        params.set('user_groups', userGroups.join(','))
      }
    } else {
      // 没有选择任何组别，发送特殊标识符表示返回0条数据
      params.set('user_groups', '__NONE__')
    }

    // 添加统一搜索参数（客户ID精确或客户名称模糊，由后端实现）
    if (searchDebounced) {
      params.set('search', searchDebounced)
    }

    // 切换为新的 ETL API（直查 PostgreSQL 的 pnl_user_summary）
    const url = `/api/v1/etl/pnl-user-summary/paginated?${params.toString()}`
    const res = await fetchWithTimeout(url, { headers: { accept: "application/json" } }, 20000)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const payload = (await res.json()) as PaginatedPnlSummaryResponse
    if (!payload?.ok) throw new Error(payload?.error || "加载失败")
    
    // 新接口不返回产品配置，显式清空以避免沿用旧值
    setProductConfig(null)
    
    // 设置分页信息
    setTotalCount(payload.total)
    setTotalPages(payload.total_pages)
    
    return Array.isArray(payload.data) ? payload.data : []
  }, [server, pageIndex, pageSize, sortModel, userGroups, searchDebounced])

  

  // 监听服务器变化，获取组别列表
  useEffect(() => {
    fetchUserGroups()
    // 服务器变化时重置组别选择
    setUserGroups(["__ALL__"])
  }, [server, fetchUserGroups])

  // 监听分页、排序、服务器/品种变化，自动重新获取数据
  useEffect(() => {
    ;(async () => {
      try {
        setError(null)
        const data = await fetchData()
        setRows(data)
        setLastUpdated(new Date())
        // after data loaded, try fit columns
        try { gridApi?.sizeColumnsToFit() } catch {}
      } catch (e) {
        setRows([])
        setError(e instanceof Error ? e.message : "加载失败")
      }
    })()
  }, [pageIndex, pageSize, sortModel, server, userGroups, searchDebounced])

  // 观察容器尺寸变化，触发布局与列宽自适应
  useEffect(() => {
    if (!gridContainerRef.current) return
    if (!gridApi) return
    const ro = new ResizeObserver(() => {
      try { gridApi.sizeColumnsToFit() } catch {}
    })
    ro.observe(gridContainerRef.current)
    return () => ro.disconnect()
  }, [gridApi])

  // auto-refresh: every 10 minutes trigger ETL for all products, then fetch current data
  useEffect(() => {
    const t = setInterval(() => {
      ;(async () => {
        try {
          try {
            const res = await fetchWithTimeout(`/api/v1/pnl/summary/refresh`, {
              method: "POST",
              headers: { "Content-Type": "application/json", accept: "application/json" },
              body: JSON.stringify({ server, symbol: "__ALL__" }),
            }, 30000)
            const refreshResult = await res.json()
            if (refreshResult?.status === 'success') {
              const details: string[] = []
              if (typeof refreshResult.processed_rows === 'number') {
                details.push(`处理了 ${refreshResult.processed_rows} 行数据`)
              }
              if (typeof refreshResult.duration_seconds === 'number') {
                details.push(`耗时 ${Number(refreshResult.duration_seconds).toFixed(1)} 秒`)
              }
              const msg = details.length > 0 ? `${refreshResult.message} (${details.join(', ')})` : refreshResult.message
              setSuccessMessage(msg)
            }
          } catch {}
          const data = await fetchData()
          setRows(data)
          setLastUpdated(new Date())
          if (error) setError(null)
        } catch (e) {
          setSuccessMessage(null)
          setError(e instanceof Error ? e.message : "自动刷新失败")
        }
      })()
    }, AUTO_REFRESH_MS)
    return () => clearInterval(t)
  }, [fetchData, server])

  // 成功提示自动清除（10秒）
  useEffect(() => {
    if (!successMessage) return
    const t = setTimeout(() => setSuccessMessage(null), 10000)
    return () => clearTimeout(t)
  }, [successMessage])

  return (
    <div className="flex h-full w-full flex-col gap-2 p-1 sm:p-4">
      {/* filter & actions card: responsive layout per guide */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">客户盈亏监控 - 筛选</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 md:gap-x-9 md:gap-y-3">
              {/* server select */}
              <div className="flex items-center gap-1">
                <span className="text-sm text-muted-foreground whitespace-nowrap w-12">服务器</span>
                <Select value={server} onValueChange={setServer}>
                  <SelectTrigger className="h-9 w-52">
                    <SelectValue placeholder="选择服务器" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="MT4Live">MT4Live</SelectItem>
                    <SelectItem value="MT4Live2">MT4Live2</SelectItem>
                    <SelectItem value="MT5">MT5</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* product select removed as requested */}

              {/* user group select */}
              <div className="flex items-center gap-1">
                <span className="text-sm text-muted-foreground whitespace-nowrap w-12">组别</span>
                <MultiSelectCombobox
                  options={availableGroups}
                  value={userGroups}
                  onValueChange={setUserGroups}
                  placeholder={isLoadingGroups ? "加载组别中..." : "选择组别..."}
                  searchPlaceholder="搜索组别..."
                  className="w-52"
                />
              </div>

              {/* unified search: customer id or name */}
              <div className="flex items-center gap-1">
                <span className="text-sm text-muted-foreground whitespace-nowrap w-12">搜索</span>
                <div className="relative w-52">
                  <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    value={searchInput}
                    onChange={(e) => {
                      setSearchInput(e.target.value)
                      // when search changes, reset to first page
                      setPageIndex(0)
                    }}
                    placeholder="客户ID或名称..."
                    className="pl-8 h-9"
                  />
                </div>
              </div>

            </div>

            {/* actions */}
            <div className="flex flex-col sm:flex-row sm:items-center gap-2 w-full sm:w-auto">
              <Button disabled className="h-9 w-full sm:w-auto" title="暂未接入API，刷新已禁用">
                刷新已禁用
              </Button>
            </div>
          </div>

          {/* mobile hint row removed to avoid duplication */}
        </CardContent>
      </Card>

      {/* 刷新结果消息显示区域 */}
      {error && (
        <div className="px-1 sm:px-0">
          <div className="flex items-center gap-2 px-4 py-3 bg-red-50 dark:bg-red-950/20 border border-red-200 dark:border-red-800 rounded-lg">
            <div className="flex-shrink-0">
              <svg className="w-4 h-4 text-red-600 dark:text-red-400" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
              </svg>
            </div>
            <p className="text-sm text-red-800 dark:text-red-200">{error}</p>
          </div>
        </div>
      )}


      {/* 状态栏 + 列显示切换 */}
      <Card>
        <CardContent className="py-3">
          <div className="flex items-center justify-between gap-3">
            {/* 左侧状态信息 */}
            <div className="flex flex-wrap items-center gap-3 text-xs sm:text-sm text-muted-foreground">
              <span>共 {totalCount} 条记录</span>
              <span>当前页 {pageIndex + 1}/{totalPages}</span>
              {sortModel.length > 0 && (
                <span className="px-2 py-1 bg-purple-100 dark:bg-purple-900/20 rounded text-purple-700 dark:text-purple-300">
                  排序: {sortModel.map(s => `${s.colId} ${s.sort === 'desc' ? '↓' : '↑'}`).join(', ')}
                </span>
              )}
              {lastUpdated && (
                <span className="px-2 py-1 bg-slate-100 dark:bg-slate-800/40 rounded">
                  上次刷新：{lastUpdated.toLocaleString()}
                </span>
              )}
              {successMessage && (
                <span className="px-2 py-1 bg-green-100 dark:bg-green-900/20 rounded text-green-700 dark:text-green-300">
                  {successMessage}
                </span>
              )}
            </div>
            {/* 右侧：列显示切换按钮 */}
            <div className="flex items-center">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" className="h-9 gap-2 whitespace-nowrap">
                    <Settings2 className="h-4 w-4" />
                    列显示切换
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-56">
                  <DropdownMenuLabel>显示列</DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  {Object.entries(columnVisibility).map(([columnId, isVisible]) => {
                    const columnLabels: Record<string, string> = {
                      login: "账户ID",
                      user_name: "客户名称",
                      user_group: "Group",
                      symbol: "Symbol",
                      country: "国家/地区",
                      zipcode: "ZipCode",
                      user_id: "ClientID",
                      user_balance: "balance",
                      user_credit: "credit",
                      positions_floating_pnl: "持仓浮动盈亏",
                      equity: "equity",
                      closed_sell_volume_lots: "closed_sell_volume_lots",
                      closed_sell_count: "closed_sell_count",
                      closed_sell_profit: "closed_sell_profit",
                      closed_sell_swap: "closed_sell_swap",
                      closed_sell_overnight_count: "closed_sell_overnight_count",
                      closed_sell_overnight_volume_lots: "closed_sell_overnight_volume_lots",
                      closed_buy_volume_lots: "closed_buy_volume_lots",
                      closed_buy_count: "closed_buy_count",
                      closed_buy_profit: "closed_buy_profit",
                      closed_buy_swap: "closed_buy_swap",
                      closed_buy_overnight_count: "closed_buy_overnight_count",
                      closed_buy_overnight_volume_lots: "closed_buy_overnight_volume_lots",
                      total_commission: "total_commission",
                      deposit_count: "入金笔数",
                      deposit_amount: "入金金额",
                      withdrawal_count: "出金笔数",
                      withdrawal_amount: "出金金额",
                      net_deposit: "net_deposit",
                      last_updated: "更新时间",
                    }
                    return (
                      <DropdownMenuCheckboxItem
                        key={columnId}
                        checked={isVisible}
                        onCheckedChange={(value: boolean) => 
                          setColumnVisibility(prev => ({ ...prev, [columnId]: !!value }))
                        }
                      >
                        {columnLabels[columnId] || columnId}
                      </DropdownMenuCheckboxItem>
                    )
                  })}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* AG Grid Table */}
      <div className="flex-1">
        {/* ag-grid requires an explicit height on the container */}
        <div
          ref={gridContainerRef}
          className={`${(theme === 'dark' || (theme === 'system' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)) ? 'ag-theme-quartz-dark' : 'ag-theme-quartz'} h-[600px] w-full min-h-[400px] relative`}
        >
          <AgGridReact
            rowData={rows}
            columnDefs={columnDefs}
            gridOptions={{ theme: 'legacy' }}
            defaultColDef={{
              sortable: true,
              filter: true,
              resizable: true,
              flex: 1,
              minWidth: 100,
            }}
            onGridReady={onGridReady}
            onSortChanged={onSortChanged}
            animateRows={true}
            rowSelection="multiple"
            suppressRowClickSelection={true}
            enableCellTextSelection={true}
            domLayout="normal"
            getRowStyle={(params) => {
              if (params.node.rowIndex && params.node.rowIndex % 2 === 0) {
                return { backgroundColor: 'var(--ag-background-color)' }
              }
              return { backgroundColor: 'var(--ag-odd-row-background-color)' }
            }}
          />
        </div>
      </div>

      {/* 分页控件 */}
      <Card>
        <CardContent className="py-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            {/* 左侧：显示信息和每页条数选择 */}
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:space-x-4">
              <div className="text-sm text-muted-foreground">
                显示 {pageIndex * pageSize + 1} 到{" "}
                {Math.min((pageIndex + 1) * pageSize, totalCount)}{" "}
                条，共 {totalCount} 条记录
              </div>
              
              {/* 每页条数选择 */}
              <div className="flex items-center space-x-2">
                <span className="text-sm text-muted-foreground">每页显示</span>
                <Select
                  value={pageSize.toString()}
                  onValueChange={(value: string) => {
                    const newSize = Number(value)
                    setPageSize(newSize)
                    setPageIndex(0)
                    // 使用后端分页，无需调用 ag-Grid 内部分页 API
                  }}
                >
                  <SelectTrigger className="h-8 w-20">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {[50, 100, 200, 300, 500].map((size) => (
                      <SelectItem key={size} value={size.toString()}>
                        {size}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <span className="text-sm text-muted-foreground">条</span>
              </div>
            </div>

            {/* 右侧：分页按钮 */}
            <div className="flex items-center flex-wrap gap-2 w-full sm:w-auto justify-center sm:justify-end">
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setPageIndex(0)
                }}
                disabled={pageIndex === 0}
              >
                首页
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  const newIndex = Math.max(0, pageIndex - 1)
                  setPageIndex(newIndex)
                }}
                disabled={pageIndex === 0}
              >
                上一页
              </Button>
              
              {/* 页码显示 */}
              <div className="flex items-center space-x-1">
                <span className="text-sm text-muted-foreground">
                  第 {pageIndex + 1} 页，共 {totalPages} 页
                </span>
              </div>
              
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  const newIndex = Math.min(totalPages - 1, pageIndex + 1)
                  setPageIndex(newIndex)
                }}
                disabled={pageIndex >= totalPages - 1}
              >
                下一页
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  const lastIndex = totalPages - 1
                  setPageIndex(lastIndex)
                }}
                disabled={pageIndex >= totalPages - 1}
              >
                末页
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}





