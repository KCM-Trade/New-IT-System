import { useEffect, useMemo, useRef, useState } from "react"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
// removed Select in favor of capsule toggle for timezone
import {
  Bar,
  BarChart,
  CartesianGrid,
  Tooltip,
  XAxis,
  YAxis,
  ResponsiveContainer,
} from "recharts"
import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Calendar } from "@/components/ui/calendar"
import { Calendar as CalendarIcon, Loader2, ArrowUp, ArrowDown } from "lucide-react"
import { DateRange } from "react-day-picker"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
// removed Input/Separator from old custom range UI

type ProfitRow = {
  date: string // e.g. "2025-05-01"
  hour: number // 0-23 (source timezone: UTC+3)
  profit: number
}

type AggKey = "timeline" | "hourOfDay"
type TzKey = "+3" | "+8"
type AggTypeKey = "open" | "close"

// fresh grad: 小时段交易明细类型定义
type HourlyTradeDetail = {
  login: string
  ticket: number
  symbol: string
  side: string // buy/sell
  lots: number
  open_time: string
  close_time: string
  open_price: number
  close_price: number
  profit: number
  swaps: number
}

type HourlyDetailsResponse = {
  trades: HourlyTradeDetail[]
  total_count: number
  total_profit: number
  time_range: string
  symbol: string
}

// fresh grad: simple date formatting helper
function formatLabel(dt: Date) {
  const mm = String(dt.getUTCMonth() + 1).padStart(2, "0")
  const dd = String(dt.getUTCDate()).padStart(2, "0")
  const hh = String(dt.getUTCHours()).padStart(2, "0")
  return `${mm}-${dd} ${hh}:00`
}

// fresh grad: simple animated number hook for smooth value changes
function useAnimatedNumber(target: number, durationMs = 600) {
  const [displayValue, setDisplayValue] = useState(target)
  const previousRef = useRef(target)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    const startValue = previousRef.current
    const delta = target - startValue
    if (delta === 0) return

    const startTime = performance.now()

    const tick = (now: number) => {
      const elapsed = now - startTime
      const t = Math.min(1, elapsed / durationMs)
      // ease-out cubic
      const eased = 1 - Math.pow(1 - t, 3)
      setDisplayValue(startValue + delta * eased)
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick)
      }
    }

    rafRef.current = requestAnimationFrame(tick)
    previousRef.current = target

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [target, durationMs])

  return displayValue
}

