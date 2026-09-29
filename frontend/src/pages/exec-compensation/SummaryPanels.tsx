/**
 * Presentational pieces of the execution-compensation page (OPT-0068):
 * the mandatory notice banner, coverage warnings, summary cards and the
 * grouped breakdown tables. No fetching here.
 */

import type { ReactNode } from "react";
import { AlertTriangle, Info } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { fmtInt, fmtLots, fmtMs, fmtUsd, signedClass } from "./helpers";
import type { Coverage, GroupRow, SummaryData } from "./types";
import { CLASS_LABELS, ENTRY_LABELS } from "./types";

// ── mandatory notice (01 D13 / D14 / D17 — a banner, never a tooltip) ──────

export function NoticeBanner({ data }: { data: SummaryData }) {
  const asOf = data.query.as_of;
  const ex = data.excluded_open;
  return (
    <div
      role="note"
      className="rounded-xl border-2 border-amber-500/60 bg-amber-50 px-4 py-3 text-sm text-amber-950 dark:border-amber-400/50 dark:bg-amber-950/30 dark:text-amber-100 md:px-6"
    >
      <div className="mb-2 flex items-center gap-2 font-semibold">
        <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
        特别说明
      </div>
      <ol className="list-decimal space-y-1.5 pl-5 leading-relaxed">
        <li className="font-semibold">
          只適用於市價單開倉和平倉的差價
          <div className="text-xs font-normal opacity-80">
            Applies only to the price difference on market-order opens and closes.
          </div>
        </li>
        <li>
          <span className="font-semibold">数据截至 {asOf}</span>
          （查询日前一天，MT 服务器日），当天的成交不计入
          <div className="text-xs opacity-80">
            Data is as of {asOf} (the day before the query, MT server day); fills on the query day are not included.
          </div>
        </li>
        <li>
          <span className="font-semibold">截至 {asOf} 仍未完全平仓的仓位不计算</span>
          ：本次共剔除{" "}
          <span className="font-semibold tabular-nums">{fmtInt(ex.positions)}</span> 个仓位、
          <span className="font-semibold tabular-nums">{fmtInt(ex.deals)}</span> 笔成交、
          <span className="font-semibold tabular-nums">{fmtLots(ex.lots)}</span> 手；
          这些仓位平仓后再查询才会计入，所以同一段日期在不同日子查询，结果可能不同
          <div className="text-xs opacity-80">
            Positions not fully closed by {asOf} are excluded ({fmtInt(ex.positions)} positions, {fmtInt(ex.deals)} fills,{" "}
            {fmtLots(ex.lots)} lots this time). They count once closed, so the same date range can give different results on
            different days.
          </div>
        </li>
      </ol>
      <p className="mt-2 border-t border-amber-500/30 pt-2 text-xs opacity-90">
        口径 = 客户下单时的请求价；只统计客户主动市价单（手机 / 网页 / 客户端 / EA）；金额以正负相抵为准。
      </p>
    </div>
  );
}

// ── coverage / data-quality warnings ────────────────────────────────────────

function WarnLine({ tone, children }: { tone: "red" | "amber"; children: ReactNode }) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-xl border px-4 py-2.5 text-sm md:px-6",
        tone === "red"
          ? "border-red-500/50 bg-red-50 text-red-900 dark:bg-red-950/30 dark:text-red-100"
          : "border-amber-500/40 bg-amber-50/60 text-amber-900 dark:bg-amber-950/20 dark:text-amber-100",
      )}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div>{children}</div>
    </div>
  );
}

