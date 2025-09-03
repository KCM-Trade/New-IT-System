import { useEffect, useMemo, useRef, useState } from "react"
import {
  Card,
  CardContent,
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
import { Calendar as CalendarIcon } from "lucide-react"
import { DateRange } from "react-day-picker"
// removed Input/Separator from old custom range UI

type ProfitRow = {
  date: string // e.g. "2025-05-01"
  hour: number // 0-23 (source timezone: UTC+3)
  profit: number
}

type AggKey = "timeline" | "hourOfDay"
type TzKey = "+3" | "+8"
type AggTypeKey = "open" | "close"

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

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < 640) // 640px ~ tailwind sm breakpoint
    onResize()
    window.addEventListener("resize", onResize)
    return () => window.removeEventListener("resize", onResize)
  }, [])

  // removed: custom range text inputs and history

  // removed: history persistence and input sync

  // removed: custom input apply handler

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
          <CardTitle className="text-2xl font-bold">筛选与视图（XAU-CNH）</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 md:flex-row md:items-center md:gap-6">
          {/* 时间范围（单按钮 + Range 日历） */}
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
          {/* 聚合类型（与聚合维度采用一致风格与宽度） */}
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">聚合类型</span>
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
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">聚合维度</span>
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
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">时区</span>
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
                    <Bar dataKey="profit" fill="var(--primary)" />
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


