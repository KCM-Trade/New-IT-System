import { IconCircleCheck, IconAlertCircle, IconAlertTriangle, IconLoader2 } from "@tabler/icons-react"

import { Badge } from "@/components/ui/badge"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { useI18n } from "@/components/i18n-provider"
import { cn } from "@/lib/utils"
import type { ToolCall } from "@/hooks/useAiTurn"
import { formatHk } from "@/lib/hk-time"

/**
 * The provenance badge next to a tool result.
 *
 * Three states (docs/ai-agent/02 §2.5 / §10.3):
 *   ✓ certified   — "✓ 认证口径 · <function>", the one accent colour on the page
 *   ⚠ uncertified — "⚠ 即时 SQL · 未认证 · run_sql", amber: the number came from
 *                   SQL the model wrote itself. The popover shows that SQL
 *                   verbatim — an uncertified figure with no visible query
 *                   behind it would not be reviewable, so the SQL is never hidden.
 *   ✗ failed      — neutral, reads as a fact ("scope denied"), not an alarm.
 *
 * The wire `tool_done` carries `source` only (service / function / as_of); the
 * full `definition` object stays on the model side, so the popover shows what
 * the browser actually knows rather than pretending to a longer document.
 */
/**
 * `unique` (compare mode only): this tool was called by this model alone. The
 * badge gets an outline ring — when the answers disagree, a source only one
 * model consulted is the first place to look.
 */
const UNIQUE_RING = "ring-2 ring-foreground/30 ring-offset-1 ring-offset-background"

export function SourceBadge({ tool, unique = false }: { tool: ToolCall; unique?: boolean }) {
  const { t } = useI18n()

  if (tool.ok === null) {
    return (
      <Badge
        variant="outline"
        title={unique ? t("ai.compare.onlyThisModel") : undefined}
        className={cn("gap-1 font-normal text-muted-foreground", unique && UNIQUE_RING)}
      >
        <IconLoader2 className="animate-spin" />
        {t("ai.querying", { tool: tool.name })}
      </Badge>
    )
  }

  const certified = tool.ok && tool.certified
  const uncertified = tool.ok && !tool.certified
  const sql = sqlOf(tool.input)

  let label: string
  if (certified) label = `${t("ai.badgeCertified")} · ${tool.name}`
  else if (uncertified) label = `${t("ai.badgeUncertified")} · ${tool.name}`
  else label = `${tool.name} · ${tool.errorCode ?? "error"}`

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          title={
            (uncertified ? t("ai.badgeUncertifiedTooltip") : t("ai.badgeTooltip")) +
            (unique ? ` · ${t("ai.compare.onlyThisModel")}` : "")
          }
          className={cn(
            "rounded-md focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
            unique && UNIQUE_RING,
          )}
        >
          <Badge
            variant="outline"
            className={cn(
              "cursor-pointer gap-1 font-normal",
              certified &&
                "border-emerald-600/40 bg-emerald-50 text-emerald-700 dark:border-emerald-400/40 dark:bg-emerald-950/40 dark:text-emerald-300",
              uncertified &&
                "border-amber-600/40 bg-amber-50 text-amber-800 dark:border-amber-400/40 dark:bg-amber-950/40 dark:text-amber-300",
              !certified && !uncertified && "text-muted-foreground",
            )}
          >
            {certified ? <IconCircleCheck /> : uncertified ? <IconAlertTriangle /> : <IconAlertCircle />}
            {label}
          </Badge>
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className={cn("text-sm", sql ? "w-[28rem] max-w-[90vw]" : "w-80")}>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("ai.sourceTitle")}
        </p>
        {tool.source ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <dt className="text-muted-foreground">{t("ai.sourceService")}</dt>
            <dd className="break-all font-mono text-xs leading-5">{tool.source.service}</dd>
            <dt className="text-muted-foreground">{t("ai.sourceFunction")}</dt>
            <dd className="break-all font-mono text-xs leading-5">{tool.source.function}</dd>
            <dt className="text-muted-foreground">{t("ai.sourceAsOf")}</dt>
            <dd className="text-xs leading-5">{formatHk(tool.source.as_of)}</dd>
            <dt className="text-muted-foreground">{t("ai.sourceCertified")}</dt>
            <dd className="text-xs leading-5">{tool.source.certified ? t("ai.yes") : t("ai.no")}</dd>
          </dl>
        ) : (
          <p className="text-muted-foreground">
            {tool.ok ? t("ai.sourceMissing") : t(`ai.toolErrors.${tool.errorCode ?? "error"}`)}
          </p>
        )}
        {!tool.ok && tool.source && (
          <p className="mt-2 text-xs text-muted-foreground">
            {t(`ai.toolErrors.${tool.errorCode ?? "error"}`)}
          </p>
        )}
        {uncertified && (
          <p className="mt-2 text-xs text-amber-800 dark:text-amber-300">{t("ai.uncertifiedNote")}</p>
        )}
        {sql && (
          <div className="mt-3">
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t("ai.sqlShown")}
            </p>
            {/* Always rendered, never collapsed: the query is the evidence. */}
            <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-2 font-mono text-xs leading-5">
              {sql}
            </pre>
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}

/** `run_sql`'s `input.sql`, when the tool input has one; null otherwise. */
function sqlOf(input: unknown): string | null {
  if (!input || typeof input !== "object") return null
  const sql = (input as { sql?: unknown }).sql
  return typeof sql === "string" && sql.trim() ? sql : null
}
