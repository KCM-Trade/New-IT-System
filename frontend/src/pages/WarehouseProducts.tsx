import * as React from "react"

import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Card, CardContent } from "@/components/ui/card"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Calendar } from "@/components/ui/calendar"
import { Calendar as CalendarIcon, Loader2 } from "lucide-react"
import { Progress } from "@/components/ui/progress"
import { Badge } from "@/components/ui/badge"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Input } from "@/components/ui/input"

// Static sample data from backend response for preview (no API calls yet)
const sampleItems = [
  { grp: "正在持仓", settlement: "过夜", direction: "buy", total_volume: 11.22, total_profit: -216.6099999999999 },
  { grp: "正在持仓", settlement: "过夜", direction: "sell", total_volume: 14.7, total_profit: -789.6500000000001 },
  { grp: "昨日已平", settlement: "过夜", direction: "sell", total_volume: 34.76, total_profit: -4955.52 },
  { grp: "昨日已平", settlement: "过夜", direction: "buy", total_volume: 155, total_profit: 72797.41 },
  { grp: "当日已平", settlement: "过夜", direction: "sell", total_volume: 237.76, total_profit: 29739.25 },
  { grp: "正在持仓", settlement: "当天", direction: "buy", total_volume: 80.23, total_profit: -6163.829999999999 },
  { grp: "正在持仓", settlement: "当天", direction: "sell", total_volume: 28.22, total_profit: 147.94999999999987 },
  { grp: "昨日已平", settlement: "当天", direction: "sell", total_volume: 69.7, total_profit: -3340.7799999999993 },
  { grp: "昨日已平", settlement: "当天", direction: "buy", total_volume: 199.31, total_profit: 81233.08 },
  { grp: "当日已平", settlement: "当天", direction: "sell", total_volume: 245.83, total_profit: 31473.5 },
  { grp: "当日已平", settlement: "当天", direction: "buy", total_volume: 8, total_profit: 268.21 },
]

// API response types for trade summary
type TradeSummaryItem = {
  grp: "正在持仓" | "当日已平" | "昨日已平"
  settlement: "当天" | "过夜"
  direction: "buy" | "sell"
  total_volume: number
  total_profit: number
}
type TradeSummaryResp = { ok: boolean; items: TradeSummaryItem[]; error: string | null }

type DirectionType = "Buy" | "Sell" | "Total"

type MetricKeys =
  | "current_day_volume" | "current_day_profit" | "current_overnight_volume" | "current_overnight_profit"
  | "closedToday_day_volume" | "closedToday_day_profit" | "closedToday_overnight_volume" | "closedToday_overnight_profit"
  | "closedYesterday_day_volume" | "closedYesterday_day_profit" | "closedYesterday_overnight_volume" | "closedYesterday_overnight_profit"

type PivotRow = {
  type: DirectionType
} & Record<MetricKeys, number>

function createEmptyRow(type: DirectionType): PivotRow {
  // Create an empty row with all metrics set to 0
  return {
    type,
    current_day_volume: 0,
    current_day_profit: 0,
    current_overnight_volume: 0,
    current_overnight_profit: 0,
    closedToday_day_volume: 0,
    closedToday_day_profit: 0,
    closedToday_overnight_volume: 0,
    closedToday_overnight_profit: 0,
    closedYesterday_day_volume: 0,
    closedYesterday_day_profit: 0,
    closedYesterday_overnight_volume: 0,
    closedYesterday_overnight_profit: 0,
  }
}

function toCap(dir: string): DirectionType {
  return dir === "buy" ? "Buy" : dir === "sell" ? "Sell" : "Total"
}

