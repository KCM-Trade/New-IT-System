import { useI18n } from "@/components/i18n-provider"
import type { TurnError } from "@/hooks/useAiTurn"

/**
 * One failed turn (or one failed run of a compare turn) as a line of text.
 *
 * The locale table is the whitelist: `t()` returns the key path itself for a
 * code it does not know, so a new backend code needs an i18n entry and nothing
 * else. Unknown codes fall back to the server's message plus the raw code
 * (always shown) and the trace id.
 */
export function ErrorLine({ error }: { error: TurnError }) {
  const { t } = useI18n()
  const key = `ai.errors.${error.code}`
  const translated = t(key)
  const text = translated !== key ? translated : error.message || error.code
  return (
    <p className="text-xs text-destructive/80">
      {text}
      <span className="ml-2 font-mono text-muted-foreground">
        {error.code}
        {error.traceId ? ` · trace ${error.traceId}` : ""}
      </span>
    </p>
  )
}