export default function ProfitPage() {
  const [rows, setRows] = useState<ProfitRow[]>([])
  const [loading, setLoading] = useState(true)
  // fresh grad: date range via single Popover + range Calendar
  const [range, setRange] = useState<DateRange | undefined>(() => {
    // fresh grad: default to the last 7 days on first load
    const to = new Date()
    const from = new Date()
    from.setDate(from.getDate() - 7)
    return { from, to }
  })
  const [agg, setAgg] = useState<AggKey>("timeline")
  const [tz, setTz] = useState<TzKey>("+8")
  const [aggType, setAggType] = useState<AggTypeKey>("open")
  // fresh grad: detect mobile to adjust layout/Chart
  const [isMobile, setIsMobile] = useState(false)
  // last refreshed tag (shared across users via backend marker)
  const [lastRefreshed, setLastRefreshed] = useState<string | null>(null)
  
  // fresh grad: 小时段明细相关状态
  const [hourlyDetails, setHourlyDetails] = useState<HourlyDetailsResponse | null>(null)
  const [detailsLoading, setDetailsLoading] = useState(false)
  const [detailsError, setDetailsError] = useState<string | null>(null)
  const [selectedTimeRange, setSelectedTimeRange] = useState<string>("")
  const [profitSortOrder, setProfitSortOrder] = useState<"desc" | "asc">("desc") // 利润排序，默认从高到低

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < 640) // 640px ~ tailwind sm breakpoint
    onResize()
    window.addEventListener("resize", onResize)
    return () => window.removeEventListener("resize", onResize)
  }, [])

  // fresh grad: 当聚合维度改变时清除已选择的交易明细
  useEffect(() => {
    setHourlyDetails(null)
    setSelectedTimeRange("")
    setDetailsError(null)
  }, [agg])

  // fresh grad: 处理利润排序
  const handleProfitSort = () => {
    setProfitSortOrder(prev => prev === "desc" ? "asc" : "desc")
  }

  // fresh grad: 根据排序顺序对交易明细进行排序
  const sortedTrades = useMemo(() => {
    if (!hourlyDetails?.trades) return []
    
    const sorted = [...hourlyDetails.trades].sort((a, b) => {
      if (profitSortOrder === "desc") {
        return b.profit - a.profit // 从高到低
      } else {
        return a.profit - b.profit // 从低到高
      }
    })
    
    return sorted
  }, [hourlyDetails?.trades, profitSortOrder])

  // fresh grad: 分析数据 - 交易次数统计
  const tradeCountAnalysis = useMemo(() => {
    if (!hourlyDetails?.trades) return []
    
    const countByLogin = new Map<string, number>()
    hourlyDetails.trades.forEach(trade => {
      countByLogin.set(trade.login, (countByLogin.get(trade.login) || 0) + 1)
    })
    
    return Array.from(countByLogin.entries())
      .map(([login, count]) => ({ login, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 10) // 只显示前10
  }, [hourlyDetails?.trades])

  // fresh grad: 分析数据 - 利润排序（按用户分组）
  const profitByUserAnalysis = useMemo(() => {
    if (!hourlyDetails?.trades) return []
    
    const profitByLogin = new Map<string, { 
      total: number, 
      buyProfit: number, 
      sellProfit: number, 
      buyCount: number, 
      sellCount: number 
    }>()
    
    hourlyDetails.trades.forEach(trade => {
      const current = profitByLogin.get(trade.login) || {
        total: 0, buyProfit: 0, sellProfit: 0, buyCount: 0, sellCount: 0
      }
      
      current.total += trade.profit
      if (trade.side === 'buy') {
        current.buyProfit += trade.profit
        current.buyCount++
      } else {
        current.sellProfit += trade.profit
        current.sellCount++
      }
      
      profitByLogin.set(trade.login, current)
    })
    
    return Array.from(profitByLogin.entries())
      .map(([login, data]) => ({ login, ...data }))
      .sort((a, b) => b.total - a.total)
      .slice(0, 10) // 只显示前10
  }, [hourlyDetails?.trades])

  // fresh grad: 分析数据 - 交易时间和手数相关性（简单版）
  const timeLotsCorrelation = useMemo(() => {
    if (!hourlyDetails?.trades) return { correlation: 0, analysis: "暂无数据" }
    
    const trades = hourlyDetails.trades
    if (trades.length < 2) return { correlation: 0, analysis: "数据量不足" }
    
    // 提取小时和手数数据
    const hourData: number[] = []
    const lotsData: number[] = []
    
    trades.forEach(trade => {
      const hour = parseInt(trade.open_time.split(' ')[1].split(':')[0])
      hourData.push(hour)
      lotsData.push(trade.lots)
    })
    
    // 计算简单相关系数
    const n = hourData.length
    const meanHour = hourData.reduce((a, b) => a + b, 0) / n
    const meanLots = lotsData.reduce((a, b) => a + b, 0) / n
    
    let numerator = 0
    let denomHour = 0
    let denomLots = 0
    
    for (let i = 0; i < n; i++) {
      const hourDiff = hourData[i] - meanHour
      const lotsDiff = lotsData[i] - meanLots
      numerator += hourDiff * lotsDiff
      denomHour += hourDiff * hourDiff
      denomLots += lotsDiff * lotsDiff
    }
    
    const correlation = denomHour === 0 || denomLots === 0 
      ? 0 
      : numerator / Math.sqrt(denomHour * denomLots)
    
    let analysis = ""
    if (Math.abs(correlation) < 0.1) analysis = "时间与手数无明显相关性"
    else if (correlation > 0.3) analysis = "午后倾向于加大交易手数"
    else if (correlation < -0.3) analysis = "午后倾向于减少交易手数"
    else if (correlation > 0) analysis = "时间越晚手数略有增加趋势"
    else analysis = "时间越晚手数略有减少趋势"
    
    return { correlation, analysis }
  }, [hourlyDetails?.trades])

  // fresh grad: API调用函数 - 获取小时段交易明细
  const fetchHourlyDetails = async (startTime: string, endTime: string, timeRange: string) => {
    if (agg === "hourOfDay") {
      return // 小时段聚合模式不支持明细查询功能
    }

    setDetailsLoading(true)
    setDetailsError(null)
    setSelectedTimeRange(timeRange)

    try {
      const response = await fetch("/api/v1/trading/hourly-details", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          start_time: startTime,
          end_time: endTime,
          symbol: "XAUUSD", // 目前Profit页面固定为XAUUSD
          time_type: aggType === "open" ? "open" : "close",
          limit: 100,
        }),
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }

      const data: HourlyDetailsResponse = await response.json()
      setHourlyDetails(data)
    } catch (error: any) {
      setDetailsError(error?.message ?? "获取明细数据失败")
    } finally {
      setDetailsLoading(false)
    }
  }

  // removed: custom range text inputs and history

  // removed: history persistence and input sync

  // removed: custom input apply handler

  // fresh grad: 柱状图点击处理函数
  const handleBarClick = (data: any) => {
    if (!data || agg === "hourOfDay") {
      return // 小时段模式不支持点击
    }

    const label = data.label as string
    const userTzOffset = tz === "+8" ? 8 : 3 // 用户选择的时区偏移
    const dbTzOffset = 3 // 数据库时区偏移 UTC+3

    try {
      // 解析时间标签，例如 "05-15 14:00"
      const [monthDay, hour] = label.split(" ")
      const [month, day] = monthDay.split("-")
      
      // 构造用户时区的时间范围
      const currentYear = new Date().getFullYear()
      const userStartHour = parseInt(hour.split(":")[0])
      const userEndHour = userStartHour + 1
      
      // 转换为数据库时区时间 (UTC+3)
      // 公式: 数据库时间 = 用户时区时间 - (用户时区偏移 - 数据库时区偏移)
      const dbStartHour = userStartHour - (userTzOffset - dbTzOffset)
      const dbEndHour = userEndHour - (userTzOffset - dbTzOffset)
      
      // 处理跨日情况
      let dbStartDate = new Date(currentYear, parseInt(month) - 1, parseInt(day))
      let dbEndDate = new Date(currentYear, parseInt(month) - 1, parseInt(day))
      
      if (dbStartHour < 0) {
        dbStartDate.setDate(dbStartDate.getDate() - 1)
        dbStartDate.setHours(24 + dbStartHour, 0, 0, 0)
      } else if (dbStartHour >= 24) {
        dbStartDate.setDate(dbStartDate.getDate() + 1)
        dbStartDate.setHours(dbStartHour - 24, 0, 0, 0)
      } else {
        dbStartDate.setHours(dbStartHour, 0, 0, 0)
      }
      
      if (dbEndHour < 0) {
        dbEndDate.setDate(dbEndDate.getDate() - 1)
        dbEndDate.setHours(24 + dbEndHour, 0, 0, 0)
      } else if (dbEndHour >= 24) {
        dbEndDate.setDate(dbEndDate.getDate() + 1)
        dbEndDate.setHours(dbEndHour - 24, 0, 0, 0)
      } else {
        dbEndDate.setHours(dbEndHour, 0, 0, 0)
      }
      
      // 格式化为MySQL datetime格式 (YYYY-MM-DD HH:MM:SS)
      const formatToMySQLDateTime = (date: Date) => {
        const year = date.getFullYear()
        const month = String(date.getMonth() + 1).padStart(2, '0')
        const day = String(date.getDate()).padStart(2, '0')
        const hour = String(date.getHours()).padStart(2, '0')
        const minute = String(date.getMinutes()).padStart(2, '0')
        const second = String(date.getSeconds()).padStart(2, '0')
        return `${year}-${month}-${day} ${hour}:${minute}:${second}`
      }

      const startTimeStr = formatToMySQLDateTime(dbStartDate)
      const endTimeStr = formatToMySQLDateTime(new Date(dbEndDate.getTime() - 1000)) // 减1秒，避免包含下一小时的00:00:00

      console.log(`时区转换: 用户${tz}时区 ${label} → 数据库UTC+3时区 ${startTimeStr} - ${endTimeStr}`)
      
      fetchHourlyDetails(startTimeStr, endTimeStr, label)
    } catch (error) {
      console.error("解析时间标签失败:", error)
      alert("无法解析时间段，请重试")
    }
  }

  // fresh grad: shared loader to fetch NDJSON according to aggType
  const fetchRows = useMemo(() => {
    return async () => {
      setLoading(true)
      try {
        const url = aggType === "open" ? "/profit_xauusd_hourly.json" : "/profit_xauusd_hourly_close.json"
        const res = await fetch(url)
        if (!res.ok) throw new Error(`Failed to load NDJSON: ${res.status}`)
        const text = await res.text()
        const lines = text.split(/\r?\n/).filter(Boolean)
        const data: ProfitRow[] = []
        for (const line of lines) {
          try {
            const obj = JSON.parse(line)
            if (
              typeof obj?.date === "string" &&
              typeof obj?.hour === "number" &&
              typeof obj?.profit === "number"
            ) {
              data.push({ date: obj.date, hour: obj.hour, profit: obj.profit })
            }
          } catch {
            // skip bad line
          }
        }
        setRows(data)
      } catch {
        // fresh grad: on any error, clear data and continue; loading will stop in finally
        setRows([])
      } finally {
        setLoading(false)
      }
    }
  }, [aggType])

  // Note: The dataset rendered on this page is XAU-CNH (exported by backend aggregate to /public JSON files).
  // fresh grad: source file is NDJSON (one JSON object per line), not a JSON array
  useEffect(() => {
    if (!range?.from || !range?.to) return
    let cancelled = false
    ;(async () => {
      if (cancelled) return
      await fetchRows()
    })()
    return () => {
      cancelled = true
    }
  }, [fetchRows, range])

  // load last refresh marker on mount and after refresh
  const setRangeFromRefreshed = (ref: string) => {
    // fresh grad: ref format "YYYY-MM-DD HH:MM:SS" at UTC+3; use only date part for calendar
    const [datePart] = ref.split(" ")
    const [y, m, d] = datePart.split("-").map((v) => parseInt(v, 10))
    if (!y || !m || !d) return
    const to = new Date(y, m - 1, d)
    const from = new Date(to)
    from.setDate(from.getDate() - 7)
    setRange({ from, to })
  }

  const loadLastRefresh = async (): Promise<string | null> => {
    try {
      const res = await fetch('/api/v1/aggregate/last-refresh')
      const json = await res.json()
      const refreshedAt: string | null = json?.refreshed_at ?? null
      setLastRefreshed(refreshedAt)
      return refreshedAt
    } catch {
      setLastRefreshed(null)
      return null
    }
  }
  useEffect(() => {
    ;(async () => {
      const ref = await loadLastRefresh()
      if (ref && !range) {
        setRangeFromRefreshed(ref)
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // fresh grad: click to refresh backend aggregation then reload NDJSON
  const onRefresh = async () => {
    setLoading(true)
    try {
      await fetch("/api/v1/aggregate/refresh", { method: "POST" })
    } catch {
      // ignore
    } finally {
      await fetchRows()
      const ref = await loadLastRefresh()
      if (ref) setRangeFromRefreshed(ref)
    }
  }

  // fresh grad: memoized rows with UTC timestamp
  const withUtc = useMemo(
    () =>
      rows.map((r) => {
        const [y, m, d] = r.date.split("-").map((v) => parseInt(v, 10))
        const tsUtc = Date.UTC(y, m - 1, d, r.hour - 3, 0, 0) // shift from UTC+3 → UTC
        return { ...r, tsUtc }
      }),
    [rows],
  )

  // fresh grad: filter rows by selected date range and timezone
  const selectedRangeUtc = useMemo(() => {
    if (!range?.from || !range?.to) return null
    const tzOffsetHours = tz === "+8" ? 8 : 3
    const getTimestamp = (d: Date, atEndOfDay: boolean) => {
      const y = d.getFullYear()
      const m = d.getMonth()
      const day = d.getDate()
      if (atEndOfDay) return Date.UTC(y, m, day, 23, 59, 59, 999) - tzOffsetHours * 3600000
      return Date.UTC(y, m, day, 0, 0, 0) - tzOffsetHours * 3600000
    }
    let startUtc = getTimestamp(range.from, false)
    let endUtc = getTimestamp(range.to, true)
    if (startUtc > endUtc) [startUtc, endUtc] = [endUtc, startUtc]
    return { startUtc, endUtc }
  }, [range, tz])

  const inRangeRows = useMemo(() => {
    if (!selectedRangeUtc || withUtc.length === 0) return withUtc
    const { startUtc, endUtc } = selectedRangeUtc
    return withUtc.filter((x) => x.tsUtc >= startUtc && x.tsUtc <= endUtc)
  }, [withUtc, selectedRangeUtc])

  // Convert source (UTC+3) to UTC epoch ms, then label in chosen tz
  const prepared = useMemo(() => {
    const tzOffsetHours = tz === "+8" ? 8 : 3

    if (agg === "timeline") {
      // label by chosen tz within selected date range
      const timeline = inRangeRows
        .map((x) => {
          const dt = new Date(x.tsUtc + tzOffsetHours * 3600000)
          return {
            label: formatLabel(dt),
            profit: x.profit,
            ts: x.tsUtc, // for stable sorting
          }
        })
        .sort((a, b) => a.ts - b.ts)

      // merge same label (unlikely but safe if multiple rows map to same local hour)
      const merged = new Map<string, number>()
      for (const it of timeline) {
        merged.set(it.label, (merged.get(it.label) ?? 0) + it.profit)
      }
      return Array.from(merged.entries()).map(([label, profit]) => ({ label, profit }))
    }

    // hour-of-day aggregation in chosen tz (0-23) within selected date range
    const buckets = new Array(24).fill(0) as number[]
    for (const x of inRangeRows) {
      const local = new Date(x.tsUtc + tzOffsetHours * 3600000)
      const hour = local.getUTCHours()
      buckets[hour] += x.profit
    }
    return buckets.map((profit, hour) => ({ label: `${String(hour).padStart(2, "0")}:00`, profit }))
  }, [inRangeRows, agg, tz])

  // fresh grad: totals (within selected date range + chosen tz)
  const { totalProfit, totalLoss, pnl } = useMemo(() => {
    let profit = 0
    let loss = 0
    for (const x of inRangeRows) {
      if (x.profit >= 0) profit += x.profit
      else loss += Math.abs(x.profit)
    }
    const pnl = profit - loss
    return { totalProfit: profit, totalLoss: loss, pnl }
  }, [inRangeRows])

  // fresh grad: previous period comparison removed per latest design; keep layout concise

  // fresh grad: animated numbers for better UX feedback on changes
  const animatedProfit = useAnimatedNumber(totalProfit)
  const animatedLoss = useAnimatedNumber(totalLoss)
  const animatedPnl = useAnimatedNumber(pnl)

  // fresh grad: format date range like "Jan 20, 2023 - Feb 09, 2023"
  const rangeLabel = useMemo(() => {
    if (!range?.from || !range?.to) return "选择日期范围"
    const opts: Intl.DateTimeFormatOptions = { month: "short", day: "2-digit", year: "numeric" }
    return `${range.from.toLocaleDateString("en-US", opts)} - ${range.to.toLocaleDateString("en-US", opts)}`
  }, [range])

  return (
    <div className="space-y-4 px-4 pb-6 lg:px-6">
      {/* Toolbar */}
      <Card>
        <CardHeader>
          <CardTitle className="text-2xl font-bold">筛选与视图（XAUUSD）</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 md:flex-row md:items-center md:gap-6">
          {/* 时间范围（单按钮 + Range 日历） */}
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">时间范围：</span>
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
          {/* 聚合类型（与聚合维度采用一致风格与宽度） */}
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">聚合类型：</span>
            <ToggleGroup
              type="single"
              value={aggType}
              onValueChange={(v) => v && setAggType(v as AggTypeKey)}
              className="inline-flex w-[240px] items-center rounded-full bg-muted p-1"
            >
              <ToggleGroupItem
                value="open"
                className="flex-1 rounded-full first:rounded-l-full last:rounded-r-full px-3 py-1 text-center text-sm text-muted-foreground
                           data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow"
              >
                Open Time
              </ToggleGroupItem>
              <ToggleGroupItem
                value="close"
                className="flex-1 rounded-full first:rounded-l-full last:rounded-r-full px-3 py-1 text-center text-sm text-muted-foreground
                           data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow"
              >
                Close Time
              </ToggleGroupItem>
            </ToggleGroup>
          </div>

          {/* 聚合维度（与聚合类型保持一致宽度与风格） */}
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">聚合维度：</span>
            <ToggleGroup
              type="single"
              value={agg}
              onValueChange={(v) => v && setAgg(v as AggKey)}
              className="inline-flex w-[240px] items-center rounded-full bg-muted p-1"
            >
              <ToggleGroupItem
                value="timeline"
                className="flex-1 rounded-full first:rounded-l-full last:rounded-r-full px-3 py-1 text-center text-sm text-muted-foreground
                           data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow"
              >
                时间轴（小时）
              </ToggleGroupItem>
              <ToggleGroupItem
                value="hourOfDay"
                className="flex-1 rounded-full first:rounded-l-full last:rounded-r-full px-3 py-1 text-center text-sm text-muted-foreground
                           data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow"
              >
                小时段(0-23)
              </ToggleGroupItem>
            </ToggleGroup>
          </div>

          {/* 时区（胶囊式等宽切换） */}
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">时区：</span>
            <ToggleGroup
              type="single"
              value={tz}
              onValueChange={(v) => v && setTz(v as TzKey)}
              className="inline-flex w-[240px] items-center rounded-full bg-muted p-1"
            >
              <ToggleGroupItem
                value="+3"
                className="flex-1 rounded-full first:rounded-l-full last:rounded-r-full px-3 py-1 text-center text-sm text-muted-foreground
                           data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow"
              >
                UTC+3
              </ToggleGroupItem>
              <ToggleGroupItem
                value="+8"
                className="flex-1 rounded-full first:rounded-l-full last:rounded-r-full px-3 py-1 text-center text-sm text-muted-foreground
                           data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow"
              >
                UTC+8
              </ToggleGroupItem>
            </ToggleGroup>
          </div>
          {/* 刷新按钮（紧挨着时区） */}
          <div className="flex items-center gap-3">
            <Button onClick={onRefresh} disabled={loading}>
              {loading ? "刷新中…" : "刷新"}
            </Button>
            {lastRefreshed && (
              <span className="text-xs text-muted-foreground">上次刷新(UTC+3)：{lastRefreshed}</span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Chart */}
      <Card>
        <CardContent className="pt-6">
          {loading ? (
            <div className="text-sm text-muted-foreground px-2 py-8">Loading…</div>
          ) : (
            <div className="flex flex-col gap-4 sm:flex-row">
              <div className="w-full h-[200px] sm:h-[400px] lg:w-4/5">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={prepared}>
                    <CartesianGrid vertical={false} />
                    <XAxis dataKey="label" tickMargin={8} minTickGap={24} tick={{ fontSize: 10 }}/>
                    {!isMobile && (
                      <YAxis tickFormatter={(v) => new Intl.NumberFormat().format(v)} tick={{ fontSize: 10 }} />
                    )}
                    <Tooltip
                      formatter={(value: number) => new Intl.NumberFormat().format(value)}
                      labelFormatter={(label: string) => label}
                    />
                    <Bar 
                      dataKey="profit" 
                      fill="var(--primary)" 
                      onClick={handleBarClick}
                      style={{ cursor: agg === "timeline" ? "pointer" : "default" }}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div className="w-full lg:w-1/6">
                <div className="flex flex-col gap-0 lg:gap-15 justify-between">
                  {/* 盈利（纯文本） */}
                  <div className="min-w-0 px-4 py-2">
                    <div className="text-sm font-medium text-muted-foreground">盈利</div>
                    <div
                      className="mt-1 text-xl lg:text-2xl font-extrabold text-red-500"
                      aria-live="polite"
                    >
                      {`${animatedProfit >= 0 ? "+" : "-"}${new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(Math.abs(animatedProfit))}`}
                    </div>
                  </div>

                  {/* 亏损（纯文本） */}
                  <div className="min-w-0 px-4 py-2">
                    <div className="text-sm font-medium text-muted-foreground">亏损</div>
                    <div
                      className="mt-1 text-xl lg:text-2xl font-extrabold text-green-500"
                      aria-live="polite"
                    >
                      {`${animatedLoss <= 0 ? "+" : "-"}${new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(Math.abs(animatedLoss))}`}
                    </div>
                  </div>

                  {/* 净利润（纯文本） */}
                  <div className="min-w-0 px-4 py-2">
                    <div className="text-sm font-medium text-muted-foreground">净利润</div>
                    <div
                      className={`mt-1 text-xl lg:text-2xl font-extrabold ${pnl >= 0 ? "text-red-500" : "text-green-500"}`}
                      aria-live="polite"
                    >
                      {`${animatedPnl >= 0 ? "+" : "-"}${new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(Math.abs(animatedPnl))}`}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 交易明细与分析 - 响应式布局 */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {/* 左侧交易明细 - 桌面端占2/3，移动端全宽 */}
        <div className="xl:col-span-2">
          <Card className="h-fit">
            <CardHeader>
              <CardTitle className="text-lg font-semibold">
                交易明细
                {selectedTimeRange && ` - ${selectedTimeRange}`}
              </CardTitle>
              <CardDescription>
                {agg === "timeline" && !selectedTimeRange && 
                  "点击上方柱状图的任意小时段查看该时间段内的详细交易记录"
                }
                {agg === "hourOfDay" && 
                  "小时段聚合模式不支持查看交易明细功能，请切换到\"时间轴\"模式以启用此功能"
                }
                {selectedTimeRange && 
                  `时间段：${selectedTimeRange} · 聚合类型：${aggType === "open" ? "开仓时间" : "平仓时间"} · 时区：UTC${tz}`
                }
              </CardDescription>
            </CardHeader>
            <CardContent>
              {detailsLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin mr-2" />
                  <span className="text-muted-foreground">加载交易明细中...</span>
                </div>
              ) : detailsError ? (
                <div className="flex items-center justify-center py-8 text-destructive">
                  <span>加载失败：{detailsError}</span>
                </div>
              ) : hourlyDetails ? (
                <>
                  {/* 汇总信息 */}
                  <div className="mb-4 p-3 bg-muted/50 rounded-lg">
                    <div className="grid grid-cols-3 gap-4 text-center">
                      <div>
                        <div className="text-sm text-muted-foreground">总交易数</div>
                        <div className="font-semibold">{hourlyDetails.total_count}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted-foreground">总利润</div>
                        <div className={`font-semibold ${hourlyDetails.total_profit >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                          ${hourlyDetails.total_profit.toFixed(2)}
                        </div>
                      </div>
                      <div>
                        <div className="text-sm text-muted-foreground">品种</div>
                        <div className="font-semibold">{hourlyDetails.symbol}</div>
                      </div>
                    </div>
                  </div>

                  {/* 交易明细表格 - 桌面端限制高度 */}
                  <div className="border rounded-md overflow-hidden">
                    <div className="xl:max-h-96 xl:overflow-y-auto">
                      <Table>
                        <TableHeader className="xl:sticky xl:top-0 bg-background">
                          <TableRow>
                            <TableHead className="text-xs font-medium">Login</TableHead>
                            <TableHead className="text-xs font-medium">Ticket</TableHead>
                            <TableHead className="text-xs font-medium">Symbol</TableHead>
                            <TableHead className="text-xs font-medium">Side</TableHead>
                            <TableHead className="text-xs font-medium">Lots</TableHead>
                            <TableHead className="text-xs font-medium">Open Time</TableHead>
                            <TableHead className="text-xs font-medium">Close Time</TableHead>
                            <TableHead className="text-right text-xs font-medium">
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-auto p-1 text-xs font-medium hover:bg-transparent"
                                onClick={handleProfitSort}
                              >
                                Profit
                                {profitSortOrder === "desc" ? (
                                  <ArrowDown className="ml-1 h-3 w-3" />
                                ) : (
                                  <ArrowUp className="ml-1 h-3 w-3" />
                                )}
                              </Button>
                            </TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {sortedTrades.length > 0 ? (
                            sortedTrades.map((trade, index) => (
                              <TableRow key={`${trade.login}-${trade.ticket}`} className={index < 3 ? "bg-accent/50" : ""}>
                                <TableCell className="text-xs font-mono">{trade.login}</TableCell>
                                <TableCell className="text-xs font-mono">{trade.ticket}</TableCell>
                                <TableCell className="text-xs font-semibold">{trade.symbol}</TableCell>
                                <TableCell className={`text-xs font-medium ${trade.side === 'buy' ? 'text-green-600' : 'text-red-600'}`}>
                                  {trade.side.toUpperCase()}
                                </TableCell>
                                <TableCell className="text-xs tabular-nums">{trade.lots.toFixed(2)}</TableCell>
                                <TableCell className="text-xs tabular-nums">{trade.open_time}</TableCell>
                                <TableCell className="text-xs tabular-nums">{trade.close_time}</TableCell>
                                <TableCell className={`text-right text-xs font-bold tabular-nums ${trade.profit >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                                  ${trade.profit.toFixed(2)}
                                </TableCell>
                              </TableRow>
                            ))
                          ) : (
                            <TableRow>
                              <TableCell colSpan={8} className="text-center text-muted-foreground py-8">
                                该时间段内暂无交易记录
                              </TableCell>
                            </TableRow>
                          )}
                        </TableBody>
                      </Table>
                    </div>
                  </div>

                  {/* 数据说明 */}
                  <div className="mt-3 p-3 bg-amber-50 border border-amber-200 rounded-lg">
                    <div className="text-xs text-amber-800">
                      <div className="font-semibold mb-1">📊 数据说明</div>
                      <div className="space-y-1">
                        <div>• <strong>开仓时间聚合</strong>：可能因SWAPS（隔夜利息）动态调整导致与明细数据略有差异</div>
                        <div>• <strong>平仓时间聚合</strong>：数据与交易明细一致，建议用于精确分析</div>
                        <div>• <strong>利润计算</strong>：包含交易盈亏 + SWAPS，排除测试账户和挂单</div>
                      </div>
                    </div>
                  </div>
                </>
              ) : (
                <div className="text-center text-muted-foreground py-8">
                  {agg === "timeline" 
                    ? "点击上方柱状图查看对应时间段的交易明细" 
                    : "切换到时间轴模式以查看交易明细功能"
                  }
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* 右侧分析模块 - 桌面端占1/3，移动端全宽 */}
        <div className="space-y-4">
          {/* 1. 交易次数排序 */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base font-semibold">交易次数排行</CardTitle>
              <CardDescription>按用户交易笔数排序</CardDescription>
            </CardHeader>
            <CardContent>
              {hourlyDetails && tradeCountAnalysis.length > 0 ? (
                <div className="space-y-2">
                  {tradeCountAnalysis.map((item, index) => (
                    <div key={item.login} className="flex items-center justify-between py-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground w-4">#{index + 1}</span>
                        <span className="text-sm font-mono">{item.login}</span>
                      </div>
                      <span className="text-sm font-semibold">{item.count}笔</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center text-muted-foreground py-4 text-sm">
                  暂无数据
                </div>
              )}
            </CardContent>
          </Card>

          {/* 2. 利润排序 */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base font-semibold">用户利润排行</CardTitle>
              <CardDescription>包含买卖方向分析</CardDescription>
            </CardHeader>
            <CardContent>
              {hourlyDetails && profitByUserAnalysis.length > 0 ? (
                <div className="space-y-3">
                  {profitByUserAnalysis.map((item, index) => (
                    <div key={item.login} className="space-y-1">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-muted-foreground w-4">#{index + 1}</span>
                          <span className="text-sm font-mono">{item.login}</span>
                        </div>
                        <span className={`text-sm font-bold ${item.total >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                          ${item.total.toFixed(2)}
                        </span>
                      </div>
                      <div className="flex justify-between text-xs text-muted-foreground pl-6">
                        <span>买: ${item.buyProfit.toFixed(2)} ({item.buyCount}笔)</span>
                        <span>卖: ${item.sellProfit.toFixed(2)} ({item.sellCount}笔)</span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center text-muted-foreground py-4 text-sm">
                  暂无数据
                </div>
              )}
            </CardContent>
          </Card>

          {/* 3. 时间-手数相关性分析 */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base font-semibold">时间手数分析</CardTitle>
              <CardDescription>交易时间与手数相关性（测试版）</CardDescription>
            </CardHeader>
            <CardContent>
              {hourlyDetails ? (
                <div className="space-y-3">
                  <div className="text-center">
                    <div className="text-2xl font-bold tabular-nums">
                      {timeLotsCorrelation.correlation.toFixed(3)}
                    </div>
                    <div className="text-xs text-muted-foreground">相关系数</div>
                  </div>
                  <div className="text-sm text-center">
                    {timeLotsCorrelation.analysis}
                  </div>
                  <div className="text-xs text-muted-foreground text-center">
                    基于 {hourlyDetails.trades.length} 笔交易数据
                  </div>
                </div>
              ) : (
                <div className="text-center text-muted-foreground py-4 text-sm">
                  暂无数据
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

// export default function ProfitPage() {
//   return (
//     <div className="flex min-h-svh items-center justify-center text-3xl font-semibold">
//       利润分析 开发ing
//     </div>
//   )
// }


