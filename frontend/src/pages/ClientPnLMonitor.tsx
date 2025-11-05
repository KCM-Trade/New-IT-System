import { useState, useCallback, useMemo, useRef, useEffect } from "react"
import { useTheme } from "@/components/theme-provider"
import { useI18n } from "@/components/i18n-provider"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Card, CardContent } from "@/components/ui/card"
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Badge } from "@/components/ui/badge"
import { Settings2, Filter, X, Search, RefreshCw } from "lucide-react"
import { AgGridReact } from 'ag-grid-react'
import { ColDef, GridReadyEvent, SortChangedEvent, GridApi, PostSortRowsParams, IRowNode } from 'ag-grid-community'
import { FilterBuilder } from "@/components/FilterBuilder"
import { FilterGroup } from "@/types/filter"
import { getColumnMeta } from "@/config/filterColumns"
import { OPERATOR_LABELS } from "@/types/filter"

// ClientID 汇总数据接口
interface ClientPnLSummaryRow {
  // 主键
  client_id: number | string
  
  // 客户基本信息
  client_name?: string | null
  primary_server?: string | null
  countries?: string[] | null
  currencies?: string[] | null
  
  // 账户统计
  account_count: number
  account_list?: number[] | null
  
  // 聚合金额（统一美元）
  total_balance_usd: number | string
  total_credit_usd: number | string
  total_floating_pnl_usd: number | string
  total_equity_usd: number | string
  
  // 平仓盈亏
  total_closed_profit_usd: number | string
  total_commission_usd: number | string
  
  // 资金流动
  total_deposit_usd: number | string
  total_withdrawal_usd: number | string
  net_deposit_usd: number | string
  
  // 聚合手数
  total_volume_lots: number | string
  total_overnight_volume_lots: number | string
  overnight_volume_ratio?: number | string
  
  // 聚合订单数
  total_closed_count: number
  total_overnight_count: number
  
  // 更新时间
  last_updated?: string | null
  
  // 账户明细（一次性加载）
  accounts?: ClientAccountDetail[]
}

