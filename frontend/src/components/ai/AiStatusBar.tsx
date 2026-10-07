import { useI18n } from "@/components/i18n-provider"
import type { AiModel, TurnUsage } from "@/hooks/useAiTurn"
import { formatTokens, formatUsd } from "@/lib/ai-compare"

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
 * Status line: `gpt-5.6-terra · 本轮 12.3k tokens · $0.02 · 今日已用 7/100 轮 · $0.28/$20.00`.
 * "本轮" = the last completed answer; "今日已用" = this person's turns and
 * spend against the daily quota (02 §6), reset at HK midnight.
 *
 * Tertiary information by design (docs/ai-agent/02 §6): it answers "what did
 * that just cost me and how much is left today" without competing with the
 * conversation, so it is one muted line with middle-dot separators rather
 * than a row of stat cards. Per-turn numbers come from the `usage` event;
 * the daily counters from `GET /ai/usage/today`, refreshed after every turn.
 *
 * Compare mode (OPT-0076): with the switch on, the leading model name becomes
 * `对比 N 个模型 · 本次计 N 轮` — the next question costs N turns of the daily
 * quota, and the reader should know before sending. When the last answer was
 * a compare turn, "本轮" is the total across its models and says so.
 */
export function AiStatusBar({
  model,
  turnUsage,
  today,
  lead,
  turnRuns = 0,
}: {
  model: AiModel
  turnUsage: TurnUsage | null
  today: TodayUsage | null
  /** Replaces the leading model name (compare mode: "对比 N 个模型 · 本次计 N 轮"). */
  lead?: string
  /** Number of models `turnUsage` is summed over; 0 = a single-model turn. */
  turnRuns?: number
}) {
  const { t } = useI18n()

  // Before the first turn there is nothing to say about "this turn", so say
  // nothing rather than printing dashes the reader has to decode.
  const parts: string[] = [lead ?? model]
  if (turnUsage) {
    const cost = turnUsage.cost_usd == null ? "" : ` · ${formatUsd(turnUsage.cost_usd)}`
    const scope = turnRuns > 0 ? `${t("ai.compare.totalOf", { n: turnRuns })} ` : ""
    parts.push(
      `${t("ai.status.thisTurn")} ${scope}${formatTokens(turnUsage.input_tokens + turnUsage.output_tokens)} tokens${cost}`,
    )
  }
  if (today) {
    parts.push(
      `${t("ai.status.todayUsed")} ${today.turns}/${today.turns_limit} ${t("ai.status.turnsUnit")} · ${formatUsd(today.cost_usd)}/${formatUsd(today.cost_limit_usd)}`,
    )
  }

  return (
    <div
      className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground"
      aria-live="polite"
      title={t("ai.status.tooltip")}
    >
      {parts.map((p, i) => (
        <span key={i} className="flex items-center gap-2">
          {i > 0 && <span aria-hidden>·</span>}
          <span className={i === 0 && lead === undefined ? "font-mono" : undefined}>{p}</span>
        </span>
      ))}
    </div>
  )
}
