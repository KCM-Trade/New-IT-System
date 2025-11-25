import { useMemo, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Calendar } from "@/components/ui/calendar"
import { Input } from "@/components/ui/input"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { cn } from "@/lib/utils"
import { type DateRange } from "react-day-picker"

// default combos nudge fresh grads to surface typical batch queries
const defaultIBGroups = [{ label: "重点组合", ids: ["107779", "129860"] }]

const mockSummary = [
  { label: "Deposit (USD)", value: "1,254,800.00" },
  { label: "Total Withdrawal (USD)", value: "982,300.00" },
  { label: "IB Withdrawal (USD)", value: "312,450.00" },
  { label: "IB Wallet Balance (USD)", value: "118,900.00" },
  { label: "Net Deposit (USD)", value: "-152,400.00" },
]

const mockTableRows = [
  {
    ibid: "107779",
    deposit: "752,400.00",
    totalWithdrawal: "603,200.00",
    ibWithdrawal: "210,000.00",
    walletBalance: "74,500.00",
    netDeposit: "75,700.00",
  },
  {
    ibid: "129860",
    deposit: "502,400.00",
    totalWithdrawal: "379,100.00",
    ibWithdrawal: "102,450.00",
    walletBalance: "44,400.00",
    netDeposit: "76,450.00",
  },
  {
    ibid: "143002",
    deposit: "110,000.00",
    totalWithdrawal: "72,000.00",
    ibWithdrawal: "35,400.00",
    walletBalance: "0.00",
    netDeposit: "2,600.00",
  },
]

type QuickRangeValue = "week" | "month" | "custom"

const getPresetRange = (preset: Exclude<QuickRangeValue, "custom">): DateRange => {
  const today = new Date()
  const endOfRange = new Date(today.getFullYear(), today.getMonth(), today.getDate(), 23, 59, 59)
  const startOfRange = new Date(endOfRange)
  if (preset === "week") {
    startOfRange.setDate(endOfRange.getDate() - 6)
  } else {
    startOfRange.setDate(endOfRange.getDate() - 29)
  }
  startOfRange.setHours(0, 0, 0, 0)
  return { from: startOfRange, to: endOfRange }
}

export default function IBDataPage() {
  const [quickRange, setQuickRange] = useState<QuickRangeValue>("week")
  const [dateRange, setDateRange] = useState<DateRange | undefined>(getPresetRange("week"))

  const rangeLabel = useMemo(() => {
    if (!dateRange?.from || !dateRange?.to) {
      return "自定义时间范围"
    }
    const formatter = new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    })
    return `${formatter.format(dateRange.from)} ~ ${formatter.format(dateRange.to)}`
  }, [dateRange])

  const handleQuickRangeSelect = (value: QuickRangeValue) => {
    setQuickRange(value)
    if (value !== "custom") {
      setDateRange(getPresetRange(value))
    }
  }

  const quickRangeOptions = [
    { label: "本周", value: "week" as const },
    { label: "本月", value: "month" as const },
    { label: "自定义", value: "custom" as const },
  ]

  return (
    <div className="space-y-5 p-3 sm:space-y-6 sm:p-6">
      <div className="space-y-1.5">
        <Badge variant="secondary" className="text-xs uppercase tracking-wide">IB - 数据</Badge>
        <h1 className="text-2xl font-semibold">IB 数据查询</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>查询条件</CardTitle>
          <CardDescription>支持常用组合查询，也可输入单个 IBID，暂为静态表单。</CardDescription>
        </CardHeader>

        <CardContent className="space-y-4 text-sm sm:space-y-5">
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:gap-3">
              <span className="w-full text-xs font-medium text-muted-foreground sm:w-20 sm:text-sm">IBID：</span>
              <div className="flex-1 space-y-2">
                <Input
                  id="ib-ids"
                  placeholder="107779,129860"
                  className="h-10"
                />
                <div className="flex flex-wrap gap-2">
                  {defaultIBGroups.map((group) => (
                    <Button key={group.label} variant="outline" size="sm" className="rounded-full px-3">
                      {group.label} · {group.ids.join(", ")}
                    </Button>
                  ))}
                </div>
              </div>
            </div>

            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:gap-3">
              <span className="w-full text-xs font-medium text-muted-foreground sm:w-20 sm:text-sm">时间范围：</span>
              <div className="flex-1 space-y-3">
                <div className="flex flex-wrap gap-2">
                  {quickRangeOptions.map((option) => (
                    <Button
                      key={option.value}
                      size="sm"
                      variant={quickRange === option.value ? "default" : "outline"}
                      className={cn("h-8 rounded-full px-4 text-xs sm:text-sm", quickRange !== option.value && "bg-background")}
                      onClick={() => handleQuickRangeSelect(option.value)}
                    >
                      {option.label}
                    </Button>
                  ))}
                </div>
                <Popover>
                  <PopoverTrigger asChild>
                    <Button variant="outline" className="h-10 justify-start gap-2 text-left font-normal">
                      <span className="text-xs uppercase text-muted-foreground">当前区间</span>
                      <span className="text-sm font-medium text-foreground">{rangeLabel}</span>
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto p-2" align="start">
                    <Calendar
                      mode="range"
                      numberOfMonths={1}
                      selected={dateRange}
                      onSelect={(range) => {
                        setDateRange(range)
                        setQuickRange("custom")
                      }}
                      initialFocus
                    />
                  </PopoverContent>
                </Popover>
              </div>
            </div>
          </div>

          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-end">
            <Button className="w-full sm:w-auto">查询</Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>查询结果</CardTitle>
          <CardDescription>静态示例解释 SQL 输出字段，后续将根据实际 API 渲染。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 sm:space-y-5">
          <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
            <Badge variant="outline">参数：107779,129860</Badge>
            <Badge variant="outline">区间：{rangeLabel}</Badge>
          </div>

          <div className="grid gap-3 text-sm sm:grid-cols-5">
            {mockSummary.map((item) => (
              <div key={item.label} className="rounded-lg border p-3 sm:p-4">
                <p className="text-xs uppercase tracking-wide text-muted-foreground">{item.label}</p>
                <p className="text-lg font-semibold">{item.value}</p>
              </div>
            ))}
          </div>

          <div className="overflow-x-auto rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>IBID</TableHead>
                  <TableHead>Deposit (USD)</TableHead>
                  <TableHead>Total Withdrawal (USD)</TableHead>
                  <TableHead>IB Withdrawal (USD)</TableHead>
                  <TableHead>IB Wallet Balance (USD)</TableHead>
                  <TableHead>Net Deposit (USD)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {mockTableRows.map((row) => (
                  <TableRow key={row.ibid}>
                    <TableCell className="font-medium">{row.ibid}</TableCell>
                    <TableCell>{row.deposit}</TableCell>
                    <TableCell>{row.totalWithdrawal}</TableCell>
                    <TableCell>{row.ibWithdrawal}</TableCell>
                    <TableCell>{row.walletBalance}</TableCell>
                    <TableCell>{row.netDeposit}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <p className="text-xs text-muted-foreground">
            SQL 计算方式：总提现 = Withdrawal + IB Withdrawal，Net Deposit = Deposit + 总提现 - IB Wallet Balance。
          </p>
        </CardContent>
      </Card>
    </div>
  )
}