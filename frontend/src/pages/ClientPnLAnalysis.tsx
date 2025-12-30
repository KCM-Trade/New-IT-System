import { useState, useCallback, useMemo, useEffect } from "react"
import { useTheme } from "@/components/theme-provider"
import { useI18n } from "@/components/i18n-provider"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Card, CardContent } from "@/components/ui/card"
import { Search, RefreshCw, X, Calendar as CalendarIcon } from "lucide-react"
import { AgGridReact } from 'ag-grid-react'
import { ColDef, GridApi } from 'ag-grid-community'
import { format } from "date-fns"
import { cn } from "@/lib/utils"
import { Calendar } from "@/components/ui/calendar"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { DateRange } from "react-day-picker"

interface ClientPnLAnalysisRow {
  client_id: number | string
  client_name?: string
  account?: string | number
  group?: string
  zipcode?: string
  currency?: string
  sid?: number
  partner_id?: number | string
  ib_net_deposit?: number
  server?: string
  total_trades: number
  trade_profit_usd: number
  total_volume_lots: number
  ib_commission_usd: number
  commission_usd: number
  swap_usd: number
}

function formatCurrency(value: number) {
  const sign = value >= 0 ? "" : "-"
  const abs = Math.abs(value)
  return `${sign}$${Math.round(abs).toLocaleString()}`
}

function toNumber(v: unknown, fallback = 0): number {
  if (typeof v === "number" && Number.isFinite(v)) return v
  if (typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v))) return Number(v)
  return fallback
}


const SETTINGS_KEY = 'CLIENT_PNL_SETTINGS_V1'

