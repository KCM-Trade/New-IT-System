/**
 * Column definitions of the ledger grid. Kept apart from LedgerGrid.tsx so
 * that file exports components only (fast refresh).
 *
 * Only the two columns in the backend sort whitelist are sortable — a header
 * arrow on any other column would be a sort that does not happen.
 */
import { useMemo, type MutableRefObject } from "react";
import type { ColDef, ICellRendererParams } from "ag-grid-community";
import { CopyPlus, Pencil, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { crmUserUrl } from "@/lib/crm-links";
import { formatHk } from "@/lib/hk-time";

import {
  DEFAULT_ORDER,
  effectiveVenue,
  fmtAmount,
  fmtFreeze,
  fmtRegion,
} from "./helpers";
import {
  AUTHORITY_LABELS,
  DIRECTION_LABELS,
  FROZEN_SCOPE_LABELS,
  labelOf,
  TRANSFER_SUCCEEDED_LABELS,
} from "./labels";
import type { Incident } from "./types";

/** Row actions, read through a ref so columnDefs stay referentially stable. */
export interface LedgerRowActions {
  onEdit: (row: Incident) => void;
  onAddToGroup: (row: Incident) => void;
  onDelete: (row: Incident) => void;
  /** Soft delete is manager-only; the button is not rendered otherwise. */
  canDelete: boolean;
}

// Server-sorted column: the grid shows the arrow, the backend does the work.
const SERVER_SORTED = { sortable: true, comparator: () => 0 } as const;

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
  // Deps stay empty: column visibility is persisted, so nothing here may vary
  // by mode or by user (the manager-only delete button reads the ref).
  return useMemo<ColDef<Incident>[]>(
    () => [
      {
        headerName: "事件组",
        field: "incident_group_id",
        width: 90,
        cellRenderer: (p: ICellRendererParams<Incident>) => {
          const row = p.data;
          if (!row) return null;
          return (
            <span className="tabular-nums">
              #{row.incident_group_id}
              {row.group_size > 1 && (
                <span
                  className="ml-1 text-xs text-muted-foreground"
                  title={`同一事件共 ${row.group_size} 笔订单`}
                >
                  ×{row.group_size}
                </span>
              )}
            </span>
          );
        },
      },
      {
        headerName: "订单号",
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
                  测试
                </Badge>
              )}
              {row.source === "import" && (
                <Badge variant="secondary" className="px-1 py-0 text-[10px]">
                  导入
                </Badge>
              )}
            </span>
          );
        },
      },
      {
        headerName: "客户ID",
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
        headerName: "客户",
        colId: "client_name",
        width: 130,
        valueGetter: (p) => p.data?.order?.client_name ?? "—",
      },
      {
        headerName: "场地",
        colId: "venue",
        width: 90,
        valueGetter: (p) => (p.data ? (effectiveVenue(p.data) ?? "—") : "—"),
      },
      {
        headerName: "方向",
        colId: "direction",
        width: 80,
        valueGetter: (p) => labelOf(DIRECTION_LABELS, p.data?.order?.direction),
      },
      {
        headerName: "渠道",
        colId: "channel",
        width: 130,
        valueGetter: (p) => p.data?.order?.channel ?? "—",
      },
      {
        headerName: "金额CNY",
        colId: "amount_cny",
        width: 120,
        headerClass: "ag-right-aligned-header",
        cellClass: "text-right tabular-nums",
        valueGetter: (p) => p.data?.order?.amount_cny ?? null,
        valueFormatter: (p) => fmtAmount(p.value as number | null),
      },
      {
        headerName: "订单状态",
        colId: "txn_status",
        width: 100,
        valueGetter: (p) => p.data?.order?.txn_status ?? "—",
      },
      {
        headerName: "处理时间 (HK)",
        colId: "txn_processed_at",
        width: 160,
        ...SERVER_SORTED,
        valueGetter: (p) => p.data?.order?.txn_processed_at ?? null,
        valueFormatter: (p) => formatHk(p.value as string | null),
      },
      {
        headerName: "转账成功",
        field: "transfer_succeeded",
        width: 90,
        valueFormatter: (p) => labelOf(TRANSFER_SUCCEEDED_LABELS, p.value as string),
      },
      {
        headerName: "被封资料",
        field: "frozen_scope",
        width: 100,
        valueFormatter: (p) => labelOf(FROZEN_SCOPE_LABELS, p.value as string),
      },
      {
        headerName: "封卡天数",
        colId: "freeze_days",
        width: 120,
        valueGetter: (p) => fmtFreeze(p.data?.freeze_days, p.data?.freeze_note),
      },
      {
        headerName: "封卡单位",
        field: "authority",
        width: 100,
        valueFormatter: (p) => labelOf(AUTHORITY_LABELS, p.value as string),
      },
      {
        headerName: "客户银行省市",
        colId: "client_bank_region",
        width: 130,
        valueGetter: (p) =>
          fmtRegion(p.data?.client_bank_province, p.data?.client_bank_city),
      },
      {
        headerName: "交易员卡省市",
        colId: "trader_bank_region",
        width: 130,
        valueGetter: (p) =>
          fmtRegion(p.data?.trader_bank_province, p.data?.trader_bank_city),
      },
      {
        headerName: "登记日期",
        field: "reported_date",
        width: 115,
        ...SERVER_SORTED,
        initialSort: DEFAULT_ORDER,
        valueFormatter: (p) => (p.value as string | null) ?? "—",
      },
      {
        headerName: "事件",
        field: "event_text",
        width: 260,
        cellRenderer: (p: ICellRendererParams<Incident>) => {
          const text = p.data?.event_text;
          return text ? renderLongText(text) : "—";
        },
      },
      {
        headerName: "查询结果",
        field: "result_text",
        width: 260,
        cellRenderer: (p: ICellRendererParams<Incident>) => {
          const text = p.data?.result_text;
          if (text) return renderLongText(text);
          return (
            <span className="text-xs font-medium text-amber-700 dark:text-amber-300">
              待补
            </span>
          );
        },
      },
      { headerName: "登记人", field: "created_by", width: 150 },
      {
        headerName: "更新时间 (HK)",
        field: "updated_at",
        width: 160,
        valueFormatter: (p) => formatHk(p.value as string | null),
        tooltipValueGetter: (p) =>
          p.data ? `最后修改人：${p.data.updated_by}` : "",
      },
      {
        // Renderer-only column -> explicit stable colId (ui-pitfalls §2).
        headerName: "操作",
        colId: "actions",
        width: 124,
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
                title="编辑"
                aria-label="编辑"
                onClick={() => actions.onEdit(row)}
              >
                <Pencil className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-8 w-8 p-0"
                title="同事件加单"
                aria-label="同事件加单"
                onClick={() => actions.onAddToGroup(row)}
              >
                <CopyPlus className="h-4 w-4" />
              </Button>
              {actions.canDelete && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 p-0 text-destructive hover:text-destructive"
                  title="删除"
                  aria-label="删除"
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
    [actionsRef],
  );
}
