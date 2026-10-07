import { memo, useCallback, useEffect, useRef, useState } from "react"
import { IconCheck, IconCircleCheck, IconCircleX, IconCopy, IconLoader2 } from "@tabler/icons-react"

import { ErrorLine } from "@/components/ai/ErrorLine"
import { MarkdownMessage } from "@/components/ai/MarkdownMessage"
import { SourceBadge } from "@/components/ai/SourceBadge"
import { useI18n } from "@/components/i18n-provider"
import { Button } from "@/components/ui/button"
import { formatElapsed, formatTokens, type CompareRun, type CompareState } from "@/lib/ai-compare"
import { cn } from "@/lib/utils"

/**
 * One model's answer inside a compare block (OPT-0076 layout).
 *
 * Top to bottom: a sticky header (model, status, elapsed + tokens, copy), the
 * tool strip — always visible, because which sources a model consulted is the
 * cheapest defence against picking the answer that merely reads better — the
 * Markdown body at the normal 14px, and the one filled button of the screen,
 * pinned to the column bottom so the buttons of all columns share a row.
 *
 * Memoised with primitive / stable props only: while one model streams, the
 * other columns keep their `run` reference and do not re-render their
 * Markdown.
 */
export interface CompareColumnProps {
  run: CompareRun
  /** `choose` = live / pending turn with a select button; `alternatives` = read-only history. */
  mode: "choose" | "alternatives"
  /** The whole turn's state; a finished run cannot be chosen until every run has ended. */
  turnState: CompareState
  selectable: boolean
  /** The server is finishing this turn without the browser listening. */
  detached: boolean
  startedAt?: number
  /** Tool names only this model called, joined by "|" (a primitive, for memo). */
  uniqueTools: string
  /** A choice is being committed: `this` model, another one, or none. */
  selecting: "this" | "other" | null
  onSelect?: (model: string) => void
}

export const CompareColumn = memo(function CompareColumn({
  run,
  mode,
  turnState,
  selectable,
  detached,
  startedAt,
  uniqueTools,
  selecting,
  onSelect,
}: CompareColumnProps) {
  const { t } = useI18n()
  const readOnly = mode === "alternatives"
  const unique = uniqueTools ? uniqueTools.split("|") : []
  const running = run.status === "running"
  const toolsSettled = run.tools.every((tl) => tl.ok !== null)

  let buttonLabel: string
  if (selecting === "this") buttonLabel = t("ai.compare.selectingNow")
  else if (running) buttonLabel = detached ? t("ai.compare.finishing") : `${t("ai.compare.generating")}…`
  else if (selectable) buttonLabel = t("ai.compare.continueWith", { model: run.model })
  else if (turnState === "running" && run.status === "done" && run.text) buttonLabel = t("ai.compare.waitOthers")
  else buttonLabel = t("ai.compare.notSelectable")

  return (
    <section
      data-run={run.model}
      aria-label={run.model}
      className="flex min-w-0 flex-col rounded-xl border border-border/70 bg-card"
    >
      <header className="sticky top-0 z-10 flex items-center gap-2 rounded-t-xl bg-muted px-3 py-2">
        <div className="min-w-0 flex-1">
          <div
            className={cn(
              "truncate font-mono text-sm font-medium",
              readOnly ? "text-muted-foreground" : "text-foreground",
            )}
          >
            {run.model}
          </div>
          <div className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
            <RunStatus run={run} detached={detached} muted={readOnly} />
            <RunMeta run={run} startedAt={running && !detached ? startedAt : undefined} />
          </div>
        </div>
        <CopyButton text={run.text} />
      </header>

      {run.tools.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-3 pt-3">
          {run.tools.map((tl) => (
            <SourceBadge key={tl.key} tool={tl} unique={unique.includes(tl.name)} />
          ))}
        </div>
      )}

      <div className="min-w-0 flex-1 space-y-2 px-3 py-3">
        {run.text && <MarkdownMessage text={run.text} />}
        {running && !run.text && toolsSettled && (
          <p className="text-sm text-muted-foreground">
            {detached ? t("ai.compare.finishing") : t("ai.thinking")}
          </p>
        )}
        {run.status === "done" && !run.text && (
          <p className="text-sm text-muted-foreground">{t("ai.compare.emptyAnswer")}</p>
        )}
        {run.error && <ErrorLine error={run.error} />}
      </div>

      {!readOnly && (
        <div className="mt-auto px-3 pb-3">
          <Button
            className="h-auto min-h-9 w-full whitespace-normal py-2"
            disabled={!selectable || selecting !== null}
            onClick={() => onSelect?.(run.model)}
          >
            {buttonLabel}
          </Button>
        </div>
      )}
    </section>
  )
})

function RunStatus({ run, detached, muted }: { run: CompareRun; detached: boolean; muted: boolean }) {
  const { t } = useI18n()
  if (run.status === "running") {
    return (
      <span className="inline-flex items-center gap-1">
        <IconLoader2 className="size-3.5 animate-spin" />
        {detached ? t("ai.compare.finishing") : t("ai.compare.generating")}
      </span>
    )
  }
  if (run.status === "failed") {
    return (
      <span className={cn("inline-flex items-center gap-1", !muted && "text-destructive")}>
        <IconCircleX className="size-3.5" />
        {t("ai.compare.failed")}
      </span>
    )
  }
  return (
    <span className={cn("inline-flex items-center gap-1", !muted && "text-emerald-700 dark:text-emerald-400")}>
      <IconCircleCheck className="size-3.5" />
      {t("ai.compare.done")}
    </span>
  )
}

/** `12s · 8.1k tok`; while generating, a live counter from the turn's start. */
function RunMeta({ run, startedAt }: { run: CompareRun; startedAt?: number }) {
  const parts: string[] = []
  const live = useLiveElapsed(startedAt)
  if (typeof run.elapsedMs === "number") parts.push(formatElapsed(run.elapsedMs))
  else if (live !== null) parts.push(formatElapsed(live))
  if (run.usage) parts.push(`${formatTokens(run.usage.input_tokens + run.usage.output_tokens)} tok`)
  if (parts.length === 0) return null
  return <span className="tabular-nums">{parts.join(" · ")}</span>
}

/** Milliseconds since `startedAt`, re-rendered once a second; null when off. */
function useLiveElapsed(startedAt?: number): number | null {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (startedAt === undefined) return
    setNow(Date.now())
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [startedAt])
  return startedAt === undefined ? null : Math.max(0, now - startedAt)
}

function CopyButton({ text }: { text: string }) {
  const { t } = useI18n()
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current)
  }, [])

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard blocked (plain http on a LAN IP): nothing to report */
    }
  }, [text])

  const label = copied ? t("ai.compare.copied") : t("ai.compare.copy")
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={() => void copy()}
      disabled={!text}
      aria-label={label}
      title={label}
      className="size-7 shrink-0 text-muted-foreground"
    >
      {copied ? <IconCheck className="size-4" /> : <IconCopy className="size-4" />}
    </Button>
  )
}