export default function ClientPnLAnalysis() {
  const { theme } = useTheme()
  const { t } = useI18n()
  
  // Helpers
  const tx = useCallback((key: string, fallback: string) => {
    try {
      const v = (t as any)(key)
      return (typeof v === 'string' && v && v !== key) ? v : fallback
    } catch {
      return fallback
    }
  }, [t])

  const isZh = useMemo(() => {
    try {
      const sep = (t as any)('common.comma')
      return typeof sep === 'string' && sep !== ', '
    } catch {
      return false
    }
  }, [t])

  const tz = useCallback((key: string, zhFallback: string, enFallback: string) => {
    try {
      const v = (t as any)(key)
      if (typeof v === 'string' && v && v !== key) return v
    } catch {}
    return isZh ? zhFallback : enFallback
  }, [t, isZh])

  // State
  // Load initial settings from localStorage
  const [initialSettings] = useState(() => {
    try {
      const s = localStorage.getItem(SETTINGS_KEY)
      if (!s) return null
      const p = JSON.parse(s)
      if (p.date) {
        if (p.date.from) p.date.from = new Date(p.date.from)
        if (p.date.to) p.date.to = new Date(p.date.to)
      }
      return p
    } catch { return null }
  })

  const [rows, setRows] = useState<ClientPnLAnalysisRow[]>([])
  const [loading, setLoading] = useState(false)
  const [searchInput, setSearchInput] = useState(initialSettings?.searchInput ?? "")
  const [timeRange, setTimeRange] = useState<string>(initialSettings?.timeRange ?? "1m") // Default to 1 month
  const [hasSearched, setHasSearched] = useState(!!initialSettings?.hasSearched)
  const [date, setDate] = useState<DateRange | undefined>(initialSettings?.date)
  const [stats, setStats] = useState<{ elapsed?: number; rows_read?: number; bytes_read?: number } | null>(null)

  // Persist settings
  useEffect(() => {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify({
        timeRange,
        searchInput,
        date,
        hasSearched
      }))
    } catch (e) {
      console.error("Failed to save settings", e)
    }
  }, [timeRange, searchInput, date, hasSearched])

  // Grid State
  const [gridApi, setGridApi] = useState<GridApi | null>(null)
  
  // Pagination State
  const [pageSize, setPageSize] = useState(50)
  const [currentPage, setCurrentPage] = useState(1)
  const [totalPages, setTotalPages] = useState(0)

  // Calculate Dates based on Range
  const getDateRange = useCallback((range: string) => {
    // Demo 限制：强制截止日期为 2025-12-27
    const MAX_DATE = new Date("2025-12-27")
    const end = new Date(MAX_DATE) // Clone it
    const start = new Date(MAX_DATE) // Clone it
    
    switch (range) {
      case '1w': start.setDate(end.getDate() - 7); break;
      case '2w': start.setDate(end.getDate() - 14); break;
      case '1m': start.setMonth(end.getMonth() - 1); break;
      case '3m': start.setMonth(end.getMonth() - 3); break;
      case '6m': start.setMonth(end.getMonth() - 6); break;
      default: start.setMonth(end.getMonth() - 1); // Default 1m
    }
    return {
      start_date: start.toISOString().split('T')[0],
      end_date: end.toISOString().split('T')[0]
    }
  }, [])

  // Fetch Data
  const handleSearch = useCallback(async () => {
    setLoading(true)
    setHasSearched(true)
    setStats(null)
    try {
      let start_date, end_date
      
      if (date?.from && date?.to) {
        start_date = format(date.from, 'yyyy-MM-dd')
        end_date = format(date.to, 'yyyy-MM-dd')
      } else {
        // Fallback to timeRange if date not fully selected
        const range = getDateRange(timeRange || '1m')
        start_date = range.start_date
        end_date = range.end_date
      }
      
      const params = new URLSearchParams({
        start_date,
        end_date,
      })
      
      if (searchInput.trim()) {
        params.append('search', searchInput.trim())
      }

      const response = await fetch(`/api/v1/client-pnl-analysis/query?${params}`)
      
      if (!response.ok) {
        // Handle HTTP errors
        const errorData = await response.json().catch(() => ({}))
        const errorMessage = errorData.detail || `Server error: ${response.status}`
        
        // Check for specific 503 Service Unavailable (likely ClickHouse waking up)
        if (response.status === 503) {
           alert("⚠️ 数据库服务正在唤醒中，请耐心等待 30-60 秒后再次点击查询。\n\nDatabase is waking up, please retry in 30-60 seconds.")
        } else {
           console.error('Query failed:', errorMessage)
           alert(`查询失败 (Query Failed): ${errorMessage}`)
        }
        setRows([])
        setStats(null)
        return
      }

      const result = await response.json()
      
      if (result.ok) {
        setRows(result.data || [])
        if (result.statistics) {
          setStats(result.statistics)
        }
      } else {
        console.error('Fetch failed:', result.error)
        setRows([])
      }
    } catch (error) {
      console.error('Fetch error:', error)
      setRows([])
    } finally {
      setLoading(false)
    }
  }, [timeRange, date, searchInput, getDateRange])

  // Auto search on mount if we have previous search state
  useEffect(() => {
    if (initialSettings?.hasSearched) {
      handleSearch()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSearch()
  }, [handleSearch])

  const isDarkMode = useMemo(() => {
    if (theme === 'dark') return true
    if (theme === 'light') return false
    try {
      return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
    } catch { return false }
  }, [theme])

  const getServerName = useCallback((sid: number | undefined) => {
    switch (sid) {
      case 1: return "MT4"
      case 5: return "MT5"
      case 6: return "MT4Live2"
      default: return sid ? `Server ${sid}` : "Unknown"
    }
  }, [])

  // Column Definitions
  const columnDefs = useMemo<ColDef[]>(() => [
    {
      field: "client_id",
      headerName: tz("clientPnl.columns.clientId", "Client ID", "Client ID"),
      width: 100,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => {
        const id = params.value
        if (!id) return null
        const link = `https://mt4.kohleglobal.com/crm/users/${id}`
        return (
          <a 
            href={link} 
            target="_blank" 
            rel="noopener noreferrer" 
            className="font-medium text-blue-600 hover:underline dark:text-blue-400"
            onClick={e => e.stopPropagation()}
          >
            {id}
          </a>
        )
      }
    },
    {
      field: "client_name",
      headerName: tz("clientPnl.columns.clientName", "客户名称", "Client Name"),
      width: 180,
      sortable: true,
      filter: true,
    },
    {
      field: "group",
      headerName: "Group",
      width: 120,
      sortable: true,
      filter: true,
    },
    {
      field: "zipcode",
      headerName: "Zipcode",
      width: 100,
      sortable: true,
      filter: true,
    },
    {
      field: "account",
      headerName: tz("clientPnl.columns.account", "账号", "Account"),
      width: 100,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => {
        const account = params.value
        const sid = params.data?.sid
        
        if (!account) return null
        if (!sid) return <span className="font-medium">{account}</span>
        
        const link = `https://mt4.kohleglobal.com/crm/accounts/${sid}-${account}`
        return (
          <a 
            href={link} 
            target="_blank" 
            rel="noopener noreferrer" 
            className="font-medium text-blue-600 hover:underline dark:text-blue-400"
            onClick={e => e.stopPropagation()}
          >
            {account}
          </a>
        )
      }
    },
    {
      field: "currency",
      headerName: tz("clientPnl.columns.currency", "币种", "Currency"),
      width: 90,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => {
        const cur = String(params.value || '').toUpperCase()
        let badge = 'bg-slate-200 text-slate-800 dark:bg-slate-700 dark:text-slate-200'
        if (cur === 'CEN') badge = 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300'
        else if (cur === 'USD') badge = 'bg-cyan-100 text-cyan-800 dark:bg-cyan-900/30 dark:text-cyan-300'
        else if (cur === 'USDT') badge = 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300'
        
        return (
          <span className={`text-sm font-semibold font-mono`}>
            <span className={`inline-block rounded px-1.5 py-0.5 ${badge}`}>{cur || '-'}</span>
          </span>
        )
      }
    },
    {
      field: "sid",
      headerName: tz("clientPnl.columns.server", "服务器", "Server"),
      width: 100,
      sortable: true,
      filter: true,
      valueFormatter: (params: any) => getServerName(params.value)
    },
    {
      field: "partner_id",
      headerName: tz("clientPnl.columns.directPartner", "直属上级IB", "Direct Parent IB"),
      width: 120,
      sortable: true,
      filter: true,
      // Highlight: Direct Parent IB (match Net PnL style but with red tint)
      cellStyle: { backgroundColor: 'rgba(255,0,0,0.08)' },
      cellRenderer: (params: any) => {
        const id = params.value
        if (!id || id === 0 || id === "0") return <span className="text-muted-foreground">-</span>
        const link = `https://mt4.kohleglobal.com/crm/users/${id}`
        return (
          <a 
            href={link} 
            target="_blank" 
            rel="noopener noreferrer" 
            className="font-medium text-blue-600 hover:underline dark:text-blue-400"
            onClick={e => e.stopPropagation()}
          >
            {id}
          </a>
        )
      }
    },
    {
      field: "ib_net_deposit",
      headerName: tz("clientPnl.columns.ibNetDeposit", "IB 净入金 (USD)", "IB Net Deposit (USD)"),
      width: 140,
      sortable: true,
      filter: 'agNumberColumnFilter',
      // Highlight: IB Net Deposit (match Net PnL style but with red tint)
      cellStyle: { backgroundColor: 'rgba(255,0,0,0.08)' },
      cellRenderer: (params: any) => {
        const val = toNumber(params.value)
        const color = val >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'
        return <span className={`font-semibold ${color}`}>{formatCurrency(val)}</span>
      }
    },
    {
      field: "total_trades",
      headerName: tz("clientPnl.columns.totalTrades", "总交易数", "Total Trades"),
      width: 110,
      sortable: true,
      filter: true,
    },
    {
      field: "total_volume_lots",
      headerName: tz("clientPnl.columns.totalVolume", "总手数", "Total Volume"),
      width: 120,
      sortable: true,
      filter: true,
      valueFormatter: (params: any) => toNumber(params.value).toFixed(2)
    },
    {
      field: "trade_profit_usd",
      headerName: tz("clientPnl.columns.tradeProfit", "交易盈亏 (USD)", "Trade Profit (USD)"),
      width: 150,
      sortable: true,
      filter: true,
      cellStyle: { backgroundColor: 'rgba(0,0,0,0.035)' },
      cellRenderer: (params: any) => {
        const val = toNumber(params.value)
        const color = val >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'
        return <span className={`font-semibold ${color}`}>{formatCurrency(val)}</span>
      }
    },
    {
      colId: "ib_commission_usd",
      valueGetter: (params: any) => toNumber(params.data?.ib_commission_usd),
      headerName: tz("clientPnl.columns.ibCommission", "IB 佣金 (USD)", "IB Commission (USD)"),
      width: 150,
      sortable: true,
      filter: 'agNumberColumnFilter',
      cellRenderer: (params: any) => (
        <span className="font-semibold text-blue-600 dark:text-blue-400">
          {formatCurrency(params.value)}
        </span>
      )
    },
    {
      headerName: tz("clientPnl.columns.netPnLWithComm", "净盈亏(含佣金) (USD)", "Net PnL (w/ Comm) (USD)"),
      width: 170,
      sortable: true,
      filter: 'agNumberColumnFilter',
      valueGetter: (params: any) => {
        const tradeProfit = toNumber(params.data?.trade_profit_usd)
        const ibCommission = toNumber(params.data?.ib_commission_usd)
        return tradeProfit + ibCommission
      },
      cellStyle: { backgroundColor: 'rgba(255,165,0,0.08)' },
      cellRenderer: (params: any) => {
        const val = toNumber(params.value)
        const color = val >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'
        return <span className={`font-semibold ${color}`}>{formatCurrency(val)}</span>
      }
    },
    {
      field: "commission_usd",
      headerName: tz("clientPnl.columns.commission", "佣金 (USD)", "Commission (USD)"),
      width: 130,
      sortable: true,
      filter: true,
      valueFormatter: (params: any) => formatCurrency(toNumber(params.value))
    },
    {
      field: "swap_usd",
      headerName: tz("clientPnl.columns.swap", "Swap (USD)", "Swap (USD)"),
      width: 130,
      sortable: true,
      filter: true,
      valueFormatter: (params: any) => formatCurrency(toNumber(params.value))
    },
  ], [tz])

  const getRangeLabel = useCallback((range: string, labelZh: string, labelEn: string) => {
    const { start_date, end_date } = getDateRange(range)
    const label = tz(`pnlMonitor.timeRange${range}`, labelZh, labelEn)
    return `${label} (${start_date} ~ ${end_date})`
  }, [getDateRange, tz])

  const onPaginationChanged = useCallback(() => {
    if (gridApi) {
      setCurrentPage(gridApi.paginationGetCurrentPage() + 1)
      setTotalPages(gridApi.paginationGetTotalPages())
    }
  }, [gridApi])

  // Update total pages when data loaded
  useEffect(() => {
    if (gridApi) {
        setTotalPages(gridApi.paginationGetTotalPages())
    }
  }, [rows, pageSize, gridApi])

  return (
    <div className="flex h-full w-full flex-col gap-2 p-1 sm:p-4">
      {/* Demo Warning Banner */}
      <div className="bg-amber-100 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-800 rounded px-4 py-2 text-amber-800 dark:text-amber-200 text-sm flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <span className="font-semibold">预览版 (Preview)</span>
          <span>— 当前数据截止至 2025-12-27。</span>
        </div>
        <div className="ml-0 sm:ml-7 text-xs opacity-90">
          当前 ClickHouse database 服务器处于 Dev 模式（30min 自动休眠）。首次加载时间可能略长（需唤醒），后续刷新时间恢复正常。
        </div>
        <div className="ml-0 sm:ml-7 text-xs opacity-90">
          服务器筛选时，需要通过服务器编号筛选：1: MT4, 5: MT5, 6: MT4Live2
        </div>
      </div>

      <Card>
        <CardContent className="py-3">
          <div className="flex flex-col sm:flex-row gap-3 items-center justify-between">
            <div className="flex items-center gap-3 w-full sm:w-auto">
              <Popover>
                <PopoverTrigger asChild>
                  <Button
                    id="date"
                    variant={"outline"}
                    className={cn(
                      "w-[260px] justify-start text-left font-normal",
                      !date && "text-muted-foreground"
                    )}
                  >
                    <CalendarIcon className="mr-2 h-4 w-4" />
                    {date?.from ? (
                      date.to ? (
                        <>
                          {format(date.from, "LLL dd, y")} -{" "}
                          {format(date.to, "LLL dd, y")}
                        </>
                      ) : (
                        format(date.from, "LLL dd, y")
                      )
                    ) : (
                      <span>Pick date range</span>
                    )}
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-0" align="start">
                  <Calendar
                    initialFocus
                    mode="range"
                    defaultMonth={date?.from}
                    selected={date}
                    onSelect={(newDate) => {
                      setDate(newDate)
                      if (newDate?.from) {
                        setTimeRange("")
                      }
                    }}
                    numberOfMonths={2}
                    disabled={(date) => date > new Date("2025-12-27")}
                  />
                </PopoverContent>
              </Popover>

              <Select 
                value={timeRange} 
                onValueChange={(val) => {
                  setTimeRange(val)
                  setDate(undefined)
                }}
              >
                <SelectTrigger className="w-[260px]">
                  <SelectValue placeholder="Quick Range" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="1w">{getRangeLabel('1w', '过去 1 周', 'Past 1 Week')}</SelectItem>
                  <SelectItem value="2w">{getRangeLabel('2w', '过去 2 周', 'Past 2 Weeks')}</SelectItem>
                  <SelectItem value="1m">{getRangeLabel('1m', '过去 1 个月', 'Past 1 Month')}</SelectItem>
                  <SelectItem value="3m">{getRangeLabel('3m', '过去 3 个月', 'Past 3 Months')}</SelectItem>
                  <SelectItem value="6m">{getRangeLabel('6m', '过去 6 个月', 'Past 6 Months')}</SelectItem>
                </SelectContent>
              </Select>

              <div className="flex items-center gap-1 flex-1 sm:flex-none">
                <Input
                  placeholder={tz('clientPnl.searchPlaceholder', '搜索 ClientID / AccountID', 'Search ClientID / AccountID')}
                  value={searchInput}
                  onChange={e => setSearchInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  className="w-full sm:w-[260px]"
                />
                {searchInput && (
                  <Button variant="ghost" size="sm" onClick={() => setSearchInput("")} className="px-2">
                    <X className="h-4 w-4" />
                  </Button>
                )}
              </div>

              <Button onClick={handleSearch} disabled={loading} className="whitespace-nowrap min-w-[80px]">
                {loading ? <RefreshCw className="h-4 w-4 animate-spin mr-2" /> : <Search className="h-4 w-4 mr-2" />}
                {tx('common.search', '查询')}
              </Button>
            </div>

            <div className="text-sm text-muted-foreground hidden sm:block">
              {stats ? (
                <div className="flex items-center gap-3 text-xs bg-slate-100 dark:bg-slate-800 px-3 py-1.5 rounded-full border border-slate-200 dark:border-slate-700">
                  <span className="font-medium text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                     耗时： {stats.elapsed?.toFixed(3)}s
                  </span>
                  <span className="w-px h-3 bg-slate-300 dark:bg-slate-600"></span>
                  <span className="flex items-center gap-1">
                     读取: {(stats.rows_read || 0).toLocaleString()} rows
                  </span>
                  <span className="w-px h-3 bg-slate-300 dark:bg-slate-600"></span>
                  <span className="flex items-center gap-1">
                    读取数据: {((stats.bytes_read || 0) / 1024 / 1024).toFixed(2)} MB
                  </span>
                </div>
              ) : (
                hasSearched ? `${t('pnlMonitor.totalRecords', { count: rows.length })}` : tx('clientPnl.readyToSearch', '请选择时间范围并查询')
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Banner below filters: data freshness reminder */}
      <div className="bg-rose-50 dark:bg-rose-900/20 border border-rose-200 dark:border-rose-800 rounded px-4 py-2 text-rose-800 dark:text-rose-200 text-sm">
        数据仅更新至 <span className="font-semibold">2025-12-27</span>，并非最新实时数据。
      </div>

      <div className="flex-1 relative">
        <div
          className={`${isDarkMode ? 'ag-theme-quartz-dark' : 'ag-theme-quartz'} clientpnl-theme h-[750px] w-full min-h-[400px] relative`}
          style={{
            ['--primary' as any]: '243 75% 59%',
            ['--primary-foreground' as any]: '0 0% 100%',
            ['--accent' as any]: '243 75% 65%',
            ['--accent-foreground' as any]: '0 0% 14%',
            ['--ag-header-background-color' as any]: isDarkMode ? 'hsl(0 0% 100% / 1)' : 'hsl(0 0% 8% / 1)',
            ['--ag-header-foreground-color' as any]: isDarkMode ? 'hsl(0 0% 0% / 1)' : 'hsl(0 0% 100% / 1)',
            ['--ag-header-column-separator-color' as any]: isDarkMode ? 'hsl(0 0% 0% / 1)' : 'hsl(0 0% 100% / 1)',
            ['--ag-header-column-separator-width' as any]: '1px',
            ['--ag-background-color' as any]: 'hsl(var(--card))',
            ['--ag-foreground-color' as any]: 'hsl(var(--foreground))',
            ['--ag-row-border-color' as any]: 'hsl(var(--border))',
            ['--ag-odd-row-background-color' as any]: 'hsl(var(--primary) / 0.04)'
          }}
        >
          {(!hasSearched && !loading) ? (
            <div className="absolute inset-0 flex items-center justify-center text-muted-foreground bg-background/50 z-10">
              <div className="text-center">
                <Search className="h-12 w-12 mx-auto mb-2 opacity-20" />
                <p>{tx('clientPnl.startPrompt', '')}</p>
              </div>
            </div>
          ) : null}
          
          <AgGridReact
            rowData={rows}
            columnDefs={columnDefs}
            gridOptions={{ theme: 'legacy' }}
            defaultColDef={{
              sortable: true,
              filter: true,
              resizable: true,
              minWidth: 100,
              wrapHeaderText: true,
              autoHeaderHeight: true,
            }}
            getRowStyle={(params: any) => {
              const idx = typeof params.node.rowIndex === 'number' ? params.node.rowIndex : -1
              if (idx % 2 === 0) {
                return { backgroundColor: 'hsl(var(--primary) / 0.03)', paddingLeft: 0, borderLeft: 'none' }
              }
              return { backgroundColor: 'hsl(var(--primary) / 0.06)', paddingLeft: 0, borderLeft: 'none' }
            }}
            onGridReady={(params) => {
              setGridApi(params.api)
              // @ts-ignore
              if (params.api.paginationSetPageSize) {
                 // @ts-ignore
                 params.api.paginationSetPageSize(pageSize)
              }
            }}
            animateRows={true}
            enableCellTextSelection={true}
            domLayout="normal"
            suppressScrollOnNewData={true}
            rowSelection="multiple"
            suppressRowClickSelection={true}
            pagination={true}
            paginationPageSize={pageSize}
            suppressPaginationPanel={true}
            onPaginationChanged={onPaginationChanged}
          />
        </div>
        <style>{`
          .clientpnl-theme .ag-header {
            border: 1px solid ${isDarkMode ? '#000' : '#fff'};
            border-bottom-width: 1px;
          }
        `}</style>
      </div>

      {/* Pagination Controls */}
      <Card>
        <CardContent className="py-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:space-x-4">
              <div className="text-sm text-muted-foreground">
                {t("pnlMonitor.totalRecordsDisplay", { 
                  start: (currentPage - 1) * pageSize + 1, 
                  end: Math.min(currentPage * pageSize, rows.length), 
                  total: rows.length 
                })}
              </div>
              
              <div className="flex items-center space-x-2">
                <span className="text-sm text-muted-foreground">{tx('pnlMonitor.perPage', '每页')}</span>
                <Select
                  value={pageSize.toString()}
                  onValueChange={(val) => {
                    const newSize = Number(val)
                    setPageSize(newSize)
                    if (gridApi) {
                      // @ts-ignore
                      if (gridApi.paginationSetPageSize) {
                          // @ts-ignore
                          gridApi.paginationSetPageSize(newSize)
                      }
                    }
                  }}
                >
                  <SelectTrigger className="h-8 w-20">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {[50, 100, 500].map(size => (
                      <SelectItem key={size} value={size.toString()}>{size}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <span className="text-sm text-muted-foreground">{tx('pnlMonitor.records', '条记录')}</span>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" 
                onClick={() => gridApi?.paginationGoToFirstPage()} 
                disabled={currentPage === 1}
              >
                {tx('pnlMonitor.firstPage', '首页')}
              </Button>
              <Button variant="outline" size="sm" 
                onClick={() => gridApi?.paginationGoToPreviousPage()} 
                disabled={currentPage === 1}
              >
                {tx('pnlMonitor.prevPage', '上一页')}
              </Button>
              <span className="text-sm text-muted-foreground mx-2">
                {t('pnlMonitor.pageInfo', { current: currentPage, total: totalPages })}
              </span>
              <Button variant="outline" size="sm" 
                onClick={() => gridApi?.paginationGoToNextPage()} 
                disabled={currentPage === totalPages}
              >
                {tx('pnlMonitor.nextPage', '下一页')}
              </Button>
              <Button variant="outline" size="sm" 
                onClick={() => gridApi?.paginationGoToLastPage()} 
                disabled={currentPage === totalPages}
              >
                {tx('pnlMonitor.lastPage', '尾页')}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

