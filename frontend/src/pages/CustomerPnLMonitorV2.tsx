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
import { SimpleMultiSelect } from "@/components/ui/simple-multi-select"

// 产品配置接口
interface ProductConfig {
  account_type: 'standard' | 'cent'
  volume_divisor: number
  display_divisor: number
  currency: string
  description: string
}

// backend API response schema aligned with reporting pnl_summary
interface PnlSummaryRow {
  login: number | string
  symbol: string
  user_group?: string | null
  user_name?: string | null
  country?: string | null
  balance?: number | string | null
  total_closed_trades: number | string
  buy_trades_count: number | string
  sell_trades_count: number | string
  total_closed_volume: number | string
  buy_closed_volume: number | string
  sell_closed_volume: number | string
  total_closed_pnl: number | string
  floating_pnl: number | string
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
  // server/product filters
  const [server, setServer] = useState<string>("MT5")
  const [symbols, setSymbols] = useState<string[]>(["__ALL__"])
  
  // 用户组别筛选
  const [userGroups, setUserGroups] = useState<string[]>(["__ALL__"])
  const [availableGroups, setAvailableGroups] = useState<Array<{value: string, label: string}>>([])
  const [isLoadingGroups, setIsLoadingGroups] = useState(false)

  // 统一搜索：客户ID或客户名称（前端输入，后端检索）
  // fresh grad note: keep two states for debounce - immediate input and debounced value
  const [searchInput, setSearchInput] = useState("")
  const [searchDebounced, setSearchDebounced] = useState("")

  // 品种选项列表
  const symbolOptions = [
    { value: "XAUUSD.kcmc", label: "XAUUSD.kcmc " },
    { value: "XAUUSD.kcm", label: "XAUUSD.kcm " },
    { value: "XAUUSD", label: "XAUUSD " },
    { value: "XAUUSD.cent", label: "XAUUSD.cent " },
  ]

