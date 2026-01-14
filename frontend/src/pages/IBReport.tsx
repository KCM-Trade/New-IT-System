import { useState, useCallback, useMemo, useEffect } from "react"
import { useTheme } from "@/components/theme-provider"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Search, BarChart3, Calendar as CalendarIcon, Filter, Settings2 } from "lucide-react"
import { AgGridReact } from 'ag-grid-react'
import { ColDef, GridApi, ICellRendererParams } from 'ag-grid-community'
import { format } from "date-fns"
import { cn } from "@/lib/utils"
import { Calendar } from "@/components/ui/calendar"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { DateRange } from "react-day-picker"
import { Checkbox } from "@/components/ui/checkbox"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

// --- Constants ---
const GRID_STATE_STORAGE_KEY = 'IB_REPORT_GRID_STATE_V1'

// --- Types ---

interface IBValue {
  range_val: number
  month_val: number
}

interface IBReportRow {
  group: string
  user_name: string
  time_range: string
  deposit: IBValue
  withdrawal: IBValue
  ib_withdrawal: IBValue
  net_deposit: IBValue
  volume: IBValue
  adjustments: IBValue
  // Extended columns
  commission: IBValue
  ib_commission: IBValue
  swap: IBValue
  profit: IBValue
  new_clients: IBValue
  new_agents: IBValue
}

// --- Components ---

/**
 * Custom Cell Renderer for dual-row display (Range Value / Monthly Value)
 */
