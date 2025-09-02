import * as React from "react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Calendar } from "@/components/ui/calendar"
import { Calendar as CalendarIcon, Download, Loader2 } from "lucide-react"
import { DateRange } from "react-day-picker"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
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
  getPaginationRowModel,
  useReactTable,
} from "@tanstack/react-table"

// fresh grad: This page keeps a consistent skeleton
// Top: filters card; Bottom: data table card

type DownloadRow = {
  ticket: number
  account_id: number
  client_id: number | null
  symbol: string
  volume: number
  open_time: string | null
  close_time: string | null
  modify_time: string | null
  profit: number
  cmd: number
  open_price: number
  close_price: number | null
  swaps: number | null
  comment: string | null
  sl: number | null
  tp: number | null
  ibid: string | null
}

export default function DownloadsPage() {
  // controlled filters
  const [symbol, setSymbol] = React.useState<string>("XAU-CNH")
  const [customSymbol, setCustomSymbol] = React.useState<string>("")
  const [server, setServer] = React.useState<"mt4_live" | "mt4_live2">("mt4_live")
  const [range, setRange] = React.useState<DateRange | undefined>({ from: new Date(), to: new Date() })
  // defaults for visibility/order (ensure default columns on first load)
  const defaultVisibility: VisibilityState = React.useMemo(() => ({
    ticket: false,
    account_id: true,
    client_id: true,
    symbol: true,
    open_time: true,
    close_time: true,
    modify_time: false,
    volume: true,
    profit: true,
    cmd: true,
    open_price: false,
    close_price: false,
    swaps: false,
    comment: false,
    sl: false,
    tp: false,
    ibid: false,
  }), [])
  const defaultOrder: ColumnOrderState = React.useMemo(
    () => [
      "account_id","client_id","symbol","open_time","close_time","volume","profit","cmd",
      "ticket","open_price","close_price","modify_time","swaps","comment","sl","tp","ibid",
    ],
    [],
  )
  // table states
  const [rows, setRows] = React.useState<DownloadRow[]>([])
  const [loading, setLoading] = React.useState<boolean>(false)
  const [error, setError] = React.useState<string | null>(null)
  // tanstack table states
  const [sorting, setSorting] = React.useState<SortingState>([])
  const [columnFilters, setColumnFilters] = React.useState<ColumnFiltersState>([])
  const [columnVisibility, setColumnVisibility] = React.useState<VisibilityState>(defaultVisibility)
  const [columnOrder, setColumnOrder] = React.useState<ColumnOrderState>(defaultOrder)
  const [pageSize, setPageSize] = React.useState<number>(() => {
    try { return Number(localStorage.getItem("downloads_page_size") || 50) } catch { return 50 }
  })
  const [pageIndex, setPageIndex] = React.useState<number>(0)

  // defaults for visibility/order

  // drag state
  const dragCol = React.useRef<string | null>(null)

  // fresh grad: format range label like "Aug 01, 2025 - Aug 07, 2025"
  const rangeLabel = React.useMemo(() => {
    if (!range?.from || !range?.to) return "选择日期范围"
    const opts: Intl.DateTimeFormatOptions = { month: "short", day: "2-digit", year: "numeric" }
    return `${range.from.toLocaleDateString("en-US", opts)} - ${range.to.toLocaleDateString("en-US", opts)}`
  }, [range])

  // fresh grad: placeholder handlers (wire to API later)
  async function onQuery() {
    if (!range?.from || !range?.to) return
    setLoading(true)
    setError(null)
    try {
      const effectiveSymbol = symbol === "other" ? customSymbol.trim() : symbol
      const body = {
        symbols: [effectiveSymbol],
        start_date: `${range.from.getFullYear()}-${String(range.from.getMonth() + 1).padStart(2, "0")}-${String(range.from.getDate()).padStart(2, "0")}`,
        end_date: `${range.to.getFullYear()}-${String(range.to.getMonth() + 1).padStart(2, "0")}-${String(range.to.getDate()).padStart(2, "0")}`,
        source: server,
      }
      const res = await fetch(`/api/v1/downloads/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json", accept: "application/json" },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      if (!json.ok) throw new Error(json.error || "unknown error")
      const items = json.items as DownloadRow[]
      setRows(items)
      // persist last query & data in session
      try {
        sessionStorage.setItem("downloads_rows", JSON.stringify(items))
        sessionStorage.setItem(
          "downloads_query_params",
          JSON.stringify({
            symbol,
            customSymbol,
            server,
            rangeFrom: range?.from?.toISOString?.(),
            rangeTo: range?.to?.toISOString?.(),
          }),
        )
      } catch {}
    } catch (e: any) {
      setError(e?.message || "请求失败")
    } finally {
      setLoading(false)
    }
  }

  async function onExport() {
    if (!range?.from || !range?.to) return
    const effectiveSymbol = symbol === "other" ? customSymbol.trim() : symbol
    const body = {
      symbols: [effectiveSymbol],
      start_date: `${range.from.getFullYear()}-${String(range.from.getMonth() + 1).padStart(2, "0")}-${String(range.from.getDate()).padStart(2, "0")}`,
      end_date: `${range.to.getFullYear()}-${String(range.to.getMonth() + 1).padStart(2, "0")}-${String(range.to.getDate()).padStart(2, "0")}`,
      source: server,
    }
    try {
      const res = await fetch(`/api/v1/downloads/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const blob = await res.blob()
      // 解析文件名
      const disposition = res.headers.get("Content-Disposition") || ""
      const m = disposition.match(/filename=([^;]+)/)
      const filename = m ? m[1] : "downloads.csv"
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      // ignore for now; optional toast
    }
  }

  // tanstack columns & filters
  const filterFns = React.useMemo(() => ({
    numberRange: (row: any, columnId: string, value: { min?: number; max?: number }) => {
      const v = Number(row.getValue(columnId) ?? 0)
      const minOk = value?.min == null || v >= Number(value.min)
      const maxOk = value?.max == null || v <= Number(value.max)
      return minOk && maxOk
    },
    dateRange: (row: any, columnId: string, value: { from?: string; to?: string }) => {
      const ts = row.getValue(columnId) ? new Date(row.getValue(columnId) as string).getTime() : 0
      const fromTs = value?.from ? new Date(value.from).getTime() : undefined
      const toTs = value?.to ? new Date(value.to).getTime() : undefined
      const fromOk = fromTs == null || ts >= fromTs
      const toOk = toTs == null || ts <= toTs
      return fromOk && toOk
    },
    equalsCmd: (row: any, columnId: string, value: "buy" | "sell" | "") => {
      if (!value) return true
      const v = Number(row.getValue(columnId))
      return value === "buy" ? v === 0 : value === "sell" ? v === 1 : true
    },
    nonZero: (row: any, columnId: string, value: boolean) => {
      if (!value) return true
      const v = Number(row.getValue(columnId) ?? 0)
      return v !== 0
    },
  }), [])

  const columns = React.useMemo<ColumnDef<DownloadRow>[]>(() => [
    { id: "ticket", accessorKey: "ticket", header: "Ticket", enableHiding: true, enableSorting: true },
    { id: "account_id", accessorKey: "account_id", header: "AccountID" },
    { id: "client_id", accessorKey: "client_id", header: "ClientID" },
    { id: "symbol", accessorKey: "symbol", header: ({ column }) => (
      <div className="flex flex-col gap-1">
        <div>Symbol</div>
        <Input className="h-8 w-[140px]" placeholder="筛选..." value={(column.getFilterValue() as string) ?? ""} onChange={(e) => column.setFilterValue(e.target.value)} />
      </div>
    ) },
    { id: "open_time", accessorKey: "open_time", header: ({ column }) => (
      <div className="flex flex-col gap-1">
        <div>Open Time</div>
        <Input className="h-8 w-[160px]" placeholder="起(YYYY-MM-DD HH:mm:ss)" onChange={(e) => column.setFilterValue({ ...(column.getFilterValue() as any), from: e.target.value })} />
        <Input className="h-8 w-[160px]" placeholder="止(YYYY-MM-DD HH:mm:ss)" onChange={(e) => column.setFilterValue({ ...(column.getFilterValue() as any), to: e.target.value })} />
      </div>
    ), cell: ({ row }) => formatDateTime(row.getValue("open_time")), filterFn: filterFns.dateRange as any },
    { id: "close_time", accessorKey: "close_time", header: ({ column }) => (
      <div className="flex flex-col gap-1">
        <div>Close Time</div>
        <Input className="h-8 w-[160px]" placeholder="起" onChange={(e) => column.setFilterValue({ ...(column.getFilterValue() as any), from: e.target.value })} />
        <Input className="h-8 w-[160px]" placeholder="止" onChange={(e) => column.setFilterValue({ ...(column.getFilterValue() as any), to: e.target.value })} />
      </div>
    ), cell: ({ row }) => formatDateTime(row.getValue("close_time")), filterFn: filterFns.dateRange as any },
    { id: "modify_time", accessorKey: "modify_time", header: ({ column }) => (
      <div className="flex flex-col gap-1">
        <div>Modify Time</div>
        <Input className="h-8 w-[160px]" placeholder="起" onChange={(e) => column.setFilterValue({ ...(column.getFilterValue() as any), from: e.target.value })} />
        <Input className="h-8 w-[160px]" placeholder="止" onChange={(e) => column.setFilterValue({ ...(column.getFilterValue() as any), to: e.target.value })} />
      </div>
    ), cell: ({ row }) => formatDateTime(row.getValue("modify_time")), filterFn: filterFns.dateRange as any },
    { id: "volume", accessorKey: "volume", header: ({ column }) => (
      <div className="flex flex-col gap-1">
        <div>Volume</div>
        <div className="flex items-center gap-1">
          <Input className="h-8 w-[80px]" placeholder=">=min" onChange={(e) => column.setFilterValue({ ...(column.getFilterValue() as any), min: e.target.value ? Number(e.target.value) : undefined })} />
          <Input className="h-8 w-[80px]" placeholder="<=max" onChange={(e) => column.setFilterValue({ ...(column.getFilterValue() as any), max: e.target.value ? Number(e.target.value) : undefined })} />
        </div>
      </div>
    ), cell: ({ row }) => format2(row.getValue("volume")), filterFn: filterFns.numberRange as any },
    { id: "profit", accessorKey: "profit", header: ({ column }) => (
      <div className="flex flex-col gap-1">
        <div>Profit</div>
        <div className="flex items-center gap-1">
          <Input className="h-8 w-[80px]" placeholder=">=min" onChange={(e) => column.setFilterValue({ ...(column.getFilterValue() as any), min: e.target.value ? Number(e.target.value) : undefined })} />
          <Input className="h-8 w-[80px]" placeholder="<=max" onChange={(e) => column.setFilterValue({ ...(column.getFilterValue() as any), max: e.target.value ? Number(e.target.value) : undefined })} />
        </div>
      </div>
    ), cell: ({ row }) => (<span className={`${profitClass(row.getValue("profit"))}`}>{format2(row.getValue("profit"))}</span>), filterFn: filterFns.numberRange as any },
    { id: "cmd", accessorKey: "cmd", header: ({ column }) => (
      <div className="flex flex-col gap-1">
        <div>Type</div>
        <Select
          value={((column.getFilterValue() as string) ?? "all")}
          onValueChange={(v) => column.setFilterValue(v === "all" ? "" : v)}
        >
          <SelectTrigger className="h-8 w-[120px]"><SelectValue placeholder="All" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All</SelectItem>
            <SelectItem value="buy">Buy</SelectItem>
            <SelectItem value="sell">Sell</SelectItem>
          </SelectContent>
        </Select>
      </div>
    ), cell: ({ row }) => (Number(row.getValue("cmd")) === 0 ? "Buy" : "Sell"), filterFn: filterFns.equalsCmd as any },
    { id: "open_price", accessorKey: "open_price", header: ({ column }) => (
      <div className="flex flex-col gap-1">
        <div>Open Price</div>
        <div className="flex items-center gap-1">
          <Input className="h-8 w-[80px]" placeholder=">=min" onChange={(e) => column.setFilterValue({ ...(column.getFilterValue() as any), min: e.target.value ? Number(e.target.value) : undefined })} />
          <Input className="h-8 w-[80px]" placeholder="<=max" onChange={(e) => column.setFilterValue({ ...(column.getFilterValue() as any), max: e.target.value ? Number(e.target.value) : undefined })} />
        </div>
      </div>
    ), cell: ({ row }) => format2(row.getValue("open_price")), filterFn: filterFns.numberRange as any },
    { id: "close_price", accessorKey: "close_price", header: ({ column }) => (
      <div className="flex flex-col gap-1">
        <div>Close Price</div>
        <div className="flex items-center gap-1">
          <Input className="h-8 w-[80px]" placeholder=">=min" onChange={(e) => column.setFilterValue({ ...(column.getFilterValue() as any), min: e.target.value ? Number(e.target.value) : undefined })} />
          <Input className="h-8 w-[80px]" placeholder="<=max" onChange={(e) => column.setFilterValue({ ...(column.getFilterValue() as any), max: e.target.value ? Number(e.target.value) : undefined })} />
        </div>
      </div>
    ), cell: ({ row }) => format2(row.getValue("close_price") ?? 0), filterFn: filterFns.numberRange as any },
    { id: "swaps", accessorKey: "swaps", header: ({ column }) => (
      <div className="flex flex-col gap-1">
        <div>Swaps</div>
        <div className="flex items-center gap-2">
          <Checkbox checked={Boolean(column.getFilterValue())} onCheckedChange={(v) => column.setFilterValue(Boolean(v))} />
          <span className="text-xs">仅显示≠0</span>
        </div>
      </div>
    ), cell: ({ row }) => format2(row.getValue("swaps") ?? 0), filterFn: filterFns.nonZero as any },
    { id: "comment", accessorKey: "comment", header: ({ column }) => (
      <div className="flex flex-col gap-1">
        <div>Comment</div>
        <Input className="h-8 w-[160px]" placeholder="包含..." value={(column.getFilterValue() as string) ?? ""} onChange={(e) => column.setFilterValue(e.target.value)} />
      </div>
    ) },
    { id: "sl", accessorKey: "sl", header: "SL", cell: ({ row }) => row.getValue("sl") ?? "-" },
    { id: "tp", accessorKey: "tp", header: "TP", cell: ({ row }) => row.getValue("tp") ?? "-" },
    { id: "ibid", accessorKey: "ibid", header: "IBID" },
  ], [filterFns])

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting, columnFilters, columnVisibility, columnOrder, pagination: { pageIndex, pageSize } },
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    onColumnVisibilityChange: setColumnVisibility,
    onColumnOrderChange: setColumnOrder,
    onPaginationChange: (updater) => {
      const next = typeof updater === "function" ? updater({ pageIndex, pageSize }) : updater
      setPageIndex((next as any).pageIndex ?? pageIndex)
      setPageSize((next as any).pageSize ?? pageSize)
    },
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    filterFns,
  })

  // persist table state
  React.useEffect(() => {
    try {
      const raw = localStorage.getItem("downloads_table_state")
      if (raw) {
        const s = JSON.parse(raw) as any
        if (s.visibility) setColumnVisibility(s.visibility)
        if (s.order) setColumnOrder(s.order)
        if (s.sorting) setSorting(s.sorting)
        if (s.filters) setColumnFilters(s.filters)
        if (s.pageSize) setPageSize(s.pageSize)
      } else {
        // first load: apply defaults
        setColumnVisibility(defaultVisibility)
        setColumnOrder(defaultOrder)
      }
    } catch {}
  }, [defaultVisibility, defaultOrder])
  React.useEffect(() => {
    try {
      localStorage.setItem("downloads_table_state", JSON.stringify({
        visibility: columnVisibility,
        order: columnOrder,
        sorting,
        filters: columnFilters,
        pageSize,
      }))
      localStorage.setItem("downloads_page_size", String(pageSize))
    } catch {}
  }, [columnVisibility, columnOrder, sorting, columnFilters, pageSize])

  // restore last query params & data (session)
  React.useEffect(() => {
    try {
      const qRaw = sessionStorage.getItem("downloads_query_params")
      if (qRaw) {
        const q = JSON.parse(qRaw) as any
        if (q.symbol) setSymbol(q.symbol)
        if (q.customSymbol != null) setCustomSymbol(q.customSymbol)
        if (q.server) setServer(q.server)
        if (q.rangeFrom && q.rangeTo) {
          const from = new Date(q.rangeFrom)
          const to = new Date(q.rangeTo)
          if (!Number.isNaN(from.getTime()) && !Number.isNaN(to.getTime())) setRange({ from, to })
        }
      }
      const rowsRaw = sessionStorage.getItem("downloads_rows")
      if (rowsRaw) {
        const data = JSON.parse(rowsRaw) as DownloadRow[]
        if (Array.isArray(data)) setRows(data)
      }
    } catch {}
  }, [])

  function format2(n: number | null | undefined): string {
    return Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  }
  function profitClass(n: number): string {
    if (n > 0) return "text-green-600 dark:text-green-400"
    if (n < 0) return "text-red-600 dark:text-red-400"
    return "text-foreground"
  }

  // time formatting helper
  function formatDateTime(iso: string | null | undefined): string {
    if (!iso) return "-"
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return String(iso)
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, "0")
    const day = String(d.getDate()).padStart(2, "0")
    const hh = String(d.getHours()).padStart(2, "0")
    const mm = String(d.getMinutes()).padStart(2, "0")
    const ss = String(d.getSeconds()).padStart(2, "0")
    return `${y}-${m}-${day} ${hh}:${mm}:${ss}`
  }

  // removed legacy local sorting/filtering/pagination (now TanStack controlled)

  return (
    <div className="space-y-4 px-4 pb-6 lg:px-6">
      {/* Toolbar */}
      <Card>
        <CardHeader>
          <CardTitle className="text-2xl font-bold">筛选与下载</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 md:flex-row md:items-center md:gap-6">
          {/* 产品选择 + 其他 */}
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">产品</span>
            <Select value={symbol} onValueChange={(v) => setSymbol(v)}>
              <SelectTrigger className="h-10 w-[180px] rounded-md">
                <SelectValue placeholder="请选择产品" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="XAU-CNH">XAU-CNH</SelectItem>
                <SelectItem value="XAUUSD">XAUUSD</SelectItem>
                <SelectItem value="other">其他</SelectItem>
              </SelectContent>
            </Select>
            {symbol === "other" && (
              <Input
                className="h-10 w-[180px]"
                placeholder="自定义产品"
                value={customSymbol}
                onChange={(e) => setCustomSymbol(e.target.value)}
              />
            )}
          </div>

          {/* 交易服务器（胶囊式切换） */}
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">交易服务器</span>
            <ToggleGroup
              type="single"
              value={server}
              onValueChange={(v) => v && setServer(v as "mt4_live" | "mt4_live2")}
              className="inline-flex w-[240px] items-center rounded-full bg-muted p-1"
            >
              <ToggleGroupItem value="mt4_live" className="flex-1 rounded-full first:rounded-l-full last:rounded-r-full px-3 py-1 text-center text-sm text-muted-foreground data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow">MT4 Live 1</ToggleGroupItem>
              <ToggleGroupItem value="mt4_live2" className="flex-1 rounded-full first:rounded-l-full last:rounded-r-full px-3 py-1 text-center text-sm text-muted-foreground data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow">MT4 Live 2</ToggleGroupItem>
            </ToggleGroup>
          </div>

          {/* 日期范围（Range 日历） */}
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">时间范围</span>
            <Popover>
              <PopoverTrigger asChild>
                <Button variant="outline" className="justify-start gap-2 font-normal">
                  <CalendarIcon className="h-4 w-4" />
                  <span>{rangeLabel}</span>
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-auto p-0" align="start">
                <Calendar
                  mode="range"
                  selected={range}
                  onSelect={(v) => setRange(v)}
                  numberOfMonths={2}
                  initialFocus
                />
              </PopoverContent>
            </Popover>
          </div>

          {/* 操作按钮 */}
          <div className="flex items-center gap-3">
            <Button className="h-9 w-[96px] gap-2" onClick={() => onQuery()} disabled={loading}>
              {loading && <Loader2 className="h-4 w-4 animate-spin" />}
              查询
            </Button>
            <Button className="h-9 w-[120px] gap-2" variant="secondary" onClick={onExport} disabled={loading}>
              <Download className="h-4 w-4" /> 导出CSV
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Table */}
      <Card>
        <CardContent className="pt-6">
          <div className="mx-auto w-[90%] max-w-[1600px]">
            {/* Filters now per-column in headers via TanStack */}
            {/* Column visibility controls (mapped from tanstack state) */}
            <div className="mb-3 flex flex-wrap gap-3 items-center text-xs">
              {(() => {
                const labels: Record<string, string> = {
                  ticket: "Ticket",
                  account_id: "AccountID",
                  client_id: "ClientID",
                  symbol: "Symbol",
                  open_time: "Open Time",
                  close_time: "Close Time",
                  modify_time: "Modify Time",
                  volume: "Volume",
                  profit: "Profit",
                  cmd: "Type",
                  open_price: "Open Price",
                  close_price: "Close Price",
                  swaps: "Swaps",
                  comment: "Comment",
                  sl: "SL",
                  tp: "TP",
                  ibid: "IBID",
                }
                const order = [
                  "ticket","account_id","client_id","symbol",
                  "open_time","close_time","modify_time",
                  "volume","profit","cmd",
                  "open_price","close_price",
                  "swaps","comment","sl","tp","ibid",
                ] as const

                return order.map((id) => {
                  const col = table.getColumn(id as string)
                  if (!col) return null
                  return (
                    <div key={id as string} className="flex items-center gap-2">
                      <Checkbox id={`col-${id as string}`} checked={col.getIsVisible()} onCheckedChange={(v) => col.toggleVisibility(Boolean(v))} />
                      <Label htmlFor={`col-${id as string}`} className="text-xs cursor-pointer select-none">{labels[id as string]}</Label>
                    </div>
                  )
                })
              })()}
            </div>
            <div className="overflow-hidden rounded-md border-2 shadow-md">
              <Table className="min-w-[960px]">
                <TableHeader>
                  {table.getHeaderGroups().map((hg) => (
                    <TableRow key={hg.id}>
                      {hg.headers.map((header) => (
                        <TableHead
                          key={header.id}
                          draggable
                          onDragStart={() => (dragCol.current = header.column.id)}
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={() => {
                            const from = dragCol.current
                            const to = header.column.id
                            if (!from || from === to) return
                            const order = table.getState().columnOrder
                            const fromIdx = order.indexOf(from)
                            const toIdx = order.indexOf(to)
                            if (fromIdx === -1 || toIdx === -1) return
                            const next = [...order]
                            next.splice(fromIdx, 1)
                            next.splice(toIdx, 0, from)
                            setColumnOrder(next)
                            dragCol.current = null
                          }}
                          className="align-middle border-r font-semibold text-base"
                        >
                          {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                        </TableHead>
                      ))}
                    </TableRow>
                  ))}
                </TableHeader>
                <TableBody>
                  {table.getRowModel().rows.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={table.getAllLeafColumns().length} className="text-center py-8 text-sm text-muted-foreground">{error ? error : "暂无数据，请设置筛选后查询"}</TableCell>
                    </TableRow>
                  ) : (
                    table.getRowModel().rows.map((row) => (
                      <TableRow key={row.id}>
                        {row.getVisibleCells().map((cell) => (
                          <TableCell key={cell.id} className="border-r tabular-nums">
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>
            <div className="flex items-center justify-between py-3">
              <div className="text-sm text-muted-foreground">共 {table.getFilteredRowModel().rows.length.toLocaleString()} 条</div>
              <div className="flex items-center gap-2">
                <Select value={String(pageSize)} onValueChange={(v) => { setPageSize(Number(v)); setPageIndex(0); table.setPageSize(Number(v)) }}>
                  <SelectTrigger className="h-9 w-[100px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="20">20/页</SelectItem>
                    <SelectItem value="50">50/页</SelectItem>
                    <SelectItem value="100">100/页</SelectItem>
                  </SelectContent>
                </Select>
                <Button variant="outline" disabled={!table.getCanPreviousPage() || loading} onClick={() => table.previousPage()}>上一页</Button>
                <div className="px-2 text-sm">第 {table.getState().pagination.pageIndex + 1} 页</div>
                <Button variant="outline" disabled={!table.getCanNextPage() || loading} onClick={() => table.nextPage()}>下一页</Button>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
