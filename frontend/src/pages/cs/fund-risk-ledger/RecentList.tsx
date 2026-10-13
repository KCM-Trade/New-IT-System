/**
 * 「最近登记」 under the entry input: the last few records as quiet text rows.
 * Clicking a row opens that record for editing. Renders nothing when empty.
 */
import { formatHk } from "@/lib/hk-time";

import { fmtAmount } from "./helpers";
import type { Incident } from "./types";
import { useLedgerT } from "./useLedgerT";

interface RecentListProps {
  rows: Incident[];
  onOpen: (row: Incident) => void;
  disabled?: boolean;
}

function clientLabel(row: Incident): string {
  const id = row.order?.client_id;
  const name = row.order?.client_name;
  if (id === null || id === undefined) return name ?? "—";
  return name ? `${id} ${name}` : String(id);
}

export function RecentList({ rows, onOpen, disabled = false }: RecentListProps) {
  const t = useLedgerT();
  if (rows.length === 0) return null;
  return (
    <div className="pt-6">
      <div className="px-2 pb-1 text-xs text-muted-foreground">
        {t("fundRiskLedgerPage.entry.recentTitle")}
      </div>
      <ul>
        {rows.map((row) => {
          const parts = [
            clientLabel(row),
            row.order?.channel ?? "—",
            fmtAmount(row.order?.amount_cny),
            row.created_by,
            formatHk(row.created_at),
          ];
          return (
            <li key={row.id}>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onOpen(row)}
                title={t("fundRiskLedgerPage.entry.recentOpen")}
                className="flex w-full flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded-md px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-muted focus-visible:bg-muted focus-visible:outline-none disabled:pointer-events-none disabled:opacity-60"
              >
                <span className="font-medium tabular-nums text-foreground">
                  {row.transaction_id ?? `#${row.id}`}
                </span>
                {parts.map((text, i) => (
                  // Fixed-length list in a fixed order: the index is a stable key.
                  <span key={i} className="flex min-w-0 items-baseline gap-x-2">
                    <span aria-hidden="true" className="opacity-50">
                      ·
                    </span>
                    <span className="break-words tabular-nums">{text}</span>
                  </span>
                ))}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
