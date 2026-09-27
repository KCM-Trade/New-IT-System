import { IconCircleCheck, IconAlertCircle, IconLoader2 } from "@tabler/icons-react"

import { Badge } from "@/components/ui/badge"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { useI18n } from "@/components/i18n-provider"
import { cn } from "@/lib/utils"
import type { ToolCall } from "@/hooks/useAiTurn"
import { formatHk } from "@/lib/hk-time"

/**
 * The provenance badge next to a tool result: "✓ certified · <function>".
 *
 * This is the one accent colour on the page. Every number the model quotes
 * has to be traceable to a certified definition (docs/ai-agent/02 §2.5), so
 * the certified state is the only thing that earns emphasis — pending calls
 * and failures stay neutral, and a refused call reads as a fact ("scope
 * denied"), not an alarm.
 *
 * The wire `tool_done` carries `source` only (service / function / as_of); the
 * full `definition` object stays on the model side, so the popover shows what
 * the browser actually knows rather than pretending to a longer document.
 */
export function SourceBadge({ tool }: { tool: ToolCall }) {
  const { t } = useI18n()

  if (tool.ok === null) {
    return (
      <Badge variant="outline" className="gap-1 font-normal text-muted-foreground">
        <IconLoader2 className="animate-spin" />
        {t("ai.querying", { tool: tool.name })}
      </Badge>
    )
  }

  const certified = tool.ok && tool.certified

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          title={t("ai.badgeTooltip")}
          className="rounded-md focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          <Badge
            variant="outline"
            className={cn(
              "cursor-pointer gap-1 font-normal",
              certified
                ? "border-emerald-600/40 bg-emerald-50 text-emerald-700 dark:border-emerald-400/40 dark:bg-emerald-950/40 dark:text-emerald-300"
                : "text-muted-foreground",
            )}
          >
            {certified ? <IconCircleCheck /> : <IconAlertCircle />}
            {tool.ok
              ? `${t("ai.badgeCertified")} · ${tool.name}`
              : `${tool.name} · ${tool.errorCode ?? "error"}`}
          </Badge>
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 text-sm">
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
      </PopoverContent>
    </Popover>
  )
}
