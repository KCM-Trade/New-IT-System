/**
 * 成交价差补偿 — Execution Compensation, MT5 v1 (OPT-0068).
 *
 * SSOT: docs/exec-compensation/ (01 spec D10–D21, 03 §5 frontend, 04 §5 checks).
 * API contract: backend/app/schemas/exec_compensation.py (frozen).
 *
 * Flow: the Query button fires /summary (server may take up to ~60s; a result
 * is cached server-side per subject/range/as_of). The summary echoes the
 * effective as_of; /orders and /export are then pinned to that as_of so the
 * three can never disagree, even across midnight. /orders is fetched only
 * AFTER the summary lands — firing both at once would compute the same query
 * twice and eat both of the server's concurrency slots (01 D21).
 *
 * Persisted (EXEC_COMP_FILTERS_V1): orders view + page size only. The client /
 * account input and the absolute date range are investigation context.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { CellClassParams, ColDef, GridApi, SortChangedEvent } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import type { DateRange } from "react-day-picker";
import {
  Calendar as CalendarIcon,
  ChevronLeft,
  ChevronRight,
  Download,
  Loader2,
  Search,
} from "lucide-react";
import { useTheme } from "@/components/theme-provider";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { InfoHeader } from "@/components/ui/info-header";
import { ColumnVisibilityMenu } from "@/components/ColumnVisibilityMenu";
import { GRID_STORAGE_KEYS, useGridColumnPersist } from "@/hooks/useGridColumnPersist";
import { readFilterState, useFilterPersist } from "@/hooks/useFilterPersist";
import { apiFetch } from "@/lib/fetch";
import { cn } from "@/lib/utils";
import {
  COL_TO_SORT_BY,
  addDays,
  apiErrorMessage,
  buildCommonParams,
  clampRange,
  defaultRange,
  filenameFromDisposition,
  fmtInt,
  fmtLots,
  fmtMs,
  fmtPrice,
  fmtUsd,
  fromYmd,
  isAbortError,
  isTimeoutError,
  parseSubject,
  reqTimeSrv,
  signedClass,
  subjectLabel,
  toYmd,
  utcToHk,
} from "./exec-compensation/helpers";
import type { CommonQuery } from "./exec-compensation/helpers";
import {
  BreakdownCard,
  CoverageWarnings,
  NoticeBanner,
  SummaryCards,
} from "./exec-compensation/SummaryPanels";
import type {
  OrderRow,
  OrdersResponse,
  OrdersView,
  SortBy,
  SortDir,
  StatusData,
  StatusResponse,
  SummaryResponse,
} from "./exec-compensation/types";
import {
  CLASS_LABELS,
  ENTRY_LABELS,
  OUTCOME_LABELS,
  REASON_LABELS,
  SIDE_LABELS,
  VIEW_OPTIONS,
} from "./exec-compensation/types";

const API = "/api/v1/exec-compensation";
/** Server budget is ~60s per query (01 D21); leave headroom for the network. */
const QUERY_TIMEOUT_MS = 90_000;
const EXPORT_TIMEOUT_MS = 180_000;
/** A heavy query is not retried automatically: a retry on 5xx would just
 *  queue the same 60s computation again behind a BUSY server. */
const HEAVY_OPTS = { timeoutMs: QUERY_TIMEOUT_MS, retries: 0 } as const;

/** MT5 first fill day — used only until /status answers. */
const FALLBACK_EARLIEST = "2023-02-20";

const FILTERS_KEY = "EXEC_COMP_FILTERS_V1";
const PAGE_SIZES = [100, 200, 500, 1000] as const;
const FILTER_DEFAULTS = {
  view: "counted" as OrdersView,
  pageSize: 200 as number,
};

const DEFAULT_SORT_BY: SortBy = "fill_time_srv";
const DEFAULT_SORT_DIR: SortDir = "asc";

const WRAP_CELL_STYLE = {
  whiteSpace: "normal",
  lineHeight: "1.35",
  overflowWrap: "anywhere",
} as const;

const CONTROL_CLASS = "h-9 w-full min-w-0";
const ACTION_BUTTON_CLASS = "h-9 w-full gap-2 sm:w-[140px]";

function sanitizeView(v: unknown): OrdersView {
  return v === "excluded_open" || v === "all" ? v : "counted";
}

