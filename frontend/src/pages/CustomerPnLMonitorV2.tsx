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
import { ColDef, ColGroupDef, GridReadyEvent, SortChangedEvent, GridApi } from 'ag-grid-community'
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

  // 新增：平仓总盈亏（buy+sell 合计）
  closed_total_profit?: number | string

  // 审计
  last_updated?: string | null
  
  // 夜间成交量占比（-1 表示不可计算，0-1 表示比例）
  overnight_volume_ratio?: number | string
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
  // 后端新增：ETL 水位时间（UTC+0）
  watermark_last_updated?: string | null
}

// 后端刷新响应接口（简化给前端）
interface EtlRefreshResponse {
  status: string
  message?: string | null
  server: string
  processed_rows: number
  duration_seconds: number
  new_max_deal_id?: number | null
  new_trades_count?: number | null
  floating_only_count?: number | null
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
  // 组别是否已初始化完毕（用于避免首次加载抖动）
  const [groupsReady, setGroupsReady] = useState(false)

  // 本地存储 key 生成（按服务器隔离）
  const storageKeyForGroups = useCallback((srv: string) => `pnl_user_groups:${srv}`, [])

  // 统一处理组别变更：更新状态并持久化
  const handleUserGroupsChange = useCallback((next: string[]) => {
    setUserGroups(next)
    try {
      localStorage.setItem(storageKeyForGroups(server), JSON.stringify(next))
    } catch {}
  }, [server, storageKeyForGroups])

  // 统一搜索：客户ID或客户名称（前端输入，后端检索）
  // fresh grad note: keep two states for debounce - immediate input and debounced value
  const [searchInput, setSearchInput] = useState("")
  const [searchDebounced, setSearchDebounced] = useState("")

  

  // data state and refresh
  const [rows, setRows] = useState<PnlSummaryRow[]>([])
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  
  const [error, setError] = useState<string | null>(null)
  const [productConfig, setProductConfig] = useState<ProductConfig | null>(null)
  // 刷新状态
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [refreshInfo, setRefreshInfo] = useState<string | null>(null)

  // 分页状态管理
  const [pageIndex, setPageIndex] = useState(0)
  const [pageSize, setPageSize] = useState(50)
  const [totalCount, setTotalCount] = useState(0)
  const [totalPages, setTotalPages] = useState(0)

  // AG Grid 状态管理
  const GRID_STATE_STORAGE_KEY = "pnl_v2_grid_state" // 统一 LocalStorage Key
  const gridStateStorageKey = useMemo(() => `${GRID_STATE_STORAGE_KEY}:${server}`, [server])
  // fresh grad note: no default sorting on first load
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

    total_commission: false,
    deposit_count: false,
    deposit_amount: false,
    withdrawal_count: false,
    withdrawal_amount: false,
    net_deposit: true,

    closed_total_profit: true,

    overnight_volume_ratio: true,
    overnight_volume_all: true,
    total_volume_all: true,
    overnight_order_all: true,
    total_order_all: true,