  // data state and refresh
  const [rows, setRows] = useState<PnlSummaryRow[]>([])
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [isRefreshing, setIsRefreshing] = useState(false)
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
    login: true,
    user_name: true,
    symbol: true,
    balance: true,
    total_closed_pnl: true,
    floating_pnl: true,
    total_closed_volume: false,
    total_closed_trades: false,
    last_updated: true,
    user_group: true,
    country: false,
    buy_trades_count: false,
    sell_trades_count: false,
    buy_closed_volume: false,
    sell_closed_volume: false,
  })
  const [gridApi, setGridApi] = useState<any>(null)
  const gridContainerRef = useRef<HTMLDivElement | null>(null)

  // AG Grid 列定义
  const columnDefs = useMemo<ColDef<PnlSummaryRow>[]>(() => [
    {
      field: "login",
      headerName: "客户ID",
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
      headerName: "组别",
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
      field: "symbol",
      headerName: "交易产品",
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
      field: "balance",
      headerName: "余额",
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
      hide: !columnVisibility.balance,
    },
    {
      field: "total_closed_pnl",
      headerName: "平仓总盈亏",
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
      hide: !columnVisibility.total_closed_pnl,
    },
    {
      field: "floating_pnl",
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
      hide: !columnVisibility.floating_pnl,
    },
    {
      field: "total_closed_volume",
      headerName: "总成交量",
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
      hide: !columnVisibility.total_closed_volume,
    },
    {
      field: "buy_closed_volume",
      headerName: "买单成交量",
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
      hide: !columnVisibility.buy_closed_volume,
    },
    {
      field: "sell_closed_volume",
      headerName: "卖单成交量",
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
      hide: !columnVisibility.sell_closed_volume,
    },
    {
      field: "total_closed_trades",
      headerName: "平仓交易笔数",
      width: 140,
      minWidth: 100,
      maxWidth: 180,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-right tabular-nums">
          {toNumber(params.value).toLocaleString()}
        </span>
      ),
      hide: !columnVisibility.total_closed_trades,
    },
    {
      field: "buy_trades_count",
      headerName: "买单笔数",
      width: 120,
      minWidth: 90,
      maxWidth: 180,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-right tabular-nums">
          {toNumber(params.value).toLocaleString()}
        </span>
      ),
      hide: !columnVisibility.buy_trades_count,
    },
    {
      field: "sell_trades_count",
      headerName: "卖单笔数",
      width: 120,
      minWidth: 90,
      maxWidth: 180,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="text-right tabular-nums">
          {toNumber(params.value).toLocaleString()}
        </span>
      ),
      hide: !columnVisibility.sell_trades_count,
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
    const currentPage = page ?? pageIndex + 1
    const currentPageSize = newPageSize ?? pageSize
    const currentSortBy = sortBy ?? (sortModel.length > 0 ? sortModel[0].colId : undefined)
    const currentSortOrder = sortOrder ?? (sortModel.length > 0 ? sortModel[0].sort : 'asc')
    
    // 处理多品种选择
    let symbolParam = "__ALL__"
    if (symbols && symbols.length > 0) {
      if (symbols.includes("__ALL__")) {
        symbolParam = "__ALL__"
      } else {
        symbolParam = symbols.join(",")
      }
    }
    
    const params = new URLSearchParams({
      server: server,
      symbols: symbolParam,
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
    
    const url = `/api/v1/pnl/summary/paginated?${params.toString()}`
    const res = await fetchWithTimeout(url, { headers: { accept: "application/json" } }, 20000)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const payload = (await res.json()) as PaginatedPnlSummaryResponse
    if (!payload?.ok) throw new Error(payload?.error || "加载失败")
    
    // 设置产品配置
    if (payload.product_config) {
      setProductConfig(payload.product_config)
    }
    
    // 设置分页信息
    setTotalCount(payload.total)
    setTotalPages(payload.total_pages)
    
    return Array.isArray(payload.data) ? payload.data : []
  }, [server, symbols, pageIndex, pageSize, sortModel, userGroups, searchDebounced])

  const refreshNow = useCallback(async () => {
    setIsRefreshing(true)
    try {
      setError(null)
      setSuccessMessage(null)
      
      // 1) 执行ETL同步（现在是同步等待完成）
      // 对于刷新操作，始终同步所有产品数据
      const refreshResponse = await fetchWithTimeout(`/api/v1/pnl/summary/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json", accept: "application/json" },
        body: JSON.stringify({ server, symbol: "__ALL__" }),
      }, 30000)
      
      const refreshResult = await refreshResponse.json()
      
      if (refreshResult.status === "success") {
        const details: string[] = []
        if (typeof refreshResult.processed_rows === 'number') {
          details.push(`处理了 ${refreshResult.processed_rows} 行数据`)
        }
        if (typeof refreshResult.duration_seconds === 'number') {
          details.push(`耗时 ${Number(refreshResult.duration_seconds).toFixed(1)} 秒`)
        }
        const msg = details.length > 0 ? `${refreshResult.message} (${details.join(', ')})` : refreshResult.message
        setSuccessMessage(msg)
      } else {
        setSuccessMessage(null)
        setError(`${refreshResult.message}${refreshResult.error_details ? `: ${refreshResult.error_details}` : ''}`)
      }
      
      // 2) 拉取最新数据
      const data = await fetchData()
      setRows(data)
      setLastUpdated(new Date())
      
    } catch (e) {
      setSuccessMessage(null)
      setError(e instanceof Error ? e.message : "刷新失败")
    } finally {
      setIsRefreshing(false)
    }
  }, [fetchData, server])

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
  }, [pageIndex, pageSize, sortModel, server, symbols, userGroups, searchDebounced])

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

              {/* product select */}
              <div className="flex items-center gap-1">
                <span className="text-sm text-muted-foreground whitespace-nowrap w-12">品种</span>
                <SimpleMultiSelect
                  options={symbolOptions}
                  value={symbols}
                  onValueChange={setSymbols}
                  placeholder="选择品种..."
                  searchPlaceholder="搜索品种..."
                  className="w-52"
                />
              </div>

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
              <Button onClick={refreshNow} disabled={isRefreshing} className="h-9 w-full sm:w-auto">
                {isRefreshing ? "同步数据中..." : "立即刷新"}
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
                      login: "客户ID",
                      user_name: "客户名称",
                      symbol: "交易产品",
                      balance: "余额",
                      total_closed_pnl: "平仓总盈亏",
                      floating_pnl: "持仓浮动盈亏",
                      total_closed_volume: "总成交量",
                      buy_closed_volume: "买单成交量",
                      sell_closed_volume: "卖单成交量",
                      total_closed_trades: "平仓交易笔数",
                      buy_trades_count: "买单笔数",
                      sell_trades_count: "卖单笔数",
                      last_updated: "更新时间",
                      user_group: "组别",
                      country: "国家/地区",
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




