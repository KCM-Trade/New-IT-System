/**
 * Ledger list: AG-Grid with server-side pagination and server-side sort.
 *
 * Column defs (columns.tsx) and column state (useGridColumnPersist) are owned
 * by the parent so the toolbar's ColumnVisibilityMenu can share them.
 */
import { useCallback } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, SortChangedEvent } from "ag-grid-community";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { useTheme } from "@/components/theme-provider";
import { Button } from "@/components/ui/button";
import type { UseGridColumnPersistResult } from "@/hooks/useGridColumnPersist";
import { cn } from "@/lib/utils";
import { useGridThemeStyle } from "@/pages/cs/fund-flow/gridTheme";

import { COL_TO_SORT, DEFAULT_ORDER, DEFAULT_SORT } from "./helpers";
import type { Incident, SortKey, SortOrder } from "./types";

// Global wrap so narrowed columns never silently clip (ag-grid-style §4b).
const WRAP_CELL_STYLE = {
  whiteSpace: "normal",
  lineHeight: "1.35",
  overflowWrap: "anywhere",
} as const;

const DEFAULT_COL_DEF: ColDef = {
  // Server-paged list: client-side sort / filter would only act on one page.
  sortable: false,
  filter: false,
  resizable: true,
  minWidth: 70,
  wrapHeaderText: true,
  autoHeaderHeight: true,
  wrapText: true,
  autoHeight: true,
  cellStyle: WRAP_CELL_STYLE,
};

interface LedgerGridProps {
  rows: Incident[];
  columnDefs: ColDef<Incident>[];
  columnPersist: UseGridColumnPersistResult;
  loading: boolean;
  page: number;
  totalPages: number;
  total: number;
  onPageChange: (page: number) => void;
  sort: SortKey;
  order: SortOrder;
  onSortChange: (sort: SortKey, order: SortOrder) => void;
}

export function LedgerGrid({
  rows,
  columnDefs,
  columnPersist,
  loading,
  page,
  totalPages,
  total,
  onPageChange,
  sort,
  order,
  onSortChange,
}: LedgerGridProps) {
  const { theme } = useTheme();
  const isDarkMode = theme === "dark";
  const themeStyle = useGridThemeStyle(isDarkMode);

  // Grid sort -> server sort whitelist. Also fires when the persist hook
  // restores a saved sort on mount, which is wanted: the restored header
  // arrow then matches the order the server returns.
  const handleSortChanged = useCallback(
    (e: SortChangedEvent<Incident>) => {
      columnPersist.gridEventProps.onSortChanged();
      const state = e.api.getColumnState();
      if (!Array.isArray(state)) return;
      const sorted = state
        .filter((c) => c.sort)
        .sort((a, b) => (a.sortIndex ?? 0) - (b.sortIndex ?? 0))[0];
      const mapped = sorted ? COL_TO_SORT[sorted.colId] : undefined;
      const nextSort = mapped ?? DEFAULT_SORT;
      const nextOrder = mapped ? (sorted!.sort as SortOrder) : DEFAULT_ORDER;
      if (nextSort !== sort || nextOrder !== order) onSortChange(nextSort, nextOrder);
    },
    [columnPersist.gridEventProps, sort, order, onSortChange],
  );

  return (
    <div className="space-y-3">
      <div
        className={cn(
          isDarkMode ? "ag-theme-quartz-dark" : "ag-theme-quartz",
          "h-[calc(100vh-330px)] min-h-[420px] w-full overflow-hidden rounded-xl border",
        )}
        style={themeStyle}
      >
        <AgGridReact<Incident>
          rowData={rows}
          columnDefs={columnDefs}
          defaultColDef={DEFAULT_COL_DEF}
          gridOptions={{ theme: "legacy", enableBrowserTooltips: true }}
          sortingOrder={["desc", "asc", null]}
          loading={loading}
          animateRows={false}
          enableCellTextSelection
          suppressCellFocus
          getRowId={(p) => String(p.data.id)}
          overlayNoRowsTemplate={
            '<span style="padding:12px;color:var(--muted-foreground,#888)">当前筛选条件下没有登记记录。点右上角「新增登记」开始录入。</span>'
          }
          // Compose persist events — never spread gridEventProps.
          onGridReady={(e) => columnPersist.gridEventProps.onGridReady(e)}
          onSortChanged={handleSortChanged}
          onColumnMoved={columnPersist.gridEventProps.onColumnMoved}
          onColumnVisible={columnPersist.gridEventProps.onColumnVisible}
          onColumnPinned={columnPersist.gridEventProps.onColumnPinned}
          onColumnResized={columnPersist.gridEventProps.onColumnResized}
        />
      </div>

      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span className="tabular-nums">
          共 {total} 条 · 第 {page}/{totalPages} 页
        </span>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1 || loading}
            onClick={() => onPageChange(Math.max(1, page - 1))}
          >
            <ChevronLeft className="h-4 w-4" />
            上一页
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= totalPages || loading}
            onClick={() => onPageChange(Math.min(totalPages, page + 1))}
          >
            下一页
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
