/**
 * History grid: the whole ledger client-side, so every column sorts and
 * filters from its header and AG-Grid does the paging.
 *
 * Column defs (columns.tsx) and column state (useGridColumnPersist) are owned
 * by the parent so the toolbar's ColumnVisibilityMenu can share them.
 */
import { AgGridReact } from "ag-grid-react";
import type { ColDef } from "ag-grid-community";

import { useI18n } from "@/components/i18n-provider";
import { useTheme } from "@/components/theme-provider";
import type { UseGridColumnPersistResult } from "@/hooks/useGridColumnPersist";
import { cn } from "@/lib/utils";
import { useGridThemeStyle } from "@/pages/cs/fund-flow/gridTheme";

import type { Incident } from "./types";
import { useLedgerT } from "./useLedgerT";

// Global wrap so narrowed columns never silently clip (ag-grid-style §4b).
const WRAP_CELL_STYLE = {
  whiteSpace: "normal",
  lineHeight: "1.35",
  overflowWrap: "anywhere",
} as const;

const DEFAULT_COL_DEF: ColDef = {
  sortable: true,
  filter: true,
  resizable: true,
  minWidth: 70,
  wrapHeaderText: true,
  autoHeaderHeight: true,
  wrapText: true,
  autoHeight: true,
  cellStyle: WRAP_CELL_STYLE,
};

const PAGE_SIZE_OPTIONS = [20, 50, 100];

interface LedgerGridProps {
  rows: Incident[];
  columnDefs: ColDef<Incident>[];
  columnPersist: UseGridColumnPersistResult;
  loading: boolean;
}

export function LedgerGrid({ rows, columnDefs, columnPersist, loading }: LedgerGridProps) {
  const { theme } = useTheme();
  const isDarkMode = theme === "dark";
  const themeStyle = useGridThemeStyle(isDarkMode);
  const { language } = useI18n();
  const t = useLedgerT();

  return (
    <div
      className={cn(
        isDarkMode ? "ag-theme-quartz-dark" : "ag-theme-quartz",
        "h-[calc(100vh-260px)] min-h-[420px] w-full overflow-hidden rounded-xl border",
      )}
      style={themeStyle}
    >
      <AgGridReact<Incident>
        // Remount on a language change. Updating columnDefs in place makes
        // AG-Grid re-apply each def's width / pinned over what the user set
        // (and the persist hook would then save that); a fresh grid instead
        // restores the saved column state in onGridReady. Header filters are
        // cleared by the remount, which is wanted: they match label text of
        // the previous language.
        key={language}
        rowData={rows}
        columnDefs={columnDefs}
        defaultColDef={DEFAULT_COL_DEF}
        gridOptions={{ theme: "legacy", enableBrowserTooltips: true }}
        sortingOrder={["desc", "asc", null]}
        loading={loading}
        animateRows={false}
        enableCellTextSelection
        suppressCellFocus
        pagination
        paginationPageSize={50}
        paginationPageSizeSelector={PAGE_SIZE_OPTIONS}
        getRowId={(p) => String(p.data.id)}
        overlayNoRowsTemplate={
          `<span style="padding:12px;color:var(--muted-foreground,#888)">${t("fundRiskLedgerPage.history.empty")}</span>`
        }
        // Compose persist events — never spread gridEventProps.
        onGridReady={(e) => columnPersist.gridEventProps.onGridReady(e)}
        onSortChanged={() => columnPersist.gridEventProps.onSortChanged()}
        onColumnMoved={columnPersist.gridEventProps.onColumnMoved}
        onColumnVisible={columnPersist.gridEventProps.onColumnVisible}
        onColumnPinned={columnPersist.gridEventProps.onColumnPinned}
        onColumnResized={columnPersist.gridEventProps.onColumnResized}
      />
    </div>
  );
}
