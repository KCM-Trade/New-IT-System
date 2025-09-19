import { useMemo } from "react"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useState, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

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

  // sorting state
  type SortKey =
    | "user_name"
    | "balance"
    | "total_closed_pnl"
    | "floating_pnl"
    | "total_closed_volume"
    | "total_closed_trades"
  const [sortKey, setSortKey] = useState<SortKey | null>(null)
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc")

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

  // apply client-side sorting
  const sortedRows = useMemo(() => {
    if (!sortKey) return rows
    const mul = sortDir === "asc" ? 1 : -1
    const dup = [...rows]
    dup.sort((a, b) => {
      const va = a[sortKey] as unknown
      const vb = b[sortKey] as unknown
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * mul
      const na = toNumber(va)
      const nb = toNumber(vb)
      if (!Number.isNaN(na) && !Number.isNaN(nb)) return (na - nb) * mul
      return String(va ?? "").localeCompare(String(vb ?? "")) * mul
    })
    return dup
  }, [rows, sortKey, sortDir])

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"))
    } else {
      setSortKey(key)
      setSortDir("desc")
    }
  }

  const sortIcon = (key: SortKey) => {
    if (sortKey !== key) return "↕"
    return sortDir === "asc" ? "↑" : "↓"
  }

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

      {/* fresh grad note: full-height scroll area with single scroll container for table */}
      <div className="border rounded-md overflow-hidden flex-1">
        <div className="overflow-auto h-full">
          <Table className="min-w-[960px]">
            <TableHeader className="sticky top-0 z-10 bg-background">
              <TableRow>
                <TableHead className="whitespace-nowrap">客户ID</TableHead>
                <TableHead className="whitespace-nowrap">客户名称
                  <Button variant="ghost" size="sm" className="h-6 px-1 ml-1"
                          aria-label="排序" title="排序"
                          onClick={() => handleSort("user_name")}>{sortIcon("user_name")}</Button>
                </TableHead>
                <TableHead className="whitespace-nowrap text-right">余额
                  <Button variant="ghost" size="sm" className="h-6 px-1 ml-1"
                          aria-label="排序" title="排序"
                          onClick={() => handleSort("balance")}>{sortIcon("balance")}</Button>
                </TableHead>
                <TableHead className="whitespace-nowrap text-right">平仓总盈亏
                  <Button variant="ghost" size="sm" className="h-6 px-1 ml-1"
                          aria-label="排序" title="排序"
                          onClick={() => handleSort("total_closed_pnl")}>{sortIcon("total_closed_pnl")}</Button>
                </TableHead>
                <TableHead className="whitespace-nowrap text-right">持仓浮动盈亏
                  <Button variant="ghost" size="sm" className="h-6 px-1 ml-1"
                          aria-label="排序" title="排序"
                          onClick={() => handleSort("floating_pnl")}>{sortIcon("floating_pnl")}</Button>
                </TableHead>
                <TableHead className="whitespace-nowrap text-right">总成交量
                  <Button variant="ghost" size="sm" className="h-6 px-1 ml-1"
                          aria-label="排序" title="排序"
                          onClick={() => handleSort("total_closed_volume")}>{sortIcon("total_closed_volume")}</Button>
                </TableHead>
                <TableHead className="whitespace-nowrap text-right">平仓交易笔数
                  <Button variant="ghost" size="sm" className="h-6 px-1 ml-1"
                          aria-label="排序" title="排序"
                          onClick={() => handleSort("total_closed_trades")}>{sortIcon("total_closed_trades")}</Button>
                </TableHead>
                <TableHead className="whitespace-nowrap">更新时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedRows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={9} className="text-center text-sm text-muted-foreground">
                    {error ? `加载失败：${error}` : "暂无数据"}
                  </TableCell>
                </TableRow>
              ) : (
                sortedRows.map((r) => (
                  <TableRow key={`${r.login}-${r.symbol}`}>
                    <TableCell>{r.login}</TableCell>
                    <TableCell className="max-w-[220px] truncate">{r.user_name || `客户-${r.login}`}</TableCell>
                    <TableCell className={`${toNumber(r.balance) < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"} text-right`}>
                      {formatCurrency(toNumber(r.balance))}
                    </TableCell>
                    <TableCell className={`${toNumber(r.total_closed_pnl) < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"} text-right`}>
                      {formatCurrency(toNumber(r.total_closed_pnl))}
                    </TableCell>
                    <TableCell className={`${toNumber(r.floating_pnl) < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"} text-right`}>
                      {formatCurrency(toNumber(r.floating_pnl))}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{toNumber(r.total_closed_volume).toLocaleString()}</TableCell>
                    <TableCell className="text-right tabular-nums">{toNumber(r.total_closed_trades).toLocaleString()}</TableCell>
                    <TableCell className="whitespace-nowrap text-muted-foreground">{r.last_updated ? new Date(r.last_updated).toLocaleString() : ""}</TableCell>
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