export function CoverageWarnings({ data, coverage }: { data: SummaryData; coverage: Coverage }) {
  const q = data.query;
  return (
    <>
      {!coverage.complete && (
        <WarnLine tone="red">
          <span className="font-semibold">数据完整到 {coverage.ready_through_srv_date}</span>
          ：之后的成交尚未同步，以下数字不完整（缺的部分未计入，并不是 0）。
          {coverage.reason && <span className="ml-1 opacity-80">原因：{coverage.reason}</span>}
        </WarnLine>
      )}
      {q.date_to_clipped && (
        <WarnLine tone="amber">
          结束日期已从 {q.date_to_requested} 截到 {q.date_to}（数据只到 {q.as_of}）。
        </WarnLine>
      )}
      {coverage.unmatched_deals > 0 && (
        <WarnLine tone="amber">
          有 <span className="font-semibold tabular-nums">{fmtInt(coverage.unmatched_deals)}</span>{" "}
          笔成交找不到对应的订单记录，无法取得请求价，未计入。
        </WarnLine>
      )}
      {data.anomaly_positions > 0 && (
        <WarnLine tone="amber">
          有 <span className="font-semibold tabular-nums">{fmtInt(data.anomaly_positions)}</span>{" "}
          个仓位生命周期异常，未计入（「全部」视图可看到原因为「仓位异常」的成交）。
        </WarnLine>
      )}
    </>
  );
}

// ── summary cards ───────────────────────────────────────────────────────────

function StatCard({ title, children, className }: { title: ReactNode; children: ReactNode; className?: string }) {
  return (
    <Card className={cn("gap-2 py-4", className)}>
      <CardHeader className="px-4">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1 px-4">{children}</CardContent>
    </Card>
  );
}

function Kv({ k, v, className }: { k: string; v: ReactNode; className?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-sm">
      <span className="text-muted-foreground">{k}</span>
      <span className={cn("font-medium tabular-nums", className)}>{v}</span>
    </div>
  );
}

export function SummaryCards({ data, coverage }: { data: SummaryData; coverage: Coverage }) {
  const partial = !coverage.complete;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
      <StatCard
        className="sm:col-span-2 xl:col-span-2"
        title={
          <span className="flex items-center gap-2">
            补偿金额（USD）
            {partial && (
              <Badge variant="outline" className="border-red-500/60 text-red-600 dark:text-red-400">
                数据不完整
              </Badge>
            )}
          </span>
        }
      >
        <div className="flex flex-wrap items-end gap-x-8 gap-y-2">
          <div>
            <div className="text-xs text-muted-foreground">正负相抵（为准）</div>
            <div className={cn("text-3xl font-bold tabular-nums", signedClass(data.comp_net_usd))}>
              {fmtUsd(data.comp_net_usd)}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">只补正数（参考）</div>
            <div className={cn("text-xl font-semibold tabular-nums", signedClass(data.comp_positive_usd))}>
              {fmtUsd(data.comp_positive_usd)}
            </div>
          </div>
        </div>
        {data.max_single_comp_usd != null && (
          <div className="pt-1 text-xs text-muted-foreground">
            单笔最大补偿 <span className="tabular-nums">{fmtUsd(data.max_single_comp_usd, 4)}</span>
          </div>
        )}
      </StatCard>

      <StatCard title="计入的成交">
        <Kv k="成交笔数" v={fmtInt(data.deals)} />
        <Kv k="手数" v={fmtLots(data.lots)} />
        <Kv k="更差" v={fmtInt(data.outcomes.worse)} className="text-red-600 dark:text-red-400" />
        <Kv k="不变" v={fmtInt(data.outcomes.same)} />
        <Kv k="更好" v={fmtInt(data.outcomes.better)} className="text-green-600 dark:text-green-400" />
      </StatCard>

      <StatCard title={`成交延迟（${fmtInt(data.delay.n)} 笔）`}>
        <Kv k="中位数" v={fmtMs(data.delay.median_ms)} />
        <Kv k="P95" v={fmtMs(data.delay.p95_ms)} />
        <Kv k="最大" v={fmtMs(data.delay.max_ms)} />
      </StatCard>

      <StatCard title="未计入（未完全平仓）" className="border-amber-500/50">
        <Kv k="仓位" v={fmtInt(data.excluded_open.positions)} />
        <Kv k="成交笔数" v={fmtInt(data.excluded_open.deals)} />
        <Kv k="手数" v={fmtLots(data.excluded_open.lots)} />
        <div className="pt-1 text-xs text-muted-foreground">
          若计入（仅供参考）：
          <span className={cn("tabular-nums", signedClass(data.excluded_open.comp_net_usd_if_counted))}>
            {fmtUsd(data.excluded_open.comp_net_usd_if_counted)}
          </span>
        </div>
      </StatCard>
    </div>
  );
}