const DoubleValueRenderer = (params: ICellRendererParams) => {
  const value = params.value as IBValue
  if (!value) return null

  const isPositive = (val: number) => val > 0
  const isNegative = (val: number) => val < 0

  const getClassName = (val: number) => {
    if (isPositive(val)) return "text-emerald-600 dark:text-emerald-400"
    if (isNegative(val)) return "text-red-600 dark:text-red-400"
    return "text-muted-foreground"
  }

  return (
    <div className="flex flex-col leading-tight py-1">
      <span className={cn("font-medium", getClassName(value.range_val))}>
        {value.range_val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </span>
      <span className="text-[10px] text-muted-foreground opacity-70 border-t border-dashed mt-0.5">
        Month: {value.month_val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </span>
    </div>
  )
}

const PREDEFINED_GROUPS = [
  "HZL", "CCX", "JSA", "SZS", "SZU", "SHY", "SHT037", 
  "SHT042", "SHT049", "SHS", "SHP", "CS/Company", "SP01"
]

export default function IBReport() {
  const { theme } = useTheme()
  const isDarkMode = theme === 'dark'

  // --- State ---
  const [date, setDate] = useState<DateRange | undefined>({
    from: new Date(2026, 0, 4),
    to: new Date(2026, 0, 8),
  })
  const [selectedGroups, setSelectedGroups] = useState<string[]>([])
  const [includeMonthly, setIncludeMonthly] = useState(true)
  const [loading, setLoading] = useState(false)
  const [rows, setRows] = useState<IBReportRow[]>([])
  const [gridApi, setGridApi] = useState<GridApi | null>(null)
  const [columnState, setColumnState] = useState<any[]>([])

  // --- Grid State Persistence ---
  const refreshColumnState = useCallback((api?: GridApi | null) => {
    const a = api || gridApi
    if (!a) return
    try {
      const state = (a as any).getColumnState?.()
      if (Array.isArray(state)) setColumnState(state)
    } catch {}
  }, [gridApi])

  const saveGridState = useCallback(() => {
    if (!gridApi) return
    try {
      const state = (gridApi as any).getColumnState?.()
      if (!Array.isArray(state)) return
      localStorage.setItem(GRID_STATE_STORAGE_KEY, JSON.stringify(state))
      setColumnState(state)
    } catch {}
  }, [gridApi])

  const throttledSaveGridState = useMemo(() => {
    let last = 0
    let timer: any
    return () => {
      const now = Date.now()
      const run = () => {
        last = Date.now()
        saveGridState()
      }
      if (now - last >= 300) {
        run()
      } else {
        clearTimeout(timer)
        timer = setTimeout(run, 300 - (now - last))
      }
    }
  }, [saveGridState])

  // --- Data Fetching ---
  const handleSearch = useCallback(async () => {
    setLoading(true)
    try {
      const response = await fetch('/data/ib_report_mock.csv')
      if (!response.ok) throw new Error('Failed to fetch mock data')
      
      const csvText = await response.text()
      const lines = csvText.split('\n')
      
      const parsedRows: IBReportRow[] = lines.slice(1)
        .filter(line => line.trim() !== '')
        .map(line => {
          const values = line.split(',')
          const row: any = {}
          
          row.group = values[0]
          row.user_name = values[1]
          row.time_range = values[2]
          
          const toIBValue = (rangeIdx: number, monthIdx: number): IBValue => ({
            range_val: parseFloat(values[rangeIdx]) || 0,
            month_val: parseFloat(values[monthIdx]) || 0
          })
          
          row.deposit = toIBValue(3, 4)
          row.withdrawal = toIBValue(5, 6)
          row.ib_withdrawal = toIBValue(7, 8)
          row.net_deposit = toIBValue(9, 10)
          row.volume = toIBValue(11, 12)
          row.adjustments = toIBValue(13, 14)
          
          // Default extended columns to 0 for now
          const zeroVal = { range_val: 0, month_val: 0 }
          row.commission = zeroVal
          row.ib_commission = zeroVal
          row.swap = zeroVal
          row.profit = zeroVal
          row.new_clients = zeroVal
          row.new_agents = zeroVal
          
          return row as IBReportRow
        })

      const filteredRows = selectedGroups.length > 0 
        ? parsedRows.filter(r => selectedGroups.includes(r.group))
        : parsedRows

      setRows(filteredRows)
    } catch (error) {
      console.error('Error loading IB report data:', error)
    } finally {
      setLoading(false)
    }
  }, [selectedGroups])

  // --- Auto search on mount ---
  useEffect(() => {
    handleSearch()
  }, [])

  // --- Table Configuration ---

  const defaultColDef = useMemo(() => ({
    sortable: true,
    resizable: true,
    filter: true,
    minWidth: 120,
  }), [])

  const ibValueComparator = (valA: IBValue, valB: IBValue) => {
    if (!valA) return -1
    if (!valB) return 1
    return valA.range_val - valB.range_val
  }

  const columnDefs = useMemo<ColDef[]>(() => [
    { field: "group", headerName: "组别", pinned: "left", width: 100 },
    { field: "user_name", headerName: "User Name", pinned: "left", width: 150 },
    { field: "time_range", headerName: "时间段", width: 200 },
    { 
      field: "deposit", 
      headerName: "入金 (USD)", 
      cellRenderer: DoubleValueRenderer,
      comparator: ibValueComparator,
      type: 'numericColumn'
    },
    { 
      field: "withdrawal", 
      headerName: "出金 (USD)", 
      cellRenderer: DoubleValueRenderer,
      comparator: ibValueComparator,
      type: 'numericColumn'
    },
    { 
      field: "ib_withdrawal", 
      headerName: "IB出金 (USD)", 
      cellRenderer: DoubleValueRenderer,
      comparator: ibValueComparator,
      type: 'numericColumn'
    },
    { 
      field: "net_deposit", 
      headerName: "净入金 (USD)", 
      cellRenderer: DoubleValueRenderer,
      comparator: ibValueComparator,
      type: 'numericColumn'
    },
    { 
      field: "volume", 
      headerName: "平仓交易量 (lots)", 
      cellRenderer: DoubleValueRenderer,
      comparator: ibValueComparator,
      type: 'numericColumn'
    },
    { 
      field: "adjustments", 
      headerName: "交易调整", 
      cellRenderer: DoubleValueRenderer,
      comparator: ibValueComparator,
      type: 'numericColumn'
    },
    // Extended columns (initially hidden)
    { 
      field: "commission", 
      headerName: "佣金 (Commission)", 
      cellRenderer: DoubleValueRenderer,
      comparator: ibValueComparator,
      type: 'numericColumn',
      hide: true
    },
    { 
      field: "ib_commission", 
      headerName: "IB 佣金", 
      cellRenderer: DoubleValueRenderer,
      comparator: ibValueComparator,
      type: 'numericColumn',
      hide: true
    },
    { 
      field: "swap", 
      headerName: "平仓利息 (Swap)", 
      cellRenderer: DoubleValueRenderer,
      comparator: ibValueComparator,
      type: 'numericColumn',
      hide: true
    },
    { 
      field: "profit", 
      headerName: "平仓盈亏 (Profit)", 
      cellRenderer: DoubleValueRenderer,
      comparator: ibValueComparator,
      type: 'numericColumn',
      hide: true
    },
    { 
      field: "new_clients", 
      headerName: "当天新开客户", 
      cellRenderer: DoubleValueRenderer,
      comparator: ibValueComparator,
      type: 'numericColumn',
      hide: true
    },
    { 
      field: "new_agents", 
      headerName: "当天新开代理", 
      cellRenderer: DoubleValueRenderer,
      comparator: ibValueComparator,
      type: 'numericColumn',
      hide: true
    }
  ], [])

  const toggleColumns = useMemo(() => {
    return (columnDefs || [])
      .map((c: any) => ({ colId: c.field, label: c.headerName }))
      .filter(x => x.colId)
  }, [columnDefs])

  const columnVisibilityMap = useMemo(() => {
    const m: Record<string, boolean> = {}
    ;(columnState || []).forEach((s: any) => {
      if (s && typeof s.colId === 'string') {
        m[s.colId] = !s.hide
      }
    })
    return m
  }, [columnState])

  // Totals Row
  const pinnedTopRowData = useMemo(() => {
    if (rows.length === 0) return []
    const sum = (field: keyof IBReportRow) => {
      return rows.reduce((acc, row) => {
        const val = row[field] as IBValue
        return {
          range_val: acc.range_val + val.range_val,
          month_val: acc.month_val + val.month_val
        }
      }, { range_val: 0, month_val: 0 })
    }

    return [{
      group: "汇总",
      user_name: "ALL GROUPS",
      time_range: "-",
      deposit: sum("deposit"),
      withdrawal: sum("withdrawal"),
      ib_withdrawal: sum("ib_withdrawal"),
      net_deposit: sum("net_deposit"),
      volume: sum("volume"),
      adjustments: sum("adjustments"),
      commission: sum("commission"),
      ib_commission: sum("ib_commission"),
      swap: sum("swap"),
      profit: sum("profit"),
      new_clients: sum("new_clients"),
      new_agents: sum("new_agents")
    }]
  }, [rows])

  return (
    <div className="flex flex-col gap-4 p-4 min-h-svh bg-background">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">IB 报表 (IB Report)</h1>
      </div>

      {/* Filter Card */}
      <Card>
        <CardContent className="py-3">
          <div className="flex flex-col sm:flex-row gap-3 items-center justify-between">
            <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 w-full sm:w-auto">
              {/* Date Range */}
              <Popover>
                <PopoverTrigger asChild>
                  <Button
                    variant="outline"
                    className={cn(
                      "w-full sm:w-[260px] justify-start text-left font-normal h-10",
                      !date && "text-muted-foreground"
                    )}
                  >
                    <CalendarIcon className="mr-2 h-4 w-4" />
                    {date?.from ? (
                      date.to ? (
                        <>{format(date.from, "LLL dd, y")} - {format(date.to, "LLL dd, y")}</>
                      ) : (
                        format(date.from, "LLL dd, y")
                      )
                    ) : (
                      <span>选择日期范围</span>
                    )}
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-0" align="start">
                  <Calendar
                    initialFocus
                    mode="range"
                    defaultMonth={date?.from}
                    selected={date}
                    onSelect={setDate}
                    numberOfMonths={2}
                  />
                </PopoverContent>
              </Popover>

              {/* Groups Select */}
              <Popover>
                <PopoverTrigger asChild>
                  <Button variant="outline" className="w-full sm:w-[260px] justify-between h-10">
                    <div className="flex items-center gap-2 overflow-hidden">
                      <Filter className="h-4 w-4 shrink-0" />
                      <span className="truncate">
                        {selectedGroups.length === 0 ? "全部组别" : `已选 ${selectedGroups.length} 个`}
                      </span>
                    </div>
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-[300px] p-0" align="start">
                  <div className="p-2 grid grid-cols-3 gap-1">
                    {PREDEFINED_GROUPS.map(g => (
                      <Button 
                        key={g} 
                        variant={selectedGroups.includes(g) ? "default" : "outline"}
                        size="sm"
                        className="text-xs px-1 h-7"
                        onClick={() => {
                          setSelectedGroups(prev => 
                            prev.includes(g) ? prev.filter(x => x !== g) : [...prev, g]
                          )
                        }}
                      >
                        {g}
                      </Button>
                    ))}
                  </div>
                  <div className="border-t p-2 flex justify-between bg-muted/50">
                    <Button variant="ghost" size="sm" className="text-xs h-7" onClick={() => setSelectedGroups([])}>清空</Button>
                    <Button variant="ghost" size="sm" className="text-xs h-7" onClick={() => setSelectedGroups([...PREDEFINED_GROUPS])}>全选</Button>
                  </div>
                </PopoverContent>
              </Popover>

              {/* Monthly Toggle */}
              <div className="flex items-center space-x-2 h-10">
                <Checkbox 
                  id="monthly-data" 
                  checked={includeMonthly} 
                  onCheckedChange={(checked) => setIncludeMonthly(!!checked)} 
                />
                <Label htmlFor="monthly-data" className="text-sm cursor-pointer select-none whitespace-nowrap">展示当月数据</Label>
              </div>
            </div>

            {/* Action Buttons */}
            <div className="flex flex-col sm:flex-row gap-2 w-full sm:w-auto">
              <Button onClick={handleSearch} disabled={loading} className="w-full sm:w-[140px]">
                <Search className={cn("h-4 w-4 mr-2", loading && "animate-spin")} />
                {loading ? "查询中..." : "查询"}
              </Button>

              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" className="w-full sm:w-[140px] whitespace-nowrap gap-2">
                    <Settings2 className="h-4 w-4" />
                    列显示
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-72 max-h-[60vh] overflow-auto">
                  <DropdownMenuLabel>显示列</DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  
                  <div className="px-2 pb-2 flex items-center gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 px-2"
                      onClick={() => {
                        if (!gridApi) return
                        try {
                          const ids = toggleColumns.map(c => c.colId)
                          ;(gridApi as any).setColumnsVisible?.(ids, true)
                          throttledSaveGridState()
                          setTimeout(() => refreshColumnState(gridApi), 0)
                        } catch {}
                      }}
                    >
                      全选
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 px-2"
                      onClick={() => {
                        if (!gridApi) return
                        try {
                          localStorage.removeItem(GRID_STATE_STORAGE_KEY)
                          ;(gridApi as any).resetColumnState?.()
                          setTimeout(() => refreshColumnState(gridApi), 0)
                        } catch {}
                      }}
                    >
                      重置
                    </Button>
                  </div>

                  <DropdownMenuSeparator />

                  {toggleColumns.map(({ colId, label }) => {
                    const checked = columnVisibilityMap[colId] ?? true
                    return (
                      <DropdownMenuCheckboxItem
                        key={colId}
                        checked={checked}
                        onSelect={(e) => { e.preventDefault() }}
                        onCheckedChange={(value: boolean) => {
                          if (!gridApi) return
                          try {
                            ;(gridApi as any).setColumnsVisible?.([colId], !!value)
                            throttledSaveGridState()
                            setTimeout(() => refreshColumnState(gridApi), 0)
                          } catch {}
                        }}
                      >
                        {label}
                      </DropdownMenuCheckboxItem>
                    )
                  })}
                </DropdownMenuContent>
              </DropdownMenu>

              <Button variant="outline" className="w-full sm:w-[140px]">
                <BarChart3 className="h-4 w-4 mr-2" />
                可视化图表
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Table Section - Removed Card to match ClientPnLAnalysis sandwich pattern */}
      <div className="flex-1 relative">
        <div className={cn(
          "w-full h-[750px]",
          isDarkMode ? "ag-theme-quartz-dark" : "ag-theme-quartz"
        )}>
          <AgGridReact
            rowData={rows}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            pinnedTopRowData={pinnedTopRowData}
            gridOptions={{ theme: 'legacy' }}
            rowHeight={50}
            headerHeight={40}
            animateRows={true}
            onGridReady={params => {
              setGridApi(params.api)
              // Restore column state from localStorage
              try {
                const raw = localStorage.getItem(GRID_STATE_STORAGE_KEY)
                if (raw) {
                  const saved = JSON.parse(raw)
                  if (Array.isArray(saved) && saved.length > 0) {
                    ;(params.api as any).applyColumnState?.({ state: saved, applyOrder: true })
                  }
                }
              } catch {}
              setTimeout(() => refreshColumnState(params.api), 0)
            }}
            onColumnResized={(e: any) => { if (e?.finished) throttledSaveGridState() }}
            onColumnMoved={() => throttledSaveGridState()}
            onColumnVisible={() => throttledSaveGridState()}
            onColumnPinned={() => throttledSaveGridState()}
          />
        </div>
      </div>

    </div>
  )
}
