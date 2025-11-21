"use client"

import * as React from "react"
import { Calendar as CalendarIcon } from "lucide-react"
import type { DateRange } from "react-day-picker"
import { Area, AreaChart, Bar, BarChart, CartesianGrid, XAxis } from "recharts"

import { useTheme } from "@/components/theme-provider"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Input } from "@/components/ui/input"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Calendar } from "@/components/ui/calendar"
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart"

// fresh grad: this page is a static equity risk dashboard mock.
// Later you can replace the mocked chart data with real API data that comes from your SQL.

// fresh grad: demo data – structure is similar to what you will get after aggregating mt5_daily.
const mockEquitySeries = [
  { date: "2025-11-01", equityClean: 10000, tradingPnl: 0, cashCreditFlow: 0 },
  { date: "2025-11-02", equityClean: 10150, tradingPnl: 150, cashCreditFlow: 0 },
  { date: "2025-11-03", equityClean: 10320, tradingPnl: 170, cashCreditFlow: 0 },
  { date: "2025-11-04", equityClean: 10240, tradingPnl: -80, cashCreditFlow: 0 },
  { date: "2025-11-05", equityClean: 10480, tradingPnl: 240, cashCreditFlow: 0 },
  { date: "2025-11-06", equityClean: 10610, tradingPnl: 130, cashCreditFlow: 0 },
  { date: "2025-11-07", equityClean: 10890, tradingPnl: 280, cashCreditFlow: 0 },
  { date: "2025-11-08", equityClean: 11010, tradingPnl: 120, cashCreditFlow: 0 },
  { date: "2025-11-09", equityClean: 11180, tradingPnl: 170, cashCreditFlow: 0 },
  { date: "2025-11-10", equityClean: 11220, tradingPnl: 40, cashCreditFlow: 0 },
] satisfies {
  date: string
  equityClean: number
  tradingPnl: number
  cashCreditFlow: number
}[]

// fresh grad: simple helper to format date label as "MM-DD".
function formatShortDateLabel(value: string) {
  const d = new Date(value)
  const month = `${d.getMonth() + 1}`.padStart(2, "0")
  const day = `${d.getDate()}`.padStart(2, "0")
  return `${month}-${day}`
}

