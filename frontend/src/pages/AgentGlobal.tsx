import * as React from "react"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Calendar } from "@/components/ui/calendar"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Calendar as CalendarIcon } from "lucide-react"
import { DateRange } from "react-day-picker"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Tooltip,
  XAxis,
  YAxis,
  ResponsiveContainer,
} from "recharts"

type Row = {
  date: string
  agent_id: string
  symbol: string
  volume_adj: number
  trade_count: number
}

type TopNOption = 10 | 20 | 30 | 0 // 0 表示全部

// fresh grad: parse YYYY-MM-DD safely
function parseDateToUTC(dateStr: string): number {
  const [y, m, d] = dateStr.split("-").map((v) => parseInt(v, 10))
  return Date.UTC(y, m - 1, d, 0, 0, 0, 0)
}

// fresh grad: clamp a date to start/end of day (UTC) for inclusive filtering
function dayRangeUtc(d: Date) {
  const y = d.getFullYear()
  const m = d.getMonth()
  const day = d.getDate()
  const start = Date.UTC(y, m, day, 0, 0, 0, 0)
  const end = Date.UTC(y, m, day, 23, 59, 59, 999)
  return { start, end }
}

// chart row shapes
type ChartRowStacked = {
  agent_id: string
  s1?: number
  s2?: number
  s3?: number
  others?: number
  s1_label?: string
  s2_label?: string
  s3_label?: string
  total: number
}

type ChartRowSimple = {
  agent_id: string
  total: number
}