    last_updated: true,
  })
  const [gridApi, setGridApi] = useState<GridApi | null>(null)
  const gridContainerRef = useRef<HTMLDivElement | null>(null)
  

  // 持久化列宽/顺序/可见性/排序：统一保存到 localStorage
  const saveGridState = useCallback(() => {
    if (!gridApi) return
    try {
      const state = gridApi.getColumnState()
      localStorage.setItem(gridStateStorageKey, JSON.stringify(state))
    } catch {}
  }, [gridApi, gridStateStorageKey])

  // AG Grid 列定义
  const columnDefs = useMemo<Array<ColDef<PnlSummaryRow> | ColGroupDef<PnlSummaryRow>>>(() => [
    {
      field: "login",
      headerName: "账户ID",
      width: 120,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      cellRenderer: (params: any) => {
        // CRM 账户链接：MT5 -> 5-<login>；MT4Live2 -> 6-<login>；MT4Live 不可用
        const login = params.value
        if (server === "MT5") {
          return (
            <a 
              href={`https://mt4.kohleglobal.com/crm/accounts/5-${login}`}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 underline hover:no-underline transition-colors cursor-pointer"
              onClick={(e) => { e.stopPropagation() }}
            >
              {login}
            </a>
          )
        }
        if (server === "MT4Live2") {
          return (
            <a 
              href={`https://mt4.kohleglobal.com/crm/accounts/6-${login}`}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 underline hover:no-underline transition-colors cursor-pointer"
              onClick={(e) => { e.stopPropagation() }}
            >
              {login}
            </a>
          )
        }
        // MT4Live 或其他：纯文本
        return (
          <span className="font-medium">{login}</span>
        )
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
      width: 80,
      minWidth: 80,
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
      cellRenderer: (params: any) => {
        const value = params.value
        // Make ClientID clickable for MT5 & MT4Live2 to navigate CRM user profile
        if ((server === "MT5" || server === "MT4Live2") && value) {
          return (
            <a
              href={`https://mt4.kohleglobal.com/crm/users/${value}`}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 underline hover:no-underline transition-colors cursor-pointer"
              onClick={(e) => {
                e.stopPropagation()
              }}
            >
              {value}
            </a>
          )
        }
        return (
          <span className="text-muted-foreground">{value || ""}</span>
        )
      },
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
      field: "closed_total_profit",
      headerName: "平仓总盈亏",
      width: 150,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      // very light gray background for readability
      cellStyle: () => ({ backgroundColor: 'rgba(0,0,0,0.035)' }),
      cellRenderer: (params: any) => {
        const value = toNumber(params.value)
        return (
          <span className={`text-right ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}>
            {formatCurrency(value, productConfig || undefined)}
          </span>
        )
      },
      hide: !columnVisibility.closed_total_profit,
    },
    {
      headerName: "过夜",
      groupId: "overnight",
      marryChildren: true,
      children: [
        // 折叠时仅显示比例
        {
          field: "overnight_volume_ratio",
          headerName: "过夜成交量占比",
          width: 180,
          minWidth: 140,
          maxWidth: 240,
          sortable: true,
          filter: true,
          cellRenderer: (params: any) => {
            const raw = toNumber(params.value, -1)
            if (!Number.isFinite(raw) || raw < 0) {
              return (
                <span className="text-muted-foreground">-</span>
              )
            }
            const ratio = Math.max(0, Math.min(1, raw))
            const pct = (ratio * 100).toFixed(1) + '%'
            return (
              <span className="tabular-nums">{pct}</span>
            )
          },
          cellStyle: (params: any) => {
            const raw = toNumber(params.value, -1)
            if (!Number.isFinite(raw) || raw < 0) {
              return null
            }
            const ratio = Math.max(0, Math.min(1, raw))
            if (ratio < 0.2) {
              return { backgroundColor: 'rgba(16,185,129,0.15)', color: '#111' } as any
            }
            if (ratio < 0.5) {
              return { backgroundColor: 'rgba(245,158,11,0.18)', color: '#111' } as any
            }
            return { backgroundColor: 'rgba(239,68,68,0.18)', color: '#111' } as any
          },
          hide: !columnVisibility.overnight_volume_ratio,
        },
        // 展开后显示聚合 Volume
        {
          headerName: "手数",
          columnGroupShow: "open",
          children: [
            {
              colId: "overnight_volume_all",
              headerName: "过夜订单手数",
              width: 160,
              minWidth: 120,
              maxWidth: 220,
              sortable: true,
              filter: true,
              valueGetter: (p: any) => {
                const b = toNumber(p.data?.closed_buy_overnight_volume_lots)
                const s = toNumber(p.data?.closed_sell_overnight_volume_lots)
                return b + s
              },
              cellRenderer: (p: any) => (
                <span className="text-right tabular-nums">{toNumber(p.value).toLocaleString()}</span>
              ),
              hide: !columnVisibility.overnight_volume_all,
            },
            {
              colId: "total_volume_all",
              headerName: "总订单手数",
              width: 160,
              minWidth: 120,
              maxWidth: 220,
              sortable: true,
              filter: true,
              valueGetter: (p: any) => {
                const b = toNumber(p.data?.closed_buy_volume_lots)
                const s = toNumber(p.data?.closed_sell_volume_lots)
                return b + s
              },
              cellRenderer: (p: any) => (
                <span className="text-right tabular-nums">{toNumber(p.value).toLocaleString()}</span>
              ),
              hide: !columnVisibility.total_volume_all,
            },
          ],
        },
        // 展开后显示聚合 Orders
        {
          headerName: "订单",
          columnGroupShow: "open",
          children: [
            {
              colId: "overnight_order_all",
              headerName: "过夜订单数",
              width: 160,
              minWidth: 120,
              maxWidth: 220,
              sortable: true,
              filter: true,
              valueGetter: (p: any) => {
                const b = toNumber(p.data?.closed_buy_overnight_count)
                const s = toNumber(p.data?.closed_sell_overnight_count)
                return b + s
              },
              cellRenderer: (p: any) => (
                <span className="text-right tabular-nums">{toNumber(p.value).toLocaleString()}</span>
              ),
              hide: !columnVisibility.overnight_order_all,
            },
            {
              colId: "total_order_all",
              headerName: "总订单数",
              width: 160,
              minWidth: 120,
              maxWidth: 220,
              sortable: true,
              filter: true,
              valueGetter: (p: any) => {
                const b = toNumber(p.data?.closed_buy_count)
                const s = toNumber(p.data?.closed_sell_count)
                return b + s
              },
              cellRenderer: (p: any) => (
                <span className="text-right tabular-nums">{toNumber(p.value).toLocaleString()}</span>
              ),
              hide: !columnVisibility.total_order_all,
            },
          ],
        },
      ],
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
    setGridApi(params.api as any)

    try {
      const savedStateRaw = localStorage.getItem(gridStateStorageKey)
      if (savedStateRaw) {
        const savedState = JSON.parse(savedStateRaw)
        if (Array.isArray(savedState) && savedState.length > 0) {
          // 恢复列状态（顺序、宽度、可见性、排序）
          ;(params.api as any).applyColumnState({ state: savedState, applyOrder: true })

          // 从恢复的状态中同步 React state
          const visibilityFromState: Record<string, boolean> = {}
          const sortModelFromState: any[] = []
          
          savedState.forEach((s: any) => {
            if (s && typeof s.colId === 'string') {
              visibilityFromState[s.colId] = !s.hide
            }
            if (s.sort) {
              sortModelFromState.push({ colId: s.colId, sort: s.sort })
            }
          })

          if (Object.keys(visibilityFromState).length > 0) {
            setColumnVisibility(prev => ({ ...prev, ...visibilityFromState }))
          }
          if (sortModelFromState.length > 0) {
            setSortModel(sortModelFromState)
          } else {
            setSortModel([]) // 如果保存的状态里没有排序信息，则清空
          }
          return
        }
      }

      // 如果没有有效的已保存状态，则应用默认排序
      ;(params.api as any).applyColumnState({ state: sortModel, defaultState: { sort: null } })
    } catch (e) {
      console.error("Failed to restore grid state", e)
      // 如果恢复失败，也回退到应用默认排序
      try {
        ;(params.api as any).applyColumnState({ state: sortModel, defaultState: { sort: null } })
      } catch {}
    }
  }, [sortModel, gridStateStorageKey]) // sortModel 仅用于首次加载或无缓存时的默认排序

  // 当切换 server 时，尝试按 server 维度恢复列状态
  useEffect(() => {
    if (!gridApi) return
    try {
      const savedStateRaw = localStorage.getItem(gridStateStorageKey)
      if (savedStateRaw) {
        const savedState = JSON.parse(savedStateRaw)
        if (Array.isArray(savedState) && savedState.length > 0) {
          ;(gridApi as any).applyColumnState({ state: savedState, applyOrder: true })

          const visibilityFromState: Record<string, boolean> = {}
          const sortModelFromState: any[] = []
          savedState.forEach((s: any) => {
            if (s && typeof s.colId === 'string') {
              visibilityFromState[s.colId] = !s.hide
            }
            if (s.sort) {
              sortModelFromState.push({ colId: s.colId, sort: s.sort })
            }
          })
          if (Object.keys(visibilityFromState).length > 0) {
            setColumnVisibility(prev => ({ ...prev, ...visibilityFromState }))
          }
          setSortModel(sortModelFromState)
          return
        }
      }
      // 没有保存的状态则清空排序
      setSortModel([])
      ;(gridApi as any).applyColumnState({ state: [], defaultState: { sort: null } })
    } catch {}
  }, [gridApi, gridStateStorageKey])

  const onSortChanged = useCallback((event: SortChangedEvent) => {
    const newSortModel = (event.api as any).getColumnState()
      .filter((col: any) => col.sort)
      .map((col: any) => ({ colId: col.colId, sort: col.sort }))
    setSortModel(newSortModel)
    // fresh grad note: sorting should trigger a backend re-query from the first page
    setPageIndex(0)
    saveGridState()
  }, [saveGridState])

  // 获取用户组别列表
  const fetchUserGroups = useCallback(async () => {
    setGroupsReady(false)
    // 暂不接入 MT4Live：前端直接跳过
    if (server === "MT4Live") {
      // 立即清空数据，避免显示上一服务器的残留数据
      setRows([])
      setTotalCount(0)
      setTotalPages(0)
      setLastUpdated(null)
      setError(null)
      setAvailableGroups([])
      setGroupsReady(true)
      return
    }
    
    setIsLoadingGroups(true)
    try {
      const url = `/api/v1/etl/groups?server=${encodeURIComponent(server)}`
      const res = await fetchWithTimeout(url, { headers: { accept: "application/json" } }, 10000)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const groups = await res.json()
      
      // 转换为选项格式
      const groupOptions = groups.map((group: string) => ({
        value: group,
        label: group
      }))
      setAvailableGroups(groupOptions)

      // MT4Live2 初次加载：默认选择 __ALL__（该服务器无 KCM/AKCM 前缀）
      if (server === "MT4Live2") {
        try {
          const savedRaw = localStorage.getItem(storageKeyForGroups(server))
          if (savedRaw) {
            const saved = JSON.parse(savedRaw)
            if (Array.isArray(saved) && saved.length > 0) {
              const allowed = new Set(groupOptions.map((g: { value: string; label: string }) => g.value))
              const restored = saved.filter((v: string) => v === "__USER_NAME_TEST__" || v === "__EXCLUDE_USER_NAME_TEST__" || allowed.has(v as string))
              if (restored.length > 0) {
                handleUserGroupsChange(restored)
                setGroupsReady(true)
                return
              }
            }
          }
        } catch {}
        handleUserGroupsChange(["__ALL__"]) 
        setGroupsReady(true)
        return
      }

      // 1) 优先从本地恢复
      try {
        const savedRaw = localStorage.getItem(storageKeyForGroups(server))
        if (savedRaw) {
          const saved = JSON.parse(savedRaw)
          if (Array.isArray(saved)) {
            const allowed = new Set(groupOptions.map((g: { value: string; label: string }) => g.value))
            const restored = saved.filter((v: string) => v === "__USER_NAME_TEST__" || v === "__EXCLUDE_USER_NAME_TEST__" || allowed.has(v as string))
            if (restored.length > 0) {
              handleUserGroupsChange(restored)
              setGroupsReady(true)
              return
            }
          }
        }
      } catch {}

      // 2) 默认选择 KCM* 与 AKCM*
      const defaults = groupOptions
        .map((g: { value: string; label: string }) => g.value)
        .filter((v: string) => /^kcm/i.test(v) || /^akcm/i.test(v))
      const next = defaults.length > 0
        ? [...defaults, "__EXCLUDE_GROUP_NAME_TEST__", "__EXCLUDE_USER_NAME_TEST__"]
        : ["__ALL__"]
      handleUserGroupsChange(next)
      setGroupsReady(true)
    } catch (e) {
      console.error("获取用户组别失败:", e)
      setAvailableGroups([])
      setGroupsReady(true)
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

    // 切换为新的 ETL API（直查 PostgreSQL 的 pnl_user_summary）
    const url = `/api/v1/etl/pnl-user-summary/paginated?${params.toString()}`
    const res = await fetchWithTimeout(url, { headers: { accept: "application/json" } }, 20000)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const payload = (await res.json()) as PaginatedPnlSummaryResponse
    if (!payload?.ok) throw new Error(payload?.error || "加载失败")
    
    // 新接口不返回产品配置，显式清空以避免沿用旧值
    setProductConfig(null)
    // 使用后端返回的UTC时间，按UTC解析；渲染时用 Asia/Shanghai 显示
    if (payload.watermark_last_updated) {
      try {
        const raw = String(payload.watermark_last_updated)
        const iso = raw.endsWith('Z') ? raw : raw + 'Z'
        const dt = new Date(iso)
        setLastUpdated(Number.isNaN(dt.getTime()) ? null : dt)
      } catch {
        setLastUpdated(null)
      }
    } else {
      setLastUpdated(null)
    }
    
    // 设置分页信息
    setTotalCount(payload.total)
    setTotalPages(payload.total_pages)
    
    return Array.isArray(payload.data) ? payload.data : []
  }, [server, pageIndex, pageSize, sortModel, userGroups, searchDebounced])

  

  // 监听服务器变化，获取组别列表
  useEffect(() => {
    fetchUserGroups()
  }, [server, fetchUserGroups])

  // 监听分页、排序、服务器/品种变化，自动重新获取数据
  useEffect(() => {
    if (!groupsReady) return
    // 切换到 MT4Live 时立即清空，避免短暂显示旧服务器的数据
    if (server === "MT4Live") {
      setRows([])
      return
    }
    ;(async () => {
      try {
        setError(null)
        const data = await fetchData()
        setRows(data)
      } catch (e) {
        setRows([])
        setError(e instanceof Error ? e.message : "加载失败")
      }
    })()
  }, [pageIndex, pageSize, sortModel, server, userGroups, searchDebounced, groupsReady])

  

  

  // 手动刷新处理（MT5 / MT4Live2）
  const handleManualRefresh = useCallback(async () => {
    if (server !== "MT5" && server !== "MT4Live2") {
      setError("仅支持 MT5/MT4Live2 服务器刷新")
      return
    }
    setIsRefreshing(true)
    setError(null)
    try {
      const res = await fetchWithTimeout(`/api/v1/etl/pnl-user-summary/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json", accept: "application/json" },
        body: JSON.stringify({ server })
      }, 60000)
      if (!res.ok) {
        try {
          const err = await res.json()
          const msg = (err && (err.detail || err.message)) ? `HTTP ${res.status}: ${err.detail || err.message}` : `HTTP ${res.status}`
          throw new Error(msg)
        } catch {
          throw new Error(`HTTP ${res.status}`)
        }
      }
      const data = (await res.json()) as EtlRefreshResponse
      const parts: string[] = []
      if (typeof data.new_trades_count === 'number') parts.push(`处理${data.new_trades_count}新交易`)
      // MT4Live2：不显示“浮动盈亏更新条数”
      if (server !== 'MT4Live2' && typeof data.floating_only_count === 'number') {
        parts.push(`更新${data.floating_only_count}条浮动盈亏`)
      }
      if (typeof data.duration_seconds === 'number') parts.push(`耗时 ${Number(data.duration_seconds).toFixed(1)} 秒`)
      const msg = parts.length > 0 ? parts.join('，') : '刷新完成'
      setRefreshInfo(msg)

      const refreshed = await fetchData()
      setRows(refreshed)
    } catch (e) {
      setRefreshInfo(null)
      setRows([])
      setError(e instanceof Error ? e.message : "刷新失败")
    } finally {
      setIsRefreshing(false)
    }
  }, [server, fetchData])

  // 刷新提示自动清除（20秒）
  useEffect(() => {
    if (!refreshInfo) return
    const t = setTimeout(() => setRefreshInfo(null), 20000)
    return () => clearTimeout(t)
  }, [refreshInfo])

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
                  onValueChange={handleUserGroupsChange}
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
                    placeholder="账户ID，姓名，ClientID"
                    className="pl-8 h-9"
                  />
                </div>
              </div>

            </div>

            {/* actions */}
            <div className="flex flex-col sm:flex-row sm:items-center gap-2 w-full sm:w-auto">
              <Button onClick={handleManualRefresh} disabled={isRefreshing || (server !== 'MT5' && server !== 'MT4Live2')} className="h-9 w-full sm:w-auto">
                {isRefreshing ? '刷新中...' : '刷新'}
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

      {/* MT4Live 暂未接入提示（简洁文本，无额外装饰） */}
      {server === 'MT4Live' && (
        <div className="px-1 sm:px-0">
          <p className="text-sm">该服务器暂未接入</p>
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
                  数据更新时间（UTC+8）：{new Intl.DateTimeFormat('zh-CN', {
                    timeZone: 'Asia/Shanghai',
                    year: 'numeric', month: '2-digit', day: '2-digit',
                    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
                  }).format(lastUpdated)}
                </span>
              )}
              {refreshInfo && (
                <span className="px-2 py-1 bg-green-100 dark:bg-green-900/20 rounded text-green-700 dark:text-green-300">
                  {refreshInfo}
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
                      closed_total_profit: "平仓总盈亏",
                      overnight_volume_ratio: "overnight_volume_ratio",
                      overnight_volume_all: "过夜订单手数",
                      total_volume_all: "总订单手数",
                      overnight_order_all: "过夜订单数",
                      total_order_all: "总订单数",
                      last_updated: "更新时间",
                    }
                    return (
                      <DropdownMenuCheckboxItem
                        key={columnId}
                        checked={isVisible}
                        onSelect={(e) => { e.preventDefault() }}
                        onCheckedChange={(value: boolean) => 
                          {
                            // 同步到 Grid 并保存列状态（新版 API 使用 setColumnsVisible）
                            try { gridApi?.setColumnsVisible([columnId], !!value) } catch {}
                            setColumnVisibility(prev => ({ ...prev, [columnId]: !!value }))
                            saveGridState()
                          }
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
            headerHeight={32}
            groupHeaderHeight={36}
            defaultColDef={{
              sortable: true,
              filter: true,
              resizable: true,
              minWidth: 100,
            }}
            onGridReady={onGridReady}
            onSortChanged={onSortChanged}
            onColumnResized={(e: any) => { if (e.finished) saveGridState() }}
            onColumnMoved={() => saveGridState()}
            onColumnVisible={() => saveGridState()}
            onColumnPinned={() => saveGridState()}
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





