import * as React from "react"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
  DrawerTrigger,
} from "@/components/ui/drawer"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Badge } from "@/components/ui/badge"
import { Calendar } from "@/components/ui/calendar"
// removed DateRange (range mode) in favor of dual single calendars
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import {
  ColumnDef,
  ColumnFiltersState,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  SortingState,
  useReactTable,
  VisibilityState,
} from "@tanstack/react-table"
import { ArrowUpDown, ChevronDown, MoreHorizontal } from "lucide-react"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  Pie,
  PieChart,
  XAxis,
  YAxis,
} from "recharts"

// Page: 客户交易分析（静态原型）
// fresh grad: All data below are static mock data to show layout and style.

// 顶部指标卡静态数据
const kpis = [
  { label: "净收益 (PnL)", value: 5000, unit: "USD", trend: "+", tone: "pos" },
  { label: "盈亏因子", value: 1.6, trend: "=", tone: "neu" },
  { label: "胜率", value: 0.62, unit: "%", trend: "+", tone: "neu" },
  { label: "最大回撤", value: -25, unit: "%", trend: "-", tone: "neg" },
  { label: "Sharpe Ratio", value: 1.2, trend: "=", tone: "neu" },
]

// 资金曲线与回撤（静态）
const equityData = Array.from({ length: 24 }).map((_, i) => {
  const base = 10000
  const equity = base + i * 120 + (i % 5 === 0 ? -300 : 0)
  const peak = Math.max(...Array.from({ length: i + 1 }).map((__, j) => base + j * 120 + (j % 5 === 0 ? -300 : 0)))
  const drawdown = Math.min(0, ((equity - peak) / peak) * 100)
  return { t: `Day ${i + 1}`, equity, drawdown }
})

const equityChartConfig: ChartConfig = {
  equity: { label: "权益", color: "var(--primary)" },
  drawdown: { label: "回撤%", color: "hsl(0 80% 60%)" },
}

// 成本拆解（瀑布式条形图的近似，用堆叠柱）
const costData = [
  { label: "点差", cost: 1200 },
  { label: "佣金", cost: 800 },
  { label: "隔夜利息", cost: 300 },
  { label: "滑点", cost: 200 },
]

const costChartConfig: ChartConfig = {
  cost: { label: "成本", color: "hsl(220 80% 60%)" },
}

// 品种占比（饼图）
const symbolShare = [
  { name: "XAUUSD", value: 45, fill: "hsl(45 90% 55%)" },
  { name: "XAGUSD", value: 20, fill: "hsl(0 80% 60%)" },
  { name: "EURUSD", value: 18, fill: "hsl(210 80% 60%)" },
  { name: "GBPUSD", value: 10, fill: "hsl(280 70% 60%)" },
  { name: "US30", value: 7, fill: "hsl(150 70% 45%)" },
]

const symbolConfig: ChartConfig = {
  share: { label: "占比", color: "var(--primary)" },
}

// Orders table static demo data and columns
type OrderPayment = {
  id: string
  amount: number
  status: "pending" | "processing" | "success" | "failed"
  email: string
}

const ordersData: OrderPayment[] = [
  { id: "m5gr84i9", amount: 316, status: "success", email: "ken99@example.com" },
  { id: "3u1reuv4", amount: 242, status: "success", email: "Abe45@example.com" },
  { id: "derv1ws0", amount: 837, status: "processing", email: "Monserrat44@example.com" },
  { id: "5kma53ae", amount: 874, status: "success", email: "Silas22@example.com" },
  { id: "bhqecj4p", amount: 721, status: "failed", email: "carmella@example.com" },
]

const ordersColumns: ColumnDef<OrderPayment>[] = [
  {
    id: "select",
    header: ({ table }) => (
      <Checkbox
        checked={table.getIsAllPageRowsSelected() || (table.getIsSomePageRowsSelected() && "indeterminate")}
        onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
        aria-label="Select all"
      />
    ),
    cell: ({ row }) => (
      <Checkbox
        checked={row.getIsSelected()}
        onCheckedChange={(value) => row.toggleSelected(!!value)}
        aria-label="Select row"
      />
    ),
    enableSorting: false,
    enableHiding: false,
  },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <div className="capitalize">{row.getValue("status")}</div>,
  },
  {
    accessorKey: "email",
    header: ({ column }) => (
      <Button variant="ghost" onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}>
        Email
        <ArrowUpDown />
      </Button>
    ),
    cell: ({ row }) => <div className="lowercase">{row.getValue("email")}</div>,
  },
  {
    accessorKey: "amount",
    header: () => <div className="text-right">Amount</div>,
    cell: ({ row }) => {
      const amount = parseFloat(row.getValue("amount"))
      const formatted = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(amount)
      return <div className="text-right font-medium">{formatted}</div>
    },
  },
  {
    id: "actions",
    enableHiding: false,
    cell: ({ row }) => {
      const payment = row.original
      return (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" className="h-8 w-8 p-0">
              <span className="sr-only">Open menu</span>
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>Actions</DropdownMenuLabel>
            <DropdownMenuItem onClick={() => navigator.clipboard.writeText(payment.id)}>
              Copy payment ID
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem>View customer</DropdownMenuItem>
            <DropdownMenuItem>View payment details</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )
    },
  },
]