// 账户明细接口
interface ClientAccountDetail {
  client_id: number
  login: number
  server: string
  currency?: string | null
  user_name?: string | null
  user_group?: string | null
  country?: string | null
  balance_usd: number | string
  credit_usd: number | string
  floating_pnl_usd: number | string
  equity_usd: number | string
  closed_profit_usd: number | string
  commission_usd: number | string
  deposit_usd: number | string
  withdrawal_usd: number | string
  volume_lots: number | string
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

// fresh grad note: keep CRM link helpers isolated for reuse
function getClientCrmLink(clientId: number | string | null | undefined) {
  if (clientId === null || clientId === undefined || clientId === "") return null
  return `https://mt4.kohleglobal.com/crm/users/${clientId}`
}

function getAccountCrmLink(server: string | null | undefined, login: number | string | null | undefined) {
  if (login === null || login === undefined || login === "") return null
  const loginStr = String(login)
  const serverKey = (server || "").toUpperCase()
  if (serverKey === "MT5") return `https://mt4.kohleglobal.com/crm/accounts/5-${loginStr}`
  if (serverKey === "MT4LIVE2") return `https://mt4.kohleglobal.com/crm/accounts/6-${loginStr}`
  return null
}

export default function ClientPnLMonitor() {
  const { theme } = useTheme()
  const { t } = useI18n()
  
  // 数据状态
  const [rows, setRows] = useState<ClientPnLSummaryRow[]>([])
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [loading, setLoading] = useState(false)
  
  // 分页状态
  const [pageIndex, setPageIndex] = useState(0)
  const [pageSize, setPageSize] = useState(50)
  const [totalCount, setTotalCount] = useState(0)
  const [totalPages, setTotalPages] = useState(0)
  
  // 搜索状态
  const [searchValue, setSearchValue] = useState("")
  const [searchInput, setSearchInput] = useState("")
  
  // 筛选状态
  const [filterBuilderOpen, setFilterBuilderOpen] = useState(false)
  const [appliedFilters, setAppliedFilters] = useState<FilterGroup | null>(null)
  
  // 展开/收起状态：记录哪些 client_id 被展开
  const [expandedClients, setExpandedClients] = useState<Set<number | string>>(new Set())
  // Track per-client detail loading to avoid duplicate fetches
  const [accountLoadingMap, setAccountLoadingMap] = useState<Record<string, boolean>>({})
  
  // AG Grid 状态
  const [sortModel, setSortModel] = useState<any[]>([
    { colId: 'total_closed_profit_usd', sort: 'desc' }
  ])
  const [columnVisibility, setColumnVisibility] = useState<Record<string, boolean>>({
    client_id: true,
    client_name: true,
    primary_server: false,
    account_count: true,
    total_balance_usd: true,
    total_floating_pnl_usd: true,
    total_equity_usd: false,
    total_closed_profit_usd: true,
    total_commission_usd: false,
    total_deposit_usd: false,
    total_withdrawal_usd: false,
    net_deposit_usd: true,
    total_volume_lots: true,
    overnight_volume_ratio: true,
    total_closed_count: false,
    last_updated: true,
  })
  const [gridApi, setGridApi] = useState<GridApi | null>(null)
  const gridContainerRef = useRef<HTMLDivElement | null>(null)

  // 记录主行，便于在排序时获取父级值
  const parentRowMap = useMemo(() => {
    const map = new Map<number | string, ClientPnLSummaryRow>()
    rows.forEach(row => {
      map.set(row.client_id, row)
    })
    return map
  }, [rows])

  const detailSortValueGetter = useCallback((params: any) => {
    if (params?.data?._rowType === 'detail' && params?.data?._parentClientId != null && params?.colDef?.field) {
      const parent = parentRowMap.get(params.data._parentClientId)
      if (parent) {
        return (parent as any)[params.colDef.field]
      }
    }
    return params.value
  }, [parentRowMap])

  const postSortRows = useCallback((params: PostSortRowsParams<ClientPnLSummaryRow>) => {
    const mainOrder: IRowNode<ClientPnLSummaryRow>[] = []
    const detailMap = new Map<number | string, IRowNode<ClientPnLSummaryRow>[]>()

    params.nodes.forEach(node => {
      const data = node.data as (ClientPnLSummaryRow & { _rowType?: string; _parentClientId?: number | string }) | undefined
      if (!data) return
      if (data._rowType === 'detail' && data._parentClientId != null) {
        const list = detailMap.get(data._parentClientId) || []
        list.push(node)
        detailMap.set(data._parentClientId, list)
      } else {
        mainOrder.push(node)
      }
    })

    const reordered: IRowNode<ClientPnLSummaryRow>[] = []

    mainOrder.forEach(node => {
      reordered.push(node)
      const data = node.data as (ClientPnLSummaryRow & { _rowType?: string }) | undefined
      if (!data) return
      const detailNodes = detailMap.get(data.client_id)
      if (detailNodes && detailNodes.length > 0) {
        reordered.push(...detailNodes)
        detailMap.delete(data.client_id)
      }
    })

    detailMap.forEach(nodes => {
      reordered.push(...nodes)
    })

    params.nodes.length = 0
    reordered.forEach(node => params.nodes.push(node))
  }, [])
  
  // 数据获取函数
  const fetchData = useCallback(async (page: number = pageIndex + 1) => {
    setLoading(true)
    try {
      // 构建排序参数
      const sortBy = sortModel.length > 0 ? sortModel[0].colId : undefined
      const sortOrder = sortModel.length > 0 ? sortModel[0].sort : 'asc'
      
      // 构建查询参数
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
      })
      
      if (sortBy) {
        params.append('sort_by', sortBy)
        params.append('sort_order', sortOrder)
      }
      
      if (searchValue) {
        params.append('search', searchValue)
      }
      
      const response = await fetch(`/api/client-pnl/summary?${params}`)
      const result = await response.json()
      
      if (result.ok) {
        setRows(result.data || [])
        setTotalCount(result.total || 0)
        setTotalPages(result.total_pages || 0)
        if (result.last_updated) {
          setLastUpdated(new Date(result.last_updated))
        }
      } else {
        console.error('获取数据失败:', result.error)
      }
    } catch (error) {
      console.error('获取数据异常:', error)
    } finally {
      setLoading(false)
    }
  }, [pageIndex, pageSize, sortModel, searchValue])
  
  // 初始化加载数据
  useEffect(() => {
    fetchData()
  }, [fetchData])
  
  // 刷新按钮
  const handleManualRefresh = useCallback(async () => {
    await fetchData()
  }, [fetchData])
  
  // 搜索处理
  const handleSearch = useCallback(() => {
    setSearchValue(searchInput)
    setPageIndex(0)
  }, [searchInput])
  
  const handleSearchKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch()
    }
  }, [handleSearch])
  
  const handleClearSearch = useCallback(() => {
    setSearchInput("")
    setSearchValue("")
    setPageIndex(0)
  }, [])
  
  // Fetch account rows for a client only when needed
  const fetchAccountsForClient = useCallback(async (clientId: number | string) => {
    const key = String(clientId)
    if (accountLoadingMap[key]) {
      return
    }

    setAccountLoadingMap(prev => ({ ...prev, [key]: true }))

    try {
      const response = await fetch(`/api/client-pnl/accounts/${clientId}`)
      const result = await response.json()

      if (result.ok && Array.isArray(result.accounts)) {
        setRows(prevRows =>
          prevRows.map(row =>
            row.client_id === clientId
              ? {
                  ...row,
                  accounts: result.accounts,
                }
              : row,
          ),
        )
      } else {
        console.error('加载账户失败:', result.error)
      }
    } catch (error) {
      console.error('加载账户异常:', error)
    } finally {
      setAccountLoadingMap(prev => {
        const { [key]: _ignored, ...rest } = prev
        return rest
      })
    }
  }, [accountLoadingMap])

  // 切换展开/收起
  const toggleExpand = useCallback(async (row: ClientPnLSummaryRow) => {
    const clientId = row.client_id

    if (expandedClients.has(clientId)) {
      setExpandedClients(prev => {
        const next = new Set(prev)
        next.delete(clientId)
        return next
      })
      return
    }

    if (typeof row.accounts === "undefined") {
      await fetchAccountsForClient(clientId)
    }

    setExpandedClients(prev => {
      const next = new Set(prev)
      next.add(clientId)
      return next
    })
  }, [expandedClients, fetchAccountsForClient])
  
  // 保存列状态
  const saveGridState = useCallback(() => {
    if (!gridApi) return
    try {
      const state = gridApi.getColumnState()
      localStorage.setItem('client_pnl_grid_state', JSON.stringify(state))
    } catch {}
  }, [gridApi])
  
  // 节流保存
  const throttledSaveGridState = useMemo(() => {
    let last = 0
    let timer: any
    return () => {
      const now = Date.now()
      if (now - last >= 300) {
        last = now
        saveGridState()
      } else {
        clearTimeout(timer)
        timer = setTimeout(() => {
          last = Date.now()
          saveGridState()
        }, 300 - (now - last))
      }
    }
  }, [saveGridState])
  
  // AG Grid 列定义
  const columnDefs = useMemo<ColDef[]>(() => [
    {
      field: "client_id",
      headerName: "Client ID / Login",
      width: 140,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      sortValueGetter: detailSortValueGetter,
      cellRenderer: (params: any) => {
        const rowType = params.data?._rowType
        
        // 明细行显示 Login
        if (rowType === 'detail') {
          const loginLink = getAccountCrmLink(params.data?.server, params.data?.login)
          return (
            <span className="pl-8 font-mono text-sm text-muted-foreground">
              └ {loginLink ? (
                <a
                  href={loginLink}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline hover:no-underline"
                  onClick={(e) => e.stopPropagation()}
                >
                  {params.data.login}
                </a>
              ) : params.data.login}
            </span>
          )
        }
        
        // 主行显示 Client ID
        const clientLink = getClientCrmLink(params.value)
        if (clientLink) {
          return (
            <a
              href={clientLink}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 underline hover:no-underline"
              onClick={(e) => e.stopPropagation()}
            >
              {params.value}
            </a>
          )
        }
        return <span className="font-medium">{params.value}</span>
      },
      hide: !columnVisibility.client_id,
    },
    {
      field: "client_name",
      headerName: "客户名称 / 组别",
      width: 180,
      minWidth: 150,
      maxWidth: 300,
      sortable: true,
      filter: true,
      sortValueGetter: detailSortValueGetter,
      cellRenderer: (params: any) => {
        const rowType = params.data?._rowType
        
        // 明细行显示组别
        if (rowType === 'detail') {
          return (
            <span className="text-sm text-muted-foreground">
              {params.data.user_group || '-'}
            </span>
          )
        }
        
        // 主行显示客户名称
        return (
          <span className="max-w-[180px] truncate">
            {params.value || `客户-${params.data.client_id}`}
          </span>
        )
      },
      hide: !columnVisibility.client_name,
    },
    {
      field: "primary_server",
      headerName: "服务器",
      width: 120,
      minWidth: 100,
      maxWidth: 150,
      sortable: true,
      filter: true,
      sortValueGetter: detailSortValueGetter,
      cellRenderer: (params: any) => {
        const rowType = params.data?._rowType
        
        // 明细行显示账户服务器
        if (rowType === 'detail') {
          return (
            <span className="text-sm">{params.data.server || ''}</span>
          )
        }
        
        // 主行显示主服务器
        return <span className="text-muted-foreground">{params.value || ""}</span>
      },
      hide: !columnVisibility.primary_server,
    },
    {
      field: "account_count",
      headerName: "账户数 / 币种",
      width: 120,
      minWidth: 100,
      maxWidth: 150,
      sortable: true,
      filter: true,
      sortValueGetter: detailSortValueGetter,
      cellRenderer: (params: any) => {
        const rowType = params.data?._rowType
        
        // 明细行显示币种
        if (rowType === 'detail') {
          return (
            <span className="text-sm font-mono">{params.data.currency || '-'}</span>
          )
        }
        
        // 主行显示账户数（可点击展开）
        const clientRow = params.data as ClientPnLSummaryRow & { _rowType?: string }
        const clientId = clientRow.client_id
        const isExpanded = expandedClients.has(clientId)
        const isLoadingAccounts = !!accountLoadingMap[String(clientId)]
        const count = params.value || 0
        
        return (
          <button
            onClick={(e) => {
              e.stopPropagation()
              void toggleExpand(clientRow)
            }}
            className="flex items-center gap-1 text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 cursor-pointer font-medium transition-colors"
            disabled={isLoadingAccounts}
          >
            <span className={`transform transition-transform ${isExpanded ? 'rotate-90' : ''} ${isLoadingAccounts ? 'opacity-0' : ''}`}>
              ▶
            </span>
            {isLoadingAccounts && (
              <RefreshCw className="h-3 w-3 animate-spin" />
            )}
            <span className="tabular-nums">{count}</span>
          </button>
        )
      },
      hide: !columnVisibility.account_count,
    },
    {
      field: "total_balance_usd",
      headerName: "余额 (USD)",
      width: 140,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      sortValueGetter: detailSortValueGetter,
      cellRenderer: (params: any) => {
        const rowType = params.data?._rowType
        
        // 明细行显示账户余额
        if (rowType === 'detail') {
          const value = toNumber(params.data.balance_usd)
          return (
            <span 
              className={`text-right text-sm ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}
            >
              {formatCurrency(value)}
            </span>
          )
        }
        
        // 主行显示总余额
        const value = toNumber(params.value)
        return (
          <span 
            className={`text-right ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}
          >
            {formatCurrency(value)}
          </span>
        )
      },
      hide: !columnVisibility.total_balance_usd,
    },
    {
      field: "total_floating_pnl_usd",
      headerName: "浮动盈亏 (USD)",
      width: 150,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      sortValueGetter: detailSortValueGetter,
      cellRenderer: (params: any) => {
        const rowType = params.data?._rowType
        const value = rowType === 'detail' ? toNumber(params.data.floating_pnl_usd) : toNumber(params.value)
        
        return (
          <span 
            className={`text-right ${rowType === 'detail' ? 'text-sm' : ''} ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}
          >
            {formatCurrency(value)}
          </span>
        )
      },
      hide: !columnVisibility.total_floating_pnl_usd,
    },
    {
      field: "total_equity_usd",
      headerName: "净值 (USD)",
      width: 150,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      sortValueGetter: detailSortValueGetter,
      cellRenderer: (params: any) => {
        const rowType = params.data?._rowType
        const value = rowType === 'detail' ? toNumber(params.data.equity_usd) : toNumber(params.value)
        
        return (
          <span 
            className={`text-right ${rowType === 'detail' ? 'text-sm' : ''} ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}
          >
            {formatCurrency(value)}
          </span>
        )
      },
      hide: !columnVisibility.total_equity_usd,
    },
    {
      field: "total_closed_profit_usd",
      headerName: "平仓盈亏 (USD)",
      width: 150,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      sortValueGetter: detailSortValueGetter,
      cellStyle: (params: any) => {
        const rowType = params.data?._rowType
        return rowType === 'detail' ? undefined : { backgroundColor: 'rgba(0,0,0,0.035)' }
      },
      cellRenderer: (params: any) => {
        const rowType = params.data?._rowType
        const value = rowType === 'detail' ? toNumber(params.data.closed_profit_usd) : toNumber(params.value)
        
        return (
          <span className={`text-right ${rowType === 'detail' ? 'text-sm' : ''} ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}>
            {formatCurrency(value)}
          </span>
        )
      },
      hide: !columnVisibility.total_closed_profit_usd,
    },
    {
      field: "total_commission_usd",
      headerName: "总佣金 (USD)",
      width: 140,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      sortValueGetter: detailSortValueGetter,
      cellRenderer: (params: any) => {
        const value = toNumber(params.value)
        return (
          <span className="text-right">{formatCurrency(value)}</span>
        )
      },
      hide: !columnVisibility.total_commission_usd,
    },
    {
      field: "total_deposit_usd",
      headerName: "总入金 (USD)",
      width: 140,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      sortValueGetter: detailSortValueGetter,
      cellRenderer: (params: any) => (
        <span className="text-right">{formatCurrency(toNumber(params.value))}</span>
      ),
      hide: !columnVisibility.total_deposit_usd,
    },
    {
      field: "total_withdrawal_usd",
      headerName: "总出金 (USD)",
      width: 140,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      sortValueGetter: detailSortValueGetter,
      cellRenderer: (params: any) => (
        <span className="text-right">{formatCurrency(toNumber(params.value))}</span>
      ),
      hide: !columnVisibility.total_withdrawal_usd,
    },
    {
      field: "net_deposit_usd",
      headerName: "净入金 (USD)",
      width: 140,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      sortValueGetter: detailSortValueGetter,
      cellRenderer: (params: any) => {
        const value = toNumber(params.value)
        return (
          <span className={`text-right ${value < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}>
            {formatCurrency(value)}
          </span>
        )
      },
      hide: !columnVisibility.net_deposit_usd,
    },
    {
      field: "total_volume_lots",
      headerName: "交易手数",
      width: 140,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      sortValueGetter: detailSortValueGetter,
      cellRenderer: (params: any) => {
        const rowType = params.data?._rowType
        const value = rowType === 'detail' ? toNumber(params.data.volume_lots) : toNumber(params.value)
        
        return (
          <span className={`text-right tabular-nums ${rowType === 'detail' ? 'text-sm' : ''}`}>
            {value.toLocaleString()}
          </span>
        )
      },
      hide: !columnVisibility.total_volume_lots,
    },
    {
      field: "overnight_volume_ratio",
      headerName: "过夜成交量占比",
      width: 150,
      minWidth: 120,
      maxWidth: 200,
      sortable: true,
      filter: true,
      sortValueGetter: detailSortValueGetter,
      cellRenderer: (params: any) => {
        const rowType = params.data?._rowType
        
        // 明细行不显示此字段
        if (rowType === 'detail') {
          return <span className="text-muted-foreground text-sm">-</span>
        }
        
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
        const rowType = params.data?._rowType
        if (rowType === 'detail') return null
        
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
    {
      field: "total_closed_count",
      headerName: "总平仓订单数",
      width: 140,
      minWidth: 100,
      maxWidth: 200,
      sortable: true,
      filter: true,
      sortValueGetter: detailSortValueGetter,
      cellRenderer: (params: any) => (
        <span className="text-right tabular-nums">{toNumber(params.value).toLocaleString()}</span>
      ),
      hide: !columnVisibility.total_closed_count,
    },
    {
      field: "last_updated",
      headerName: "最后更新",
      width: 180,
      minWidth: 160,
      maxWidth: 220,
      sortable: true,
      filter: false,
      sortValueGetter: detailSortValueGetter,
      cellRenderer: (params: any) => (
        <span className="whitespace-nowrap text-muted-foreground">
          {params.value ? new Date(params.value).toLocaleString() : ""}
        </span>
      ),
      hide: !columnVisibility.last_updated,
    },
  ], [columnVisibility, expandedClients, toggleExpand, accountLoadingMap, detailSortValueGetter])
  
  // AG Grid 事件处理
  const onGridReady = useCallback((params: GridReadyEvent) => {
    setGridApi(params.api as any)
    
    try {
      const savedStateRaw = localStorage.getItem('client_pnl_grid_state')
      if (savedStateRaw) {
        const savedState = JSON.parse(savedStateRaw)
        if (Array.isArray(savedState) && savedState.length > 0) {
          ;(params.api as any).applyColumnState({ state: savedState, applyOrder: true })
          
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
          }
          return
        }
      }
      
      ;(params.api as any).applyColumnState({ state: sortModel, defaultState: { sort: null } })
    } catch (e) {
      console.error("Failed to restore grid state", e)
    }
  }, [sortModel])
  
  const onSortChanged = useCallback((event: SortChangedEvent) => {
    const newSortModel = (event.api as any).getColumnState()
      .filter((col: any) => col.sort)
      .map((col: any) => ({ colId: col.colId, sort: col.sort }))
    setSortModel(newSortModel)
    saveGridState()
  }, [saveGridState])
  
  // 生成扁平化的行数据（主行 + 展开的账户明细行）
  const flatRows = useMemo(() => {
    const result: any[] = []
    
    rows.forEach(row => {
      // 添加主行
      result.push({
        ...row,
        _rowType: 'main',
      })
      
      // 如果该客户被展开，则添加账户明细行
      if (expandedClients.has(row.client_id) && row.accounts && row.accounts.length > 0) {
        row.accounts.forEach(acc => {
          result.push({
            ...acc,
            _rowType: 'detail',
            _parentClientId: row.client_id,
          })
        })
      }
    })
    
    return result
  }, [rows, expandedClients])
  
  // 筛选处理
  const handleApplyFilters = useCallback((filters: FilterGroup) => {
    setAppliedFilters(filters)
    console.log('应用筛选条件:', JSON.stringify(filters, null, 2))
  }, [])
  
  const handleRemoveFilter = useCallback((ruleIndex: number) => {
    setAppliedFilters(prev => {
      if (!prev) return null
      const nextRules = prev.rules.filter((_, i) => i !== ruleIndex)
      return nextRules.length > 0 ? { ...prev, rules: nextRules } : null
    })
  }, [])
  
  const handleClearFilters = useCallback(() => {
    setAppliedFilters(null)
  }, [])
  
  return (
    <div className="flex h-full w-full flex-col gap-2 p-1 sm:p-4">
      {/* 状态栏 + 筛选 + 列显示切换 */}
      <Card>
        <CardContent className="py-3">
          <div className="flex flex-col gap-3">
            {/* 第一行：状态信息与按钮 */}
            <div className="flex items-center justify-between gap-3">
              {/* 左侧状态信息 */}
              <div className="flex flex-wrap items-center gap-3 text-xs sm:text-sm text-muted-foreground">
                <span>共 {totalCount.toLocaleString()} 条记录</span>
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
              </div>
              
              {/* 右侧：搜索 + 刷新 + 筛选 + 列显示切换按钮 */}
              <div className="flex items-center gap-2">
                {/* 搜索框 */}
                <div className="flex items-center gap-1">
                  <Input
                    type="text"
                    placeholder="搜索 ClientID / AccountID"
                    value={searchInput}
                    onChange={(e) => setSearchInput(e.target.value)}
                    onKeyDown={handleSearchKeyDown}
                    className="h-9 w-48"
                  />
                  {searchValue && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={handleClearSearch}
                      className="h-9 px-2"
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  )}
                  <Button
                    onClick={handleSearch}
                    className="h-9 px-3"
                    disabled={loading}
                  >
                    <Search className="h-4 w-4" />
                  </Button>
                </div>
                
                {/* 刷新按钮 */}
                <Button 
                  onClick={handleManualRefresh} 
                  className="h-9 gap-2 whitespace-nowrap"
                  disabled={loading}
                >
                  <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                  刷新
                </Button>
                
                {/* 筛选按钮 */}
                <Button 
                  onClick={() => setFilterBuilderOpen(true)} 
                  className="h-9 gap-2 whitespace-nowrap bg-black hover:bg-black/90 dark:bg-white dark:text-black dark:hover:bg-white/90"
                >
                  <Filter className="h-4 w-4" />
                  筛选
                  {appliedFilters && appliedFilters.rules.length > 0 && (
                    <Badge variant="secondary" className="ml-1 h-5 min-w-5 px-1.5 text-xs">
                      {appliedFilters.rules.length}
                    </Badge>
                  )}
                </Button>
                
                {/* 列显示切换按钮 */}
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
                        client_id: "Client ID",
                        client_name: "客户名称",
                        primary_server: "主服务器",
                        account_count: "账户数",
                        total_balance_usd: "总余额 (USD)",
                        total_floating_pnl_usd: "总浮动盈亏 (USD)",
                        total_equity_usd: "总净值 (USD)",
                        total_closed_profit_usd: "总平仓盈亏 (USD)",
                        total_commission_usd: "总佣金 (USD)",
                        total_deposit_usd: "总入金 (USD)",
                        total_withdrawal_usd: "总出金 (USD)",
                        net_deposit_usd: "净入金 (USD)",
                        total_volume_lots: "总交易手数",
                        overnight_volume_ratio: "过夜成交量占比",
                        total_closed_count: "总平仓订单数",
                        last_updated: "最后更新",
                      }
                      return (
                        <DropdownMenuCheckboxItem
                          key={columnId}
                          checked={isVisible}
                          onSelect={(e) => { e.preventDefault() }}
                          onCheckedChange={(value: boolean) => 
                            {
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
            
            {/* 第二行：激活的筛选条件展示 */}
            {appliedFilters && appliedFilters.rules.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 pt-2 border-t">
                <span className="text-xs text-muted-foreground">筛选条件（{appliedFilters.join}）：</span>
                {appliedFilters.rules.map((rule, index) => {
                  const colMeta = getColumnMeta(rule.field)
                  const opLabel = OPERATOR_LABELS[rule.op]
                  let valueDisplay = ''
                  if (rule.op !== 'blank' && rule.op !== 'not_blank') {
                    if (rule.op === 'between') {
                      valueDisplay = ` ${rule.value ?? ''} ~ ${rule.value2 ?? ''}`
                    } else {
                      valueDisplay = ` ${rule.value ?? ''}`
                    }
                  }
                  return (
                    <Badge 
                      key={index} 
                      variant="outline" 
                      className="gap-1.5 pr-1 bg-blue-50 dark:bg-blue-950/30 border-blue-200 dark:border-blue-800 text-blue-700 dark:text-blue-300"
                    >
                      <span className="text-xs">
                        {colMeta?.label || rule.field} {opLabel}{valueDisplay}
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleRemoveFilter(index)}
                        className="h-4 w-4 p-0 hover:bg-blue-200 dark:hover:bg-blue-900"
                      >
                        <X className="h-3 w-3" />
                      </Button>
                    </Badge>
                  )
                })}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleClearFilters}
                  className="h-7 text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300"
                >
                  清空全部
                </Button>
              </div>
            )}
          </div>
        </CardContent>
      </Card>
      
      {/* AG Grid 表格 */}
      <div className="flex-1">
        <div
          ref={gridContainerRef}
          className={`${(theme === 'dark' || (theme === 'system' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)) ? 'ag-theme-quartz-dark' : 'ag-theme-quartz'} h-[600px] w-full min-h-[400px] relative`}
        >
          <AgGridReact
            rowData={flatRows}
            columnDefs={columnDefs}
            gridOptions={{ theme: 'legacy' }}
            headerHeight={40}
            defaultColDef={{
              sortable: true,
              filter: true,
              resizable: true,
              minWidth: 100,
            }}
            onGridReady={onGridReady}
            onSortChanged={onSortChanged}
            onColumnResized={(e: any) => { if (e.finished) throttledSaveGridState() }}
            onColumnMoved={() => throttledSaveGridState()}
            onColumnVisible={() => throttledSaveGridState()}
            onColumnPinned={() => throttledSaveGridState()}
            postSortRows={postSortRows}
            animateRows={true}
            rowSelection="multiple"
            suppressRowClickSelection={true}
            enableCellTextSelection={true}
            domLayout="normal"
            getRowStyle={(params: any) => {
              const rowType = params.data?._rowType
              
              // 明细行样式：浅色背景
              if (rowType === 'detail') {
                return {
                  backgroundColor: 'rgba(59, 130, 246, 0.05)',
                }
              }
              
              // 主行隔行变色
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
                显示第 {pageIndex * pageSize + 1} - {Math.min((pageIndex + 1) * pageSize, totalCount)} 条，共 {totalCount.toLocaleString()} 条记录
              </div>
              
              {/* 每页条数选择 */}
              <div className="flex items-center space-x-2">
                <span className="text-sm text-muted-foreground">每页</span>
                <Select
                  value={pageSize.toString()}
                  onValueChange={(value: string) => {
                    const newSize = Number(value)
                    setPageSize(newSize)
                    setPageIndex(0)
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
                onClick={() => setPageIndex(0)}
                disabled={pageIndex === 0}
              >
                首页
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPageIndex(Math.max(0, pageIndex - 1))}
                disabled={pageIndex === 0}
              >
                上一页
              </Button>
              
              {/* 页码显示 */}
              <div className="flex items-center space-x-1">
                <span className="text-sm text-muted-foreground">
                  第 {pageIndex + 1} / {totalPages} 页
                </span>
              </div>
              
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPageIndex(Math.min(totalPages - 1, pageIndex + 1))}
                disabled={pageIndex >= totalPages - 1}
              >
                下一页
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPageIndex(totalPages - 1)}
                disabled={pageIndex >= totalPages - 1}
              >
                末页
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Filter Builder Dialog/Drawer */}
      <FilterBuilder
        open={filterBuilderOpen}
        onOpenChange={setFilterBuilderOpen}
        initialFilters={appliedFilters || undefined}
        onApply={handleApplyFilters}
      />
    </div>
  )
}

