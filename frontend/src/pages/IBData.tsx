import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Calendar } from "@/components/ui/calendar";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { type DateRange } from "react-day-picker";

// default combos nudge fresh grads to surface typical batch queries
const defaultIBGroups = [{ label: "组合1", ids: ["107779", "129860"] }];

type IBAnalyticsRow = {
  ibid: string;
  deposit_usd: number;
  total_withdrawal_usd: number;
  ib_withdrawal_usd: number;
  ib_wallet_balance: number;
  net_deposit_usd: number;
};

type IBAnalyticsTotals = Omit<IBAnalyticsRow, "ibid">;

type IBAnalyticsResponsePayload = {
  rows: IBAnalyticsRow[];
  totals: IBAnalyticsTotals;
  last_query_time?: string | null;
};

type LastRunResponsePayload = {
  last_query_time: string | null;
};

const EMPTY_METRICS: IBAnalyticsTotals = {
  deposit_usd: 0,
  total_withdrawal_usd: 0,
  ib_withdrawal_usd: 0,
  ib_wallet_balance: 0,
  net_deposit_usd: 0,
};

const currencyFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const formatCurrency = (value: number | null | undefined) =>
  currencyFormatter.format(value ?? 0);

const formatLastRun = (value: string | null | undefined) => {
  if (!value) return "暂无记录";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return "暂无记录";
  const y = dt.getFullYear();
  const m = String(dt.getMonth() + 1).padStart(2, "0");
  const d = String(dt.getDate()).padStart(2, "0");
  const hh = String(dt.getHours()).padStart(2, "0");
  const mm = String(dt.getMinutes()).padStart(2, "0");
  const ss = String(dt.getSeconds()).padStart(2, "0");
  return `${y}-${m}-${d} ${hh}:${mm}:${ss}`;
};

const toSqlDateTime = (date: Date) => {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  const ss = String(date.getSeconds()).padStart(2, "0");
  return `${y}-${m}-${d} ${hh}:${mm}:${ss}`;
};

// Fresh grad note: backend expects inclusive day ranges, so clamp to full-day boundaries.
const normalizeRange = (range: DateRange | undefined) => {
  if (!range?.from) return null;
  const start = new Date(range.from);
  const end = new Date(range.to ?? range.from);
  start.setHours(0, 0, 0, 0);
  end.setHours(23, 59, 59, 0);
  return { start, end };
};

type QuickRangeValue = "week" | "month" | "custom";

const getPresetRange = (
  preset: Exclude<QuickRangeValue, "custom">
): DateRange => {
  const today = new Date();
  const endOfRange = new Date(
    today.getFullYear(),
    today.getMonth(),
    today.getDate(),
    23,
    59,
    59
  );
  const startOfRange = new Date(endOfRange);
  if (preset === "week") {
    startOfRange.setDate(endOfRange.getDate() - 6);
  } else {
    startOfRange.setDate(1);
    startOfRange.setHours(0, 0, 0, 0);
    return { from: startOfRange, to: endOfRange };
  }
  startOfRange.setHours(0, 0, 0, 0);
  return { from: startOfRange, to: endOfRange };
};

