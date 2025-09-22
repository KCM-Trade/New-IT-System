import { useMemo } from "react"
import { useState, useEffect, useCallback, useRef } from "react"
import { useTheme } from "@/components/theme-provider"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Settings2, Search } from "lucide-react"
import { AgGridReact } from 'ag-grid-react'
import { ColDef, GridReadyEvent, SortChangedEvent } from 'ag-grid-community'

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

export default function CustomerPnLMonitor() {
  const { theme } = useTheme()
  // server/product filters
  const [server, setServer] = useState<string>("MT5")
  const [symbol, setSymbol] = useState<string>("XAUUSD.kcmc")

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
  const [globalFilter, setGlobalFilter] = useState("")
  const [columnVisibility, setColumnVisibility] = useState<Record<string, boolean>>({
    login: true,
    user_name: true,
    symbol: true,
    balance: true,
    total_closed_pnl: true,
    floating_pnl: true,
    total_closed_volume: true,
    total_closed_trades: true,
    last_updated: true,
  })
  const [gridApi, setGridApi] = useState<any>(null)
  const gridContainerRef = useRef<HTMLDivElement | null>(null)

  // AG Grid 列定义
  const columnDefs = useMemo<ColDef<PnlSummaryRow>[]>(() => [
    {
      field: "login",
      headerName: "客户ID",
      width: 120,
      minWidth: 80,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => (
        <span className="font-medium">{params.value}</span>
      ),
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
      field: "symbol",
      headerName: "交易产品",
      width: 140,
      minWidth: 100,
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
  ], [productConfig, columnVisibility])

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

  // 持久化表格状态
  useEffect(() => {
    try {
      const tableState = {
        columnVisibility,
        sortModel,
      }
      localStorage.setItem("pnl_table_state", JSON.stringify(tableState))
    } catch {}
  }, [columnVisibility, sortModel])

  // 恢复表格状态
  useEffect(() => {
    try {
      const saved = localStorage.getItem("pnl_table_state")
      if (saved) {
        const state = JSON.parse(saved)
        if (state.columnVisibility) setColumnVisibility(state.columnVisibility)
        if (state.sortModel) setSortModel(state.sortModel)
      }
    } catch {}
  }, [])

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
    
    const params = new URLSearchParams({
      server: server,
      symbol: symbol,
      page: currentPage.toString(),
      page_size: currentPageSize.toString(),
    })
    
    if (currentSortBy) {
      params.set('sort_by', currentSortBy)
      params.set('sort_order', currentSortOrder)
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
  }, [server, symbol, pageIndex, pageSize, sortModel])

  const refreshNow = useCallback(async () => {
    setIsRefreshing(true)
    try {
      setError(null)
      setSuccessMessage(null)
      
      // 1) 执行ETL同步（现在是同步等待完成）
      const refreshResponse = await fetchWithTimeout(`/api/v1/pnl/summary/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json", accept: "application/json" },
        body: JSON.stringify({ server, symbol }),
      }, 30000) // 增加超时时间到30秒，因为现在是同步等待ETL完成
      
      const refreshResult = await refreshResponse.json()
      
      // 显示ETL执行结果信息
      if (refreshResult.status === "success") {
        const details = []
        if (refreshResult.processed_rows > 0) {
          details.push(`处理了 ${refreshResult.processed_rows} 行数据`)
        } else {
          details.push("无新数据需要处理")
        }
        if (refreshResult.duration_seconds > 0) {
          details.push(`耗时 ${refreshResult.duration_seconds.toFixed(1)} 秒`)
        }
        const successMsg = `${refreshResult.message}${details.length > 0 ? ` (${details.join(', ')})` : ''}`
        setSuccessMessage(successMsg)
        // 成功消息10秒后自动清除
        setTimeout(() => setSuccessMessage(null), 10000)
      } else {
        setError(`${refreshResult.message}${refreshResult.error_details ? `: ${refreshResult.error_details}` : ''}`)
      }
      
      // 2) 拉取最新数据（ETL已完成，无需等待）
      const data = await fetchData()
      setRows(data)
      setLastUpdated(new Date())
      
    } catch (e) {
      setError(e instanceof Error ? e.message : "刷新失败")
      setSuccessMessage(null)
    } finally {
      setIsRefreshing(false)
    }
  }, [fetchData, server, symbol])

  // 监听分页和排序变化，自动重新获取数据
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
        setSuccessMessage(null)
      }
    })()
  }, [pageIndex, pageSize, sortModel, server, symbol])

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

  // auto-refresh every 10 minutes; re-run when server/symbol changes
  useEffect(() => {
    const t = setInterval(() => {
      ;(async () => {
        try {
          const data = await fetchData()
          setRows(data)
          setLastUpdated(new Date())
          if (error) setError(null)
        } catch (e) {
          setError(e instanceof Error ? e.message : "自动刷新失败")
          setSuccessMessage(null)
        }
      })()
    }, AUTO_REFRESH_MS)
    return () => clearInterval(t)
  }, [fetchData])

  return (
    <div className="flex h-full w-full flex-col gap-2 p-1 sm:p-4">
      {/* filter & actions card: responsive layout per guide */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">客户盈亏监控 - 筛选</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-3">
              {/* server select */}
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground whitespace-nowrap w-16">服务器</span>
                <Select value={server} onValueChange={setServer}>
                  <SelectTrigger className="h-9 w-40">
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
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground whitespace-nowrap w-16">品种</span>
                <Select value={symbol} onValueChange={setSymbol}>
                  <SelectTrigger className="h-9 w-52">
                    <SelectValue placeholder="选择品种" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__ALL__">全部产品</SelectItem>
                    <SelectItem value="XAUUSD.kcmc">XAUUSD.kcmc </SelectItem>
                    <SelectItem value="XAUUSD.kcm">XAUUSD.kcm </SelectItem>
                    <SelectItem value="XAUUSD">XAUUSD </SelectItem>
                    <SelectItem value="XAUUSD.cent">XAUUSD.cent</SelectItem>
                    <SelectItem value="others" disabled>其他（开发中）</SelectItem>
                  </SelectContent>
                </Select>
              </div>

            </div>

            {/* actions */}
            <div className="flex flex-col sm:flex-row sm:items-center gap-2 w-full sm:w-auto">
              <div className="text-xs text-muted-foreground">
                默认每10分钟自动刷新{lastUpdated ? `，上次：${lastUpdated.toLocaleString()}` : ""}
              </div>
              <Button onClick={refreshNow} disabled={isRefreshing} className="h-9 w-full sm:w-auto">
                {isRefreshing ? "同步数据中..." : "立即刷新"}
              </Button>
            </div>
          </div>

          {/* mobile hint row */}
          <div className="sm:hidden text-xs text-muted-foreground">
            默认每10分钟自动刷新{lastUpdated ? `，上次：${lastUpdated.toLocaleString()}` : ""}
          </div>
        </CardContent>
      </Card>

      {/* 刷新结果消息显示区域 */}
      {(successMessage || error) && (
        <div className="px-1 sm:px-0">
          {successMessage ? (
            <div className="flex items-center gap-2 px-4 py-3 bg-green-50 dark:bg-green-950/20 border border-green-200 dark:border-green-800 rounded-lg">
              <div className="flex-shrink-0">
                <svg className="w-4 h-4 text-green-600 dark:text-green-400" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
              </div>
              <p className="text-sm text-green-800 dark:text-green-200">{successMessage}</p>
            </div>
          ) : error ? (
            <div className="flex items-center gap-2 px-4 py-3 bg-red-50 dark:bg-red-950/20 border border-red-200 dark:border-red-800 rounded-lg">
              <div className="flex-shrink-0">
                <svg className="w-4 h-4 text-red-600 dark:text-red-400" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                </svg>
              </div>
              <p className="text-sm text-red-800 dark:text-red-200">{error}</p>
            </div>
          ) : null}
        </div>
      )}

      {/* 表格控制卡片 - 全局搜索、列选择、分页设置 */}
      <Card>
        <CardContent className="py-4">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            {/* 左侧：全局搜索 */}
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <Search className="h-4 w-4 text-muted-foreground flex-shrink-0" />
              <Input
                placeholder="全局搜索..."
                value={globalFilter ?? ""}
                onChange={(e) => setGlobalFilter(e.target.value)}
                className="h-9 flex-1"
              />
              {globalFilter && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setGlobalFilter("")}
                  className="h-9 px-2 text-muted-foreground hover:text-foreground"
                >
                  清除
                </Button>
              )}
            </div>
            
            {/* 右侧：控制按钮组 */}
            <div className="flex items-center gap-2 flex-shrink-0">
              {/* 列显示选择 */}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" className="h-9 gap-2 whitespace-nowrap">
                    <Settings2 className="h-4 w-4" />
                    列设置
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
                      total_closed_trades: "平仓交易笔数",
                      last_updated: "更新时间",
                    }
                    return (
                      <DropdownMenuCheckboxItem
                        key={columnId}
                        checked={isVisible}
                        onCheckedChange={(value) => 
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

          {/* 状态信息 */}
          <div className="flex flex-wrap items-center gap-2 mt-3 text-xs text-muted-foreground">
            <span>共 {totalCount} 条记录</span>
            <span>当前页 {pageIndex + 1}/{totalPages}</span>
            {globalFilter && (
              <span className="px-2 py-1 bg-blue-100 dark:bg-blue-900/20 rounded text-blue-700 dark:text-blue-300">
                搜索: "{globalFilter}"
              </span>
            )}
            {sortModel.length > 0 && (
              <span className="px-2 py-1 bg-purple-100 dark:bg-purple-900/20 rounded text-purple-700 dark:text-purple-300">
                排序: {sortModel.map(s => `${s.colId} ${s.sort === 'desc' ? '↓' : '↑'}`).join(', ')}
              </span>
            )}
            {Object.values(columnVisibility).filter(v => !v).length > 0 && (
              <span className="px-2 py-1 bg-orange-100 dark:bg-orange-900/20 rounded text-orange-700 dark:text-orange-300">
                隐藏了 {Object.values(columnVisibility).filter(v => !v).length} 列
              </span>
            )}
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
          <div className="flex items-center justify-between">
            {/* 左侧：显示信息和每页条数选择 */}
            <div className="flex items-center space-x-4">
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
                  onValueChange={(value) => {
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
            <div className="flex items-center space-x-2">
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