function sanitizePageSize(v: unknown): number {
  const n = Number(v);
  return (PAGE_SIZES as readonly number[]).includes(n) ? n : FILTER_DEFAULTS.pageSize;
}

function yesterdayLocal(): string {
  return toYmd(addDays(new Date(), -1));
}

async function readError(res: Response): Promise<string> {
  const body = await res.json().catch(() => null);
  return apiErrorMessage(res.status, body).message;
}

function describeFetchError(e: unknown): string {
  if (isTimeoutError(e)) return "查询超时（超过 90 秒）。请缩小日期范围后再试。";
  return e instanceof Error ? e.message : String(e);
}

function LoadingOverlay() {
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      <span>正在加载逐笔成交…</span>
    </div>
  );
}

export default function ExecCompensation() {
  const { theme } = useTheme();
  const isDarkMode = theme === "dark";

  // ── /status: date bounds + limits ────────────────────────────────────────
  const [status, setStatus] = useState<StatusData | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    apiFetch(`${API}/status`, { signal: controller.signal })
      .then(async (res) => {
        if (!res.ok) throw new Error(await readError(res));
        return res.json() as Promise<StatusResponse>;
      })
      .then((json) => setStatus(json.data))
      .catch((e: unknown) => {
        if (isAbortError(e)) return;
        setStatusError(describeFetchError(e));
      });
    return () => controller.abort();
  }, []);

  const minYmd = status?.earliest_srv_date ?? FALLBACK_EARLIEST;
  const maxYmd = status?.default_as_of ?? yesterdayLocal();
  const minDate = useMemo(() => fromYmd(minYmd), [minYmd]);
  const maxDate = useMemo(() => fromYmd(maxYmd), [maxYmd]);

  // ── toolbar state ────────────────────────────────────────────────────────
  const persisted = useMemo(() => readFilterState(FILTERS_KEY, FILTER_DEFAULTS), []);
  const [view, setView] = useState<OrdersView>(sanitizeView(persisted.view));
  const [pageSize, setPageSize] = useState<number>(sanitizePageSize(persisted.pageSize));
  useFilterPersist(FILTERS_KEY, FILTER_DEFAULTS, { view, pageSize });

  // Investigation context — NOT persisted.
  const [subjectInput, setSubjectInput] = useState("");
  const [range, setRange] = useState<DateRange | undefined>(() =>
    defaultRange(yesterdayLocal(), FALLBACK_EARLIEST),
  );
  const rangeTouched = useRef(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Once the real bounds arrive: reset an untouched default range to them,
  // and clamp a range the user already picked.
  useEffect(() => {
    if (!status) return;
    if (!rangeTouched.current) {
      setRange(defaultRange(status.default_as_of, status.earliest_srv_date));
      return;
    }
    setRange((r) => {
      if (!r?.from || !r?.to) return r;
      return clampRange(r.from, r.to, fromYmd(status.earliest_srv_date), fromYmd(status.default_as_of)) ?? undefined;
    });
  }, [status]);

  const pageSizes = useMemo(
    () => PAGE_SIZES.filter((n) => !status || n <= status.limits.page_size_max),
    [status],
  );

  // ── summary ──────────────────────────────────────────────────────────────
  const [request, setRequest] = useState<{ query: CommonQuery; nonce: number } | null>(null);
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  /** The query that produced `summary`, with as_of pinned to its echo. */
  const [committed, setCommitted] = useState<CommonQuery | null>(null);

  useEffect(() => {
    if (!request) return;
    const controller = new AbortController();
    let cancelled = false;
    setSummaryLoading(true);
    setSummaryError(null);
    setSummary(null);
    setCommitted(null);
    const params = buildCommonParams(request.query);
    apiFetch(`${API}/summary?${params}`, { signal: controller.signal }, HEAVY_OPTS)
      .then(async (res) => {
        if (!res.ok) throw new Error(await readError(res));
        return res.json() as Promise<SummaryResponse>;
      })
      .then((json) => {
        if (cancelled) return;
        setSummary(json);
        setCommitted({
          ...request.query,
          dateFrom: json.data.query.date_from,
          dateTo: json.data.query.date_to,
          asOf: json.as_of,
        });
      })
      .catch((e: unknown) => {
        if (cancelled || isAbortError(e)) return;
        setSummaryError(describeFetchError(e));
      })
      .finally(() => {
        if (!cancelled) setSummaryLoading(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [request]);

  // Elapsed-seconds counter while a (possibly ~60s) summary query runs. This
  // is a local clock only — it never polls the server.
  useEffect(() => {
    if (!summaryLoading) return;
    const started = Date.now();
    setElapsed(0);
    const id = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, [summaryLoading]);

  const onQuery = useCallback(() => {
    const parsed = parseSubject(subjectInput);
    if (!parsed.ok) {
      setFormError(parsed.message);
      return;
    }
    if (!range?.from || !range?.to) {
      setFormError("请选择日期范围");
      return;
    }
    const clamped = clampRange(range.from, range.to, minDate, maxDate);
    if (!clamped) {
      setFormError(`日期范围必须在 ${minYmd} ~ ${maxYmd} 之间`);
      return;
    }
    setFormError(null);
    setPage(1);
    setRequest({
      query: { subject: parsed.subject, dateFrom: toYmd(clamped.from), dateTo: toYmd(clamped.to) },
      nonce: Date.now(),
    });
  }, [subjectInput, range, minDate, maxDate, minYmd, maxYmd]);

  // ── orders (server-side paged + sorted) ──────────────────────────────────
  const [page, setPage] = useState(1);
  const [sortBy, setSortBy] = useState<SortBy>(DEFAULT_SORT_BY);
  const [sortDir, setSortDir] = useState<SortDir>(DEFAULT_SORT_DIR);
  const [rows, setRows] = useState<OrderRow[]>([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [ordersError, setOrdersError] = useState<string | null>(null);

  useEffect(() => {
    if (!committed) {
      setRows([]);
      setTotal(0);
      setTotalPages(1);
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    setOrdersLoading(true);
    setOrdersError(null);
    const params = buildCommonParams(committed);
    params.set("view", view);
    params.set("page", String(page));
    params.set("page_size", String(pageSize));
    params.set("sort_by", sortBy);
    params.set("sort_dir", sortDir);
    apiFetch(`${API}/orders?${params}`, { signal: controller.signal }, HEAVY_OPTS)
      .then(async (res) => {
        if (!res.ok) throw new Error(await readError(res));
        return res.json() as Promise<OrdersResponse>;
      })
      .then((json) => {
        if (cancelled) return;
        setRows(json.data ?? []);
        setTotal(json.total ?? 0);
        setTotalPages(Math.max(1, json.total_pages ?? 1));
      })
      .catch((e: unknown) => {
        if (cancelled || isAbortError(e)) return;
        setRows([]);
        setOrdersError(describeFetchError(e));
      })
      .finally(() => {
        if (!cancelled) setOrdersLoading(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [committed, view, page, pageSize, sortBy, sortDir]);

  const columnPersist = useGridColumnPersist(GRID_STORAGE_KEYS.EXEC_COMP_ORDERS);
  const gridApiRef = useRef<GridApi<OrderRow> | null>(null);

  // Grid sort → server sort_by whitelist. Also runs when the persist hook
  // restores a saved sort on mount, which is what we want: the restored
  // header arrow then matches the order the server returns.
  const handleSortChanged = useCallback(
    (e: SortChangedEvent<OrderRow>) => {
      columnPersist.gridEventProps.onSortChanged();
      const sorted = e.api
        .getColumnState()
        .filter((c) => c.sort)
        .sort((a, b) => (a.sortIndex ?? 0) - (b.sortIndex ?? 0))[0];
      const mapped = sorted ? COL_TO_SORT_BY[sorted.colId] : undefined;
      const nextBy = mapped ?? DEFAULT_SORT_BY;
      const nextDir = mapped ? (sorted!.sort as SortDir) : DEFAULT_SORT_DIR;
      if (nextBy !== sortBy || nextDir !== sortDir) {
        setSortBy(nextBy);
        setSortDir(nextDir);
        setPage(1);
      }
    },
    [columnPersist.gridEventProps, sortBy, sortDir],
  );

  // ── export ───────────────────────────────────────────────────────────────
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const exportCtrl = useRef<AbortController | null>(null);
  useEffect(() => () => exportCtrl.current?.abort(), []);

  const onExport = useCallback(async () => {
    if (!committed) return;
    exportCtrl.current?.abort();
    const controller = new AbortController();
    exportCtrl.current = controller;
    setExporting(true);
    setExportError(null);
    try {
      const params = buildCommonParams(committed);
      const res = await apiFetch(
        `${API}/export?${params}`,
        { signal: controller.signal },
        { timeoutMs: EXPORT_TIMEOUT_MS, retries: 0 },
      );
      if (!res.ok) throw new Error(await readError(res));
      const subjectPart =
        committed.subject.kind === "client_id" ? String(committed.subject.value) : committed.subject.value;
      const fallback = `exec_compensation_${subjectPart}_${committed.dateFrom}_${committed.dateTo}.xlsx`;
      const fileName = filenameFromDisposition(res.headers.get("Content-Disposition"), fallback);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fileName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      if (isAbortError(e)) return;
      setExportError(describeFetchError(e));
    } finally {
      if (exportCtrl.current === controller) {
        exportCtrl.current = null;
        setExporting(false);
      }
    }
  }, [committed]);

  // ── columns ──────────────────────────────────────────────────────────────
  // Deps stay empty: column visibility is persisted, so nothing here may vary
  // by view (CLAUDE.md "don't toggle `hide` by mode").
  const columnDefs = useMemo<ColDef<OrderRow>[]>(() => {
    const serverSorted = { sortable: true, comparator: () => 0 } as const;
    const signedCell = (p: CellClassParams<OrderRow>) => signedClass(p.value as number | null);
    return [
      { field: "deal_id", headerName: "成交号 Deal", width: 120, ...serverSorted },
      { field: "order_id", headerName: "订单号 Order", width: 120, sortable: false },
      { field: "position_id", headerName: "仓位号 Position", width: 130, sortable: false },
      { field: "login_sid", headerName: "账户", width: 120, ...serverSorted },
      {
        field: "account_group",
        headerName: "账户组",
        width: 160,
        sortable: false,
        headerComponent: InfoHeader,
        headerComponentParams: {
          tooltip: "MT 账户组（GROUP）。「插件直通」类成交按账户组排除，看这一列可知原因。",
        },
        valueFormatter: (p) => (p.value as string | null) || "—",
      },
      { field: "ccy", headerName: "币种", width: 80, sortable: false },
      { field: "symbol", headerName: "品种", width: 110, ...serverSorted },
      {
        field: "side",
        headerName: "方向",
        width: 80,
        sortable: false,
        valueFormatter: (p) => (p.value ? SIDE_LABELS[p.value as OrderRow["side"]] : "—"),
        cellClass: (p) =>
          p.value === "buy" ? "text-blue-600 dark:text-blue-400" : p.value === "sell" ? "text-red-600 dark:text-red-400" : "",
      },
      {
        field: "entry",
        headerName: "开 / 平",
        width: 90,
        sortable: false,
        valueFormatter: (p) => (p.value ? ENTRY_LABELS[p.value as OrderRow["entry"]] ?? p.value : "—"),
      },
      {
        field: "cls",
        headerName: "成交类别",
        width: 140,
        sortable: false,
        valueFormatter: (p) => (p.value ? CLASS_LABELS[p.value as OrderRow["cls"]] ?? p.value : "—"),
      },
      {
        colId: "counted_status",
        headerName: "是否计入",
        width: 150,
        sortable: false,
        headerComponent: InfoHeader,
        headerComponentParams: {
          tooltip:
            "计入 = 客户主动市价单，且所属仓位在数据截止日已完全平仓。不计入时显示原因：非客户市价单 / 仓位未完全平仓 / 仓位异常。",
        },
        valueGetter: (p) => {
          const r = p.data;
          if (!r) return null;
          if (r.counted) return "计入";
          const reason = r.not_counted_reason ? REASON_LABELS[r.not_counted_reason] : "";
          return reason ? `不计入：${reason}` : "不计入";
        },
        cellClass: (p) => (p.data && !p.data.counted ? "text-amber-700 dark:text-amber-400" : ""),
      },
      {
        field: "lots",
        headerName: "手数",
        width: 90,
        type: "rightAligned",
        ...serverSorted,
        valueFormatter: (p) => fmtLots(p.value as number),
      },
      {
        colId: "req_time_srv",
        headerName: "请求时间 (MT)",
        width: 190,
        sortable: false,
        // Prefer the server's own field; derive from the fill row's offset
        // only if an older backend omits it.
        valueGetter: (p) =>
          p.data
            ? p.data.req_time_srv ??
              reqTimeSrv(p.data.req_time_utc, p.data.fill_time_utc, p.data.fill_time_srv)
            : null,
      },
      {
        colId: "req_time_hk",
        headerName: "请求时间 (HK)",
        width: 190,
        sortable: false,
        valueGetter: (p) => (p.data ? utcToHk(p.data.req_time_utc) : null),
      },
      { field: "fill_time_srv", headerName: "成交时间 (MT)", width: 190, ...serverSorted },
      {
        colId: "fill_time_hk",
        headerName: "成交时间 (HK)",
        width: 190,
        sortable: false,
        valueGetter: (p) => (p.data ? utcToHk(p.data.fill_time_utc) : null),
      },
      {
        field: "delay_ms",
        headerName: "延迟",
        width: 110,
        type: "rightAligned",
        ...serverSorted,
        headerComponent: InfoHeader,
        headerComponentParams: {
          tooltip: "延迟 = 成交时间 − 客户下单（请求）时间，单位毫秒。只是参考信息，不影响补偿金额。",
        },
        valueFormatter: (p) => fmtMs(p.value as number | null),
      },
      {
        field: "ref_price",
        headerName: "请求价",
        width: 120,
        type: "rightAligned",
        sortable: false,
        headerComponent: InfoHeader,
        headerComponentParams: {
          tooltip: "请求价 = 客户下单时的价格（MT5 订单的 PriceCurrent），是计算补偿的参考价。",
        },
        valueFormatter: (p) => fmtPrice(p.value as number | null),
      },
      {
        field: "fill_price",
        headerName: "成交价",
        width: 120,
        type: "rightAligned",
        sortable: false,
        valueFormatter: (p) => fmtPrice(p.value as number | null),
      },
      {
        field: "worse_px",
        headerName: "价差",
        width: 110,
        type: "rightAligned",
        sortable: false,
        headerComponent: InfoHeader,
        headerComponentParams: {
          tooltip: "价差 = 成交价相对请求价对客户不利的幅度（价格单位）。正数 = 成交比请求价更差；负数 = 更好。",
        },
        valueFormatter: (p) => fmtPrice(p.value as number | null),
      },
      {
        field: "outcome",
        headerName: "结果",
        width: 80,
        sortable: false,
        valueFormatter: (p) => (p.value ? OUTCOME_LABELS[p.value as NonNullable<OrderRow["outcome"]>] : "—"),
      },
      {
        field: "comp_usd",
        headerName: "补偿 (USD)",
        width: 130,
        type: "rightAligned",
        ...serverSorted,
        headerComponent: InfoHeader,
        headerComponentParams: {
          tooltip:
            "补偿 = 价差 × 手数 × 合约乘数，折成 USD（CEN 账户已 ÷100）。负数 = 成交比请求价更好。汇总以正负相抵为准。",
        },
        valueFormatter: (p) => fmtUsd(p.value as number | null, 4),
        cellClass: signedCell,
      },
    ];
  }, []);

  const defaultColDef = useMemo<ColDef>(
    () => ({
      sortable: false,
      resizable: true,
      filter: false,
      minWidth: 70,
      wrapHeaderText: true,
      autoHeaderHeight: true,
      wrapText: true,
      autoHeight: true,
      cellStyle: WRAP_CELL_STYLE,
    }),
    [],
  );

  const gridStyle = useMemo(
    () =>
      ({
        ["--ag-header-background-color" as string]: isDarkMode ? "hsl(0 0% 100% / 1)" : "hsl(0 0% 8% / 1)",
        ["--ag-header-foreground-color" as string]: isDarkMode ? "hsl(0 0% 0% / 1)" : "hsl(0 0% 100% / 1)",
        ["--ag-header-column-separator-color" as string]: isDarkMode ? "hsl(0 0% 0% / 1)" : "hsl(0 0% 100% / 1)",
        ["--ag-header-column-separator-width" as string]: "1px",
        ["--ag-cell-horizontal-padding" as string]: "4px",
        ["--ag-background-color" as string]: "hsl(var(--card))",
        ["--ag-foreground-color" as string]: "hsl(var(--foreground))",
        ["--ag-row-border-color" as string]: "hsl(var(--border))",
        ["--ag-odd-row-background-color" as string]: isDarkMode ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.03)",
        height: "min(70vh, 640px)",
        minHeight: "400px",
        width: "100%",
      }) as CSSProperties,
    [isDarkMode],
  );

  const rangeLabel =
    range?.from && range?.to ? `${toYmd(range.from)} ~ ${toYmd(range.to)}` : range?.from ? `${toYmd(range.from)} ~ …` : "选择日期范围";

  const busy = summaryLoading;
  const data = summary?.data ?? null;

  return (
    <div className="flex-1 space-y-4 p-4 md:p-6">
      {/* Toolbar */}
      <div className="rounded-xl border bg-card px-4 py-4 md:px-6">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-base font-semibold tracking-tight">成交价差补偿（MT5）</h2>
          <span className="text-xs text-muted-foreground">
            可查日期 {minYmd} ~ {maxYmd}（MT 服务器日；数据截至查询日前一天）
          </span>
        </div>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
          <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="exec-comp-subject" className="text-xs font-normal text-muted-foreground">
                客户 ID 或 MT5 账户（5-&lt;login&gt;）
              </Label>
              <Input
                id="exec-comp-subject"
                className={CONTROL_CLASS}
                placeholder="例如 153034 或 5-8616169"
                value={subjectInput}
                onChange={(e) => setSubjectInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !busy) onQuery();
                }}
                autoComplete="off"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs font-normal text-muted-foreground">成交日期（MT 服务器日，含首尾）</Label>
              <Popover>
                <PopoverTrigger asChild>
                  <Button variant="outline" className={cn(CONTROL_CLASS, "justify-start gap-2 font-normal")}>
                    <CalendarIcon className="h-4 w-4 shrink-0" />
                    <span className="truncate tabular-nums">{rangeLabel}</span>
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-0" align="start">
                  <Calendar
                    mode="range"
                    selected={range}
                    onSelect={(r) => {
                      rangeTouched.current = true;
                      setRange(r);
                    }}
                    numberOfMonths={2}
                    defaultMonth={range?.from ?? addDays(maxDate, -31)}
                    startMonth={minDate}
                    endMonth={maxDate}
                    captionLayout="dropdown"
                    disabled={[{ before: minDate }, { after: maxDate }]}
                  />
                </PopoverContent>
              </Popover>
            </div>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
            <Button className={ACTION_BUTTON_CLASS} onClick={onQuery} disabled={busy}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
              {busy ? "查询中…" : "查询"}
            </Button>
            <Button
              variant="outline"
              className={ACTION_BUTTON_CLASS}
              onClick={onExport}
              disabled={!committed || exporting || busy}
            >
              {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              {exporting ? "导出中…" : "导出 Excel"}
            </Button>
          </div>
        </div>
        {formError && <p className="mt-2 text-sm text-destructive">{formError}</p>}
        {statusError && (
          <p className="mt-2 text-xs text-muted-foreground">
            无法读取数据范围（{statusError}），日期范围暂按 {FALLBACK_EARLIEST} ~ 昨天。
          </p>
        )}
        {exportError && <p className="mt-2 text-sm text-destructive">导出失败：{exportError}</p>}
      </div>

      {summaryLoading && (
        <div className="flex items-center gap-2 rounded-xl border bg-card px-4 py-3 text-sm text-muted-foreground md:px-6">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          正在计算{request ? `（${subjectLabel(request.query.subject)}，${request.query.dateFrom} ~ ${request.query.dateTo}）` : ""}
          … 已用 {elapsed} 秒；大客户长区间最长约 60 秒。
        </div>
      )}

      {summaryError && (
        <div className="rounded-xl border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive md:px-6">
          查询失败：{summaryError}
        </div>
      )}

      {!summary && !summaryLoading && !summaryError && (
        <div className="rounded-xl border border-dashed px-4 py-10 text-center text-sm text-muted-foreground">
          输入客户 ID 或 MT5 账户、选择日期范围后点「查询」。
          <div className="mt-1 text-xs">
            只適用於市價單開倉和平倉的差價 · 数据截至查询日前一天 · 未完全平仓的仓位不计算
          </div>
        </div>
      )}

      {summary && data && (
        <>
          <NoticeBanner data={data} />
          <CoverageWarnings data={data} coverage={summary.coverage} />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>
              {data.subject.client_id != null ? `客户 ${data.subject.client_id}` : `账户 ${data.subject.login_sid ?? ""}`}
              {data.subject.logins.length > 0 && `（MT5 账户 ${data.subject.logins.length} 个）`}
            </span>
            <span>
              成交日期 {data.query.date_from} ~ {data.query.date_to}
            </span>
            <span>数据截至 {data.query.as_of}</span>
            <span>
              耗时 {fmtInt(summary.statistics.query_time_ms)} ms{summary.statistics.from_cache ? "（缓存）" : ""}
            </span>
          </div>
          {data.subject.logins.length === 0 && (
            <div className="rounded-xl border border-amber-500/40 bg-amber-50/60 px-4 py-2.5 text-sm text-amber-900 dark:bg-amber-950/20 dark:text-amber-100 md:px-6">
              该客户没有 MT5 账户（本功能 v1 只计算 MT5），以下数字均为空。
            </div>
          )}
          <SummaryCards data={data} coverage={summary.coverage} />
          <BreakdownCard data={data} />
        </>
      )}

      {/* Orders grid — kept mounted so saved column state restores once. */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="text-base font-semibold">逐笔成交</h3>
            <ToggleGroup
              type="single"
              value={view}
              onValueChange={(v) => {
                if (!v) return;
                setView(v as OrdersView);
                setPage(1);
              }}
              className="inline-flex items-center rounded-full bg-muted p-0.5"
              aria-label="逐笔视图"
            >
              {VIEW_OPTIONS.map((o) => (
                <ToggleGroupItem
                  key={o.value}
                  value={o.value}
                  className="rounded-full px-3 py-1 text-center text-sm text-muted-foreground data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow"
                >
                  {o.label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
            {committed && (
              <span className="text-xs text-muted-foreground tabular-nums">共 {fmtInt(total)} 笔</span>
            )}
          </div>
          <ColumnVisibilityMenu persist={columnPersist} columnDefs={columnDefs as ColDef<unknown>[]} size="sm" />
        </div>

        {ordersError && (
          <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            逐笔加载失败：{ordersError}
          </p>
        )}

        <div
          className={cn(isDarkMode ? "ag-theme-quartz-dark" : "ag-theme-quartz", "w-full overflow-hidden rounded-xl border")}
          style={gridStyle}
        >
          <AgGridReact<OrderRow>
            rowData={rows}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            gridOptions={{ theme: "legacy" }}
            getRowId={(p) => String(p.data.deal_id)}
            onGridReady={(e) => {
              gridApiRef.current = e.api;
              columnPersist.gridEventProps.onGridReady(e);
            }}
            onSortChanged={handleSortChanged}
            onColumnMoved={columnPersist.gridEventProps.onColumnMoved}
            onColumnVisible={columnPersist.gridEventProps.onColumnVisible}
            onColumnPinned={columnPersist.gridEventProps.onColumnPinned}
            onColumnResized={columnPersist.gridEventProps.onColumnResized}
            enableCellTextSelection
            suppressCellFocus
            animateRows={false}
            loading={ordersLoading}
            loadingOverlayComponent={LoadingOverlay}
            overlayNoRowsTemplate={`<span class="text-sm text-muted-foreground">${committed ? "该视图下没有成交" : "查询后显示逐笔成交"}</span>`}
          />
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border bg-card px-4 py-2 text-sm md:px-6">
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">每页</span>
            <Select
              value={String(pageSize)}
              onValueChange={(v) => {
                setPageSize(Number(v));
                setPage(1);
              }}
            >
              <SelectTrigger className="h-8 w-[96px]" aria-label="每页行数">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {pageSizes.map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-8 px-2"
              disabled={ordersLoading || page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              aria-label="上一页"
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <span className="text-xs text-muted-foreground tabular-nums">
              第 {fmtInt(page)} / {fmtInt(totalPages)} 页
            </span>
            <Button
              variant="outline"
              size="sm"
              className="h-8 px-2"
              disabled={ordersLoading || page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              aria-label="下一页"
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
