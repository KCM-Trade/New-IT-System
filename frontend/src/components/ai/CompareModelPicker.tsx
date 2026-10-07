import { IconChevronDown } from "@tabler/icons-react"

import { useI18n } from "@/components/i18n-provider"
import { Checkbox } from "@/components/ui/checkbox"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  COMPARE_MAX_MODELS,
  COMPARE_MIN_MODELS,
  MODEL_I18N,
  toggleCompareModel,
} from "@/lib/ai-compare"
import { cn } from "@/lib/utils"

/**
 * The composer's model picker while the compare switch is on: tick 2–3 models.
 *
 * Same pill as the single-model Select it replaces, so flipping the switch
 * changes what the control means, not where it is. A fourth model cannot be
 * ticked (its row is disabled and says why); unticking down to one is allowed
 * — the composer then disables Send and explains, which is clearer than a
 * checkbox that refuses to move.
 */
export function CompareModelPicker({
  models,
  value,
  onChange,
  disabled,
}: {
  /** Every model on offer, in canonical order (= column order). */
  models: readonly string[]
  value: readonly string[]
  onChange: (next: string[]) => void
  disabled?: boolean
}) {
  const { t } = useI18n()
  const label = (m: string) => (MODEL_I18N[m] ? t(MODEL_I18N[m].label) : m)
  const full = value.length >= COMPARE_MAX_MODELS
  const summary = value.length > 0 ? value.map(label).join(" + ") : t("ai.compare.pickModels")

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          aria-label={t("ai.compare.pickModels")}
          className={cn(
            "inline-flex h-7 min-w-0 max-w-[16rem] items-center gap-1 rounded-full bg-muted px-2.5 text-xs outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 sm:max-w-[26rem]",
            value.length < COMPARE_MIN_MODELS && "text-muted-foreground",
          )}
        >
          <span className="truncate">{summary}</span>
          <IconChevronDown className="size-3.5 shrink-0 opacity-60" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-1.5">
        <p className="px-2 pb-1.5 pt-1 text-xs text-muted-foreground">
          {full ? t("ai.compare.maxReached") : t("ai.compare.pickHint")}
        </p>
        <ul>
          {models.map((m) => {
            const checked = value.includes(m)
            const rowDisabled = !checked && full
            return (
              <li key={m}>
                <label
                  className={cn(
                    "flex cursor-pointer items-start gap-2.5 rounded-md px-2 py-2 text-sm hover:bg-accent",
                    rowDisabled && "cursor-not-allowed opacity-50 hover:bg-transparent",
                  )}
                >
                  <Checkbox
                    checked={checked}
                    disabled={rowDisabled}
                    onCheckedChange={() => onChange(toggleCompareModel(value, m, models))}
                    className="mt-0.5"
                  />
                  <span className="flex min-w-0 flex-col gap-0.5">
                    <span>{label(m)}</span>
                    {MODEL_I18N[m] && (
                      <span className="text-xs text-muted-foreground">{t(MODEL_I18N[m].desc)}</span>
                    )}
                  </span>
                </label>
              </li>
            )
          })}
        </ul>
      </PopoverContent>
    </Popover>
  )
}
