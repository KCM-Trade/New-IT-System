import { useMemo } from "react"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useState, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Settings2, Search, ArrowUpDown, ArrowUp, ArrowDown } from "lucide-react"
import {
  ColumnDef,
  ColumnFiltersState,
  SortingState,
  VisibilityState,
  ColumnOrderState,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  ColumnResizeMode,
} from "@tanstack/react-table"

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

function formatCurrency(value: number) {
  const sign = value >= 0 ? "" : "-"
  const abs = Math.abs(value)
  return `${sign}$${abs.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
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
  // server/product filters
  const [server, setServer] = useState<string>("MT5")
  const [symbol, setSymbol] = useState<string>("XAUUSD.kcmc")

  // data state and refresh
  const [rows, setRows] = useState<PnlSummaryRow[]>([])
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const AUTO_REFRESH_MS = 10 * 60 * 1000 // 10 minutes

  // TanStack Table 状态管理
  const [sorting, setSorting] = useState<SortingState>([])
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([])
  const [globalFilter, setGlobalFilter] = useState("")
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({
    login: true,
    user_name: true,
    balance: true,
    total_closed_pnl: true,
    floating_pnl: true,
    total_closed_volume: true,
    total_closed_trades: true,
    last_updated: true,
  })
  const [columnOrder, setColumnOrder] = useState<ColumnOrderState>([
    "login", "user_name", "balance", "total_closed_pnl", 
    "floating_pnl", "total_closed_volume", "total_closed_trades", "last_updated"
  ])

  // TanStack Table 列定义 - 响应式比例宽度设置
  // 📍 宽度设置说明：
  // - 桌面端：所有列的 size 值总和约为 1000，每列按比例分配表格宽度，占满整个容器
  // - 移动端：表格设置了最小宽度 880px，确保内容不会溢出到相邻列，提供水平滚动
  // - 最小宽度分配：客户ID(80px) + 客户名称(120px) + 余额(100px) + 平仓总盈亏(120px) + 持仓浮动盈亏(120px) + 总成交量(90px) + 平仓交易笔数(100px) + 更新时间(150px) = 880px
  // - 用户仍可拖拽调整列宽，在设定的最小宽度和最大宽度(500px)之间调整
  const columns = useMemo<ColumnDef<PnlSummaryRow>[]>(() => [
    {
      id: "login",
      accessorKey: "login",
      header: ({ column }) => {
        const Icon = column.getIsSorted() === "asc" ? ArrowUp : 
                   column.getIsSorted() === "desc" ? ArrowDown : ArrowUpDown
        return (
          <Button 
            variant="ghost" 
            className="h-8 px-2 gap-1"
            onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
          >
            客户ID <Icon className="h-3 w-3" />
          </Button>
        )
      },
      size: 100,        // 📍 初始宽度 (比例: 约10%)
      minSize: 80,      // 📍 最小宽度 (确保客户ID完整显示)
      maxSize: 500,     // 📍 最大宽度
      enableSorting: true,
      cell: ({ row }) => <span className="font-medium">{row.getValue("login")}</span>,
    },
    {
      id: "user_name", 
      accessorKey: "user_name",
      header: ({ column }) => {
        const Icon = column.getIsSorted() === "asc" ? ArrowUp : 
                   column.getIsSorted() === "desc" ? ArrowDown : ArrowUpDown
        return (
          <Button 
            variant="ghost" 
            className="h-8 px-2 gap-1"
            onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
          >
            客户名称 <Icon className="h-3 w-3" />
          </Button>
        )
      },
      size: 200,        // 📍 初始宽度 (比例: 约20%)
      minSize: 150,     // 📍 最小宽度 (确保客户名称基本显示)
      maxSize: 500,     // 📍 最大宽度
      enableSorting: true,
      cell: ({ row }) => (
        <span className="max-w-[180px] truncate">
          {row.getValue("user_name") || `客户-${row.getValue("login")}`}
        </span>
      ),
    },
    {
      id: "balance",
      accessorKey: "balance", 
      header: ({ column }) => {
        const Icon = column.getIsSorted() === "asc" ? ArrowUp : 
                   column.getIsSorted() === "desc" ? ArrowDown : ArrowUpDown
        return (
          <Button 
            variant="ghost" 
            className="h-8 px-2 gap-1"
            onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
          >
            余额 <Icon className="h-3 w-3" />
          </Button>
        )
      },
      size: 120,        // 📍 初始宽度 (比例: 约12%)
      minSize: 100,     // 📍 最小宽度 (确保货币格式完整显示)
      maxSize: 500,     // 📍 最大宽度
      enableSorting: true,
      cell: ({ row }) => {
        const value = toNumber(row.getValue("balance"))
        return (
          <span 
            className={`text-right ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}
          >
            {formatCurrency(value)}
          </span>
        )
      },
    },
    {
      id: "total_closed_pnl",
      accessorKey: "total_closed_pnl",
      header: ({ column }) => {
        const Icon = column.getIsSorted() === "asc" ? ArrowUp : 
                   column.getIsSorted() === "desc" ? ArrowDown : ArrowUpDown
        return (
          <Button 
            variant="ghost" 
            className="h-8 px-2 gap-1"
            onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
          >
            平仓总盈亏 <Icon className="h-3 w-3" />
          </Button>
        )
      },
      size: 150,        // 📍 初始宽度 (比例: 约15%)
      minSize: 120,     // 📍 最小宽度 (确保盈亏金额完整显示)
      maxSize: 500,     // 📍 最大宽度
      enableSorting: true,
      cell: ({ row }) => {
        const value = toNumber(row.getValue("total_closed_pnl"))
        return (
          <span 
            className={`text-right ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}
          >
            {formatCurrency(value)}
          </span>
        )
      },
    },
    {
      id: "floating_pnl",
      accessorKey: "floating_pnl",
      header: ({ column }) => {
        const Icon = column.getIsSorted() === "asc" ? ArrowUp : 
                   column.getIsSorted() === "desc" ? ArrowDown : ArrowUpDown
        return (
          <Button 
            variant="ghost" 
            className="h-8 px-2 gap-1"
            onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
          >
            持仓浮动盈亏 <Icon className="h-3 w-3" />
          </Button>
        )
      },
      size: 150,        // 📍 初始宽度 (比例: 约15%)
      minSize: 120,     // 📍 最小宽度 (确保浮动盈亏金额完整显示)
      maxSize: 500,     // 📍 最大宽度
      enableSorting: true,
      cell: ({ row }) => {
        const value = toNumber(row.getValue("floating_pnl"))
        return (
          <span 
            className={`text-right ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}
          >
            {formatCurrency(value)}
          </span>
        )
      },
    },
    {
      id: "total_closed_volume",
      accessorKey: "total_closed_volume",
      header: ({ column }) => {
        const Icon = column.getIsSorted() === "asc" ? ArrowUp : 
                   column.getIsSorted() === "desc" ? ArrowDown : ArrowUpDown
        return (
          <Button 
            variant="ghost" 
            className="h-8 px-2 gap-1"
            onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
          >
            总成交量 <Icon className="h-3 w-3" />
          </Button>
        )
      },
      size: 100,        // 📍 初始宽度 (比例: 约10%)
      minSize: 90,      // 📍 最小宽度 (确保成交量数字完整显示)
      maxSize: 500,     // 📍 最大宽度
      enableSorting: true,
      cell: ({ row }) => (
        <span className="text-right tabular-nums">
          {toNumber(row.getValue("total_closed_volume")).toLocaleString()}
        </span>
      ),
    },
    {
      id: "total_closed_trades",
      accessorKey: "total_closed_trades",
      header: ({ column }) => {
        const Icon = column.getIsSorted() === "asc" ? ArrowUp : 
                   column.getIsSorted() === "desc" ? ArrowDown : ArrowUpDown
        return (
          <Button 
            variant="ghost" 
            className="h-8 px-2 gap-1"
            onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
          >
            平仓交易笔数 <Icon className="h-3 w-3" />
          </Button>
        )
      },
      size: 120,        // 📍 初始宽度 (比例: 约12%)
      minSize: 100,     // 📍 最小宽度 (确保交易笔数完整显示)
      maxSize: 500,     // 📍 最大宽度
      enableSorting: true,
      cell: ({ row }) => (
        <span className="text-right tabular-nums">
          {toNumber(row.getValue("total_closed_trades")).toLocaleString()}
        </span>
      ),
    },
    {
      id: "last_updated",
      accessorKey: "last_updated",
      header: "更新时间",
      size: 80,         // 📍 初始宽度 (比例: 约8%)
      minSize: 200,     // 📍 最小宽度 (确保完整时间格式显示)
      maxSize: 500,     // 📍 最大宽度
      enableSorting: true,
      enableColumnFilter: false,
      cell: ({ row }) => (
        <span className="whitespace-nowrap text-muted-foreground">
          {row.getValue("last_updated") ? new Date(row.getValue("last_updated") as string).toLocaleString() : ""}
        </span>
      ),
    },
  ], [])

  // TanStack Table 实例
  const table = useReactTable({
    data: rows,
    columns,
    state: {
      sorting,
      columnFilters,
      globalFilter,
      columnVisibility,
      columnOrder,
    },
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    onGlobalFilterChange: setGlobalFilter,
    onColumnVisibilityChange: setColumnVisibility,
    onColumnOrderChange: setColumnOrder,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
    enableColumnResizing: true,
    columnResizeMode: "onChange" as ColumnResizeMode,
  })

  // 持久化表格状态
  useEffect(() => {
    try {
      const tableState = {
        columnVisibility,
        columnOrder,
        sorting,
      }
      localStorage.setItem("pnl_table_state", JSON.stringify(tableState))
    } catch {}
  }, [columnVisibility, columnOrder, sorting])

  // 恢复表格状态
  useEffect(() => {
    try {
      const saved = localStorage.getItem("pnl_table_state")
      if (saved) {
        const state = JSON.parse(saved)
        if (state.columnVisibility) setColumnVisibility(state.columnVisibility)
        if (state.columnOrder) setColumnOrder(state.columnOrder)
        if (state.sorting) setSorting(state.sorting)
      }
    } catch {}
  }, [])

  // GET 拉取后端数据（不触发同步）
  const fetchData = useCallback(async () => {
    const url = `/api/v1/pnl/summary?server=${encodeURIComponent(server)}&symbol=${encodeURIComponent(symbol)}`
    const res = await fetchWithTimeout(url, { headers: { accept: "application/json" } }, 20000)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const payload = (await res.json()) as { ok?: boolean; data?: PnlSummaryRow[]; rows?: number; error?: string }
    if (!payload?.ok) throw new Error(payload?.error || "加载失败")
    return Array.isArray(payload.data) ? payload.data : []
  }, [server, symbol])

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

  // auto-refresh every 10 minutes; re-run when server/symbol changes
  useEffect(() => {
    // 首次与筛选项变更：只 GET 拉取，不触发同步
    ;(async () => {
      try {
        setError(null)
        setSuccessMessage(null) // 清除之前的成功消息
        const data = await fetchData()
        setRows(data)
        setLastUpdated(new Date())
      } catch (e) {
        setRows([])
        setError(e instanceof Error ? e.message : "加载失败")
        setSuccessMessage(null)
      }
    })()
    const t = setInterval(() => {
      ;(async () => {
        try {
          const data = await fetchData()
          setRows(data)
          setLastUpdated(new Date())
          // 自动刷新成功时清除之前的错误消息（但不显示成功消息，避免干扰）
          if (error) setError(null)
        } catch (e) {
          // 自动刷新失败仅记录错误，不打断页面
          setError(e instanceof Error ? e.message : "自动刷新失败")
          setSuccessMessage(null)
        }
      })()
    }, AUTO_REFRESH_MS)
    return () => clearInterval(t)
  }, [server, symbol, fetchData])

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
                    <SelectItem value="XAUUSD.kcmc">XAUUSD.kcmc</SelectItem>
                    <SelectItem value="XAUUSD.kcm">XAUUSD.kcm</SelectItem>
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
                  {table.getAllLeafColumns()
                    .filter((column) => column.getCanHide())
                    .map((column) => {
                      const columnLabels: Record<string, string> = {
                        login: "客户ID",
                        user_name: "客户名称", 
                        balance: "余额",
                        total_closed_pnl: "平仓总盈亏",
                        floating_pnl: "持仓浮动盈亏",
                        total_closed_volume: "总成交量",
                        total_closed_trades: "平仓交易笔数",
                        last_updated: "更新时间",
                      }
                      return (
                        <DropdownMenuCheckboxItem
                          key={column.id}
                          checked={column.getIsVisible()}
                          onCheckedChange={(value) => column.toggleVisibility(!!value)}
                        >
                          {columnLabels[column.id] || column.id}
                        </DropdownMenuCheckboxItem>
                      )
                    })}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>

          {/* 状态信息 */}
          <div className="flex flex-wrap items-center gap-2 mt-3 text-xs text-muted-foreground">
            <span>共 {table.getFilteredRowModel().rows.length} 条记录</span>
            {globalFilter && (
              <span className="px-2 py-1 bg-blue-100 dark:bg-blue-900/20 rounded text-blue-700 dark:text-blue-300">
                搜索: "{globalFilter}"
              </span>
            )}
            {sorting.length > 0 && (
              <span className="px-2 py-1 bg-purple-100 dark:bg-purple-900/20 rounded text-purple-700 dark:text-purple-300">
                排序: {sorting.map(s => `${s.id} ${s.desc ? '↓' : '↑'}`).join(', ')}
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

      {/* TanStack Table with column resizing */}
      <div className="border rounded-md overflow-hidden flex-1">
        <div className="overflow-auto h-full">
          <Table
            style={{
              width: "100%",
              minWidth: "880px", // 所有列最小宽度总和，确保移动端内容不溢出
              tableLayout: "fixed", // 使用固定表格布局以支持比例分配
            }}
          >
            <TableHeader className="sticky top-0 z-10 bg-background">
              {table.getHeaderGroups().map((headerGroup) => (
                <TableRow key={headerGroup.id}>
                  {headerGroup.headers.map((header) => (
                    <TableHead
                      key={header.id}
                      className="whitespace-nowrap border-r"
                      style={{
                        width: `${(header.getSize() / 1000) * 100}%`, // 转换为百分比宽度
                        position: "relative",
                      }}
                    >
                      {header.isPlaceholder ? null : (
                        <div>
                          {flexRender(header.column.columnDef.header, header.getContext())}
                        </div>
                      )}
                      {/* Column Resizer - 列宽调整手柄 */}
                      {header.column.getCanResize() && (
                        <div
                          className="absolute right-0 top-0 h-full w-1 bg-border hover:bg-blue-500 cursor-col-resize select-none touch-none"
                          onMouseDown={header.getResizeHandler()}
                          onTouchStart={header.getResizeHandler()}
                          style={{
                            transform: header.column.getIsResizing() ? 'scaleX(2)' : 'scaleX(1)',
                            transition: 'transform 0.1s ease-in-out',
                          }}
                          title="拖拽调整列宽"
                        />
                      )}
                </TableHead>
                  ))}
              </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {table.getRowModel().rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={table.getAllLeafColumns().length} className="text-center text-sm text-muted-foreground py-8">
                    {error ? `加载失败：${error}` : "暂无数据"}
                  </TableCell>
                </TableRow>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <TableRow key={row.id}>
                    {row.getVisibleCells().map((cell) => (
                      <TableCell
                        key={cell.id}
                        className="border-r"
                        style={{
                          width: `${(cell.column.getSize() / 1000) * 100}%`, // 转换为百分比宽度
                        }}
                      >
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                    ))}
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </div>
    </div>
  )
}


