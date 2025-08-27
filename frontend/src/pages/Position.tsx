import * as React from "react"

import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ArrowUpDown, ArrowDownRight, ArrowUpRight, DollarSign, TrendingUp } from "lucide-react"
import { ColumnDef, SortingState, flexRender, getCoreRowModel, getSortedRowModel, useReactTable } from "@tanstack/react-table"

type OpenPositionsItem = {
  symbol: string
  volume_buy: number
  volume_sell: number
  profit_buy: number
  profit_sell: number
  profit_total: number
}

type OpenPositionsResp = { ok: boolean; items: OpenPositionsItem[]; error: string | null }

function format2(n: number): string {
  return Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function profitClass(n: number): string {
  if (n > 0) return "text-green-600 dark:text-green-400"
  if (n < 0) return "text-red-600 dark:text-red-400"
  return "text-foreground"
}

function StatCard({
  title,
  value,
  positive,
  prefix,
  icon: Icon = DollarSign,
  variant = "neutral",
}: {
  title: string
  value: string
  positive: boolean
  prefix?: string
  icon?: React.ComponentType<React.SVGProps<SVGSVGElement>>
  variant?: "neutral" | "profit"
}) {
  const isProfit = variant === "profit"
  const iconBoxClass = isProfit
    ? positive
      ? "bg-green-100 text-green-600 dark:bg-green-900/30 dark:text-green-400"
      : "bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400"
    : "bg-primary/10 text-primary"
  const valueClass = isProfit
    ? positive
      ? "text-green-600 dark:text-green-400"
      : "text-red-600 dark:text-red-400"
    : "text-foreground"
  return (
    <Card className="bg-gradient-to-b from-gray-50 to-gray-100 dark:from-zinc-900 dark:to-zinc-800 shadow-md border border-black/5 dark:border-white/10">
      <CardContent className="flex items-start gap-3 p-4">
        <div className={`rounded-xl p-2 ${iconBoxClass}`}>
          <Icon className="h-5 w-5" />
        </div>
        <div className="flex-1">
          <div className="text-sm text-muted-foreground">{title}</div>
          <div className={`mt-1 text-2xl font-semibold tabular-nums ${valueClass}`}>{prefix}{value}</div>
          <div className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
            {positive ? (
              <ArrowUpRight className="h-3.5 w-3.5 text-green-600" />
            ) : (
              <ArrowDownRight className="h-3.5 w-3.5 text-red-600" />
            )}
            <span>{positive ? "Positive" : "Negative"}</span>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export default function PositionPage() {
  const [items, setItems] = React.useState<OpenPositionsItem[]>([])
  const [loading, setLoading] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = React.useState<Date | null>(null)
  const [sorting, setSorting] = React.useState<SortingState>([
    { id: "profit_total", desc: false },
  ])

  async function fetchOpenPositions(signal?: AbortSignal) {
    const res = await fetch("/api/v1/open-positions/today", { method: "GET", headers: { accept: "application/json" }, signal })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const json = (await res.json()) as OpenPositionsResp
    if (!json.ok) throw new Error(json.error || "unknown error")
    return json.items
  }

  async function onRefresh() {
    setError(null)
    setLoading(true)
    try {
      const data = await fetchOpenPositions()
      setItems(data)
      setLastUpdated(new Date())
      try {
        sessionStorage.setItem("position_items", JSON.stringify(data))
        sessionStorage.setItem("position_lastUpdated", String(Date.now()))
      } catch {}
    } catch (e: any) {
      setError(e?.message || "请求失败")
    } finally {
      setLoading(false)
    }
  }

  const totals = React.useMemo(() => {
    const sum = {
      volume_buy: 0,
      volume_sell: 0,
      profit_buy: 0,
      profit_sell: 0,
      profit_total: 0,
    }
    for (const it of items) {
      sum.volume_buy += it.volume_buy || 0
      sum.volume_sell += it.volume_sell || 0
      sum.profit_buy += it.profit_buy || 0
      sum.profit_sell += it.profit_sell || 0
      sum.profit_total += it.profit_total || 0
    }
    return sum
  }, [items])

  const columns = React.useMemo<ColumnDef<OpenPositionsItem>[]>(
    () => [
      {
        accessorKey: "symbol",
        header: "产品",
        enableSorting: false,
        cell: ({ row }) => <span className="font-medium">{row.original.symbol}</span>,
      },
      {
        header: "Volume",
        columns: [
          {
            accessorKey: "volume_buy",
            header: ({ column }) => (
              <Button variant="ghost" className="px-0" onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}>Buy<ArrowUpDown className="ml-2 h-4 w-4" /></Button>
            ),
            cell: ({ row }) => <div className="text-right tabular-nums">{format2(row.original.volume_buy)}</div>,
          },
          {
            accessorKey: "volume_sell",
            header: ({ column }) => (
              <Button variant="ghost" className="px-0" onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}>Sell<ArrowUpDown className="ml-2 h-4 w-4" /></Button>
            ),
            cell: ({ row }) => <div className="text-right tabular-nums">{format2(row.original.volume_sell)}</div>,
          },
        ],
      },
      {
        header: "Profit",
        columns: [
          {
            accessorKey: "profit_buy",
            header: ({ column }) => (
              <Button variant="ghost" className="px-0" onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}>Buy<ArrowUpDown className="ml-2 h-4 w-4" /></Button>
            ),
            cell: ({ row }) => <div className={`text-right tabular-nums ${profitClass(row.original.profit_buy)}`}>{format2(row.original.profit_buy)}</div>,
          },
          {
            accessorKey: "profit_sell",
            header: ({ column }) => (
              <Button variant="ghost" className="px-0" onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}>Sell<ArrowUpDown className="ml-2 h-4 w-4" /></Button>
            ),
            cell: ({ row }) => <div className={`text-right tabular-nums ${profitClass(row.original.profit_sell)}`}>{format2(row.original.profit_sell)}</div>,
          },
          {
            accessorKey: "profit_total",
            header: ({ column }) => (
              <Button variant="ghost" className="px-0" onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}>Total<ArrowUpDown className="ml-2 h-4 w-4" /></Button>
            ),
            cell: ({ row }) => <div className={`text-right tabular-nums ${profitClass(row.original.profit_total)}`}>{format2(row.original.profit_total)}</div>,
          },
        ],
      },
    ],
    []
  )

  const table = useReactTable({
    data: items,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  React.useEffect(() => {
    try {
      const raw = sessionStorage.getItem("position_items")
      if (raw) {
        const parsed = JSON.parse(raw) as OpenPositionsItem[]
        setItems(parsed)
      }
      const ts = sessionStorage.getItem("position_lastUpdated")
      if (ts) setLastUpdated(new Date(Number(ts)))
    } catch {}
  }, [])

  return (
    <div className="relative space-y-4 px-4 pb-6 lg:px-6">
      {/* 顶部统计卡片（5个） */}
      <div className="grid grid-cols-1 gap-3 pt-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard title="Volume Buy" value={format2(totals.volume_buy)} positive={totals.volume_buy >= 0} icon={TrendingUp} variant="neutral" />
        <StatCard title="Volume Sell" value={format2(totals.volume_sell)} positive={totals.volume_sell >= 0} icon={TrendingUp} variant="neutral" />
        <StatCard title="Profit Buy" value={format2(totals.profit_buy)} positive={totals.profit_buy >= 0} variant="profit" />
        <StatCard title="Profit Sell" value={format2(totals.profit_sell)} positive={totals.profit_sell >= 0} variant="profit" />
        <StatCard title="Profit Total" value={format2(totals.profit_total)} positive={totals.profit_total >= 0} variant="profit" />
      </div>

      {/* Toolbar：仅居中刷新 + 状态显示 */}
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-6">
          <Button className="h-9 w-[120px] gap-2" onClick={onRefresh} disabled={loading}>
            {loading && <ArrowUpDown className="h-4 w-4 animate-spin" />}
            刷新
          </Button>
          <div className="flex items-center gap-3 text-sm">
            {error && <span className="text-red-600">{error}</span>}
            {lastUpdated && <Badge variant="outline">上次刷新：{lastUpdated.toLocaleString("zh-CN", { hour12: false })}</Badge>}
            {items && !error && <span className="text-muted-foreground">记录数：{items.length}</span>}
          </div>
        </CardContent>
      </Card>

      {/* 表格：两行表头，默认按 Profit Total 升序 */}
      <Card>
        <CardContent className="pt-6">
          <div className="mx-auto w-full max-w-[1280px]">
            <div className="overflow-hidden rounded-md border-2 shadow-md">
              <Table className="min-w-[860px]">
                <TableHeader>
                  {table.getHeaderGroups().map((headerGroup) => (
                    <TableRow key={headerGroup.id}>
                      {headerGroup.headers.map((header) => (
                        <TableHead
                          key={header.id}
                          colSpan={header.colSpan}
                          className={`align-middle ${header.colSpan > 1 ? "text-center" : ""}`}
                        >
                          {header.isPlaceholder ? null : (
                            <div
                              className={`flex ${
                                header.colSpan > 1
                                  ? "justify-center"
                                  : ""
                              } ${typeof header.column.columnDef.header === "string" ? "font-semibold text-base" : ""}`}
                            >
                              {flexRender(header.column.columnDef.header, header.getContext())}
                            </div>
                          )}
                        </TableHead>
                      ))}
                    </TableRow>
                  ))}
                </TableHeader>
                <TableBody>
                  {table.getRowModel().rows.length ? (
                    table.getRowModel().rows.map((row) => (
                      <TableRow key={row.id} className="odd:bg-muted/30 dark:odd:bg-muted/10">
                        {row.getVisibleCells().map((cell) => (
                          <TableCell key={cell.id} className="align-middle">
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))
                  ) : (
                    <TableRow>
                      <TableCell colSpan={columns.length} className="py-8 text-center text-sm text-muted-foreground">
                        点击上方“刷新”加载数据
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
  )
}



