import { useMemo } from "react"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

type RiskLevel = "LOW" | "MEDIUM" | "HIGH"

interface CustomerPnLRow {
  customerId: string
  customerName: string
  pnlToday: number
  pnlMonth: number
  pnlTotal: number
  position: number
  riskLevel: RiskLevel
  updatedAt: string
}

function formatCurrency(value: number) {
  const sign = value >= 0 ? "" : "-"
  const abs = Math.abs(value)
  return `${sign}$${abs.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
}

export default function CustomerPnLMonitor() {
  // fresh grad note: data would come from API; using static demo rows for UI scaffold
  const rows = useMemo<CustomerPnLRow[]>(() => {
    const now = new Date()
    return Array.from({ length: 24 }).map((_, i) => {
      const sign = i % 3 === 0 ? -1 : 1
      const base = (i + 1) * 123.45
      const risk: RiskLevel = i % 7 === 0 ? "HIGH" : i % 3 === 0 ? "MEDIUM" : "LOW"
      return {
        customerId: `C${(100000 + i).toString()}`,
        customerName: `客户-${i + 1}`,
        pnlToday: sign * base,
        pnlMonth: sign * base * 3,
        pnlTotal: sign * base * 12,
        position: Math.round((i + 1) * 5.2),
        riskLevel: risk,
        updatedAt: new Date(now.getTime() - i * 1000 * 60 * 13).toLocaleString(),
      }
    })
  }, [])

  return (
    <div className="flex h-full w-full flex-col gap-2 p-1 sm:p-4">
      {/* fresh grad note: full-height scroll area with single scroll container for table */}
      <div className="border rounded-md overflow-hidden flex-1">
        <div className="overflow-auto h-full">
          <Table className="min-w-[960px]">
            <TableHeader className="sticky top-0 z-10 bg-background">
              <TableRow>
                <TableHead className="whitespace-nowrap">客户ID</TableHead>
                <TableHead className="whitespace-nowrap">客户名称</TableHead>
                <TableHead className="whitespace-nowrap text-right">当日盈亏</TableHead>
                <TableHead className="whitespace-nowrap text-right">当月盈亏</TableHead>
                <TableHead className="whitespace-nowrap text-right">总盈亏</TableHead>
                <TableHead className="whitespace-nowrap text-right">总仓位</TableHead>
                <TableHead className="whitespace-nowrap">风险等级</TableHead>
                <TableHead className="whitespace-nowrap">更新时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.customerId}>
                  <TableCell>{r.customerId}</TableCell>
                  <TableCell className="max-w-[220px] truncate">{r.customerName}</TableCell>
                  <TableCell className={`${r.pnlToday < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"} text-right font-medium`}>
                    {formatCurrency(r.pnlToday)}
                  </TableCell>
                  <TableCell className={`${r.pnlMonth < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"} text-right`}>
                    {formatCurrency(r.pnlMonth)}
                  </TableCell>
                  <TableCell className={`${r.pnlTotal < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"} text-right`}>
                    {formatCurrency(r.pnlTotal)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{r.position.toLocaleString()}</TableCell>
                  <TableCell>
                    <span
                      className={
                        r.riskLevel === "HIGH"
                          ? "inline-flex items-center rounded px-2 py-0.5 text-xs font-medium bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-200"
                          : r.riskLevel === "MEDIUM"
                          ? "inline-flex items-center rounded px-2 py-0.5 text-xs font-medium bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-200"
                          : "inline-flex items-center rounded px-2 py-0.5 text-xs font-medium bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-200"
                      }
                    >
                      {r.riskLevel}
                    </span>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">{r.updatedAt}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </div>
    </div>
  )
}


