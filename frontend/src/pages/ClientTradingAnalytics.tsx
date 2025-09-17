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
import { type DateRange } from "react-day-picker"
// removed DateRange (range mode) in favor of dual single calendars
import {
  ColumnDef,
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  SortingState,
  useReactTable,
} from "@tanstack/react-table"
import { ArrowUpDown, ChevronDown, Search, Loader2 } from "lucide-react"
// DropdownMenu components removed as not used in current implementation
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
// Recharts components removed as not used in current implementation

// Page: 客户交易分析（静态原型）
// fresh grad: All data below are static mock data to show layout and style.

// Static data removed - now using dynamic analysis data from backend

export default function ClientTradingAnalyticsPage() {
  const [isDesktop, setIsDesktop] = React.useState(
    typeof window !== 'undefined' ? window.matchMedia("(min-width: 640px)").matches : true
  );

  React.useEffect(() => {
    if (typeof window === 'undefined') return;
    const mediaQuery = window.matchMedia("(min-width: 640px)");
    const handler = () => setIsDesktop(mediaQuery.matches);
    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, []);

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
  // removed: dedicated preview drawer state in compact Flex Wrap

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
  const [dateRange, setDateRange] = React.useState<DateRange | undefined>({
    from: (() => {
      const d = new Date();
      d.setMonth(d.getMonth() - 1);
      d.setDate(d.getDate() + 1);
      return d;
    })(),
    to: new Date(),
  })
  const [quickRange, setQuickRange] = React.useState<"last_1w" | "last_1m" | "last_3m" | "all" | "custom">("last_1m")

  // fresh grad: when quick range changes, compute start/end based on today
  const applyQuickRange = React.useCallback((qr: typeof quickRange) => {
    const today = new Date()
    // zero time for consistency
    const d0 = new Date(today.getFullYear(), today.getMonth(), today.getDate())
    if (qr === "all") {
      setDateRange({ from: undefined, to: undefined })
      setQuickRange(qr)
      return
    }
    if (qr === "last_1w") {
      const s = new Date(d0)
      s.setDate(s.getDate() - 6)
      setDateRange({ from: s, to: d0 })
    } else if (qr === "last_1m") {
      const s = new Date(d0)
      s.setMonth(s.getMonth() - 1)
      s.setDate(s.getDate() + 1) // approx: last 1 month inclusive
      setDateRange({ from: s, to: d0 })
    } else if (qr === "last_3m") {
      const s = new Date(d0)
      s.setMonth(s.getMonth() - 3)
      s.setDate(s.getDate() + 1)
      setDateRange({ from: s, to: d0 })
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
    if (!dateRange?.from || !dateRange?.to) return "选择日期范围"
    const opts: Intl.DateTimeFormatOptions = { month: "short", day: "2-digit", year: "numeric" }
    return `${dateRange.from.toLocaleDateString("en-US", opts)} - ${dateRange.to.toLocaleDateString("en-US", opts)}`
  }, [quickRange, dateRange])

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
  
  // analysis states
  const [isAnalyzing, setIsAnalyzing] = React.useState(false)
  const [analysisData, setAnalysisData] = React.useState<any>(null)
  const [analysisError, setAnalysisError] = React.useState<string | null>(null)
  
  // transaction history filter states
  const [transactionTypeFilter, setTransactionTypeFilter] = React.useState<string>("all")
  const [amountSort, setAmountSort] = React.useState<string>("desc")

  // fresh grad: backend response types for /api/v1/trading/analysis
  type TradingSummaryByAccount = {
    pnl_signed: number
    pnl_net_abs: number
    pnl_magnitude: number
    total_orders: number
    buy_orders: number
    sell_orders: number
    win_profit_sum: number
    loss_profit_sum: number
    loss_profit_abs_sum: number
    win_trade_count: number
    loss_trade_count: number
    win_buy_count: number
    win_sell_count: number
    loss_buy_count: number
    loss_sell_count: number
    swaps_sum: number
    buy_swaps_sum: number
    sell_swaps_sum: number
    deposit_count: number
    deposit_amount: number
    withdrawal_count: number
    withdrawal_amount: number
    cash_diff: number
  }

  type TradingTradeDetail = {
    login: string
    ticket: number
    symbol: string
    side: string
    lots: number
    open_time: string
    close_time: string
    open_price: number
    close_price: number
    profit: number
    swaps: number
  }

  type TradingCashDetail = {
    login: string
    ticket: number
    close_time: string
    amount_signed: number
    amount_abs: number
    cash_type: 'deposit' | 'withdrawal'
    comment?: string | null
  }

  type TradingAnalysisResponse = {
    summaryByAccount: Record<string, TradingSummaryByAccount>
    cashDetails: TradingCashDetail[]
    tradeDetails: TradingTradeDetail[]
    topWinners: TradingTradeDetail[]
    topLosers: TradingTradeDetail[]
  }

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

  // analysis API call
  async function postAnalysisRequest(body: {
    accounts: string[]
    startDate: Date | undefined
    endDate: Date | undefined
    symbols: string[] | null
  }, signal?: AbortSignal) {
    const res = await fetch("/api/v1/trading/analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json", accept: "application/json" },
      body: JSON.stringify({
        ...body,
        startDate: body.startDate ? new Date(body.startDate).toISOString().slice(0, 19).replace("T", " ") : undefined,
        endDate: body.endDate ? new Date(body.endDate).toISOString().slice(0, 19).replace("T", " ") : undefined,
        symbols: body.symbols && body.symbols.length ? body.symbols : null,
      }),
      signal,
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return (await res.json()) as TradingAnalysisResponse
  }

  const handleAnalyzeData = React.useCallback(async () => {
    if (effectiveAccounts.length === 0) return
    
    setIsAnalyzing(true)
    setAnalysisError(null)
    setAnalysisData(null)
    
    try {
      // 构建分析请求参数
      const analysisParams = {
        accounts: effectiveAccounts,
        startDate: quickRange === "all" ? undefined : dateRange?.from,
        endDate: quickRange === "all" ? undefined : dateRange?.to,
        symbols: symbolsMode === "all" ? null : selectedSymbols
      }
      
      console.log("开始分析，参数：", analysisParams)
      
      // 模拟API调用时间（实际开发中移除）
      await new Promise(resolve => setTimeout(resolve, 2000))
      
      const result = await postAnalysisRequest(analysisParams)
      setAnalysisData(result)
      
      console.log("分析完成，结果：", result)
      
    } catch (error: any) {
      if (error?.name !== "AbortError") {
        setAnalysisError(error?.message ?? "分析失败")
        console.error("分析错误：", error)
      }
    } finally {
      setIsAnalyzing(false)
    }
  }, [effectiveAccounts, quickRange, dateRange, symbolsMode, selectedSymbols])

  return (
    <div className="space-y-4 px-1 pb-6 sm:px-4 lg:px-6">
      {/* 筛选卡片（与 Profit 风格一致） */}
      <Card>
        <CardHeader>
          <CardTitle className="text-2xl font-bold">筛选</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex w-full flex-col items-start gap-4 sm:w-auto sm:flex-row sm:flex-wrap sm:items-center">
              <div className="flex w-full items-center gap-2 sm:w-auto">
                <span className="w-20 flex-shrink-0 text-sm text-muted-foreground whitespace-nowrap">选择对象：</span>
              {/* 对象（Responsive Dialog: desktop=Dialog, mobile=Drawer） */}
              <div className="block sm:hidden">
                <Drawer open={drawerOpen} onOpenChange={setDrawerOpen}>
                  <DrawerTrigger asChild>
                    <Button variant="outline" className="h-9 px-3 flex-1 sm:flex-none">选择对象</Button>
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
                  <div className="grid w-full max-w-[600px] grid-cols-[6rem_1fr_auto_auto] items-center gap-2">
                    <Label htmlFor="cid_drawer" className="text-sm text-muted-foreground">客户ID</Label>
                    <Input id="cid_drawer" value={inputCustomerId} onChange={(e) => setInputCustomerId(e.target.value)} placeholder="如 1001" className="w-full" />
                    <div />
                    <Button variant="secondary" onClick={addCustomerIdRule}>加入对象池</Button>
                  </div>
                )}

                {/* account_ids */}
                {ruleType === "account_ids" && (
                  <div className="grid w-full max-w-[600px] grid-cols-[6rem_1fr_auto_auto] items-center gap-2">
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
                <div className="overflow-hidden rounded-md border min-w-0">
                  <div className="max-w-[600px] max-h-64 overflow-x-scroll overflow-y-auto">
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
              </div>
              <div className="hidden sm:block">
                <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                  <DialogTrigger asChild>
                    <Button variant="outline" className="h-9 px-3 flex-1 sm:flex-none">选择对象</Button>
                  </DialogTrigger>
            <DialogContent className="w-[90vw] sm:max-w-[1100px] max-h-[90vh] flex flex-col">
              <DialogHeader className="flex-shrink-0">
                <DialogTitle>对象选择</DialogTitle>
                <DialogDescription>通过不同来源添加到对象池，确认后生效（静态演示）</DialogDescription>
              </DialogHeader>
              
              <div className="flex-1 overflow-y-auto space-y-4">
                {/* 规则配置区域 */}
                <div className="space-y-3">
                  <div className="flex items-center gap-3">
                    <Label className="text-sm font-medium min-w-[4rem]">类型</Label>
                    <Select value={ruleType} onValueChange={(v) => setRuleType(v as typeof ruleType)}>
                      <SelectTrigger className="w-40">
                        <SelectValue placeholder="选择类型" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="customer_ids">客户ID</SelectItem>
                        <SelectItem value="account_ids">账户号</SelectItem>
                        <SelectItem value="customer_tags">客户Tag</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {/* customer_ids */}
                  {ruleType === "customer_ids" && (
                    <div className="flex items-center gap-3">
                      <Label htmlFor="cid_dialog" className="text-sm min-w-[4rem]">客户ID</Label>
                      <Input 
                        id="cid_dialog" 
                        value={inputCustomerId} 
                        onChange={(e) => setInputCustomerId(e.target.value)} 
                        placeholder="如 1001" 
                        className="flex-1 max-w-xs" 
                      />
                      <Button variant="secondary" onClick={addCustomerIdRule}>加入对象池</Button>
                    </div>
                  )}

                  {/* account_ids */}
                  {ruleType === "account_ids" && (
                    <div className="flex items-center gap-3">
                      <Label htmlFor="acc_dialog" className="text-sm min-w-[4rem]">账户号</Label>
                      <Input 
                        id="acc_dialog" 
                        value={inputAccountId} 
                        onChange={(e) => setInputAccountId(e.target.value)} 
                        placeholder="如 A-1001" 
                        className="flex-1 max-w-xs" 
                      />
                      <Button variant="secondary" onClick={addAccountIdRule}>加入对象池</Button>
                    </div>
                  )}

                  {/* customer_tags */}
                  {ruleType === "customer_tags" && (
                    <div className="space-y-3">
                      <div className="flex items-center gap-3">
                        <Label className="text-sm min-w-[4rem]">Tag来源</Label>
                        <Select value={tagSource} onValueChange={(v) => setTagSource(v as typeof tagSource)}>
                          <SelectTrigger className="w-32">
                            <SelectValue placeholder="来源" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="local">本地Tag</SelectItem>
                            <SelectItem value="crm">CRM Tag</SelectItem>
                          </SelectContent>
                        </Select>
                        <Label className="text-sm">逻辑</Label>
                        <Select value={tagOperator} onValueChange={(v) => setTagOperator(v as typeof tagOperator)}>
                          <SelectTrigger className="w-32">
                            <SelectValue placeholder="逻辑" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="ANY">ANY(并集)</SelectItem>
                            <SelectItem value="ALL">ALL(交集)</SelectItem>
                          </SelectContent>
                        </Select>
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
                    </div>
                  )}
                </div>

                {/* 规则列表 */}
                <div className="space-y-2">
                  <div className="text-sm font-medium">已选对象规则</div>
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

                {/* 账户预览表格 */}
                <div className="space-y-2">
                  <div className="text-sm font-medium">命中账户 {serverTotal} 个（已加载 {previewRows.length}）</div>
                  {isLoading && <div className="text-xs text-muted-foreground">加载中...</div>}
                  {error && <div className="text-xs text-red-500">{error}</div>}
                  
                  <div className="border rounded-md overflow-hidden">
                    <div className="overflow-auto max-h-64">
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
              <DialogFooter className="pt-2 flex-row justify-end space-x-2">
                <DialogClose asChild>
                  <Button variant="outline">取消</Button>
                </DialogClose>
                <DialogClose asChild>
                  <Button onClick={() => setAppliedAccounts(previewTable.getSelectedRowModel().rows.map((r) => r.original.accountId))}>确认</Button>
                </DialogClose>
              </DialogFooter>
            </DialogContent>
                </Dialog>
              </div>

            {/* 时间（按钮显示范围 + 双日历弹层 + 快捷范围选择） */}
            <div className="flex w-full flex-nowrap items-center gap-2 sm:w-auto sm:flex-wrap">
              <span className="w-20 flex-shrink-0 text-sm text-muted-foreground whitespace-nowrap">时间范围：</span>
              <Popover>
                <PopoverTrigger asChild>
                  <Button variant="outline" className="h-9 px-3 justify-start gap-2 font-normal min-w-0 flex-1 sm:flex-none sm:min-w-[140px]">
                    <span>{rangeLabel}</span>
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-2" align="start">
                  {/* fresh grad: two single calendars for start and end */}
                  <Calendar
                    mode="range"
                    selected={dateRange}
                    onSelect={(r) => {
                      setDateRange(r)
                      setQuickRange("custom")
                    }}
                    numberOfMonths={isDesktop ? 2 : 1}
                    className="rounded-md border shadow-sm"
                    initialFocus
                  />
                </PopoverContent>
              </Popover>
              {/* 快捷范围：替代时区选择 */}
              <Select value={quickRange} onValueChange={(v) => applyQuickRange(v as typeof quickRange)}>
                <SelectTrigger className="h-9 min-w-0 flex-1 sm:w-36 sm:flex-none"><SelectValue placeholder="快捷范围" /></SelectTrigger>
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
            <div className="flex w-full flex-nowrap items-center gap-2 sm:w-auto sm:flex-wrap">
              <span className="w-20 flex-shrink-0 text-sm text-muted-foreground whitespace-nowrap">交易品种：</span>
              <Select value={symbolsMode} onValueChange={(v) => setSymbolsMode(v as typeof symbolsMode)}>
                <SelectTrigger className="h-9 min-w-0 flex-1 sm:w-36 sm:flex-none"><SelectValue placeholder="选择方式" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全选（默认）</SelectItem>
                  <SelectItem value="custom">其他（自定义）</SelectItem>
                </SelectContent>
              </Select>
              {symbolsMode === "custom" && (
                <Popover>
                  <PopoverTrigger asChild>
                    <Button variant="outline" className="h-9 px-3 min-w-0 flex-1 sm:flex-none">选择品种</Button>
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

            {/* 开始分析按钮 - 桌面端同行右对齐，移动端独立行居中 */}
            <div className="hidden sm:flex">
              <Button 
                onClick={handleAnalyzeData}
                disabled={isAnalyzing || effectiveAccounts.length === 0}
                className="h-9 gap-2"
              >
                {isAnalyzing ? (
                  <>
                    <Loader2 className="size-4 animate-spin" />
                    分析中...
                  </>
                ) : (
                  <>
                    <Search className="size-4" />
                    开始分析
                  </>
                )}
              </Button>
            </div>
          </div>

          {/* 移动端开始分析按钮 - 独立一行，居中对齐 */}
          <div className="sm:hidden">
            <Button 
              onClick={handleAnalyzeData}
              disabled={isAnalyzing || effectiveAccounts.length === 0}
              className="h-9 gap-2 w-full"
            >
              {isAnalyzing ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  分析中...
                </>
              ) : (
                <>
                  <Search className="size-4" />
                  开始分析
                </>
              )}
            </Button>
          </div>
          {/* 简要提示（仅统计，不展示细节） */}
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span>选中账户 {effectiveAccounts.length} 个</span>
            <span>时间范围：{rangeLabel}</span>
            <span>选中 Symbol：{symbolsMode === "all" ? "全部" : (selectedSymbols.length > 0 ? `${selectedSymbols.length} 个` : "未选择")}</span>
          </div>
        </CardContent>
      </Card>

      {/* 分析状态提示 */}
      {analysisError && (
        <Card className="border-destructive bg-destructive/5">
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 text-destructive">
              <span className="text-sm font-medium">分析失败</span>
              <span className="text-sm">{analysisError}</span>
            </div>
          </CardContent>
        </Card>
      )}

      {!analysisData && !isAnalyzing && !analysisError && (
        <Card className="border-muted bg-muted/5">
          <CardContent className="pt-6">
            <div className="text-center text-muted-foreground">
              <div className="text-sm">请选择筛选条件后点击"开始分析"按钮查看数据分析结果</div>
              <div className="text-xs mt-1">
                当前已选择 {effectiveAccounts.length} 个账户 · {rangeLabel} · 
                {symbolsMode === "all" ? "全部品种" : `${selectedSymbols.length} 个品种`}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* 数据分析结果区域 - 仅在有分析数据时显示 */}
      {(analysisData || isAnalyzing) && (
        <>
          {/* 分析概览 - 美化的卡片布局 */}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {/* Total P&L Card */}
            <Card className="relative overflow-hidden">
              <CardHeader className="pb-2">
                <CardDescription className="text-xs font-medium text-muted-foreground">
                  TOTAL P&L
                </CardDescription>
              </CardHeader>
              <CardContent className="pt-0">
                {isAnalyzing ? (
                  <div className="space-y-2">
                    <div className="h-8 bg-muted animate-pulse rounded" />
                    <div className="h-4 bg-muted animate-pulse rounded w-3/4" />
                  </div>
                ) : (
                  (() => {
                    const s = (analysisData?.summaryByAccount ?? {}) as Record<string, TradingSummaryByAccount>
                    const agg = Object.values(s).reduce(
                      (a, x) => ({
                        pnl: a.pnl + (x.pnl_signed ?? 0),
                        orders: a.orders + (x.total_orders ?? 0),
                        win: a.win + (x.win_trade_count ?? 0),
                        loss: a.loss + (x.loss_trade_count ?? 0),
                        winProfit: a.winProfit + (x.win_profit_sum ?? 0),
                        lossProfit: a.lossProfit + (x.loss_profit_abs_sum ?? 0),
                        buyWin: a.buyWin + (x.win_buy_count ?? 0),
                        sellWin: a.sellWin + (x.win_sell_count ?? 0),
                        buyOrders: a.buyOrders + (x.buy_orders ?? 0),
                        sellOrders: a.sellOrders + (x.sell_orders ?? 0),
                        deposits: a.deposits + (x.deposit_amount ?? 0),
                        depositCount: a.depositCount + (x.deposit_count ?? 0),
                        withdrawals: a.withdrawals + (x.withdrawal_amount ?? 0),
                        withdrawalCount: a.withdrawalCount + (x.withdrawal_count ?? 0),
                        cashdiff: a.cashdiff + (x.cash_diff ?? 0),
                      }),
                      { 
                        pnl: 0, orders: 0, win: 0, loss: 0, winProfit: 0, lossProfit: 0,
                        buyWin: 0, sellWin: 0, buyOrders: 0, sellOrders: 0,
                        deposits: 0, depositCount: 0, withdrawals: 0, withdrawalCount: 0, cashdiff: 0
                      }
                    )
                    return (
                      <div className="space-y-2">
                        <div className={`text-2xl font-bold tabular-nums ${agg.pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                          {new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(agg.pnl)}
                        </div>
                        <div className="text-sm text-muted-foreground">
                          Net: {new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(agg.pnl)}
                        </div>
                      </div>
                    )
                  })()
                )}
              </CardContent>
            </Card>

            {/* Win Rate Card */}
            <Card className="relative overflow-hidden">
              <CardHeader className="pb-2">
                <CardDescription className="text-xs font-medium text-muted-foreground">
                  WIN RATE DETAILS
                </CardDescription>
              </CardHeader>
              <CardContent className="pt-0">
                {isAnalyzing ? (
                  <div className="space-y-2">
                    <div className="h-8 bg-muted animate-pulse rounded" />
                    <div className="space-y-1">
                      <div className="h-3 bg-muted animate-pulse rounded" />
                      <div className="h-3 bg-muted animate-pulse rounded w-4/5" />
                      <div className="h-3 bg-muted animate-pulse rounded w-3/5" />
                    </div>
                  </div>
                ) : (
                  (() => {
                    const s = (analysisData?.summaryByAccount ?? {}) as Record<string, TradingSummaryByAccount>
                    const agg = Object.values(s).reduce(
                      (a, x) => ({
                        pnl: a.pnl + (x.pnl_signed ?? 0),
                        orders: a.orders + (x.total_orders ?? 0),
                        win: a.win + (x.win_trade_count ?? 0),
                        loss: a.loss + (x.loss_trade_count ?? 0),
                        buyWin: a.buyWin + (x.win_buy_count ?? 0),
                        sellWin: a.sellWin + (x.win_sell_count ?? 0),
                        buyOrders: a.buyOrders + (x.buy_orders ?? 0),
                        sellOrders: a.sellOrders + (x.sell_orders ?? 0),
                      }),
                      { 
                        pnl: 0, orders: 0, win: 0, loss: 0,
                        buyWin: 0, sellWin: 0, buyOrders: 0, sellOrders: 0,
                      }
                    )
                    const winRate = agg.orders > 0 ? (agg.win / agg.orders) * 100 : 0
                    const buyWinRate = agg.buyOrders > 0 ? (agg.buyWin / agg.buyOrders) * 100 : 0
                    const sellWinRate = agg.sellOrders > 0 ? (agg.sellWin / agg.sellOrders) * 100 : 0
                    return (
                      <div className="space-y-2">
                        <div className="text-2xl font-bold tabular-nums text-blue-600">
                          {winRate.toFixed(1)}%
                        </div>
                        <div className="space-y-1 text-xs text-muted-foreground">
                          <div>Buy Win Rate: <span className="text-green-600 font-medium">{buyWinRate.toFixed(1)}%</span></div>
                          <div>Sell Win Rate: <span className="text-red-600 font-medium">{sellWinRate.toFixed(1)}%</span></div>
                          <div>Total: <span className="font-medium">{agg.win}/{agg.orders}</span></div>
                        </div>
                      </div>
                    )
                  })()
                )}
              </CardContent>
            </Card>

            {/* Cash Flow Card */}
            <Card className="relative overflow-hidden md:col-span-2 lg:col-span-1">
              <CardHeader className="pb-2">
                <CardDescription className="text-xs font-medium text-muted-foreground">
                  CASH FLOW
                </CardDescription>
              </CardHeader>
              <CardContent className="pt-0">
                {isAnalyzing ? (
                  <div className="space-y-2">
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-1">
                        <div className="h-4 bg-muted animate-pulse rounded" />
                        <div className="h-3 bg-muted animate-pulse rounded w-3/4" />
                      </div>
                      <div className="space-y-1">
                        <div className="h-4 bg-muted animate-pulse rounded" />
                        <div className="h-3 bg-muted animate-pulse rounded w-3/4" />
                      </div>
                    </div>
                    <div className="h-6 bg-muted animate-pulse rounded" />
                  </div>
                ) : (
                  (() => {
                    const s = (analysisData?.summaryByAccount ?? {}) as Record<string, TradingSummaryByAccount>
                    const agg = Object.values(s).reduce(
                      (a, x) => ({
                        deposits: a.deposits + (x.deposit_amount ?? 0),
                        depositCount: a.depositCount + (x.deposit_count ?? 0),
                        withdrawals: a.withdrawals + Math.abs(x.withdrawal_amount ?? 0),
                        withdrawalCount: a.withdrawalCount + (x.withdrawal_count ?? 0),
                        cashdiff: a.cashdiff + (x.cash_diff ?? 0),
                      }),
                      { 
                        deposits: 0, depositCount: 0, withdrawals: 0, withdrawalCount: 0, cashdiff: 0
                      }
                    )
                    return (
                      <div className="space-y-3">
                        <div className="grid grid-cols-2 gap-4">
                          <div className="space-y-1">
                            <div className="font-medium text-green-600 tabular-nums">
                              {new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(agg.deposits)}
                            </div>
                            <div className="text-xs text-muted-foreground">
                              Deposits ({agg.depositCount} transactions)
                            </div>
                          </div>
                          <div className="space-y-1">
                            <div className="font-medium text-red-600 tabular-nums">
                              {new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(agg.withdrawals)}
                            </div>
                            <div className="text-xs text-muted-foreground">
                              Withdrawals ({agg.withdrawalCount} transactions)
                            </div>
                          </div>
                        </div>
                        <div className="pt-2 border-t">
                          <div className={`font-bold tabular-nums ${agg.cashdiff >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                            {new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(agg.cashdiff)}
                          </div>
                          <div className="text-xs text-muted-foreground">Net cash flow</div>
                        </div>
                      </div>
                    )
                  })()
                )}
              </CardContent>
            </Card>
          </div>

          {/* Top 10 Winners & Losers - 响应式布局 */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {/* Top 10 Winning Trades */}
            <Card className="relative">
              <CardHeader className="pb-3">
                <CardTitle className="text-lg font-semibold text-green-600">Top 10 Winning Trades</CardTitle>
                <CardDescription>最佳盈利交易排行</CardDescription>
              </CardHeader>
              <CardContent className="p-0">
                <div className="overflow-hidden rounded-md border-0">
                  <div className="max-h-64 overflow-y-auto scrollbar-thin scrollbar-thumb-gray-300 scrollbar-track-gray-100">
                    <Table>
                      <TableHeader className="sticky top-0 bg-background">
                        <TableRow className="border-b">
                          <TableHead className="text-xs font-medium">Login</TableHead>
                          <TableHead className="text-xs font-medium">Ticket</TableHead>
                          <TableHead className="text-xs font-medium">Symbol</TableHead>
                          <TableHead className="text-xs font-medium">Side</TableHead>
                          <TableHead className="text-right text-xs font-medium">Profit</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {isAnalyzing ? (
                          Array.from({ length: 4 }).map((_, i) => (
                            <TableRow key={i}>
                              <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                              <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                              <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                              <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                              <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                            </TableRow>
                          ))
                        ) : (
                          ((analysisData?.topWinners ?? []) as TradingTradeDetail[]).slice(0, 10).map((r, index) => (
                            <TableRow key={`w-${r.login}-${r.ticket}`} className={index < 4 ? "bg-green-50/50" : ""}>
                              <TableCell className="text-xs font-mono">{r.login}</TableCell>
                              <TableCell className="text-xs font-mono">{r.ticket}</TableCell>
                              <TableCell className="text-xs font-semibold">{r.symbol}</TableCell>
                              <TableCell className={`text-xs font-medium ${r.side === 'buy' ? 'text-green-600' : 'text-red-600'}`}>
                                {r.side.toUpperCase()}
                              </TableCell>
                              <TableCell className="text-right text-xs font-bold text-green-600 tabular-nums">
                                ${r.profit.toFixed(2)}
                              </TableCell>
                            </TableRow>
                          ))
                        )}
                        {(!isAnalyzing && (!analysisData || (analysisData?.topWinners ?? []).length === 0)) && (
                          <TableRow>
                            <TableCell colSpan={5} className="text-center text-muted-foreground py-8">
                              暂无数据
                            </TableCell>
                          </TableRow>
                        )}
                      </TableBody>
                    </Table>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Top 10 Losing Trades */}
            <Card className="relative">
              <CardHeader className="pb-3">
                <CardTitle className="text-lg font-semibold text-red-600">Top 10 Losing Trades</CardTitle>
                <CardDescription>最大亏损交易排行</CardDescription>
              </CardHeader>
              <CardContent className="p-0">
                <div className="overflow-hidden rounded-md border-0">
                  <div className="max-h-64 overflow-y-auto scrollbar-thin scrollbar-thumb-gray-300 scrollbar-track-gray-100">
                    <Table>
                      <TableHeader className="sticky top-0 bg-background">
                        <TableRow className="border-b">
                          <TableHead className="text-xs font-medium">Login</TableHead>
                          <TableHead className="text-xs font-medium">Ticket</TableHead>
                          <TableHead className="text-xs font-medium">Symbol</TableHead>
                          <TableHead className="text-xs font-medium">Side</TableHead>
                          <TableHead className="text-right text-xs font-medium">Profit</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {isAnalyzing ? (
                          Array.from({ length: 4 }).map((_, i) => (
                            <TableRow key={i}>
                              <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                              <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                              <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                              <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                              <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                            </TableRow>
                          ))
                        ) : (
                          ((analysisData?.topLosers ?? []) as TradingTradeDetail[]).slice(0, 10).map((r, index) => (
                            <TableRow key={`l-${r.login}-${r.ticket}`} className={index < 4 ? "bg-red-50/50" : ""}>
                              <TableCell className="text-xs font-mono">{r.login}</TableCell>
                              <TableCell className="text-xs font-mono">{r.ticket}</TableCell>
                              <TableCell className="text-xs font-semibold">{r.symbol}</TableCell>
                              <TableCell className={`text-xs font-medium ${r.side === 'buy' ? 'text-green-600' : 'text-red-600'}`}>
                                {r.side.toUpperCase()}
                              </TableCell>
                              <TableCell className="text-right text-xs font-bold text-red-600 tabular-nums">
                                ${r.profit.toFixed(2)}
                              </TableCell>
                            </TableRow>
                          ))
                        )}
                        {(!isAnalyzing && (!analysisData || (analysisData?.topLosers ?? []).length === 0)) && (
                          <TableRow>
                            <TableCell colSpan={5} className="text-center text-muted-foreground py-8">
                              暂无数据
                            </TableCell>
                          </TableRow>
                        )}
                      </TableBody>
                    </Table>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Transaction History */}
          <Card>
            <CardHeader>
              <CardTitle className="text-lg font-semibold">Transaction History</CardTitle>
              <CardDescription>完整的资金流水记录</CardDescription>
            </CardHeader>
            <CardContent>
              {/* 筛选控件 */}
              <div className="flex flex-wrap items-center gap-3 mb-4">
                <div className="flex items-center gap-2">
                  <Label className="text-sm font-medium">Transaction Type:</Label>
                  <Select 
                    value={transactionTypeFilter} 
                    onValueChange={setTransactionTypeFilter}
                  >
                    <SelectTrigger className="w-32 h-8">
                      <SelectValue placeholder="All Types" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All Types</SelectItem>
                      <SelectItem value="deposit">Deposit</SelectItem>
                      <SelectItem value="withdrawal">Withdrawal</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex items-center gap-2">
                  <Label className="text-sm font-medium">Sort by Amount:</Label>
                  <Select 
                    value={amountSort} 
                    onValueChange={setAmountSort}
                  >
                    <SelectTrigger className="w-32 h-8">
                      <SelectValue placeholder="Desc" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="desc">High to Low</SelectItem>
                      <SelectItem value="asc">Low to High</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              {/* 交易历史表格 */}
              <div className="overflow-hidden rounded-md border">
                <div className="max-h-96 overflow-y-auto scrollbar-thin scrollbar-thumb-gray-300 scrollbar-track-gray-100">
                  <Table>
                    <TableHeader className="sticky top-0 bg-background">
                      <TableRow>
                        <TableHead className="font-semibold">Login</TableHead>
                        <TableHead className="font-semibold">Ticket</TableHead>
                        <TableHead className="font-semibold">Date & Time</TableHead>
                        <TableHead className="text-right font-semibold">
                          Amount 
                          <Button variant="ghost" size="sm" className="ml-1 h-auto p-1">
                            <ArrowUpDown className="h-3 w-3" />
                          </Button>
                        </TableHead>
                        <TableHead className="font-semibold">
                          Type
                          <Button variant="ghost" size="sm" className="ml-1 h-auto p-1">
                            <ChevronDown className="h-3 w-3" />
                          </Button>
                        </TableHead>
                        <TableHead className="font-semibold">Comment</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {isAnalyzing ? (
                        Array.from({ length: 8 }).map((_, i) => (
                          <TableRow key={i}>
                            <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                            <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                            <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                            <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                            <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                            <TableCell><div className="h-4 bg-muted animate-pulse rounded" /></TableCell>
                          </TableRow>
                        ))
                      ) : (
                        (() => {
                          // 应用筛选和排序逻辑
                          let filteredData = (analysisData?.cashDetails ?? []) as TradingCashDetail[]
                          
                          // 按交易类型筛选
                          if (transactionTypeFilter !== "all") {
                            filteredData = filteredData.filter(r => r.cash_type === transactionTypeFilter)
                          }
                          
                          // 按金额排序
                          filteredData = filteredData.sort((a, b) => {
                            const amountA = Math.abs(a.amount_signed)
                            const amountB = Math.abs(b.amount_signed)
                            return amountSort === "desc" ? amountB - amountA : amountA - amountB
                          })
                          
                          return filteredData.map((r) => (
                          <TableRow key={`${r.login}-${r.ticket}`} className="hover:bg-muted/50">
                            <TableCell className="font-mono text-sm">{r.login}</TableCell>
                            <TableCell className="font-mono text-sm">{r.ticket}</TableCell>
                            <TableCell className="tabular-nums text-sm">{r.close_time}</TableCell>
                            <TableCell className={`text-right font-bold tabular-nums ${
                              r.cash_type === 'deposit' ? 'text-green-600' : 'text-red-600'
                            }`}>
                              {r.cash_type === 'deposit' ? '+' : '-'}
                              ${Math.abs(r.amount_signed).toFixed(2)}
                            </TableCell>
                            <TableCell>
                              <Badge 
                                variant={r.cash_type === 'deposit' ? 'default' : 'destructive'}
                                className="text-xs"
                              >
                                {r.cash_type === 'deposit' ? 'DEPOSIT' : 'WITHDRAWAL'}
                              </Badge>
                            </TableCell>
                            <TableCell className="truncate max-w-[280px] text-sm text-muted-foreground">
                              {r.comment || '-'}
                            </TableCell>
                          </TableRow>
                          ))
                        })()
                      )}
                      {(!isAnalyzing && (!analysisData || (analysisData?.cashDetails ?? []).length === 0)) && (
                        <TableRow>
                          <TableCell colSpan={6} className="text-center text-muted-foreground py-12">
                            暂无交易记录
                          </TableCell>
                        </TableRow>
                      )}
                    </TableBody>
                  </Table>
                </div>
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}


