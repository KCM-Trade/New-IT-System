/**
 * Fund Risk Ledger (出入金风控台账) — /cs/fund-risk-ledger, `cs` module.
 * Spec: docs/features/fund-risk-ledger.md.
 *
 * One tab today (台账); the Tabs shell is there for the phase-2 analysis tab.
 * Toolbar + server-paged AG-Grid + right-side Sheet for create / edit.
 *
 * Persisted (useFilterPersist): the date preset, channel / direction /
 * authority and the pending toggle — how the user views the ledger.
 * Not persisted: the search text and an absolute date range — what the user
 * is currently looking for.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { ColDef } from "ag-grid-community";
import type { DateRange as PickerRange } from "react-day-picker";
import {
  Calendar as CalendarIcon,
  ClipboardList,
  Download,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { ColumnVisibilityMenu } from "@/components/ColumnVisibilityMenu";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { readFilterState, useFilterPersist } from "@/hooks/useFilterPersist";
import {
  GRID_STORAGE_KEYS,
  useGridColumnPersist,
} from "@/hooks/useGridColumnPersist";
import { apiFetch } from "@/lib/fetch";
import { cn } from "@/lib/utils";
import { filenameFromDisposition } from "@/pages/exec-compensation/helpers";
import { readErrorDetail } from "@/pages/risk-alert-mail/helpers";
import { useAuth } from "@/providers/auth-provider";

import { EntrySheet, type SheetIntent } from "./fund-risk-ledger/EntrySheet";
import {
  useLedgerColumnDefs,
  type LedgerRowActions,
} from "./fund-risk-ledger/columns";
import { LedgerGrid } from "./fund-risk-ledger/LedgerGrid";
import {
  ALL,
  API_BASE,
  buildFilterParams,
  buildListQuery,
  DATE_PRESET_LABELS,
  DATE_PRESETS,
  DEFAULT_ORDER,
  DEFAULT_SORT,
  isAbortError,
  normalizeSearch,
  PAGE_SIZE,
  toYmd,
  VIEW_FILTER_DEFAULTS,
  type DatePreset,
  type DateRange,
  type LedgerQuery,
  type LedgerViewFilters,
} from "./fund-risk-ledger/helpers";
import {
  AUTHORITY_LABELS,
  CHANNEL_OPTIONS,
  DIRECTION_LABELS,
} from "./fund-risk-ledger/labels";
import {
  AUTHORITIES,
  DIRECTIONS,
  type Incident,
  type IncidentListResponse,
  type SortKey,
  type SortOrder,
} from "./fund-risk-ledger/types";

// ── tabs (URL-controlled, same pattern as /login-ips) ────────────────────────
const TABS = ["ledger"] as const;
type LedgerTabKey = (typeof TABS)[number];
const DEFAULT_TAB: LedgerTabKey = "ledger";
// Hand-listed in view-profiles/manifest.ts UI_STATE_KEYS.
const ACTIVE_TAB_KEY = "CS_FUND_RISK_LEDGER_ACTIVE_TAB_V1";

function isTab(v: string | null): v is LedgerTabKey {
  return v !== null && (TABS as readonly string[]).includes(v);
}

// Hand-listed in view-profiles/manifest.ts FILTER_STATE_KEYS.
const FILTERS_KEY = "CS_FUND_RISK_LEDGER_LIST_FILTERS_V1";

const CUSTOM = "custom";
const FILTER_CONTROL_CLASS = "h-9 w-full min-w-0";
const ACTION_BUTTON_CLASS = "h-9 w-full sm:w-[140px]";
const EXPORT_TIMEOUT_MS = 120_000;

function LedgerTab() {
  const { user, authEnabled } = useAuth();
  // Kill switch: with auth off the backend lets everyone through and
  // /auth/me reports no user, so a bare role check would hide the button.
  const isManager = !authEnabled || user?.role === "manager";

  // ── filters ──────────────────────────────────────────────────────────────
  const persisted = useMemo(
    () => readFilterState(FILTERS_KEY, VIEW_FILTER_DEFAULTS),
    [],
  );
  const [datePreset, setDatePreset] = useState<DatePreset | typeof CUSTOM>(
    (DATE_PRESETS as readonly string[]).includes(persisted.datePreset)
      ? persisted.datePreset
      : VIEW_FILTER_DEFAULTS.datePreset,
  );
  const [customRange, setCustomRange] = useState<PickerRange | undefined>(undefined);
  const [channel, setChannel] = useState(persisted.channel);
  const [direction, setDirection] = useState(persisted.direction);
  const [authority, setAuthority] = useState(persisted.authority);
  const [pendingOnly, setPendingOnly] = useState(persisted.pendingOnly === true);
  // Search: the box holds what is being typed, `search` what was applied.
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [searchInvalid, setSearchInvalid] = useState(false);

  const isCustom = datePreset === CUSTOM;
  const viewFilters: LedgerViewFilters = {
    // Placeholder while a custom range is active; skipFields below keeps the
    // last real preset in storage instead.
    datePreset: isCustom ? VIEW_FILTER_DEFAULTS.datePreset : datePreset,
    channel,
    direction,
    authority,
    pendingOnly,
  };
  const skipFields = useMemo<(keyof LedgerViewFilters)[]>(
    () => (isCustom ? ["datePreset"] : []),
    [isCustom],
  );
  useFilterPersist(FILTERS_KEY, VIEW_FILTER_DEFAULTS, viewFilters, { skipFields });

  // A custom range only filters once both ends are picked.
  const customFrom = customRange?.from ? toYmd(customRange.from) : null;
  const customTo = customRange?.to ? toYmd(customRange.to) : null;
  const appliedRange = useMemo<DateRange | null>(
    () => (isCustom && customFrom && customTo ? { from: customFrom, to: customTo } : null),
    [isCustom, customFrom, customTo],
  );

  const query = useMemo<LedgerQuery>(
    () => ({
      // With "custom" chosen but no complete range yet, show everything
      // rather than silently applying some other preset.
      datePreset: isCustom ? "all" : datePreset,
      channel,
      direction,
      authority,
      pendingOnly,
      customRange: appliedRange,
      search,
    }),
    [isCustom, datePreset, channel, direction, authority, pendingOnly, appliedRange, search],
  );

  // ── list ─────────────────────────────────────────────────────────────────
  const [sort, setSort] = useState<SortKey>(DEFAULT_SORT);
  const [order, setOrder] = useState<SortOrder>(DEFAULT_ORDER);
  const [rows, setRows] = useState<Incident[]>([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [replicaLive, setReplicaLive] = useState(true);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  const refresh = useCallback(() => setRefreshTick((t) => t + 1), []);

  // The page number is tied to the filter it was chosen under, so any filter
  // change lands on page 1 in the same render (no reset effect, no request
  // for a stale page).
  const filterQs = useMemo(() => buildFilterParams(query, new Date()).toString(), [query]);
  const [pageState, setPageState] = useState({ qs: filterQs, page: 1 });
  const page = pageState.qs === filterQs ? pageState.page : 1;
  const setPage = useCallback(
    (next: number) => setPageState({ qs: filterQs, page: next }),
    [filterQs],
  );

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    (async () => {
      try {
        const qs = buildListQuery(
          query,
          { page, pageSize: PAGE_SIZE, sort, order },
          new Date(),
        );
        const res = await apiFetch(`${API_BASE}/incidents?${qs}`, {
          signal: controller.signal,
        });
        if (!res.ok) {
          setLoadError(await readErrorDetail(res));
          return;
        }
        const body = (await res.json()) as IncidentListResponse;
        setRows(body.data);
        setTotal(body.total);
        setTotalPages(Math.max(1, body.total_pages));
        setReplicaLive(body.statistics?.replica_live !== false);
        setLoadError(null);
      } catch (err) {
        if (isAbortError(err)) return;
        setLoadError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
    // `query` is covered by `filterQs` (same inputs, stable string).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterQs, page, sort, order, refreshTick]);

  const applySearch = () => {
    const normalized = normalizeSearch(searchInput);
    if (normalized === null) {
      setSearchInvalid(true);
      return;
    }
    setSearchInvalid(false);
    setSearch(normalized);
  };

  const clearSearch = () => {
    setSearchInput("");
    setSearchInvalid(false);
    setSearch("");
  };

  const handleSortChange = useCallback(
    (nextSort: SortKey, nextOrder: SortOrder) => {
      setSort(nextSort);
      setOrder(nextOrder);
      setPage(1);
    },
    [setPage],
  );

  // ── sheet + delete ───────────────────────────────────────────────────────
  const [sheetOpen, setSheetOpen] = useState(false);
  const [intent, setIntent] = useState<SheetIntent>({ kind: "create" });
  const [deleting, setDeleting] = useState<Incident | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const openSheet = (next: SheetIntent) => {
    setIntent(next);
    setSheetOpen(true);
  };

  const actionsRef = useRef<LedgerRowActions>({
    onEdit: () => {},
    onAddToGroup: () => {},
    onDelete: () => {},
    canDelete: false,
  });
  actionsRef.current = {
    onEdit: (row) => openSheet({ kind: "edit", row }),
    onAddToGroup: (row) => openSheet({ kind: "add-to-group", row }),
    onDelete: (row) => setDeleting(row),
    canDelete: isManager,
  };

  const columnPersist = useGridColumnPersist(GRID_STORAGE_KEYS.CS_FUND_RISK_LEDGER);
  const columnDefs = useLedgerColumnDefs(actionsRef);

  const handleDelete = async () => {
    if (!deleting) return;
    setDeleteBusy(true);
    try {
      const res = await apiFetch(
        `${API_BASE}/incidents/${deleting.id}`,
        { method: "DELETE" },
        { retries: 0 },
      );
      if (res.status === 403) {
        toast.error("删除失败: 只有 manager 可以删除登记记录");
        return;
      }
      if (!res.ok) {
        toast.error(`删除失败: ${await readErrorDetail(res)}`);
        return;
      }
      toast.success(`已删除订单 ${deleting.transaction_id ?? `#${deleting.id}`} 的登记`);
      setDeleting(null);
      refresh();
    } catch (err) {
      toast.error(`删除失败: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setDeleteBusy(false);
    }
  };

  // ── export ───────────────────────────────────────────────────────────────
  const [exporting, setExporting] = useState(false);
  const exportCtrl = useRef<AbortController | null>(null);
  useEffect(() => () => exportCtrl.current?.abort(), []);

  const handleExport = async () => {
    exportCtrl.current?.abort();
    const controller = new AbortController();
    exportCtrl.current = controller;
    setExporting(true);
    try {
      const params = buildFilterParams(query, new Date());
      params.set("sort", sort);
      params.set("order", order);
      const res = await apiFetch(
        `${API_BASE}/export?${params.toString()}`,
        { signal: controller.signal },
        { timeoutMs: EXPORT_TIMEOUT_MS, retries: 0 },
      );
      if (!res.ok) {
        toast.error(`导出失败: ${await readErrorDetail(res)}`);
        return;
      }
      const fileName = filenameFromDisposition(
        res.headers.get("Content-Disposition"),
        `fund_risk_ledger_${toYmd(new Date())}.xlsx`,
      );
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fileName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      if (isAbortError(err)) return;
      toast.error(`导出失败: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      if (exportCtrl.current === controller) {
        exportCtrl.current = null;
        setExporting(false);
      }
    }
  };

  const rangeLabel =
    customFrom && customTo
      ? `${customFrom} - ${customTo}`
      : customFrom
        ? `${customFrom} - 选择结束日期`
        : "选择日期范围";

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="rounded-xl border bg-card px-4 py-4 md:px-6">
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3">
            <Select
              value={datePreset}
              onValueChange={(v) => setDatePreset(v as DatePreset | typeof CUSTOM)}
            >
              <SelectTrigger className={FILTER_CONTROL_CLASS} aria-label="登记日期">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DATE_PRESETS.map((p) => (
                  <SelectItem key={p} value={p}>
                    登记日期 · {DATE_PRESET_LABELS[p]}
                  </SelectItem>
                ))}
                <SelectItem value={CUSTOM}>登记日期 · 自定义范围</SelectItem>
              </SelectContent>
            </Select>

            {isCustom && (
              <Popover>
                <PopoverTrigger asChild>
                  <Button
                    variant="outline"
                    className={cn(FILTER_CONTROL_CLASS, "justify-start gap-2 font-normal")}
                  >
                    <CalendarIcon className="h-4 w-4 shrink-0" />
                    <span className="truncate tabular-nums">{rangeLabel}</span>
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-0" align="start">
                  <Calendar
                    mode="range"
                    selected={customRange}
                    onSelect={setCustomRange}
                    numberOfMonths={2}
                    defaultMonth={customRange?.from ?? new Date()}
                    captionLayout="dropdown"
                  />
                </PopoverContent>
              </Popover>
            )}

            <Select value={channel} onValueChange={setChannel}>
              <SelectTrigger className={FILTER_CONTROL_CLASS} aria-label="渠道">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>全部渠道</SelectItem>
                {/* A persisted channel that has since left the list stays selectable. */}
                {channel !== ALL && !CHANNEL_OPTIONS.includes(channel) && (
                  <SelectItem value={channel}>{channel}</SelectItem>
                )}
                {CHANNEL_OPTIONS.map((c) => (
                  <SelectItem key={c} value={c}>
                    {c}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={direction} onValueChange={setDirection}>
              <SelectTrigger className={FILTER_CONTROL_CLASS} aria-label="方向">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>全部方向</SelectItem>
                {DIRECTIONS.map((d) => (
                  <SelectItem key={d} value={d}>
                    {DIRECTION_LABELS[d]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={authority} onValueChange={setAuthority}>
              <SelectTrigger className={FILTER_CONTROL_CLASS} aria-label="封卡单位">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>全部封卡单位</SelectItem>
                {AUTHORITIES.map((a) => (
                  <SelectItem key={a} value={a}>
                    封卡单位 · {AUTHORITY_LABELS[a]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <label
              className={cn(
                FILTER_CONTROL_CLASS,
                "flex cursor-pointer items-center justify-between gap-2 rounded-md border px-3 text-sm",
              )}
            >
              只看待补结果
              <Switch
                checked={pendingOnly}
                onCheckedChange={(v) => setPendingOnly(v === true)}
                aria-label="只看查询结果为空的记录"
              />
            </label>

            <div className="relative">
              <Input
                className={cn(FILTER_CONTROL_CLASS, "pr-16 tabular-nums")}
                inputMode="numeric"
                autoComplete="off"
                placeholder="订单号 / 客户ID，回车查询"
                aria-label="按订单号或客户 ID 查找"
                aria-invalid={searchInvalid}
                value={searchInput}
                onChange={(e) => {
                  setSearchInput(e.target.value);
                  if (searchInvalid) setSearchInvalid(false);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") applySearch();
                }}
              />
              {/* Inside the box so showing / hiding it never shifts the row. */}
              {(searchInput || search) && (
                <button
                  type="button"
                  aria-label="清除查找"
                  className="absolute right-8 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
                  onClick={clearSearch}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
              <button
                type="button"
                aria-label="查找"
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
                onClick={applySearch}
              >
                <Search className="h-4 w-4" />
              </button>
            </div>
          </div>

          {searchInvalid && (
            <p className="text-sm text-destructive">
              只能按纯数字的订单号或客户 ID 查找。
            </p>
          )}

          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-end">
            {!replicaLive && !loading && (
              <span className="text-xs text-amber-700 dark:text-amber-300 sm:mr-auto">
                CRM 从库暂时读不到：订单信息显示的是登记时的快照，客户姓名不显示。
              </span>
            )}
            <Button
              variant="outline"
              className={ACTION_BUTTON_CLASS}
              onClick={refresh}
              disabled={loading}
            >
              <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
              刷新
            </Button>
            <ColumnVisibilityMenu
              persist={columnPersist}
              columnDefs={columnDefs as ColDef<unknown>[]}
              buttonClassName={ACTION_BUTTON_CLASS}
            />
            <Button
              variant="outline"
              className={ACTION_BUTTON_CLASS}
              onClick={() => void handleExport()}
              disabled={exporting}
            >
              {exporting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Download className="h-4 w-4" />
              )}
              {exporting ? "导出中…" : "导出"}
            </Button>
            <Button
              className={ACTION_BUTTON_CLASS}
              onClick={() => openSheet({ kind: "create" })}
            >
              <Plus className="h-4 w-4" />
              新增登记
            </Button>
          </div>
        </div>
      </div>

      {loadError && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <span>加载台账失败：{loadError}</span>
          <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>
            重试
          </Button>
        </div>
      )}

      <LedgerGrid
        rows={rows}
        columnDefs={columnDefs}
        columnPersist={columnPersist}
        loading={loading}
        page={page}
        totalPages={totalPages}
        total={total}
        onPageChange={setPage}
        sort={sort}
        order={order}
        onSortChange={handleSortChange}
      />

      <EntrySheet
        open={sheetOpen}
        intent={intent}
        onOpenChange={setSheetOpen}
        onChanged={refresh}
      />

      {/* Delete confirm (manager only) */}
      <Dialog open={deleting !== null} onOpenChange={(o) => !o && !deleteBusy && setDeleting(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>删除登记</DialogTitle>
            <DialogDescription>
              确认删除订单 {deleting?.transaction_id ?? `#${deleting?.id}`}{" "}
              的登记？删除后这条记录不再出现在台账和导出里，操作会留下审计记录。
              {deleting && deleting.group_size > 1
                ? ` 同一事件组的其他 ${deleting.group_size - 1} 笔订单不受影响。`
                : ""}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleting(null)} disabled={deleteBusy}>
              取消
            </Button>
            <Button variant="destructive" onClick={() => void handleDelete()} disabled={deleteBusy}>
              {deleteBusy ? "删除中..." : "确认删除"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default function FundRiskLedgerPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const activeTab: LedgerTabKey = isTab(tabParam) ? tabParam : DEFAULT_TAB;

  // Drop an unknown ?tab= so the address bar matches what is rendered.
  useEffect(() => {
    if (tabParam !== null && tabParam !== activeTab) {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.delete("tab");
          return next;
        },
        { replace: true },
      );
    }
  }, [tabParam, activeTab, setSearchParams]);

  // Restore the last-active tab only when the URL is silent — a deep link
  // must never be overridden by a stale local preference.
  useEffect(() => {
    if (tabParam !== null) return;
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(ACTIVE_TAB_KEY);
    } catch {
      // private mode / storage disabled — stay on the default
    }
    if (!isTab(saved) || saved === DEFAULT_TAB) return;
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("tab", saved);
        return next;
      },
      { replace: true },
    );
    // Mount only; afterwards onTabChange keeps storage in sync.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onTabChange = useCallback(
    (value: string) => {
      if (!isTab(value)) return;
      try {
        localStorage.setItem(ACTIVE_TAB_KEY, value);
      } catch {
        // ignore
      }
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          // Default tab -> omit the param for a shorter URL.
          if (value === DEFAULT_TAB) next.delete("tab");
          else next.set("tab", value);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  return (
    <div className="flex-1 space-y-4 p-4 md:p-6">
      <Tabs value={activeTab} onValueChange={onTabChange} className="w-full">
        <TabsList className="grid w-full max-w-[180px] grid-cols-1">
          <TabsTrigger value="ledger" className="gap-1.5">
            <ClipboardList className="h-4 w-4" />
            台账
          </TabsTrigger>
        </TabsList>
        <TabsContent value="ledger" className="mt-4">
          <LedgerTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
