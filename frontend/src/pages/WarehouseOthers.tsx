import * as React from "react"

import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Loader2 } from "lucide-react"

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

export default function WarehouseOthersPage() {
  const [items, setItems] = React.useState<OpenPositionsItem[] | null>(null)
  const [loading, setLoading] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = React.useState<Date | null>(null)

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
    } catch (e: any) {
      setError(e?.message || "请求失败")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4 px-4 pb-6 lg:px-6">
      {/* Toolbar Card：仅居中刷新按钮 + 状态显示 */}
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-6">
          <Button className="h-9 w-[120px] gap-2" onClick={onRefresh} disabled={loading}>
            {loading && <Loader2 className="h-4 w-4 animate-spin" />}
            刷新
          </Button>
          <div className="flex items-center gap-3 text-sm">
            {error && <span className="text-red-600">{error}</span>}
            {lastUpdated && <Badge variant="outline">上次刷新：{lastUpdated.toLocaleString("zh-CN", { hour12: false })}</Badge>}
            {items && !error && <span className="text-muted-foreground">记录数：{items.length}</span>}
          </div>
        </CardContent>
      </Card>

      {/* 数据表格 */}
      <Card>
        <CardContent className="pt-6">
          <div className="mx-auto w-full max-w-[1280px]">
            <div className="overflow-hidden rounded-md border-2 shadow-md">
              <Table className="min-w-[780px]">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[200px] align-middle border-r font-semibold text-base">产品</TableHead>
                    <TableHead className="text-right border-r font-semibold text-base">Volume (Buy)</TableHead>
                    <TableHead className="text-right border-r font-semibold text-base">Volume (Sell)</TableHead>
                    <TableHead className="text-right border-r font-semibold text-base">Profit (Buy)</TableHead>
                    <TableHead className="text-right border-r font-semibold text-base">Profit (Sell)</TableHead>
                    <TableHead className="text-right font-semibold text-base">Profit (Total)</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(items ?? []).map((it) => (
                    <TableRow key={it.symbol}>
                      <TableCell className="border-r font-medium">{it.symbol}</TableCell>
                      <TableCell className="text-right tabular-nums border-r">{format2(it.volume_buy)}</TableCell>
                      <TableCell className="text-right tabular-nums border-r">{format2(it.volume_sell)}</TableCell>
                      <TableCell className={`text-right tabular-nums border-r ${profitClass(it.profit_buy)}`}>{format2(it.profit_buy)}</TableCell>
                      <TableCell className={`text-right tabular-nums border-r ${profitClass(it.profit_sell)}`}>{format2(it.profit_sell)}</TableCell>
                      <TableCell className={`text-right tabular-nums ${profitClass(it.profit_total)}`}>{format2(it.profit_total)}</TableCell>
                    </TableRow>
                  ))}
                  {(!items || items.length === 0) && !loading && (
                    <TableRow>
                      <TableCell colSpan={6} className="text-center text-sm text-muted-foreground py-8">
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
