import { useI18n } from "@/components/i18n-provider"
import type { AiModel, TurnUsage } from "@/hooks/useAiTurn"

export interface TodayUsage {
  day_hk: string
  turns: number
  turns_limit: number
  cost_usd: number
  cost_limit_usd: number
  input_tokens: number
  output_tokens: number
}

/**
 * Footer status line: `gpt-5.6-terra · 12.3k tok · $0.02 · 今日 7/100`.
 *
 * Tertiary information by design (docs/ai-agent/02 §6): it answers "what did
 * that just cost me and how much is left today" without competing with the
 * conversation, so it is one muted line with middle-dot separators rather
 * than a row of stat cards. Per-turn numbers come from the `usage` event;
 * the daily counters from `GET /ai/usage/today`, refreshed after every turn.
 */
export function AiStatusBar({
  model,
  turnUsage,
  today,
}: {
  model: AiModel
  turnUsage: TurnUsage | null
  today: TodayUsage | null
}) {
  const { t } = useI18n()
  const turnTokens = turnUsage ? turnUsage.input_tokens + turnUsage.output_tokens : null

  const parts: string[] = [model]
  parts.push(turnTokens === null ? `— ${t("ai.status.tokens")}` : `${formatTokens(turnTokens)} ${t("ai.status.tokens")}`)
  parts.push(turnUsage?.cost_usd == null ? "$—" : formatUsd(turnUsage.cost_usd))
  if (today) {
    parts.push(
      `${t("ai.status.today")} ${today.turns}/${today.turns_limit} · ${formatUsd(today.cost_usd)}/${formatUsd(today.cost_limit_usd)}`,
    )
  }

  return (
    <div
      className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground"
      aria-live="polite"
    >
      {parts.map((p, i) => (
        <span key={i} className="flex items-center gap-2">
          {i > 0 && <span aria-hidden>·</span>}
          <span className={i === 0 ? "font-mono" : undefined}>{p}</span>
        </span>
      ))}
    </div>
  )
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function formatUsd(v: number): string {
  // Sub-cent costs are the normal case for one turn; two decimals would print
  // "$0.00" for most of them and read as free.
  const digits = v > 0 && v < 0.1 ? 3 : 2
  return `$${v.toFixed(digits)}`
}