// ── breakdown tables ────────────────────────────────────────────────────────

const TABLE_WRAP = "max-h-[420px] overflow-auto rounded-xl border bg-card";
const TABLE_HEAD =
  "sticky top-0 z-10 bg-black [&_th]:font-semibold [&_th]:text-white [&_th:first-child]:rounded-tl-xl [&_th:last-child]:rounded-tr-xl";

function GroupTable({
  rows,
  keyTitle,
  keyLabel,
}: {
  rows: { key: string; deals: number; lots: number; comp_net_usd: number; comp_positive_usd: number }[];
  keyTitle: string;
  keyLabel?: (k: string) => string;
}) {
  if (rows.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">无数据</p>;
  }
  return (
    <div className={TABLE_WRAP}>
      <Table>
        <TableHeader className={TABLE_HEAD}>
          <TableRow>
            <TableHead>{keyTitle}</TableHead>
            <TableHead className="text-right">成交笔数</TableHead>
            <TableHead className="text-right">手数</TableHead>
            <TableHead className="text-right">补偿（正负相抵）</TableHead>
            <TableHead className="text-right">只补正数</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.key}>
              <TableCell className="font-medium">{keyLabel ? keyLabel(r.key) : r.key}</TableCell>
              <TableCell className="text-right tabular-nums">{fmtInt(r.deals)}</TableCell>
              <TableCell className="text-right tabular-nums">{fmtLots(r.lots)}</TableCell>
              <TableCell className={cn("text-right tabular-nums", signedClass(r.comp_net_usd))}>
                {fmtUsd(r.comp_net_usd)}
              </TableCell>
              <TableCell className={cn("text-right tabular-nums", signedClass(r.comp_positive_usd))}>
                {fmtUsd(r.comp_positive_usd)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function BreakdownCard({ data }: { data: SummaryData }) {
  const classRows: GroupRow[] = data.not_counted_by_class.map((r) => ({ ...r, key: r.cls }));
  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle className="text-base">分组明细</CardTitle>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="account" className="w-full">
          <TabsList className="grid h-auto w-full max-w-3xl grid-cols-2 sm:grid-cols-5">
            <TabsTrigger value="account">按账户</TabsTrigger>
            <TabsTrigger value="symbol">按品种</TabsTrigger>
            <TabsTrigger value="entry">按开平</TabsTrigger>
            <TabsTrigger value="day">按日（MT）</TabsTrigger>
            <TabsTrigger value="excluded">不计入的类别</TabsTrigger>
          </TabsList>
          <TabsContent value="account" className="mt-4">
            <GroupTable rows={data.by_account} keyTitle="账户" />
          </TabsContent>
          <TabsContent value="symbol" className="mt-4">
            <GroupTable rows={data.by_symbol} keyTitle="品种" />
          </TabsContent>
          <TabsContent value="entry" className="mt-4">
            <GroupTable
              rows={data.by_entry}
              keyTitle="开 / 平"
              keyLabel={(k) => ENTRY_LABELS[k as keyof typeof ENTRY_LABELS] ?? k}
            />
          </TabsContent>
          <TabsContent value="day" className="mt-4">
            <GroupTable rows={data.by_day} keyTitle="MT 服务器日" />
          </TabsContent>
          <TabsContent value="excluded" className="mt-4 space-y-2">
            <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              同一日期范围内、不属于「客户主动市价单」的成交（挂单、止损止盈、强平、对冲平仓、交易员操作等），
              不计入补偿；列出来供核对，金额按同一口径计算，仅供参考。
            </p>
            <GroupTable
              rows={classRows}
              keyTitle="成交类别"
              keyLabel={(k) => CLASS_LABELS[k as keyof typeof CLASS_LABELS] ?? k}
            />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