export default function AgentGlobalPage() {
  const [rows, setRows] = React.useState<Row[]>([])
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)

  // fresh grad: date range via single Popover + Calendar(range)
  const [range, setRange] = React.useState<DateRange | undefined>(undefined)

  // fresh grad: Top N=10 (默认)，也可 20/30/全部
  const [topN, setTopN] = React.useState<TopNOption>(10)

  // fresh grad: 统计口径：交易量/笔数
  const [metric, setMetric] = React.useState<"volume" | "count">("volume")

  // fresh grad: 产品占比开关（堆叠 Top3 + Others）：关闭/开启
  const [shareMode, setShareMode] = React.useState<"off" | "on">("off")

  // fresh grad: 加载静态 JSON（为数组）
  React.useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const res = await fetch("/agent_symbol_daily_global_202501_202508.json")
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = (await res.json()) as Row[]
        if (cancelled) return
        setRows(data)
        // fresh grad: 默认选择最近 7 天（以数据最大日期为基准）
        try {
          let minUtc = Number.POSITIVE_INFINITY
          let maxUtc = 0
          for (const r of data) {
            const ts = parseDateToUTC(r.date)
            if (ts < minUtc) minUtc = ts
            if (ts > maxUtc) maxUtc = ts
          }
          if (isFinite(minUtc) && maxUtc > 0) {
            const end = new Date(maxUtc)
            const start = new Date(Math.max(minUtc, maxUtc - 6 * 24 * 3600 * 1000))
            setRange({ from: start, to: end })
          }
        } catch {}
      } catch (e: any) {
        if (!cancelled) setError(e?.message || "加载失败")
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [])

  // fresh grad: 过滤出选中日期范围内的行（包含端点）
  const filtered = React.useMemo(() => {
    if (!range?.from || !range?.to) return rows
    const { start: startUtc } = dayRangeUtc(range.from)
    const { end: endUtc } = dayRangeUtc(range.to)
    let lo = startUtc
    let hi = endUtc
    if (lo > hi) [lo, hi] = [hi, lo]
    return rows.filter((r) => {
      const ts = parseDateToUTC(r.date)
      return ts >= lo && ts <= hi
    })
  }, [rows, range])

  // fresh grad: 计算每个代理的总交易量与按产品的分布
  const prepared = React.useMemo(() => {
    // 1) 汇总到 agent 级别
    const perAgent = new Map<string, { total: number; symbols: Map<string, number> }>()
    for (const r of filtered) {
      const key = r.agent_id || ""
      if (!perAgent.has(key)) perAgent.set(key, { total: 0, symbols: new Map() })
      const entry = perAgent.get(key)!
      const val = metric === "volume" ? Number(r.volume_adj || 0) : Number(r.trade_count || 0)
      entry.total += val
      if (r.symbol) {
        entry.symbols.set(r.symbol, (entry.symbols.get(r.symbol) ?? 0) + val)
      }
    }

    // 2) 按总量降序
    const arr: { agent_id: string; total: number; symbols: Map<string, number> }[] = []
    for (const [agent_id, { total, symbols }] of perAgent.entries()) {
      arr.push({ agent_id, total, symbols })
    }
    arr.sort((a, b) => b.total - a.total)

    // 3) 截断 TopN（0 表示全部）
    const maxCount = topN === 0 ? arr.length : topN
    const top = arr.slice(0, maxCount)

    if (shareMode === "off") {
      // 简单柱状：仅 total
      const simple: ChartRowSimple[] = top.map((x) => ({ agent_id: x.agent_id, total: x.total }))
      return { kind: "simple" as const, data: simple }
    }

    // 堆叠：每个代理取 symbol Top3，其余合并到 others
    const stacked: ChartRowStacked[] = []
    for (const it of top) {
      const sortedSymbols = Array.from(it.symbols.entries()).sort((a, b) => b[1] - a[1])
      const s1 = sortedSymbols[0]
      const s2 = sortedSymbols[1]
      const s3 = sortedSymbols[2]
      const others = sortedSymbols.slice(3).reduce((sum, [, v]) => sum + v, 0)
      stacked.push({
        agent_id: it.agent_id,
        s1: s1 ? s1[1] : 0,
        s2: s2 ? s2[1] : 0,
        s3: s3 ? s3[1] : 0,
        others: others || 0,
        s1_label: s1 ? s1[0] : undefined,
        s2_label: s2 ? s2[0] : undefined,
        s3_label: s3 ? s3[0] : undefined,
        total: it.total,
      })
    }
    return { kind: "stacked" as const, data: stacked }
  }, [filtered, topN, metric, shareMode])

  // fresh grad: 日期范围标签（英文短月，便于紧凑显示）
  const rangeLabel = React.useMemo(() => {
    if (!range?.from || !range?.to) return "选择日期范围"
    const opts: Intl.DateTimeFormatOptions = { month: "short", day: "2-digit", year: "numeric" }
    return `${range.from.toLocaleDateString("en-US", opts)} - ${range.to.toLocaleDateString("en-US", opts)}`
  }, [range])

  return (
    <div className="space-y-4 px-4 pb-6 lg:px-6">
      {/* 上卡片：筛选与视图 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-2xl font-bold">筛选与视图</CardTitle>
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

          {/* Top N 选择 */}
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">Top N</span>
            <Select
              value={String(topN)}
              onValueChange={(v) => setTopN(Number(v) as TopNOption)}
            >
              <SelectTrigger className="w-[160px]">
                <SelectValue placeholder="选择 N" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="10">10</SelectItem>
                <SelectItem value="20">20</SelectItem>
                <SelectItem value="30">30</SelectItem>
                <SelectItem value="0">全部</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* 统计口径（胶囊式等宽切换） */}
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">统计口径</span>
            <ToggleGroup
              type="single"
              value={metric}
              onValueChange={(v) => v && setMetric(v as "volume" | "count")}
              className="inline-flex w-[240px] items-center rounded-full bg-muted p-1"
            >
              <ToggleGroupItem
                value="volume"
                className="flex-1 rounded-full first:rounded-l-full last:rounded-r-full px-3 py-1 text-center text-sm text-muted-foreground data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow"
              >
                交易量
              </ToggleGroupItem>
              <ToggleGroupItem
                value="count"
                className="flex-1 rounded-full first:rounded-l-full last:rounded-r-full px-3 py-1 text-center text-sm text-muted-foreground data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow"
              >
                笔数
              </ToggleGroupItem>
            </ToggleGroup>
          </div>

          {/* 产品占比（胶囊式开关） */}
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">产品占比</span>
            <ToggleGroup
              type="single"
              value={shareMode}
              onValueChange={(v) => v && setShareMode(v as "off" | "on")}
              className="inline-flex w-[240px] items-center rounded-full bg-muted p-1"
            >
              <ToggleGroupItem
                value="off"
                className="flex-1 rounded-full first:rounded-l-full last:rounded-r-full px-3 py-1 text-center text-sm text-muted-foreground data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow"
              >
                关闭
              </ToggleGroupItem>
              <ToggleGroupItem
                value="on"
                className="flex-1 rounded-full first:rounded-l-full last:rounded-r-full px-3 py-1 text-center text-sm text-muted-foreground data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow"
              >
                开启
              </ToggleGroupItem>
            </ToggleGroup>
          </div>
        </CardContent>
      </Card>

      {/* 下卡片：图表 */}
      <Card>
        <CardContent className="pt-6">
          {loading ? (
            <div className="text-sm text-muted-foreground px-2 py-8">Loading…</div>
          ) : error ? (
            <div className="text-sm text-red-600 px-2 py-8">{error}</div>
          ) : (
            <div className="w-full h-[510px] sm:h-[630px]">
              <ResponsiveContainer width="100%" height="100%">
                {prepared.kind === "simple" ? (
                  <BarChart data={prepared.data as ChartRowSimple[]}>
                    <CartesianGrid vertical={false} />
                    <XAxis dataKey="agent_id" tickMargin={8} minTickGap={12} tick={{ fontSize: 10 }} angle={-20} textAnchor="end" />
                    <YAxis tickFormatter={(v) => new Intl.NumberFormat().format(v)} tick={{ fontSize: 10 }} />
                    <Tooltip
                      formatter={(value: number) => new Intl.NumberFormat().format(value)}
                      labelFormatter={(label: string) => `Agent ${label}`}
                    />
                    <Bar dataKey="total" name={metric === "volume" ? "总交易量" : "总笔数"} fill="var(--primary)" radius={[6, 6, 0, 0]} />
                  </BarChart>
                ) : (
                  <BarChart data={prepared.data as ChartRowStacked[]}>
                    <CartesianGrid vertical={false} />
                    <XAxis dataKey="agent_id" tickMargin={8} minTickGap={12} tick={{ fontSize: 10 }} angle={-20} textAnchor="end" />
                    <YAxis tickFormatter={(v) => new Intl.NumberFormat().format(v)} tick={{ fontSize: 10 }} />
                    <Tooltip
                      formatter={(value: number, name: string, props: any) => {
                        const p = props?.payload as ChartRowStacked
                        const labelMap: Record<string, string | undefined> = {
                          s1: p.s1_label,
                          s2: p.s2_label,
                          s3: p.s3_label,
                          others: "Others",
                        }
                        const seriesName = labelMap[name] || name
                        return [new Intl.NumberFormat().format(Number(value || 0)), seriesName]
                      }}
                      labelFormatter={(label: string) => `Agent ${label}`}
                    />
                    <Bar dataKey="s1" stackId="a" fill="#4f46e5" />
                    <Bar dataKey="s2" stackId="a" fill="#06b6d4" />
                    <Bar dataKey="s3" stackId="a" fill="#22c55e" />
                    <Bar dataKey="others" stackId="a" fill="#a1a1aa" />
                  </BarChart>
                )}
              </ResponsiveContainer>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}


