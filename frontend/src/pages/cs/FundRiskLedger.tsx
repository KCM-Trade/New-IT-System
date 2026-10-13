/**
 * Fund Risk Ledger (出入金风控台账) — /cs/fund-risk-ledger, `cs` module.
 * Spec: docs/features/fund-risk-ledger.md.
 *
 * The page exists to register an incident, so 「登记」 is the default tab: a
 * transaction-id input, then the order block and the form inline in the page.
 * 「历史记录」 is the whole ledger in one client-side AG-Grid (sort / filter from
 * the column headers); its row actions hand a record over to the 登记 tab.
 *
 * Nothing about the list is persisted except the grid's column state.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { ColDef } from "ag-grid-community";
import { Download, History, Loader2, PenLine, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { ColumnVisibilityMenu } from "@/components/ColumnVisibilityMenu";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  GRID_STORAGE_KEYS,
  useGridColumnPersist,
} from "@/hooks/useGridColumnPersist";
import { apiFetch } from "@/lib/fetch";
import { cn } from "@/lib/utils";
import { filenameFromDisposition } from "@/pages/exec-compensation/helpers";
import { readErrorDetail } from "@/pages/risk-alert-mail/helpers";
import { useAuth } from "@/providers/auth-provider";

import { EntryForm, type EntryIntent } from "./fund-risk-ledger/EntryForm";
import {
  useLedgerColumnDefs,
  type LedgerRowActions,
} from "./fund-risk-ledger/columns";
import { LedgerGrid } from "./fund-risk-ledger/LedgerGrid";
import {
  API_BASE,
  HISTORY_PAGE_SIZE,
  isAbortError,
  listQuery,
  MAX_HISTORY_PAGES,
  mergePagesById,
  toYmd,
} from "./fund-risk-ledger/helpers";
import type { Incident, IncidentListResponse } from "./fund-risk-ledger/types";

// ── tabs (URL-controlled, same pattern as /login-ips) ────────────────────────
const TABS = ["entry", "history"] as const;
type LedgerTabKey = (typeof TABS)[number];
const DEFAULT_TAB: LedgerTabKey = "entry";
// Hand-listed in view-profiles/manifest.ts UI_STATE_KEYS.
const ACTIVE_TAB_KEY = "CS_FUND_RISK_LEDGER_ACTIVE_TAB_V1";

function isTab(v: string | null): v is LedgerTabKey {
  return v !== null && (TABS as readonly string[]).includes(v);
}

const ACTION_BUTTON_CLASS = "h-9 w-full sm:w-[140px]";
const EXPORT_TIMEOUT_MS = 120_000;

interface HistoryTabProps {
  /** Hand a record over to the 登记 tab. */
  onOpenInEntry: (intent: EntryIntent) => void;
}

function HistoryTab({ onOpenInEntry }: HistoryTabProps) {
  const { user, authEnabled } = useAuth();
  // Kill switch: with auth off the backend lets everyone through and
  // /auth/me reports no user, so a bare role check would hide the button.
  const isManager = !authEnabled || user?.role === "manager";

  // ── list: every row, fetched page by page ────────────────────────────────
  const [rows, setRows] = useState<Incident[]>([]);
  const [replicaLive, setReplicaLive] = useState(true);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  const refresh = useCallback(() => setRefreshTick((t) => t + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    (async () => {
      try {
        const pages: Incident[][] = [];
        let live = true;
        let totalPages = 1;
        for (let page = 1; page <= totalPages && page <= MAX_HISTORY_PAGES; page++) {
          const res = await apiFetch(
            `${API_BASE}/incidents?${listQuery(page, HISTORY_PAGE_SIZE)}`,
            { signal: controller.signal },
          );
          if (!res.ok) {
            // Keep what is on screen; a partial ledger would read as complete.
            setLoadError(await readErrorDetail(res));
            return;
          }
          const body = (await res.json()) as IncidentListResponse;
          pages.push(body.data);
          totalPages = Math.max(1, body.total_pages);
          if (body.statistics?.replica_live === false) live = false;
        }
        setRows(mergePagesById(pages));
        setReplicaLive(live);
        setLoadError(null);
      } catch (err) {
        if (isAbortError(err)) return;
        setLoadError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [refreshTick]);

  // ── row actions + delete ─────────────────────────────────────────────────
  const [deleting, setDeleting] = useState<Incident | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const actionsRef = useRef<LedgerRowActions>({
    onEdit: () => {},
    onAddToGroup: () => {},
    onDelete: () => {},
    canDelete: false,
  });
  actionsRef.current = {
    onEdit: (row) => onOpenInEntry({ kind: "edit", row }),
    onAddToGroup: (row) => onOpenInEntry({ kind: "add-to-group", row }),
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
      // No filter params: the export is always the whole ledger.
      const res = await apiFetch(
        `${API_BASE}/export`,
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

  return (
    <div className="space-y-4">
      {/* Toolbar: refresh / export / columns, nothing else */}
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
        <ColumnVisibilityMenu
          persist={columnPersist}
          columnDefs={columnDefs as ColDef<unknown>[]}
          buttonClassName={ACTION_BUTTON_CLASS}
        />
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
    // Mount only; afterwards goTab keeps storage in sync.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const goTab = useCallback(
    (value: LedgerTabKey) => {
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

  // Unsaved edits in the 登记 tab (its content unmounts on a tab change), and
  // the tab the user asked for while they were there.
  const entryDirtyRef = useRef(false);
  const handleDirtyChange = useCallback((dirty: boolean) => {
    entryDirtyRef.current = dirty;
  }, []);
  const [pendingTab, setPendingTab] = useState<LedgerTabKey | null>(null);

  const onTabChange = (value: string) => {
    if (!isTab(value) || value === activeTab) return;
    if (activeTab === "entry" && entryDirtyRef.current) {
      setPendingTab(value);
      return;
    }
    goTab(value);
  };

  // A record handed from a history row action to the 登记 tab.
  const [intent, setIntent] = useState<EntryIntent | null>(null);
  const clearIntent = useCallback(() => setIntent(null), []);
  const openInEntry = useCallback(
    (next: EntryIntent) => {
      setIntent(next);
      goTab("entry");
    },
    [goTab],
  );

  return (
    <div className="flex-1 space-y-4 p-4 md:p-6">
      <Tabs value={activeTab} onValueChange={onTabChange} className="w-full">
        <TabsList className="grid w-full max-w-xs grid-cols-2">
          <TabsTrigger value="entry" className="gap-1.5">
            <PenLine className="h-4 w-4" />
            登记
          </TabsTrigger>
          <TabsTrigger value="history" className="gap-1.5">
            <History className="h-4 w-4" />
            历史记录
          </TabsTrigger>
        </TabsList>
        <TabsContent value="entry" className="mt-4">
          <EntryForm
            intent={intent}
            onIntentConsumed={clearIntent}
            onDirtyChange={handleDirtyChange}
          />
        </TabsContent>
        <TabsContent value="history" className="mt-4">
          <HistoryTab onOpenInEntry={openInEntry} />
        </TabsContent>
      </Tabs>

      {/* Leaving the 登记 tab with unsaved edits */}
      <Dialog open={pendingTab !== null} onOpenChange={(o) => !o && setPendingTab(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>放弃未保存的修改？</DialogTitle>
            <DialogDescription>
              登记表单里有还没保存的内容，切换页签后这些内容会丢失。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingTab(null)}>
              继续填写
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                const target = pendingTab;
                setPendingTab(null);
                if (target) goTab(target);
              }}
            >
              放弃修改
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