function format2(n: number): string {
  // Always show 2 decimal places with grouping separators
  return Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function WarehouseProductsPage() {
  // Controlled filters (product and date)
  const [selectedProduct, setSelectedProduct] = React.useState<string>("XAU-CNH")
  const [customSymbol, setCustomSymbol] = React.useState<string>("")
  const [date, setDate] = React.useState<string>("2025-08-27")
  const effectiveSymbol = selectedProduct === "other" ? customSymbol.trim() : selectedProduct

  // API states and helpers
  const [items, setItems] = React.useState<TradeSummaryItem[] | null>(null)
  const [loading, setLoading] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [progress, setProgress] = React.useState(0)
  const progressTimerRef = React.useRef<number | null>(null)
  const abortRef = React.useRef<AbortController | null>(null)
  const [lastUpdated, setLastUpdated] = React.useState<Date | null>(null)

  async function postTradeSummary(
    body: { date: string; symbol: string; mode?: "prefer_cache" | "refresh" },
    signal?: AbortSignal,
  ) {
    const res = await fetch("/api/v1/trade-summary/query", {
      method: "POST",
      headers: { "Content-Type": "application/json", accept: "application/json" },
      body: JSON.stringify(body),
      signal,
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const json = (await res.json()) as TradeSummaryResp
    if (!json.ok) throw new Error(json.error || "unknown error")
    return json.items
  }

  function startProgress() {
    setProgress(0)
    if (progressTimerRef.current) cancelAnimationFrame(progressTimerRef.current)
    const start = performance.now()
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / 1200) // ease to ~90%
      setProgress(5 + 85 * t)
      if (t < 1 && loading) {
        progressTimerRef.current = requestAnimationFrame(tick)
      }
    }
    progressTimerRef.current = requestAnimationFrame(tick)
  }

  function finishProgress() {
    if (progressTimerRef.current) cancelAnimationFrame(progressTimerRef.current)
    setProgress(100)
    setTimeout(() => setProgress(0), 300)
  }

  // Compute labels for headers using selected date
  const prevDateStr = React.useMemo(() => {
    // Derive previous day string for header label
    const d = new Date(date)
    const prev = new Date(d)
    prev.setDate(d.getDate() - 1)
    const y = prev.getFullYear()
    const m = String(prev.getMonth() + 1).padStart(2, "0")
    const day = String(prev.getDate()).padStart(2, "0")
    return `${y}-${m}-${day}`
  }, [date])

  // Pivot the items into 3 rows: Buy / Sell / Total
  const rows: PivotRow[] = React.useMemo(() => {
    const buy = createEmptyRow("Buy")
    const sell = createEmptyRow("Sell")

    // Map grp -> key prefix
    const grpMap: Record<string, "current" | "closedToday" | "closedYesterday"> = {
      "正在持仓": "current",
      "当日已平": "closedToday",
      "昨日已平": "closedYesterday",
    }

    // Map settlement -> suffix
    const setMap: Record<string, "day" | "overnight"> = {
      "当天": "day",
      "过夜": "overnight",
    }

    for (const it of (items ?? sampleItems)) {
      const grpKey = grpMap[it.grp]
      const setKey = setMap[it.settlement]
      if (!grpKey || !setKey) continue

      const volKey = `${grpKey}_${setKey}_volume` as MetricKeys
      const pftKey = `${grpKey}_${setKey}_profit` as MetricKeys

      const target = toCap(it.direction) === "Buy" ? buy : sell
      target[volKey] += it.total_volume || 0
      target[pftKey] += it.total_profit || 0
    }

    const total = createEmptyRow("Total")
    const allKeys: MetricKeys[] = [
      "current_day_volume", "current_day_profit", "current_overnight_volume", "current_overnight_profit",
      "closedToday_day_volume", "closedToday_day_profit", "closedToday_overnight_volume", "closedToday_overnight_profit",
      "closedYesterday_day_volume", "closedYesterday_day_profit", "closedYesterday_overnight_volume", "closedYesterday_overnight_profit",
    ]
    for (const k of allKeys) {
      total[k] = buy[k] + sell[k]
    }

    return [buy, sell, total]
  }, [items])

  // Profit cell text color based on sign
  function profitClass(n: number): string {
    if (n > 0) return "text-green-600 dark:text-green-400"
    if (n < 0) return "text-red-600 dark:text-red-400"
    return "text-foreground"
  }

  // Refresh handler with caching modes and cancellable request
  async function onRefresh(mode: "prefer_cache" | "refresh" = "refresh") {
    if (selectedProduct === "other" && !effectiveSymbol) {
      setError("请输入产品名称")
      return
    }
    if (abortRef.current) abortRef.current.abort()
    const ctl = new AbortController()
    abortRef.current = ctl
    setError(null)
    setLoading(true)
    startProgress()
    try {
      const data = await postTradeSummary({ date, symbol: effectiveSymbol, mode }, ctl.signal)
      setItems(data)
      const now = new Date()
      setLastUpdated(now)
      try {
        sessionStorage.setItem("warehouse_products_items", JSON.stringify(data))
        sessionStorage.setItem("warehouse_products_lastUpdated", String(now.getTime()))
        sessionStorage.setItem("warehouse_products_symbol", effectiveSymbol)
        sessionStorage.setItem("warehouse_products_date", date)
      } catch {}
    } catch (e: any) {
      if (e?.name !== "AbortError") setError(e?.message || "请求失败")
    } finally {
      setLoading(false)
      finishProgress()
    }
  }

  // 不自动加载；仅点击“刷新”时请求
  React.useEffect(() => {
    try {
      const raw = sessionStorage.getItem("warehouse_products_items")
      const ts = sessionStorage.getItem("warehouse_products_lastUpdated")
      if (raw) setItems(JSON.parse(raw) as TradeSummaryItem[])
      if (ts) setLastUpdated(new Date(Number(ts)))
    } catch {}
  }, [])

  // Build nested structure for row-title layout
  const nested = React.useMemo(() => {
    const [buy, sell, total] = rows
    function pick(group: "current" | "closedToday" | "closedYesterday") {
      return {
        Buy: {
          day: { volume: buy[`${group}_day_volume` as MetricKeys], profit: buy[`${group}_day_profit` as MetricKeys] },
          overnight: { volume: buy[`${group}_overnight_volume` as MetricKeys], profit: buy[`${group}_overnight_profit` as MetricKeys] },
        },
        Sell: {
          day: { volume: sell[`${group}_day_volume` as MetricKeys], profit: sell[`${group}_day_profit` as MetricKeys] },
          overnight: { volume: sell[`${group}_overnight_volume` as MetricKeys], profit: sell[`${group}_overnight_profit` as MetricKeys] },
        },
        Total: {
          day: { volume: total[`${group}_day_volume` as MetricKeys], profit: total[`${group}_day_profit` as MetricKeys] },
          overnight: { volume: total[`${group}_overnight_volume` as MetricKeys], profit: total[`${group}_overnight_profit` as MetricKeys] },
        },
      }
    }
    return {
      current: pick("current"),
      closedToday: pick("closedToday"),
      closedYesterday: pick("closedYesterday"),
    }
  }, [rows])

  function computeDisplay(block: {
    Buy: { day: { volume: number; profit: number }; overnight: { volume: number; profit: number } }
    Sell: { day: { volume: number; profit: number }; overnight: { volume: number; profit: number } }
    Total: { day: { volume: number; profit: number }; overnight: { volume: number; profit: number } }
  }, type: DirectionType) {
    const buy = block.Buy
    const sell = block.Sell
    if (type === "Buy") {
      return {
        dayVol: buy.day.volume,
        dayPft: buy.day.profit,
        overVol: buy.overnight.volume,
        overPft: buy.overnight.profit,
      }
    }
    if (type === "Sell") {
      return {
        dayVol: -sell.day.volume,
        dayPft: sell.day.profit,
        overVol: -sell.overnight.volume,
        overPft: sell.overnight.profit,
      }
    }
    // Total: Volume = Buy - Sell; Profit = Buy + Sell
    return {
      dayVol: buy.day.volume - sell.day.volume,
      dayPft: buy.day.profit + sell.day.profit,
      overVol: buy.overnight.volume - sell.overnight.volume,
      overPft: buy.overnight.profit + sell.overnight.profit,
    }
  }

  return (
    <div className="space-y-4 px-4 pb-6 lg:px-6">
      {/* Toolbar Card (一致化 Profit 页样式) */}
      <Card>
        <CardContent className="flex flex-col gap-3 md:flex-row md:items-center md:gap-6">
          {/* 产品选择（Select + 其他时可自定义） */}
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">产品</span>
            <Select value={selectedProduct} onValueChange={(v) => setSelectedProduct(v)}>
              <SelectTrigger className="h-10 w-[180px] rounded-md">
                <SelectValue placeholder="请选择产品" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="XAU-CNH">XAU-CNH</SelectItem>
                <SelectItem value="XAUUSD">XAUUSD</SelectItem>
                <SelectItem value="other">其他</SelectItem>
              </SelectContent>
            </Select>
            {selectedProduct === "other" && (
              <Input
                className="h-10 w-[180px]"
                placeholder="自定义产品"
                value={customSymbol}
                onChange={(e) => setCustomSymbol(e.target.value)}
              />
            )}
          </div>
          {/* 日期选择（shadcn Calendar 单日） */}
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">日期</span>
            <Popover>
              <PopoverTrigger asChild>
                <Button variant="outline" className="h-10 w-[180px] justify-between gap-2 font-normal">
                  <CalendarIcon className="h-4 w-4" />
                  <span>{date}</span>
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-auto p-0" align="start">
                <Calendar
                  mode="single"
                  selected={new Date(date)}
                  onSelect={(d) => {
                    if (!d) return
                    const y = d.getFullYear()
                    const m = String(d.getMonth() + 1).padStart(2, "0")
                    const day = String(d.getDate()).padStart(2, "0")
                    setDate(`${y}-${m}-${day}`)
                  }}
                  initialFocus
                />
              </PopoverContent>
            </Popover>
          </div>
          {/* 刷新 + 进度条 + 错误提示 + 上次刷新时间 */}
          <div className="flex items-center gap-3 min-w-[360px]">
            <Button className="h-9 w-[96px] gap-2" onClick={() => onRefresh("refresh")} disabled={loading || (selectedProduct === "other" && !effectiveSymbol)}>
              {loading && <Loader2 className="h-4 w-4 animate-spin" />}
              刷新
            </Button>
            {progress > 0 && (
              <div className="w-[160px]">
                <Progress value={progress} />
              </div>
            )}
            {error && <span className="text-sm text-red-600">{error}</span>}
            {lastUpdated && (
              <Badge variant="outline">上次刷新：{lastUpdated.toLocaleString("zh-CN", { hour12: false })}</Badge>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Table Card */}
      <Card>
        <CardContent className="pt-6">
          <div className="mx-auto w-full max-w-[1280px]">
            <div className="overflow-hidden rounded-md border-2 shadow-md">
              <Table className="min-w-[780px]">
          <TableHeader>
            <TableRow>
              <TableHead rowSpan={2} className="w-[200px] align-middle border-r font-semibold text-base">报仓</TableHead>
              <TableHead rowSpan={2} className="w-[140px] align-middle border-r font-semibold text-base">Type</TableHead>
              <TableHead colSpan={2} className="text-center font-semibold text-base bg-blue-50 dark:bg-blue-900/20 rounded-sm">即日</TableHead>
              <TableHead colSpan={2} className="text-center font-semibold text-base bg-yellow-50 dark:bg-yellow-900/20 rounded-sm">过夜</TableHead>
            </TableRow>
            <TableRow>
              <TableHead className="text-right border-r font-semibold text-base bg-blue-50 dark:bg-blue-900/20">Volume</TableHead>
              <TableHead className="text-right border-r font-semibold text-base bg-blue-50 dark:bg-blue-900/20">Profit</TableHead>
              <TableHead className="text-right border-r font-semibold text-base bg-yellow-50 dark:bg-yellow-900/20">Volume</TableHead>
              <TableHead className="text-right font-semibold text-base bg-yellow-50 dark:bg-yellow-900/20">Profit</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(["current", "closedToday", "closedYesterday"] as const).map((gKey) => {
              const groupLabel = gKey === "current" ? "正在持仓" : gKey === "closedToday" ? `当日已平（${date}）` : `昨日已平（${prevDateStr}）`
              const block = nested[gKey]
              const types: DirectionType[] = ["Buy", "Sell", "Total"]
              return (
                <React.Fragment key={gKey}>
                  {types.map((t, tIdx) => {
                    const d = computeDisplay(block, t)
                    return (
                      <TableRow key={t} className={`${t === "Total" ? "bg-blue-50 dark:bg-blue-900/30" : ""}`}>
                        {tIdx === 0 && (
                          <TableCell rowSpan={3} className="align-top font-medium border-r border-2 dark:border-white/20">{groupLabel}</TableCell>
                        )}
                        <TableCell className={`border-r ${t === "Total" ? "font-semibold" : ""}`}>{t}</TableCell>
                        <TableCell className={`text-right tabular-nums border-r ${t === "Total" ? "font-semibold" : ""}`}>{format2(d.dayVol)}</TableCell>
                        <TableCell className={`text-right tabular-nums border-r ${profitClass(d.dayPft)} ${t === "Total" ? "font-semibold" : ""}`}>{format2(d.dayPft)}</TableCell>
                        <TableCell className={`text-right tabular-nums border-r ${t === "Total" ? "font-semibold" : ""}`}>{format2(d.overVol)}</TableCell>
                        <TableCell className={`text-right tabular-nums ${profitClass(d.overPft)} ${t === "Total" ? "font-semibold" : ""}`}>{format2(d.overPft)}</TableCell>
                      </TableRow>
                    )
                  })}
                </React.Fragment>
              )
            })}
          </TableBody>
          </Table>
        </div>
      </div>
        </CardContent>
      </Card>
    </div>
  )
}