function OrdersTable() {
  const [sorting, setSorting] = React.useState<SortingState>([])
  const [columnFilters, setColumnFilters] = React.useState<ColumnFiltersState>([])
  const [columnVisibility, setColumnVisibility] = React.useState<VisibilityState>({})
  const [rowSelection, setRowSelection] = React.useState({})

  const table = useReactTable({
    data: ordersData,
    columns: ordersColumns,
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    onColumnVisibilityChange: setColumnVisibility,
    onRowSelectionChange: setRowSelection,
    state: { sorting, columnFilters, columnVisibility, rowSelection },
  })

  return (
    <div className="w-full">
      <div className="flex items-center py-4">
        <Input
          placeholder="Filter emails..."
          value={(table.getColumn("email")?.getFilterValue() as string) ?? ""}
          onChange={(event) => table.getColumn("email")?.setFilterValue(event.target.value)}
          className="max-w-sm"
        />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" className="ml-auto">
              Columns <ChevronDown />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {table
              .getAllColumns()
              .filter((column) => column.getCanHide())
              .map((column) => (
                <DropdownMenuCheckboxItem
                  key={column.id}
                  className="capitalize"
                  checked={column.getIsVisible()}
                  onCheckedChange={(value) => column.toggleVisibility(!!value)}
                >
                  {column.id}
                </DropdownMenuCheckboxItem>
              ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <div className="overflow-hidden rounded-md border">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <TableHead key={header.id}>
                    {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows?.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow key={row.id} data-state={row.getIsSelected() && "selected"}>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell colSpan={ordersColumns.length} className="h-24 text-center">
                  No results.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      <div className="flex items-center justify-end space-x-2 py-4">
        <div className="text-muted-foreground flex-1 text-sm">
          {table.getFilteredSelectedRowModel().rows.length} of {table.getFilteredRowModel().rows.length} row(s) selected.
        </div>
        <div className="space-x-2">
          <Button variant="outline" size="sm" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>
            Previous
          </Button>
          <Button variant="outline" size="sm" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>
            Next
          </Button>
        </div>
      </div>
    </div>
  )
}

export default function ClientTradingAnalyticsPage() {
  // --- Static filters state (for demo only) ---
  type Rule =
    | { type: "customer_ids"; ids: number[]; include: boolean }
    | { type: "customer_tags"; source: "local" | "crm"; tags: string[]; operator: "ANY" | "ALL"; include: boolean }
    | { type: "account_ids"; ids: string[]; include: boolean }

  // sample datasets (static) for symbols only
  const sampleTagsLocal = ["VIP", "HighTurnover", "NewUser"]
  const sampleTagsCRM = ["XAU-Focus", "Scalper", "Asia-Desk"]
  const sampleSymbols = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "US30"]
  // removed ib tree (not used now)

  const [rules, setRules] = React.useState<Rule[]>([])
  // removed: dedicated preview drawer state in compact mode

  // ephemeral inputs for adding rules
  const [inputCustomerId, setInputCustomerId] = React.useState("")
  const [inputAccountId, setInputAccountId] = React.useState("")
  // removed ib inputs
  const [tagSource, setTagSource] = React.useState<"local" | "crm">("local")
  const [selectedLocalTags, setSelectedLocalTags] = React.useState<string[]>([])
  const [selectedCrmTags, setSelectedCrmTags] = React.useState<string[]>([])
  const [tagOperator, setTagOperator] = React.useState<"ANY" | "ALL">("ANY")

  // time filters (static, dual single-calendars + quick ranges)
  // fresh grad: keep two single calendars for start and end with dropdown month/year.
  const [startDate, setStartDate] = React.useState<Date | undefined>(new Date())
  const [endDate, setEndDate] = React.useState<Date | undefined>(new Date())
  const [quickRange, setQuickRange] = React.useState<"last_1w" | "last_1m" | "last_3m" | "all" | "custom">("last_1m")

  // fresh grad: when quick range changes, compute start/end based on today
  const applyQuickRange = React.useCallback((qr: typeof quickRange) => {
    const today = new Date()
    // zero time for consistency
    const d0 = new Date(today.getFullYear(), today.getMonth(), today.getDate())
    if (qr === "all") {
      setStartDate(undefined)
      setEndDate(undefined)
      setQuickRange(qr)
      return
    }
    if (qr === "last_1w") {
      const s = new Date(d0)
      s.setDate(s.getDate() - 6)
      setStartDate(s)
      setEndDate(d0)
    } else if (qr === "last_1m") {
      const s = new Date(d0)
      s.setMonth(s.getMonth() - 1)
      s.setDate(s.getDate() + 1) // approx: last 1 month inclusive
      setStartDate(s)
      setEndDate(d0)
    } else if (qr === "last_3m") {
      const s = new Date(d0)
      s.setMonth(s.getMonth() - 3)
      s.setDate(s.getDate() + 1)
      setStartDate(s)
      setEndDate(d0)
    } else if (qr === "custom") {
      // keep current start/end, only mark as custom
    }
    setQuickRange(qr)
  }, [])

  React.useEffect(() => {
    // initialize to last_1m
    applyQuickRange("last_1m")
  }, [applyQuickRange])

  const rangeLabel = React.useMemo(() => {
    if (quickRange === "all") return "全部历史"
    if (quickRange === "last_1w") return "最近 1 周"
    if (quickRange === "last_1m") return "最近 1 个月"
    if (quickRange === "last_3m") return "最近 3 个月"
    if (!startDate || !endDate) return "选择日期范围"
    const opts: Intl.DateTimeFormatOptions = { month: "short", day: "2-digit", year: "numeric" }
    return `${startDate.toLocaleDateString("en-US", opts)} - ${endDate.toLocaleDateString("en-US", opts)}`
  }, [quickRange, startDate, endDate])

  // symbols (static multi-select + custom input)
  const [selectedSymbols, setSelectedSymbols] = React.useState<string[]>([])
  const [customSymbols, setCustomSymbols] = React.useState<string[]>([])
  const [customSymbolInput, setCustomSymbolInput] = React.useState("")
  const combinedSymbols = React.useMemo(() => Array.from(new Set([...
    sampleSymbols,
    ...customSymbols,
  ])), [customSymbols])
  const [ruleType, setRuleType] = React.useState<"customer_ids" | "customer_tags" | "account_ids">("customer_ids")
  const [symbolsMode, setSymbolsMode] = React.useState<"all" | "custom">("all")
  React.useEffect(() => {
    if (symbolsMode === "all") setSelectedSymbols([])
  }, [symbolsMode])

  // --- preview table (account → customer/tags) ---
  type PreviewRow = {
    accountId: string
    customerId: number | null
    cnName: string | null // backend name (pinyin)
    group: string | null
    regDate: string | null
    balance: number | null
    equity: number | null
    tags: string[]
  }

  // server-driven preview state
  const [serverRows, setServerRows] = React.useState<PreviewRow[]>([])
  const [serverTotal, setServerTotal] = React.useState<number>(0)
  const [isLoading, setIsLoading] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [tagExpanded, setTagExpanded] = React.useState<Record<string, boolean>>({})
  const toggleTagExpanded = React.useCallback((id: string) => {
    setTagExpanded((prev) => ({ ...prev, [id]: !prev[id] }))
  }, [])

  // dialog/drawer open states (controlled) to trigger loading
  const [dialogOpen, setDialogOpen] = React.useState(false)
  const [drawerOpen, setDrawerOpen] = React.useState(false)

  // API helpers (align with WarehouseProducts pattern)
  type AudiencePreviewResp = { total: number; items: any[] }
  const abortRef = React.useRef<AbortController | null>(null)

  async function postAudiencePreview(body: { rules: Rule[] }, signal?: AbortSignal) {
    const res = await fetch("/api/v1/audience/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json", accept: "application/json" },
      body: JSON.stringify(body),
      signal,
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return (await res.json()) as AudiencePreviewResp
  }

  const loadPreview = React.useCallback(async () => {
    if (abortRef.current) abortRef.current.abort()
    const ctl = new AbortController()
    abortRef.current = ctl
    setIsLoading(true)
    setError(null)
    try {
      const data = await postAudiencePreview({ rules }, ctl.signal)
      const items: PreviewRow[] = (data.items ?? []).map((it: any) => ({
        accountId: String(it.account_id),
        customerId: it.client_id ?? null,
        cnName: it.name ?? null,
        group: it.group ?? null,
        regDate: it.reg_date ?? null,
        balance: typeof it.balance === "number" ? it.balance : it.balance != null ? Number(it.balance) : null,
        equity: typeof it.equity === "number" ? it.equity : it.equity != null ? Number(it.equity) : null,
        tags: Array.isArray(it.tags) ? it.tags : [],
      }))
      setServerRows(items)
      setServerTotal(typeof (data as any).total === "number" ? (data as any).total : items.length)
    } catch (e: any) {
      if (e?.name !== "AbortError") setError(e?.message ?? "Failed to load")
      setServerRows([])
      setServerTotal(0)
    } finally {
      setIsLoading(false)
    }
  }, [rules])

  React.useEffect(() => {
    const isOpen = dialogOpen || drawerOpen
    if (isOpen) loadPreview()
  }, [dialogOpen, drawerOpen, loadPreview])
  // derive from server rows
  const derivedAccounts = React.useMemo(() => serverRows.map((r) => r.accountId), [serverRows])
  const previewRows: PreviewRow[] = serverRows

  const [previewSorting, setPreviewSorting] = React.useState<SortingState>([])
  const [previewRowSelection, setPreviewRowSelection] = React.useState<Record<string, boolean>>({})

  // 方案2：移除列表变化时的默认全选，保留用户选择；新增项默认不勾选

  const formatYmd = React.useCallback((s: string | null) => {
    if (!s) return "-"
    const part = String(s).split(" ")[0]
    const d = new Date(s as string)
    if (!Number.isNaN(d.getTime())) {
      const y = d.getFullYear()
      const m = String(d.getMonth() + 1).padStart(2, "0")
      const dd = String(d.getDate()).padStart(2, "0")
      return `${y}/${m}/${dd}`
    }
    return part
  }, [])

  const previewColumns: ColumnDef<PreviewRow>[] = React.useMemo(() => [
    {
      id: "select",
      header: ({ table }) => (
        <Checkbox
          checked={table.getIsAllPageRowsSelected() || (table.getIsSomePageRowsSelected() && "indeterminate")}
          onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
          aria-label="Select all"
        />
      ),
      cell: ({ row }) => (
        <Checkbox
          checked={row.getIsSelected()}
          onCheckedChange={(value) => row.toggleSelected(!!value)}
          aria-label="Select row"
        />
      ),
      enableSorting: false,
      enableHiding: false,
    },
    { accessorKey: "accountId", header: "Account ID" },
    {
      accessorKey: "customerId",
      header: "Client ID",
      cell: ({ row }) => <div>{row.original.customerId ?? "-"}</div>,
    },
    {
      accessorKey: "cnName",
      header: "中文姓名",
      cell: ({ row }) => <div>{row.original.cnName ?? "-"}</div>,
    },
    { accessorKey: "group", header: "Group", cell: ({ row }) => <div className="truncate max-w-[180px]">{row.original.group ?? "-"}</div> },
    {
      accessorKey: "regDate",
      header: "注册时间",
      cell: ({ row }) => <div>{formatYmd(row.original.regDate)}</div>,
    },
    {
      accessorKey: "balance",
      header: "Balance",
      cell: ({ row }) => {
        const v = row.original.balance
        return <div className="text-right">{v == null ? "-" : new Intl.NumberFormat().format(v)}</div>
      },
    },
    {
      accessorKey: "equity",
      header: "Equity",
      cell: ({ row }) => {
        const v = row.original.equity
        return <div className="text-right">{v == null ? "-" : new Intl.NumberFormat().format(v)}</div>
      },
    },
    {
      accessorKey: "tags",
      header: "Tags",
      cell: ({ row }) => {
        const id = row.original.accountId
        const t = row.original.tags
        if (!t.length) return <span className="text-muted-foreground">-</span>
        const expanded = !!tagExpanded[id]
        const show = expanded ? t : t.slice(0, 2)
        const remaining = Math.max(0, t.length - show.length)
        return (
          <div className="space-y-1">
            <div className={expanded ? "max-h-24 overflow-auto pr-1" : "flex flex-wrap gap-1"}>
              {show.map((x) => (
                <div key={x} className={expanded ? "text-xs" : undefined}>
                  {expanded ? <Badge variant="secondary" className="mr-1">{x}</Badge> : <Badge variant="secondary">{x}</Badge>}
                </div>
              ))}
            </div>
            {t.length > 2 && (
              <button className="text-xs text-muted-foreground hover:underline" onClick={() => toggleTagExpanded(id)}>
                {expanded ? "收起" : `展开${remaining > 0 ? `（+${remaining}）` : ""}`}
              </button>
            )}
          </div>
        )
      },
    },
  ], [formatYmd, tagExpanded, toggleTagExpanded])

  const previewTable = useReactTable({
    data: previewRows,
    columns: previewColumns,
    onSortingChange: setPreviewSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    onRowSelectionChange: setPreviewRowSelection,
    getRowId: (row) => row.accountId,
    state: { sorting: previewSorting, rowSelection: previewRowSelection },
  })

  // 在规则变化导致预览列表变化时：
  // 1) 保留已存在项的勾选状态
  // 2) 对于新增项，默认设为选中
  const prevIdsRef = React.useRef<Set<string>>(new Set())
  React.useEffect(() => {
    const currentIds = new Set(previewRows.map((r) => r.accountId))
    const prevIds = prevIdsRef.current
    const newIds: string[] = []
    for (const id of currentIds) if (!prevIds.has(id)) newIds.push(id)

    setPreviewRowSelection((prev) => {
      const next: Record<string, boolean> = {}
      // 保留仍存在的旧选择
      for (const id of currentIds) if (prev[id]) next[id] = prev[id]
      // 新增项默认选中
      for (const id of newIds) next[id] = true
      return next
    })

    prevIdsRef.current = currentIds
  }, [previewRows])

  // 应用到外层的“生效账户”集合：若未应用则使用 derivedAccounts
  const [appliedAccounts, setAppliedAccounts] = React.useState<string[] | null>(null)
  const effectiveAccounts = React.useMemo(
    () => (appliedAccounts && appliedAccounts.length ? appliedAccounts : derivedAccounts),
    [appliedAccounts, derivedAccounts]
  )
  // no sample details in summary per requirement


  // helpers to add rules
  function addCustomerIdRule() {
    const id = parseInt(inputCustomerId, 10)
    if (!id || Number.isNaN(id)) return
    setRules((prev) => [...prev, { type: "customer_ids", ids: [id], include: true }])
    setInputCustomerId("")
  }
  function addAccountIdRule() {
    const v = inputAccountId.trim()
    if (!v) return
    setRules((prev) => [...prev, { type: "account_ids", ids: [v], include: true }])
    setInputAccountId("")
  }
  // removed addIbRule
  function addTagRule() {
    const tags = tagSource === "local" ? selectedLocalTags : selectedCrmTags
    if (!tags.length) return
    setRules((prev) => [
      ...prev,
      { type: "customer_tags", source: tagSource, tags, operator: tagOperator, include: true },
    ])
    setSelectedLocalTags([])
    setSelectedCrmTags([])
  }
  function removeRuleAt(idx: number) {
    setRules((prev) => prev.filter((_, i) => i !== idx))
  }
  function clearRules() {
    setRules([])
  }

  function toggleSymbol(sym: string) {
    setSelectedSymbols((prev) => (prev.includes(sym) ? prev.filter((s) => s !== sym) : [...prev, sym]))
  }

  return (
    <div className="space-y-4 px-4 pb-6 lg:px-6">
      {/* 筛选卡片（与 Profit 风格一致） */}
      <Card>
        <CardHeader>
          <CardTitle className="text-2xl font-bold">筛选</CardTitle>
          
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm text-muted-foreground">选择对象：</span>
        {/* 对象（Responsive Dialog: desktop=Dialog, mobile=Drawer） */}
        <div className="block sm:hidden">
          <Drawer open={drawerOpen} onOpenChange={setDrawerOpen}>
            <DrawerTrigger asChild>
              <Button variant="outline" size="sm">选择对象</Button>
            </DrawerTrigger>
            <DrawerContent className="max-w-[100vw]">
              <DrawerHeader>
                <DrawerTitle>对象选择</DrawerTitle>
                <DrawerDescription>通过不同来源添加到对象池，确认后生效（静态演示）</DrawerDescription>
              </DrawerHeader>
              <div className="px-4 pb-4 space-y-4">
              {/* 规则类型选择 + 动态输入（紧凑） */}
              <div className="space-y-2">
                <div className="grid w-full grid-cols-[6rem_1fr_auto_auto] items-center gap-2">
                  <Label className="text-sm text-muted-foreground">类型</Label>
                  <Select value={ruleType} onValueChange={(v) => setRuleType(v as typeof ruleType)}>
                    <SelectTrigger className="w-48"><SelectValue placeholder="选择类型" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="customer_ids">客户ID</SelectItem>
                      <SelectItem value="account_ids">账户号</SelectItem>
                      <SelectItem value="customer_tags">客户Tag</SelectItem>
                    </SelectContent>
                  </Select>
                  <div />
                  <div />
                </div>

                {/* customer_ids */}
                {ruleType === "customer_ids" && (
                  <div className="grid w-full grid-cols-[6rem_1fr_auto_auto] items-center gap-2">
                    <Label htmlFor="cid_drawer" className="text-sm text-muted-foreground">客户ID</Label>
                    <Input id="cid_drawer" value={inputCustomerId} onChange={(e) => setInputCustomerId(e.target.value)} placeholder="如 1001" className="w-full" />
                    <div />
                    <Button variant="secondary" onClick={addCustomerIdRule}>加入对象池</Button>
                  </div>
                )}

                {/* account_ids */}
                {ruleType === "account_ids" && (
                  <div className="grid w-full grid-cols-[6rem_1fr_auto_auto] items-center gap-2">
                    <Label htmlFor="acc_drawer" className="text-sm text-muted-foreground">账户号</Label>
                    <Input id="acc_drawer" value={inputAccountId} onChange={(e) => setInputAccountId(e.target.value)} placeholder="如 A-1001" className="w-full" />
                    <div />
                    <Button variant="secondary" onClick={addAccountIdRule}>加入对象池</Button>
                  </div>
                )}

                {/* removed ib_id ui */}

                {/* customer_tags */}
                {ruleType === "customer_tags" && (
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="flex items-center gap-2">
                      <Label className="w-24">Tag来源</Label>
                      <Select value={tagSource} onValueChange={(v) => setTagSource(v as typeof tagSource)}>
                        <SelectTrigger className="w-28"><SelectValue placeholder="来源" /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="local">本地Tag</SelectItem>
                          <SelectItem value="crm">CRM Tag</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">逻辑</span>
                      <Select value={tagOperator} onValueChange={(v) => setTagOperator(v as typeof tagOperator)}>
                        <SelectTrigger className="w-28"><SelectValue placeholder="逻辑" /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="ANY">ANY(并集)</SelectItem>
                          <SelectItem value="ALL">ALL(交集)</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <Popover>
                      <PopoverTrigger asChild>
                        <Button variant="outline" size="sm">选择Tag</Button>
                      </PopoverTrigger>
                      <PopoverContent className="w-56 p-2">
                        <div className="grid gap-2">
                          {(tagSource === "local" ? sampleTagsLocal : sampleTagsCRM).map((t) => {
                            const selected = (tagSource === "local" ? selectedLocalTags : selectedCrmTags).includes(t)
                            return (
                              <label key={t} className="flex items-center gap-2">
                                <Checkbox
                                  checked={selected}
                                  onCheckedChange={(ck) => {
                                    const upd = (tagSource === "local" ? selectedLocalTags : selectedCrmTags)
                                    const setUpd = (tagSource === "local" ? setSelectedLocalTags : setSelectedCrmTags)
                                    if (ck) setUpd([...upd, t])
                                    else setUpd(upd.filter((x) => x !== t))
                                  }}
                                />
                                <span className="text-sm">{t}</span>
                              </label>
                            )
                          })}
                          <Button size="sm" onClick={addTagRule}>添加所选</Button>
                        </div>
                      </PopoverContent>
                    </Popover>
                  </div>
                )}
              </div>
              {/* 规则列表 */}
              <div>
                <div className="mb-2 text-sm font-medium">已选对象规则</div>
                <div className="flex flex-wrap gap-2">
                  {rules.map((r, idx) => (
                    <Badge key={idx} variant="secondary" className="flex items-center gap-2">
                      <span className="text-xs">
                        {r.type === "customer_ids" && `客户ID:${r.ids.join(',')}`}
                        {r.type === "account_ids" && `账户:${r.ids.join(',')}`}
                        {r.type === "customer_tags" && `${r.source} Tags:${r.tags.join(',')}(${r.operator})`}
                      </span>
                      <button onClick={() => removeRuleAt(idx)} className="text-muted-foreground hover:text-foreground">×</button>
                    </Badge>
                  ))}
                  {rules.length === 0 && <span className="text-xs text-muted-foreground">暂无规则</span>}
                </div>
                <div className="mt-2">
                  <Button variant="ghost" size="sm" onClick={clearRules}>清空对象池</Button>
                </div>
              </div>
              {/* 账户预览（表格，含复选，限定高度滚动） */}
              <div className="space-y-2">
                <div className="text-sm font-medium">命中账户 {serverTotal} 个（已加载 {previewRows.length}）</div>
                {isLoading && <div className="text-xs text-muted-foreground">加载中...</div>}
                {error && <div className="text-xs text-red-500">{error}</div>}
                <div className="overflow-hidden rounded-md border">
                  <div className="max-h-64 overflow-auto overflow-x-auto">
                    <Table className="min-w-[800px] [&_th]:border-r [&_td]:border-r [&_th]:border-b-2 [&_td]:border-muted-foreground/10">
                      <TableHeader>
                        {previewTable.getHeaderGroups().map((headerGroup) => (
                          <TableRow key={headerGroup.id}>
                            {headerGroup.headers.map((header) => (
                              <TableHead key={header.id}>
                                {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                              </TableHead>
                            ))}
                          </TableRow>
                        ))}
                      </TableHeader>
                      <TableBody>
                        {previewTable.getRowModel().rows?.length ? (
                          previewTable.getRowModel().rows.map((row) => (
                            <TableRow key={row.id} data-state={row.getIsSelected() && "selected"}>
                              {row.getVisibleCells().map((cell) => (
                                <TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>
                              ))}
                            </TableRow>
                          ))
                        ) : (
                          <TableRow>
                            <TableCell colSpan={previewColumns.length} className="h-24 text-center">
                              No results.
                            </TableCell>
                          </TableRow>
                        )}
                      </TableBody>
                    </Table>
                  </div>
                </div>
                <div className="flex items-center justify-between py-1">
                  <div className="text-muted-foreground text-xs">
                    已选 {previewTable.getFilteredSelectedRowModel().rows.length} / {previewTable.getFilteredRowModel().rows.length}
                  </div>
                  <div className="space-x-2">
                    <Button variant="outline" size="sm" onClick={() => previewTable.toggleAllPageRowsSelected(true)}>全选</Button>
                    <Button variant="outline" size="sm" onClick={() => previewTable.toggleAllPageRowsSelected(false)}>取消全选</Button>
                  </div>
                </div>
              </div>
              </div>
              <DrawerFooter>
                <DrawerClose asChild>
                  <Button onClick={() => setAppliedAccounts(previewTable.getSelectedRowModel().rows.map((r) => r.original.accountId))}>确认</Button>
                </DrawerClose>
                <DrawerClose asChild>
                  <Button variant="outline">取消</Button>
                </DrawerClose>
              </DrawerFooter>
            </DrawerContent>
          </Drawer>
            </div>
            <div className="hidden sm:block">
          <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm">选择对象</Button>
            </DialogTrigger>
            <DialogContent className="w-[95vw] sm:max-w-[1100px]">
              <DialogHeader>
                <DialogTitle>对象选择</DialogTitle>
                <DialogDescription>通过不同来源添加到对象池，确认后生效（静态演示）</DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                {/* 规则类型选择 + 动态输入（紧凑） */}
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <Label className="w-24">类型</Label>
                    <Select value={ruleType} onValueChange={(v) => setRuleType(v as typeof ruleType)}>
                      <SelectTrigger className="w-40"><SelectValue placeholder="选择类型" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="customer_ids">客户ID</SelectItem>
                        <SelectItem value="account_ids">账户号</SelectItem>
                        <SelectItem value="customer_tags">客户Tag</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {/* customer_ids */}
                  {ruleType === "customer_ids" && (
                    <div className="grid w-full grid-cols-[6rem_1fr_auto_auto] items-center gap-2">
                      <Label htmlFor="cid_dialog" className="text-sm text-muted-foreground">客户ID</Label>
                      <Input id="cid_dialog" value={inputCustomerId} onChange={(e) => setInputCustomerId(e.target.value)} placeholder="如 1001" className="w-full" />
                      <div />
                      <Button variant="secondary" onClick={addCustomerIdRule}>加入对象池</Button>
                    </div>
                  )}

                  {/* account_ids */}
                  {ruleType === "account_ids" && (
                    <div className="grid w-full grid-cols-[6rem_1fr_auto_auto] items-center gap-2">
                      <Label htmlFor="acc_dialog" className="text-sm text-muted-foreground">账户号</Label>
                      <Input id="acc_dialog" value={inputAccountId} onChange={(e) => setInputAccountId(e.target.value)} placeholder="如 A-1001" className="w-full" />
                      <div />
                      <Button variant="secondary" onClick={addAccountIdRule}>加入对象池</Button>
                    </div>
                  )}

                  {/* removed ib_id ui */}

                  {/* customer_tags */}
                  {ruleType === "customer_tags" && (
                    <div className="flex flex-wrap items-center gap-3">
                      <div className="flex items-center gap-2">
                        <Label className="w-24">Tag来源</Label>
                        <Select value={tagSource} onValueChange={(v) => setTagSource(v as typeof tagSource)}>
                          <SelectTrigger className="w-28"><SelectValue placeholder="来源" /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="local">本地Tag</SelectItem>
                            <SelectItem value="crm">CRM Tag</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">逻辑</span>
                        <Select value={tagOperator} onValueChange={(v) => setTagOperator(v as typeof tagOperator)}>
                          <SelectTrigger className="w-28"><SelectValue placeholder="逻辑" /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="ANY">ANY(并集)</SelectItem>
                            <SelectItem value="ALL">ALL(交集)</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <Popover>
                        <PopoverTrigger asChild>
                          <Button variant="outline" size="sm">选择Tag</Button>
                        </PopoverTrigger>
                        <PopoverContent className="w-56 p-2">
                          <div className="grid gap-2">
                            {(tagSource === "local" ? sampleTagsLocal : sampleTagsCRM).map((t) => {
                              const selected = (tagSource === "local" ? selectedLocalTags : selectedCrmTags).includes(t)
                              return (
                                <label key={t} className="flex items-center gap-2">
                                  <Checkbox
                                    checked={selected}
                                    onCheckedChange={(ck) => {
                                      const upd = (tagSource === "local" ? selectedLocalTags : selectedCrmTags)
                                      const setUpd = (tagSource === "local" ? setSelectedLocalTags : setSelectedCrmTags)
                                      if (ck) setUpd([...upd, t])
                                      else setUpd(upd.filter((x) => x !== t))
                                    }}
                                  />
                                  <span className="text-sm">{t}</span>
                                </label>
                              )
                            })}
                            <Button size="sm" onClick={addTagRule}>添加所选</Button>
                          </div>
                        </PopoverContent>
                      </Popover>
                    </div>
                  )}
                </div>
                {/* 规则列表 */}
                <div>
                  <div className="mb-2 text-sm font-medium">已选对象规则</div>
                  <div className="flex flex-wrap gap-2">
                    {rules.map((r, idx) => (
                      <Badge key={idx} variant="secondary" className="flex items-center gap-2">
                        <span className="text-xs">
                          {r.type === "customer_ids" && `客户ID:${r.ids.join(',')}`}
                          {r.type === "account_ids" && `账户:${r.ids.join(',')}`}
                          {r.type === "customer_tags" && `${r.source} Tags:${r.tags.join(',')}(${r.operator})`}
                          
                        </span>
                        <button onClick={() => removeRuleAt(idx)} className="text-muted-foreground hover:text-foreground">×</button>
                      </Badge>
                    ))}
                    {rules.length === 0 && <span className="text-xs text-muted-foreground">暂无规则</span>}
                  </div>
                </div>
              </div>
              {/* 账户预览（表格，含复选，限定高度滚动） */}
              <div className="space-y-2">
                <div className="text-sm font-medium">命中账户 {serverTotal} 个（已加载 {previewRows.length}）</div>
                {isLoading && <div className="text-xs text-muted-foreground">加载中...</div>}
                {error && <div className="text-xs text-red-500">{error}</div>}
                <div className="overflow-hidden rounded-md border">
                  <div className="max-h-64 overflow-auto overflow-x-auto">
                    <Table className="min-w-[1200px] [&_th]:border-r [&_td]:border-r [&_th]:border-b-2 [&_td]:border-muted-foreground/10">
                      <TableHeader>
                        {previewTable.getHeaderGroups().map((headerGroup) => (
                          <TableRow key={headerGroup.id}>
                            {headerGroup.headers.map((header) => (
                              <TableHead key={header.id}>
                                {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                              </TableHead>
                            ))}
                          </TableRow>
                        ))}
                      </TableHeader>
                      <TableBody>
                        {previewTable.getRowModel().rows?.length ? (
                          previewTable.getRowModel().rows.map((row) => (
                            <TableRow key={row.id} data-state={row.getIsSelected() && "selected"}>
                              {row.getVisibleCells().map((cell) => (
                                <TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>
                              ))}
                            </TableRow>
                          ))
                        ) : (
                          <TableRow>
                            <TableCell colSpan={previewColumns.length} className="h-24 text-center">
                              No results.
                            </TableCell>
                          </TableRow>
                        )}
                      </TableBody>
                    </Table>
                  </div>
                </div>
                <div className="flex items-center justify-between py-1">
                  <div className="text-muted-foreground text-xs">
                    已选 {previewTable.getFilteredSelectedRowModel().rows.length} / {previewTable.getFilteredRowModel().rows.length}
                  </div>
                  <div className="space-x-2">
                    <Button variant="outline" size="sm" onClick={() => previewTable.toggleAllPageRowsSelected(true)}>全选</Button>
                    <Button variant="outline" size="sm" onClick={() => previewTable.toggleAllPageRowsSelected(false)}>取消全选</Button>
                  </div>
                </div>
              </div>
              <DialogFooter className="pt-2">
                <DialogClose asChild>
                  <Button onClick={() => setAppliedAccounts(previewTable.getSelectedRowModel().rows.map((r) => r.original.accountId))}>确认</Button>
                </DialogClose>
                <DialogClose asChild>
                  <Button variant="outline">取消</Button>
                </DialogClose>
              </DialogFooter>
            </DialogContent>
          </Dialog>
            </div>

            {/* 时间（按钮显示范围 + 双日历弹层 + 快捷范围选择） */}
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">时间范围：</span>
              <Popover>
                <PopoverTrigger asChild>
                  <Button variant="outline" size="sm" className="justify-start gap-2 font-normal">
                    <span>{rangeLabel}</span>
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-2" align="start">
                  {/* fresh grad: two single calendars for start and end */}
                  <div className="flex items-start gap-3">
                    <div>
                      <div className="mb-1 text-xs text-muted-foreground">开始日期</div>
                      <Calendar
                        mode="single"
                        selected={startDate}
                        onSelect={(d) => {
                          if (!d) return
                          // auto-fix end if start > end
                          if (endDate && d > endDate) setEndDate(d)
                          setStartDate(d)
                          setQuickRange("custom")
                        }}
                        className="rounded-md border shadow-sm"
                        captionLayout="dropdown"
                        initialFocus
                      />
                    </div>
                    <div>
                      <div className="mb-1 text-xs text-muted-foreground">结束日期</div>
                      <Calendar
                        mode="single"
                        selected={endDate}
                        onSelect={(d) => {
                          if (!d) return
                          // auto-fix start if end < start
                          if (startDate && d < startDate) setStartDate(d)
                          setEndDate(d)
                          setQuickRange("custom")
                        }}
                        className="rounded-md border shadow-sm"
                        captionLayout="dropdown"
                      />
                    </div>
                  </div>
                </PopoverContent>
              </Popover>
              {/* 快捷范围：替代时区选择 */}
              <Select value={quickRange} onValueChange={(v) => applyQuickRange(v as typeof quickRange)}>
                <SelectTrigger className="w-36"><SelectValue placeholder="快捷范围" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="last_1w">最近 1 周</SelectItem>
                  <SelectItem value="last_1m">最近 1 个月</SelectItem>
                  <SelectItem value="last_3m">最近 3 个月</SelectItem>
                  <SelectItem value="all">全部历史</SelectItem>
                  <SelectItem value="custom">自定义</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* 交易品种 */}
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">交易品种：</span>
              <Select value={symbolsMode} onValueChange={(v) => setSymbolsMode(v as typeof symbolsMode)}>
                <SelectTrigger className="w-36"><SelectValue placeholder="选择方式" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全选（默认）</SelectItem>
                  <SelectItem value="custom">其他（自定义）</SelectItem>
                </SelectContent>
              </Select>
              {symbolsMode === "custom" && (
                <Popover>
                  <PopoverTrigger asChild>
                    <Button variant="outline" size="sm">选择品种</Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-56 p-2">
                    <div className="grid gap-2">
                      {/* fresh grad: show built-in + custom symbols */}
                      {combinedSymbols.map((s) => (
                        <label key={s} className="flex items-center gap-2">
                          <Checkbox checked={selectedSymbols.includes(s)} onCheckedChange={() => toggleSymbol(s)} />
                          <span className="text-sm">{s}</span>
                        </label>
                      ))}
                      {/* add custom symbol */}
                      <div className="flex items-center gap-2 pt-1">
                        <Input
                          placeholder="自定义品种 如 BTCUSD"
                          value={customSymbolInput}
                          onChange={(e) => setCustomSymbolInput(e.target.value.toUpperCase())}
                        />
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => {
                            const v = customSymbolInput.trim().toUpperCase()
                            if (!v) return
                            setCustomSymbols((prev) => (prev.includes(v) ? prev : [...prev, v]))
                            setSelectedSymbols((prev) => (prev.includes(v) ? prev : [...prev, v]))
                            setCustomSymbolInput("")
                          }}
                        >添加</Button>
                      </div>
                      <div className="flex items-center gap-2 pt-1">
                        <Button size="sm" variant="secondary" onClick={() => setSelectedSymbols(combinedSymbols)}>全选</Button>
                        <Button size="sm" variant="ghost" onClick={() => setSelectedSymbols([])}>清空</Button>
                      </div>
                    </div>
                  </PopoverContent>
                </Popover>
              )}
            </div>
          </div>
          {/* 简要提示（仅统计，不展示细节） */}
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span>选中账户 {effectiveAccounts.length} 个</span>
            <span>时间范围：{rangeLabel}</span>
            <span>选中 Symbol：{symbolsMode === "all" ? "全部" : (selectedSymbols.length > 0 ? `${selectedSymbols.length} 个` : "未选择")}</span>
          </div>
        </CardContent>
      </Card>

      {/* 已移除：底部预览卡片（整合进 Drawer） */}

      {/* 指标卡片区 */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3 lg:grid-cols-5 *:data-[slot=card]:bg-gradient-to-t *:data-[slot=card]:from-primary/5 *:data-[slot=card]:to-card *:data-[slot=card]:shadow-xs dark:*:data-[slot=card]:bg-card">
        {kpis.map((k) => (
          <Card key={k.label} className="@container/card">
            <CardHeader>
              <CardDescription>{k.label}</CardDescription>
              <CardTitle className={`text-2xl font-semibold tabular-nums @[250px]/card:text-3xl ${
                k.tone === "pos" ? "text-green-600" : k.tone === "neg" ? "text-red-600" : ""
              }`}>
                {k.trend}{
                  typeof k.value === "number" && k.unit === "%"
                    ? `${Math.round(k.value * 100) / 1}%`
                    : typeof k.value === "number" && k.unit === "USD"
                    ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(k.value)
                    : String(k.value)
                }
              </CardTitle>
            </CardHeader>
          </Card>
        ))}
      </div>

      {/* 第一行：资金曲线 + 回撤阴影图 + 订单表（右列） */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-1">
          <CardHeader>
            <CardTitle>资金曲线与回撤</CardTitle>
            <CardDescription>静态样例（日维度）</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={equityChartConfig} className="aspect-auto h-[300px] w-full">
              <LineChart data={equityData}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="t" tickLine={false} axisLine={false} tickMargin={8} minTickGap={24} />
                <YAxis yAxisId="left" tickFormatter={(v) => new Intl.NumberFormat().format(v)} />
                <YAxis yAxisId="right" orientation="right" tickFormatter={(v) => `${v}%`} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Line yAxisId="left" type="monotone" dataKey="equity" stroke="var(--color-equity)" dot={false} />
                <Area yAxisId="right" type="monotone" dataKey="drawdown" stroke="var(--color-drawdown)" fill="var(--color-drawdown)" />
              </LineChart>
            </ChartContainer>
          </CardContent>
        </Card>

        {/* 订单表（静态示例） */}
        <Card>
          <CardHeader>
            <CardTitle>订单表</CardTitle>
            <CardDescription>静态示例</CardDescription>
          </CardHeader>
          <CardContent>
            <OrdersTable />
          </CardContent>
        </Card>

        {/* 成本拆解（柱状模拟瀑布） */}
        <Card>
          <CardHeader>
            <CardTitle>成本拆解</CardTitle>
            <CardDescription>点差、佣金、隔夜利息、滑点</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={costChartConfig} className="aspect-auto h-[300px] w-full">
              <BarChart data={costData}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} />
                <YAxis tickFormatter={(v) => new Intl.NumberFormat().format(v)} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Bar dataKey="cost" fill="var(--color-cost)" />
              </BarChart>
            </ChartContainer>
          </CardContent>
        </Card>
      </div>

      {/* 第二行：品种占比饼图 + 多空占比条形图（静态） */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>交易品种占比</CardTitle>
            <CardDescription>静态样例</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={symbolConfig} className="aspect-auto h-[280px] w-full">
              <PieChart>
                <ChartLegend verticalAlign="top" content={<ChartLegendContent />} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Pie
                  data={symbolShare}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={50}
                  outerRadius={100}
                  strokeWidth={2}
                />
              </PieChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>多空方向占比</CardTitle>
            <CardDescription>静态样例</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={{ long: { label: "多" }, short: { label: "空" } }} className="aspect-auto h-[280px] w-full">
              <BarChart data={[{ k: "方向", long: 62, short: 38 }]}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="k" tickLine={false} axisLine={false} tickMargin={8} />
                <YAxis tickFormatter={(v) => `${v}%`} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Bar dataKey="long" stackId="a" fill="hsl(150 70% 45%)" />
                <Bar dataKey="short" stackId="a" fill="hsl(0 80% 60%)" />
              </BarChart>
            </ChartContainer>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}