export default function IBDataPage() {
  const [quickRange, setQuickRange] = useState<QuickRangeValue>("week");
  const [dateRange, setDateRange] = useState<DateRange | undefined>(
    getPresetRange("week")
  );
  const [ibIdsInput, setIbIdsInput] = useState<string>(
    defaultIBGroups[0]?.ids.join(",") ?? ""
  );
  const [rows, setRows] = useState<IBAnalyticsRow[]>([]);
  const [totals, setTotals] = useState<IBAnalyticsTotals | null>(null);
  const [lastQueryTime, setLastQueryTime] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const loadLastRun = async () => {
      try {
        const res = await fetch("/api/v1/ib-data/last-run");
        if (!res.ok) return;
        const data = (await res.json()) as LastRunResponsePayload;
        if (!cancelled) {
          setLastQueryTime(data.last_query_time ?? null);
        }
      } catch {
        // ignore network hiccups on initial render
      }
    };

    // Fresh grad note: showing last run immediately improves perceived responsiveness.
    loadLastRun();
    return () => {
      cancelled = true;
    };
  }, []);

  const rangeLabel = useMemo(() => {
    if (!dateRange?.from || !dateRange?.to) {
      return "自定义时间范围";
    }
    const formatter = new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
    return `${formatter.format(dateRange.from)} ~ ${formatter.format(
      dateRange.to
    )}`;
  }, [dateRange]);

  const activeTotals = useMemo(() => {
    if (totals) return totals;
    if (!rows.length) return EMPTY_METRICS;
    return rows.reduce<IBAnalyticsTotals>(
      (acc, row) => ({
        deposit_usd: acc.deposit_usd + row.deposit_usd,
        total_withdrawal_usd:
          acc.total_withdrawal_usd + row.total_withdrawal_usd,
        ib_withdrawal_usd: acc.ib_withdrawal_usd + row.ib_withdrawal_usd,
        ib_wallet_balance: acc.ib_wallet_balance + row.ib_wallet_balance,
        net_deposit_usd: acc.net_deposit_usd + row.net_deposit_usd,
      }),
      { ...EMPTY_METRICS }
    );
  }, [rows, totals]);

  // Color themes for summary cards - each card has a distinct color scheme
  const cardThemes = [
    {
      bg: "bg-blue-50 dark:bg-blue-950/20",
      border: "border-blue-200 dark:border-blue-800",
      labelText: "text-blue-700 dark:text-blue-300",
      valueText: "text-blue-900 dark:text-blue-100",
    },
    {
      bg: "bg-green-50 dark:bg-green-950/20",
      border: "border-green-200 dark:border-green-800",
      labelText: "text-green-700 dark:text-green-300",
      valueText: "text-green-900 dark:text-green-100",
    },
    {
      bg: "bg-purple-50 dark:bg-purple-950/20",
      border: "border-purple-200 dark:border-purple-800",
      labelText: "text-purple-700 dark:text-purple-300",
      valueText: "text-purple-900 dark:text-purple-100",
    },
    {
      bg: "bg-orange-50 dark:bg-orange-950/20",
      border: "border-orange-200 dark:border-orange-800",
      labelText: "text-orange-700 dark:text-orange-300",
      valueText: "text-orange-900 dark:text-orange-100",
    },
    {
      bg: "bg-cyan-50 dark:bg-cyan-950/20",
      border: "border-cyan-200 dark:border-cyan-800",
      labelText: "text-cyan-700 dark:text-cyan-300",
      valueText: "text-cyan-900 dark:text-cyan-100",
    },
  ];

  const summaryItems = useMemo(
    () => [
      {
        label: "Deposit (USD)",
        value: formatCurrency(activeTotals.deposit_usd),
        theme: cardThemes[0],
      },
      {
        label: "Total Withdrawal (USD)",
        value: formatCurrency(activeTotals.total_withdrawal_usd),
        theme: cardThemes[1],
      },
      {
        label: "IB Withdrawal (USD)",
        value: formatCurrency(activeTotals.ib_withdrawal_usd),
        theme: cardThemes[2],
      },
      {
        label: "IB Wallet Balance (USD)",
        value: formatCurrency(activeTotals.ib_wallet_balance),
        theme: cardThemes[3],
      },
      {
        label: "Net Deposit (USD)",
        value: formatCurrency(activeTotals.net_deposit_usd),
        theme: cardThemes[4],
      },
    ],
    [activeTotals]
  );

  const handleQuickRangeSelect = (value: QuickRangeValue) => {
    setQuickRange(value);
    if (value !== "custom") {
      setDateRange(getPresetRange(value));
    }
  };

  const handleQuery = async () => {
    if (isLoading) return;
    const normalizedIds = ibIdsInput
      .split(",")
      .map((id) => id.trim())
      .filter(Boolean);
    if (!normalizedIds.length) {
      setError("请输入至少一个 IBID");
      return;
    }
    const normalizedRange = normalizeRange(dateRange);
    if (!normalizedRange) {
      setError("请选择完整的时间区间");
      return;
    }

    setIsLoading(true);
    setError(null);
    const payload = {
      ib_ids: normalizedIds,
      start: toSqlDateTime(normalizedRange.start),
      end: toSqlDateTime(normalizedRange.end),
    };
    try {
      const res = await fetch("/api/v1/ib-data/query", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          accept: "application/json",
        },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        // Try to parse error message from response
        let errorMsg = `查询失败：HTTP ${res.status}`;
        try {
          const errorData = await res.json();
          if (errorData.detail) {
            errorMsg = errorData.detail;
          }
        } catch {
          // Ignore JSON parse errors
        }
        throw new Error(errorMsg);
      }
      const data = (await res.json()) as IBAnalyticsResponsePayload;
      setRows(Array.isArray(data.rows) ? data.rows : []);
      setTotals(data.totals ?? null);
      setLastQueryTime(data.last_query_time ?? null);
    } catch (err: any) {
      setRows([]);
      setTotals(null);
      setError(err?.message ?? "查询失败");
    } finally {
      setIsLoading(false);
    }
  };

  const quickRangeOptions = [
    { label: "本周", value: "week" as const },
    { label: "本月", value: "month" as const },
    { label: "自定义", value: "custom" as const },
  ];

  return (
    <div className="space-y-5 p-3 sm:space-y-6 sm:p-6">
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold">IB 出入金查询</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>查询条件</CardTitle>
        </CardHeader>

        <CardContent className="space-y-4 text-sm sm:space-y-5">
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:gap-6">
              <div className="flex flex-1 flex-col gap-2">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
                  <span className="text-xs font-medium text-muted-foreground sm:text-sm sm:min-w-[48px] sm:flex-none">
                    IBID：
                  </span>
                  <Input
                    id="ib-ids"
                    placeholder="107779,129860"
                    className="h-10 sm:flex-1"
                    value={ibIdsInput}
                    onChange={(event) => setIbIdsInput(event.target.value)}
                  />
                </div>
              </div>

              <div className="flex flex-1 flex-col gap-2">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
                  <span className="text-xs font-medium text-muted-foreground sm:text-sm sm:min-w-[64px] sm:flex-none">
                    时间范围：
                  </span>
                  <div className="flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      {quickRangeOptions.map((option) => (
                        <Button
                          key={option.value}
                          size="sm"
                          variant={
                            quickRange === option.value ? "default" : "outline"
                          }
                          className={cn(
                            "h-8 rounded-full px-4 text-xs sm:text-sm",
                            quickRange !== option.value && "bg-background"
                          )}
                          onClick={() => handleQuickRangeSelect(option.value)}
                        >
                          {option.label}
                        </Button>
                      ))}
                      <Popover>
                        <PopoverTrigger asChild>
                          <Button
                            variant="outline"
                            className="h-10 justify-start gap-2 text-left font-normal"
                          >
                            <span className="text-xs uppercase text-muted-foreground">
                              当前区间
                            </span>
                            <span className="text-sm font-medium text-foreground">
                              {rangeLabel}
                            </span>
                          </Button>
                        </PopoverTrigger>
                        <PopoverContent className="w-auto p-2" align="start">
                          <Calendar
                            mode="range"
                            numberOfMonths={1}
                            selected={dateRange}
                            onSelect={(range) => {
                              setDateRange(range);
                              setQuickRange("custom");
                            }}
                            initialFocus
                          />
                        </PopoverContent>
                      </Popover>
                    </div>
                  </div>
                </div>
              </div>

              <div className="flex w-full sm:w-auto sm:flex-none sm:justify-end">
                <Button
                  className="w-full sm:w-28"
                  onClick={handleQuery}
                  disabled={isLoading}
                >
                  {isLoading ? "查询中..." : "查询"}
                </Button>
              </div>
            </div>

            <div className="flex flex-wrap gap-2 sm:pl-[4.5rem]">
              {defaultIBGroups.map((group) => (
                <Button
                  key={group.label}
                  type="button"
                  variant="outline"
                  size="sm"
                  className="rounded-full px-3"
                  onClick={() => setIbIdsInput(group.ids.join(","))}
                >
                  {group.label} · {group.ids.join(", ")}
                </Button>
              ))}
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>查询结果</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 sm:space-y-5">
          <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
            <Badge variant="outline">参数：{ibIdsInput || "无"}</Badge>
            <Badge variant="outline">区间：{rangeLabel}</Badge>
            <Badge variant="outline">
              上次查询：{formatLastRun(lastQueryTime)}
            </Badge>
          </div>

          <div className="grid gap-3 text-sm sm:grid-cols-5">
            {summaryItems.map((item) => (
              <div
                key={item.label}
                className={cn(
                  "rounded-lg border-2 p-3 sm:p-4 transition-all hover:shadow-md",
                  item.theme.bg,
                  item.theme.border
                )}
              >
                <p
                  className={cn(
                    "text-xs uppercase tracking-wide font-medium",
                    item.theme.labelText
                  )}
                >
                  {item.label}
                </p>
                <p
                  className={cn(
                    "text-lg font-semibold mt-1",
                    item.theme.valueText
                  )}
                >
                  {item.value}
                </p>
              </div>
            ))}
          </div>

          <div className="overflow-x-auto rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow className="bg-slate-800 dark:bg-slate-900 hover:bg-slate-800 dark:hover:bg-slate-900">
                  <TableHead className="text-white dark:text-slate-100 font-semibold">
                    IBID
                  </TableHead>
                  <TableHead className="text-white dark:text-slate-100 font-semibold">
                    Deposit (USD)
                  </TableHead>
                  <TableHead className="text-white dark:text-slate-100 font-semibold">
                    Total Withdrawal (USD)
                  </TableHead>
                  <TableHead className="text-white dark:text-slate-100 font-semibold">
                    IB Withdrawal (USD)
                  </TableHead>
                  <TableHead className="text-white dark:text-slate-100 font-semibold">
                    IB Wallet Balance (USD)
                  </TableHead>
                  <TableHead className="text-white dark:text-slate-100 font-semibold">
                    Net Deposit (USD)
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={6}
                      className="text-center text-sm text-muted-foreground"
                    >
                      暂无数据，请输入条件后查询。
                    </TableCell>
                  </TableRow>
                ) : (
                  rows.map((row) => (
                    <TableRow key={row.ibid}>
                      <TableCell className="font-medium">{row.ibid}</TableCell>
                      <TableCell>{formatCurrency(row.deposit_usd)}</TableCell>
                      <TableCell>
                        {formatCurrency(row.total_withdrawal_usd)}
                      </TableCell>
                      <TableCell>
                        {formatCurrency(row.ib_withdrawal_usd)}
                      </TableCell>
                      <TableCell>
                        {formatCurrency(row.ib_wallet_balance)}
                      </TableCell>
                      <TableCell>
                        {formatCurrency(row.net_deposit_usd)}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>

          <p className="text-xs text-muted-foreground">
            SQL 计算方式：总提现 = Withdrawal + IB Withdrawal，Net Deposit =
            Deposit + 总提现 - IB Wallet Balance。
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