export default function EquityMonitorPage() {
  const { theme } = useTheme()
  // filter states are local only – connect them to API query when backend is ready.
  const [server, setServer] = React.useState<string>("mt5-live")
  const [accountId, setAccountId] = React.useState<string>("")
  const [dateRange, setDateRange] = React.useState<DateRange | undefined>(() => {
    const to = new Date()
    const from = new Date()
    from.setDate(from.getDate() - 30)
    return { from, to }
  })

  // fresh grad: in the future, you can use these filters to fetch data with useEffect().

  // fresh grad: we keep summary stats pre-computed for a static page.
  const latestPoint = mockEquitySeries[mockEquitySeries.length - 1]
  const firstPoint = mockEquitySeries[0]
  const equityChange = latestPoint.equityClean - firstPoint.equityClean
  const equityChangePct = firstPoint.equityClean === 0 ? 0 : (equityChange / firstPoint.equityClean) * 100

  const isDarkMode = React.useMemo(() => {
    if (theme === "dark") return true
    if (theme === "light") return false
    if (typeof window === "undefined") return false
    try {
      return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
    } catch {
      return false
    }
  }, [theme])

  const chartPalette = React.useMemo(
    () => ({
      equity: isDarkMode ? "#4ade80" : "#2563eb",
      trading: isDarkMode ? "#f97316" : "#16a34a",
      cashFlow: isDarkMode ? "#eab308" : "#f97316",
    }),
    [isDarkMode],
  )

  const equityChartConfig = React.useMemo(
    () =>
      ({
        equityClean: {
          label: "Equity (clean, without cash & credit flows)",
          color: chartPalette.equity,
        },
      }) satisfies ChartConfig,
    [chartPalette],
  )

  const pnlChartConfig = React.useMemo(
    () =>
      ({
        tradingPnl: {
          label: "Daily trading PnL",
          color: chartPalette.trading,
        },
        cashCreditFlow: {
          label: "Daily cash & credit flow",
          color: chartPalette.cashFlow,
        },
      }) satisfies ChartConfig,
    [chartPalette],
  )

  const rangeLabel = React.useMemo(() => {
    if (!dateRange?.from || !dateRange.to) return "Select date range"
    const opts: Intl.DateTimeFormatOptions = { month: "short", day: "2-digit", year: "numeric" }
    return `${dateRange.from.toLocaleDateString("en-US", opts)} - ${dateRange.to.toLocaleDateString("en-US", opts)}`
  }, [dateRange])

  return (
    <div className="space-y-4 px-1 pb-6 sm:px-4 lg:px-6">
      {/* Top-level filters: server / account / time range */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base font-semibold">Equity Monitor – Filters</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-4 lg:gap-6">
            {/* Server selector */}
            <div className="flex w-full flex-col gap-1 sm:w-auto sm:flex-row sm:items-center sm:gap-2">
              <span className="w-28 text-sm text-muted-foreground">Server</span>
              <Select value={server} onValueChange={setServer}>
                <SelectTrigger className="h-9 w-full sm:w-48">
                  <SelectValue placeholder="Select server" />
                </SelectTrigger>
                <SelectContent>
                  {/* fresh grad: options are static here – later you can load from backend config. */}
                  <SelectItem value="mt5-live">MT5 Live</SelectItem>
                  <SelectItem value="mt5-demo">MT5 Demo</SelectItem>
                  <SelectItem value="mt4-live2">MT4 Live 2</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Account Id input */}
            <div className="flex w-full flex-col gap-1 sm:w-auto sm:flex-row sm:items-center sm:gap-2">
              <span className="w-28 text-sm text-muted-foreground">Account ID</span>
              <Input
                value={accountId}
                onChange={(e) => setAccountId(e.target.value)}
                placeholder="Enter account login"
                className="h-9 w-full sm:w-48"
              />
            </div>

            {/* Time range selector */}
            <div className="flex w-full flex-col gap-1 sm:w-auto sm:flex-row sm:items-center sm:gap-2">
              <span className="w-28 text-sm text-muted-foreground">Time Range</span>
              <Popover>
                <PopoverTrigger asChild>
                  <Button variant="outline" className="h-9 w-full justify-start gap-2 sm:w-64">
                    <CalendarIcon className="h-4 w-4 text-muted-foreground" />
                    <span className="truncate text-left text-sm font-normal">{rangeLabel}</span>
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-0" align="start">
                  <Calendar
                    mode="range"
                    selected={dateRange}
                    onSelect={setDateRange}
                    numberOfMonths={2}
                    initialFocus
                  />
                </PopoverContent>
              </Popover>
            </div>
          </div>

          <div className="rounded-lg border border-dashed border-muted-foreground/30 bg-muted/20 px-4 py-2 text-xs text-muted-foreground">
            {/* fresh grad: this text explains what the dashboard is trying to show. */}
            Equity in these widgets is already cleaned: we remove net deposits and credit swings so risk teams only see
            pure trading performance.
          </div>
        </CardContent>
      </Card>

      {/* Main 2x2 dashboard grid – responsive: stack on mobile, 2x2 on desktop */}
      <div className="grid gap-4 lg:grid-cols-2">
        {/* Top-left: clean equity curve */}
        <Card className="@container">
          <CardHeader>
            <CardTitle className="text-base font-semibold">
              Clean Equity Curve (exclude net deposit & credit changes)
            </CardTitle>
          </CardHeader>
          <CardContent className="px-2 pb-4 pt-2 sm:px-4 sm:pt-4">
            <ChartContainer config={equityChartConfig} className="aspect-video w-full">
              <AreaChart data={mockEquitySeries}>
                <defs>
                  <linearGradient id="equityCleanFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="var(--color-equityClean)" stopOpacity={0.9} />
                    <stop offset="95%" stopColor="var(--color-equityClean)" stopOpacity={0.1} />
                  </linearGradient>
                </defs>
                <CartesianGrid vertical={false} />
                <XAxis
                  dataKey="date"
                  tickLine={false}
                  axisLine={false}
                  tickMargin={8}
                  minTickGap={24}
                  tickFormatter={formatShortDateLabel}
                />
                <ChartTooltip
                  content={
                    <ChartTooltipContent
                      indicator="dot"
                      labelFormatter={(value) =>
                        new Date(value as string).toLocaleDateString("en-GB", {
                          month: "short",
                          day: "numeric",
                        })
                      }
                    />
                  }
                />
                <Area
                  dataKey="equityClean"
                  type="monotone"
                  fill="url(#equityCleanFill)"
                  stroke="var(--color-equityClean)"
                  strokeWidth={1.6}
                />
              </AreaChart>
            </ChartContainer>
          </CardContent>
        </Card>

        {/* Top-right: daily trading PnL vs cash/credit flows */}
        <Card className="@container">
          <CardHeader>
            <CardTitle className="text-base font-semibold">
              Daily Trading PnL vs Cash & Credit Flows
            </CardTitle>
          </CardHeader>
          <CardContent className="px-2 pb-4 pt-2 sm:px-4 sm:pt-4">
            <ChartContainer config={pnlChartConfig} className="aspect-video w-full">
              <BarChart data={mockEquitySeries}>
                <CartesianGrid vertical={false} />
                <XAxis
                  dataKey="date"
                  tickLine={false}
                  axisLine={false}
                  tickMargin={8}
                  minTickGap={24}
                  tickFormatter={formatShortDateLabel}
                />
                <ChartTooltip
                  content={
                    <ChartTooltipContent
                      indicator="dot"
                      labelFormatter={(value) =>
                        new Date(value as string).toLocaleDateString("en-GB", {
                          month: "short",
                          day: "numeric",
                        })
                      }
                    />
                  }
                />
                {/* fresh grad: stack bars to compare trading result and funding in the same day. */}
                <Bar
                  dataKey="tradingPnl"
                  name="Trading PnL"
                  fill="var(--color-tradingPnl)"
                  radius={4}
                />
                <Bar
                  dataKey="cashCreditFlow"
                  name="Cash & Credit Flow"
                  fill="var(--color-cashCreditFlow)"
                  radius={4}
                />
              </BarChart>
            </ChartContainer>
          </CardContent>
        </Card>

        {/* Bottom-left: simple KPI summary for the selected period */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-semibold">Key KPIs (Clean Equity)</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <div className="space-y-1">
              <div className="text-xs text-muted-foreground">Start Equity (clean)</div>
              <div className="text-xl font-semibold">
                {firstPoint.equityClean.toLocaleString(undefined, {
                  maximumFractionDigits: 0,
                })}
              </div>
            </div>
            <div className="space-y-1">
              <div className="text-xs text-muted-foreground">End Equity (clean)</div>
              <div className="text-xl font-semibold">
                {latestPoint.equityClean.toLocaleString(undefined, {
                  maximumFractionDigits: 0,
                })}
              </div>
            </div>
            <div className="space-y-1">
              <div className="text-xs text-muted-foreground">Change</div>
              <div className="text-xl font-semibold">
                {equityChange >= 0 ? "+" : "-"}
                {Math.abs(equityChange).toLocaleString(undefined, {
                  maximumFractionDigits: 0,
                })}
              </div>
              <div className="text-xs text-muted-foreground">
                {equityChangePct >= 0 ? "+" : "-"}
                {Math.abs(equityChangePct).toFixed(2)}%
              </div>
            </div>
            <div className="space-y-1">
              <div className="text-xs text-muted-foreground">Max Clean Equity</div>
              <div className="text-xl font-semibold">
                {Math.max(...mockEquitySeries.map((p) => p.equityClean)).toLocaleString(undefined, {
                  maximumFractionDigits: 0,
                })}
              </div>
            </div>
            <div className="space-y-1">
              <div className="text-xs text-muted-foreground">Min Clean Equity</div>
              <div className="text-xl font-semibold">
                {Math.min(...mockEquitySeries.map((p) => p.equityClean)).toLocaleString(undefined, {
                  maximumFractionDigits: 0,
                })}
              </div>
            </div>
            <div className="space-y-1">
              <div className="text-xs text-muted-foreground">Observation Days</div>
              <div className="text-xl font-semibold">{mockEquitySeries.length}</div>
            </div>
          </CardContent>
        </Card>

        {/* Bottom-right: risk notes for the team */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-semibold">Risk Notes (Static Example)</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-xs text-muted-foreground">
            <p>
              For real data, risk team can focus on accounts where clean equity grows fast while cash & credit flows
              are small. This usually indicates strong trading performance rather than capital injection.
            </p>
            <p>
              Accounts with large negative trading PnL and continuous net deposits might be trying to recover losses and
              could deserve additional attention.
            </p>
            <p>
              Later you can add thresholds (for example, 30-day clean equity drawdown &gt; 20%) and trigger alerts in
              this area.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
