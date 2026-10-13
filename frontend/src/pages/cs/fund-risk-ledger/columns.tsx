/**
 * Column definitions of the ledger grid. Kept apart from LedgerGrid.tsx so
 * that file exports components only (fast refresh).
 *
 * The grid holds every row, so each column sorts and filters client-side from
 * its header. Columns whose cell shows a label or a formatted time expose that
 * same text to the filter (valueGetter / filterValueGetter) — what staff type
 * is what they see.
 */
import { useMemo, type MutableRefObject } from "react";
import type { ColDef, ICellRendererParams } from "ag-grid-community";
import { CopyPlus, Pencil, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { crmUserUrl } from "@/lib/crm-links";
import { formatHk } from "@/lib/hk-time";

import {
  effectiveVenue,
  fmtAmount,
  fmtFreeze,
  fmtRegion,
} from "./helpers";
import {
  AUTHORITY_LABEL_KEYS,
  DIRECTION_LABEL_KEYS,
  FROZEN_SCOPE_LABEL_KEYS,
  labelOf,
  TRANSFER_SUCCEEDED_LABEL_KEYS,
} from "./labels";
import type { Incident } from "./types";
import { useLedgerT } from "./useLedgerT";

/** Row actions, read through a ref so columnDefs stay referentially stable. */
export interface LedgerRowActions {
  onEdit: (row: Incident) => void;
  onAddToGroup: (row: Incident) => void;
  onDelete: (row: Incident) => void;
  /** Soft delete is manager-only; the button is not rendered otherwise. */
  canDelete: boolean;
}

/** Long free text: at most three lines in the cell, full text on hover. */
function renderLongText(text: string) {
  return (
    <div className="line-clamp-3 py-1" title={text}>
      {text}
    </div>
  );
}

export function useLedgerColumnDefs(
  actionsRef: MutableRefObject<LedgerRowActions>,
): ColDef<Incident>[] {
  // Column visibility is persisted, so nothing here may vary by mode or by
  // user (the manager-only delete button reads the ref). The one dependency is
  // the language: header text and label values follow it, every colId / field
  // stays the same. LedgerGrid remounts the grid on a language change, so the
  // new defs never re-apply their default width / pinning over a saved layout.
  const t = useLedgerT();
  return useMemo<ColDef<Incident>[]>(
    () => [
      {
        headerName: t("fundRiskLedgerPage.grid.group"),
        field: "incident_group_id",
        width: 90,
        filter: "agNumberColumnFilter",
        cellRenderer: (p: ICellRendererParams<Incident>) => {
          const row = p.data;
          if (!row) return null;
          return (
            <span className="tabular-nums">
              #{row.incident_group_id}
              {row.group_size > 1 && (
                <span
                  className="ml-1 text-xs text-muted-foreground"
                  title={t("fundRiskLedgerPage.grid.groupSizeTitle", { count: row.group_size })}
                >
                  ×{row.group_size}
                </span>
              )}
            </span>
          );
        },
      },
      {
        headerName: t("fundRiskLedgerPage.grid.transactionId"),
        field: "transaction_id",
        width: 130,
        cellRenderer: (p: ICellRendererParams<Incident>) => {
          const row = p.data;
          if (!row) return null;
          return (
            <span className="inline-flex flex-wrap items-center gap-1">
              <span className="tabular-nums">{row.transaction_id ?? "—"}</span>
              {row.order?.is_test_account && (
                <Badge
                  variant="outline"
                  className="border-amber-400 px-1 py-0 text-[10px] text-amber-700 dark:text-amber-300"
                >
                  {t("fundRiskLedgerPage.grid.testBadge")}
                </Badge>
              )}
              {row.source === "import" && (
                <Badge variant="secondary" className="px-1 py-0 text-[10px]">
                  {t("fundRiskLedgerPage.grid.importBadge")}
                </Badge>
              )}
            </span>
          );
        },
      },
      {
        headerName: t("fundRiskLedgerPage.grid.clientId"),
        colId: "client_id",
        width: 100,
        valueGetter: (p) => p.data?.order?.client_id ?? null,
        cellRenderer: (p: ICellRendererParams<Incident>) => {
          const url = crmUserUrl(p.value as number | null);
          if (!url) return "—";
          return (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="tabular-nums text-blue-600 hover:underline dark:text-blue-400"
            >
              {p.value}
            </a>
          );
        },
      },
      {
        headerName: t("fundRiskLedgerPage.grid.client"),
        colId: "client_name",
        width: 130,
        valueGetter: (p) => p.data?.order?.client_name ?? "—",
      },
      {
        headerName: t("fundRiskLedgerPage.grid.venue"),
        colId: "venue",
        width: 90,
        valueGetter: (p) => (p.data ? (effectiveVenue(p.data) ?? "—") : "—"),
      },
      {
        headerName: t("fundRiskLedgerPage.grid.direction"),
        colId: "direction",
        width: 80,
        valueGetter: (p) => labelOf(t, DIRECTION_LABEL_KEYS, p.data?.order?.direction),
      },
      {
        headerName: t("fundRiskLedgerPage.grid.channel"),
        colId: "channel",
        width: 130,
        valueGetter: (p) => p.data?.order?.channel ?? "—",
      },
      {
        headerName: t("fundRiskLedgerPage.grid.amount"),
        colId: "amount_cny",
        width: 120,
        headerClass: "ag-right-aligned-header",
        cellClass: "text-right tabular-nums",
        filter: "agNumberColumnFilter",
        valueGetter: (p) => p.data?.order?.amount_cny ?? null,
        valueFormatter: (p) => fmtAmount(p.value as number | null),
      },
      {
        headerName: t("fundRiskLedgerPage.grid.status"),
        colId: "txn_status",
        width: 100,
        valueGetter: (p) => p.data?.order?.txn_status ?? "—",
      },
      {
        headerName: t("fundRiskLedgerPage.grid.processedAt"),
        colId: "txn_processed_at",
        width: 160,
        // Sorts on the ISO value, filters on the HK text shown in the cell.
        valueGetter: (p) => p.data?.order?.txn_processed_at ?? null,
        valueFormatter: (p) => formatHk(p.value as string | null),
        filterValueGetter: (p) => formatHk(p.data?.order?.txn_processed_at),
      },
      {
        headerName: t("fundRiskLedgerPage.grid.transferSucceeded"),
        // colId = the former field name, so saved column state still matches.
        colId: "transfer_succeeded",
        width: 90,
        valueGetter: (p) => labelOf(t, TRANSFER_SUCCEEDED_LABEL_KEYS, p.data?.transfer_succeeded),
      },
      {
        headerName: t("fundRiskLedgerPage.grid.frozenScope"),
        // colId = the former field name, so saved column state still matches.
        colId: "frozen_scope",
        width: 100,
        valueGetter: (p) => labelOf(t, FROZEN_SCOPE_LABEL_KEYS, p.data?.frozen_scope),
      },
      {
        headerName: t("fundRiskLedgerPage.grid.freezeDays"),
        colId: "freeze_days",
        width: 120,
        valueGetter: (p) => fmtFreeze(p.data?.freeze_days, p.data?.freeze_note, t),
      },
      {
        headerName: t("fundRiskLedgerPage.grid.authority"),
        // colId = the former field name, so saved column state still matches.
        colId: "authority",
        width: 100,
        valueGetter: (p) => labelOf(t, AUTHORITY_LABEL_KEYS, p.data?.authority),
      },
      {
        headerName: t("fundRiskLedgerPage.grid.clientBankRegion"),
        colId: "client_bank_region",
        width: 130,
        valueGetter: (p) =>
          fmtRegion(p.data?.client_bank_province, p.data?.client_bank_city),
      },
      {
        headerName: t("fundRiskLedgerPage.grid.traderBankRegion"),
        colId: "trader_bank_region",
        width: 130,
        valueGetter: (p) =>
          fmtRegion(p.data?.trader_bank_province, p.data?.trader_bank_city),
      },
      {
        headerName: t("fundRiskLedgerPage.grid.reportedDate"),
        field: "reported_date",
        width: 115,
        valueFormatter: (p) => (p.value as string | null) ?? "—",
      },
      {
        headerName: t("fundRiskLedgerPage.grid.event"),
        field: "event_text",
        width: 260,
        cellRenderer: (p: ICellRendererParams<Incident>) => {
          const text = p.data?.event_text;
          return text ? renderLongText(text) : "—";
        },
      },
      {
        headerName: t("fundRiskLedgerPage.grid.result"),
        field: "result_text",
        width: 260,
        cellRenderer: (p: ICellRendererParams<Incident>) => {
          const text = p.data?.result_text;
          if (text) return renderLongText(text);
          return (
            <span className="text-xs font-medium text-amber-700 dark:text-amber-300">
              {t("fundRiskLedgerPage.grid.resultPending")}
            </span>
          );
        },
      },
      { headerName: t("fundRiskLedgerPage.grid.createdBy"), field: "created_by", width: 150 },
      {
        headerName: t("fundRiskLedgerPage.grid.updatedAt"),
        field: "updated_at",
        width: 160,
        valueFormatter: (p) => formatHk(p.value as string | null),
        filterValueGetter: (p) => formatHk(p.data?.updated_at),
        tooltipValueGetter: (p) =>
          p.data
            ? t("fundRiskLedgerPage.grid.lastEditedBy", { user: p.data.updated_by })
            : "",
      },
      {
        // Renderer-only column -> explicit stable colId (ui-pitfalls §2).
        headerName: t("fundRiskLedgerPage.grid.actions"),
        colId: "actions",
        width: 124,
        sortable: false,
        filter: false,
        suppressMovable: true,
        pinned: "right",
        cellRenderer: (p: ICellRendererParams<Incident>) => {
          const row = p.data;
          if (!row) return null;
          const actions = actionsRef.current;
          return (
            <div className="flex h-full items-center gap-0.5">
              <Button
                variant="ghost"
                size="sm"
                className="h-8 w-8 p-0"
                title={t("fundRiskLedgerPage.grid.edit")}
                aria-label={t("fundRiskLedgerPage.grid.edit")}
                onClick={() => actions.onEdit(row)}
              >
                <Pencil className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-8 w-8 p-0"
                title={t("fundRiskLedgerPage.grid.addToGroup")}
                aria-label={t("fundRiskLedgerPage.grid.addToGroup")}
                onClick={() => actions.onAddToGroup(row)}
              >
                <CopyPlus className="h-4 w-4" />
              </Button>
              {actions.canDelete && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 p-0 text-destructive hover:text-destructive"
                  title={t("fundRiskLedgerPage.grid.delete")}
                  aria-label={t("fundRiskLedgerPage.grid.delete")}
                  onClick={() => actions.onDelete(row)}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              )}
            </div>
          );
        },
      },
    ],
    [actionsRef, t],
  );
}
