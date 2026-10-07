import { memo, useCallback, useMemo, useRef, useState } from "react"

import { CompareColumn } from "@/components/ai/CompareColumn"
import { useI18n } from "@/components/i18n-provider"
import {
  isSelectable,
  promoteModel,
  reconcileOrder,
  uniqueToolNames,
  type AiCompare,
  type CompareRun,
} from "@/lib/ai-compare"
import { cn } from "@/lib/utils"

/**
 * The side-by-side block of a compare turn (OPT-0076 "布局方案").
 *
 * ONE layout rule: a column is never narrower than 480px. The block is a CSS
 * size container and measures itself; with a 16px gap, two columns need 976px
 * and three need 1472px. CSS decides how many columns show — JS only keeps
 * their order:
 *
 *   - the grid's children are [model strip, column, column, column] in display
 *     order, and container queries hide the columns past the k-th
 *     (`nth-child(n+3)` below 976px, `nth-child(n+4)` below 1472px — the strip
 *     is child 1);
 *   - one column is capped at 768px, two at 720px each, and the tracks are
 *     centred, so the strip (spanning all tracks) lines up with the columns;
 *   - a strip chip is "filled" when its column is on screen. That follows the
 *     same container thresholds through the chip's rank in the display order,
 *     so it stays right at any width without measuring anything in JS.
 *
 * Picking a hidden model moves it to the front of the order, which pushes the
 * least recently chosen one out of view (lib/ai-compare `promoteModel`).
 *
 * Range variants (`@max-[…]` / `@[…]:@max-[…]`) are used instead of
 * "set, then override at a wider size" so the result never depends on the
 * order Tailwind emits the rules in.
 */

// Grid tracks + which children are hidden, by number of runs (static strings:
// Tailwind only generates classes it can read literally).
const GRID_BY_COUNT: Record<1 | 2 | 3, string> = {
  1: "grid-cols-[minmax(0,768px)]",
  2: [
    "@max-[976px]:grid-cols-[minmax(0,768px)]",
    "@[976px]:grid-cols-[repeat(2,minmax(0,720px))]",
    "@max-[976px]:[&>*:nth-child(n+3)]:hidden",
  ].join(" "),
  3: [
    "@max-[976px]:grid-cols-[minmax(0,768px)]",
    "@[976px]:@max-[1472px]:grid-cols-[repeat(2,minmax(0,720px))]",
    "@[1472px]:grid-cols-[repeat(3,minmax(0,1fr))]",
    "@max-[976px]:[&>*:nth-child(n+3)]:hidden",
    "@max-[1472px]:[&>*:nth-child(n+4)]:hidden",
  ].join(" "),
}

const CHIP_BASE =
  "inline-flex h-7 max-w-full items-center gap-1.5 rounded-full border border-border px-2.5 text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
// "Filled" = this model's column is currently on screen; keyed by display rank.
const CHIP_FILLED_BY_RANK = [
  "border-transparent bg-secondary font-medium text-foreground",
  "@[976px]:border-transparent @[976px]:bg-secondary @[976px]:font-medium @[976px]:text-foreground",
  "@[1472px]:border-transparent @[1472px]:bg-secondary @[1472px]:font-medium @[1472px]:text-foreground",
]

export interface CompareBlockProps {
  compare: AiCompare
  /** `choose` = live / pending turn; `alternatives` = the answers not chosen, read-only. */
  mode?: "choose" | "alternatives"
  /** The model whose answer is being committed, if any. */
  selecting?: string | null
  onSelect?: (compareId: string, model: string) => void
}

export const CompareBlock = memo(function CompareBlock({
  compare,
  mode = "choose",
  selecting = null,
  onSelect,
}: CompareBlockProps) {
  const { t } = useI18n()
  const gridRef = useRef<HTMLDivElement>(null)
  const models = useMemo(() => compare.runs.map((r) => r.model), [compare.runs])
  // Most-recently-chosen order; CSS shows its first k entries.
  const [order, setOrder] = useState<string[]>(models)
  const display = useMemo(() => reconcileOrder(order, models), [order, models])
  const unique = useMemo(() => uniqueToolNames(compare.runs), [compare.runs])
  const byModel = useMemo(() => new Map(compare.runs.map((r) => [r.model, r])), [compare.runs])

  const { compareId } = compare
  const handleSelect = useCallback(
    (model: string) => onSelect?.(compareId, model),
    [onSelect, compareId],
  )

  const showModel = useCallback(
    (model: string) => {
      const column = Array.from(gridRef.current?.children ?? []).find(
        (el) => (el as HTMLElement).dataset.run === model,
      ) as HTMLElement | undefined
      // `offsetParent` is null for a `display: none` element: the container
      // query is hiding this column, so bring it into the visible set.
      if (column && column.offsetParent === null) setOrder(promoteModel(display, model))
      else column?.scrollIntoView({ block: "nearest", behavior: "smooth" })
    },
    [display],
  )

  const count = Math.min(3, Math.max(1, compare.runs.length)) as 1 | 2 | 3

  return (
    <div className="@container w-full">
      <div ref={gridRef} className={`grid justify-center gap-4 ${GRID_BY_COUNT[count]}`}>
        <div className="col-span-full flex flex-wrap items-center gap-1.5">
          {compare.runs.map((run) => (
            <ModelChip
              key={run.model}
              run={run}
              rank={display.indexOf(run.model)}
              label={t("ai.compare.showModel", { model: run.model })}
              onClick={showModel}
            />
          ))}
        </div>
        {display.map((model) => {
          const run = byModel.get(model)
          if (!run) return null
          return (
            <CompareColumn
              key={model}
              run={run}
              mode={mode}
              turnState={compare.state}
              selectable={mode === "choose" && isSelectable(compare, model)}
              detached={Boolean(compare.detached)}
              startedAt={compare.startedAt}
              uniqueTools={(unique[model] ?? []).join("|")}
              selecting={selecting == null ? null : selecting === model ? "this" : "other"}
              onSelect={handleSelect}
            />
          )
        })}
      </div>
    </div>
  )
})

/** Strip entry: model name + status dot + tool-call count. */
function ModelChip({
  run,
  rank,
  label,
  onClick,
}: {
  run: CompareRun
  rank: number
  label: string
  onClick: (model: string) => void
}) {
  const { t } = useI18n()
  const statusText =
    run.status === "running"
      ? t("ai.compare.generating")
      : run.status === "failed"
        ? t("ai.compare.failed")
        : t("ai.compare.done")
  return (
    <button
      type="button"
      onClick={() => onClick(run.model)}
      title={`${label} · ${statusText}`}
      className={cn(CHIP_BASE, CHIP_FILLED_BY_RANK[rank])}
    >
      <span
        aria-hidden
        className={cn(
          "size-2 shrink-0 rounded-full",
          run.status === "running" && "animate-pulse bg-amber-500",
          run.status === "done" && "bg-emerald-500",
          run.status === "failed" && "bg-destructive",
        )}
      />
      <span className="truncate font-mono">{run.model}</span>
      <span className="sr-only">{statusText}</span>
      {run.tools.length > 0 && (
        <span className="shrink-0 tabular-nums opacity-70">{t("ai.compare.toolCount", { n: run.tools.length })}</span>
      )}
    </button>
  )
}
