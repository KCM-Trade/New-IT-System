/**
 * Pure logic for the AI assistant's multi-model compare mode
 * (docs/ai-agent/02-contracts.md §19–§23, OPT-0076).
 *
 * One question fans out to 2–3 models; every SSE event of such a turn carries
 * a `run` field (the model name) and is folded into that run's own state here.
 * Nothing in this file touches React or the DOM, so the reduction, the model
 * strip's display order and the default model set are unit-tested directly.
 *
 * Layout rule mirrored from the approved design: a column is never narrower
 * than 480px (gap 16px), so `k = min(N, floor((A + 16) / 496))` columns are
 * visible in a block of width `A`. CSS container queries apply that rule at
 * runtime; `visibleColumnCount` is the same formula for tests and docs.
 */

import type { ToolCall, TurnError, TurnUsage } from "@/hooks/useAiTurn"
import { appendToolUse, resolveToolDone, type ToolDonePayload, type ToolUsePayload } from "@/lib/ai-tools"

export type CompareReason = "numbers" | "clearer" | "faster" | "other"
export const COMPARE_REASONS: readonly CompareReason[] = ["numbers", "clearer", "faster", "other"]

export type CompareRunStatus = "running" | "done" | "failed"
export type CompareState = "running" | "pending" | "selected" | "void"

export interface CompareRun {
  /** The model name; unique within one compare turn (it is the wire `run`). */
  model: string
  text: string
  tools: ToolCall[]
  usage?: TurnUsage
  error?: TurnError
  status: CompareRunStatus
  elapsedMs?: number
}

export interface AiCompare {
  /** Empty until the stream's `init` event names it. */
  compareId: string
  state: CompareState
  /** Every model of the turn, in column order. */
  models: string[]
  /**
   * running / pending / void: every run of the turn.
   * selected: only the answers that were NOT chosen (the chosen one is the
   * message itself).
   */
  runs: CompareRun[]
  /** Models whose answer may be chosen; only meaningful while `pending`. */
  selectable: string[]
  selectedModel?: string
  reason?: CompareReason | null
  /** Epoch ms the turn started, for the live elapsed counter. */
  startedAt?: number
  /**
   * The browser stopped listening (Stop, or a reload) while the server keeps
   * finishing the runs in the background; the columns are frozen snapshots.
   */
  detached?: boolean
}

export const COMPARE_MIN_MODELS = 2
export const COMPARE_MAX_MODELS = 3
export const COMPARE_COLUMN_MIN_PX = 480
export const COMPARE_COLUMN_GAP_PX = 16

const COMPARE_ANCHOR_MODEL = "gpt-5.6-terra"
const COMPARE_FALLBACK_MODEL = "grok-4.7"

/** i18n keys of each model's label / description (the composer's wording). */
export const MODEL_I18N: Record<string, { label: string; desc: string }> = {
  "gpt-5.6-terra": { label: "ai.modelStandard", desc: "ai.modelStandardDesc" },
  "gpt-5.6-sol": { label: "ai.modelDeep", desc: "ai.modelDeepDesc" },
  "gpt-6.1-sol": { label: "ai.modelFrontier", desc: "ai.modelFrontierDesc" },
  "grok-4.7": { label: "ai.modelGrok", desc: "ai.modelGrokDesc" },
  "DeepSeek-V4-Pro": { label: "ai.modelDeepSeek", desc: "ai.modelDeepSeekDesc" },
}

// ── wire helpers ───────────────────────────────────────────────────────────

/**
 * The `run` of an SSE payload, or null when the event belongs to the whole
 * turn. "Has `run` = that column's business; no `run` = the turn's" (02 §20) —
 * a single-model stream never carries the field, so it always yields null and
 * stays on the original code path.
 */
export function runOf(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null
  const run = (payload as { run?: unknown }).run
  return typeof run === "string" && run.length > 0 ? run : null
}

export function newCompare(models: readonly string[], startedAt?: number): AiCompare {
  return {
    compareId: "",
    state: "running",
    models: [...models],
    runs: models.map((model) => ({ model, text: "", tools: [], status: "running" as const })),
    selectable: [],
    startedAt,
  }
}

interface UsagePayload {
  input_tokens?: number
  output_tokens?: number
  cache_read_input_tokens?: number
  cost_usd?: number | null
}
interface ErrorPayload { code?: string; message?: string; trace_id?: string }
interface DonePayload { terminal_reason?: string; elapsed_ms?: number }

/** Append already-coalesced text to a run (the per-frame delta buffer's sink). */
export function appendRunText(run: CompareRun, chunk: string): CompareRun {
  return chunk ? { ...run, text: run.text + chunk } : run
}

/**
 * Fold one SSE event into a run. Returns the same object when nothing changed
 * so memoised columns keep their reference.
 */
export function applyRunEvent(run: CompareRun, event: string, payload: unknown): CompareRun {
  const p = (payload ?? {}) as Record<string, unknown>
  switch (event) {
    case "text": {
      const delta = (p as { delta?: unknown }).delta
      return typeof delta === "string" ? appendRunText(run, delta) : run
    }
    case "tool_use": {
      const u = p as ToolUsePayload
      if (!u.name) return run
      // Tools only ever append, so the current length is a stable sequence.
      return { ...run, tools: appendToolUse(run.tools, u, `${u.name}#${run.tools.length}`) }
    }
    case "tool_done": {
      const d = p as ToolDonePayload
      if (!d.name) return run
      // Paired by `call_id`; without one, the oldest pending call of this
      // name (see resolveToolDone).
      return { ...run, tools: resolveToolDone(run.tools, d, `${d.name}#${run.tools.length}`) }
    }
    case "usage": {
      const u = p as UsagePayload
      return {
        ...run,
        usage: {
          input_tokens: u.input_tokens ?? 0,
          output_tokens: u.output_tokens ?? 0,
          cache_read_input_tokens: u.cache_read_input_tokens ?? 0,
          cost_usd: u.cost_usd ?? null,
        },
      }
    }
    case "error": {
      const e = p as ErrorPayload
      return {
        ...run,
        status: "failed",
        error: { code: e.code ?? "internal", message: e.message ?? "", traceId: e.trace_id },
      }
    }
    case "done": {
      const d = p as DonePayload
      const elapsedMs = typeof d.elapsed_ms === "number" ? d.elapsed_ms : run.elapsedMs
      if (run.status === "failed") return { ...run, elapsedMs }
      if (d.terminal_reason === "error") {
        // A failed run normally announces itself with `error` first; keep the
        // column honest if only the terminal reason says so.
        return { ...run, status: "failed", elapsedMs, error: run.error ?? { code: "internal", message: "" } }
      }
      return { ...run, status: "done", elapsedMs }
    }
    default:
      return run
  }
}

/** Replace one run of a compare; every other run keeps its reference. */
export function updateRun(
  compare: AiCompare,
  model: string,
  fn: (run: CompareRun) => CompareRun,
): AiCompare {
  let changed = false
  const runs = compare.runs.map((r) => {
    if (r.model !== model) return r
    const next = fn(r)
    if (next !== r) changed = true
    return next
  })
  return changed ? { ...compare, runs } : compare
}

/** Fold a `run`-tagged event into the compare. Unknown runs are ignored. */
export function applyCompareRunEvent(
  compare: AiCompare,
  run: string,
  event: string,
  payload: unknown,
): AiCompare {
  return updateRun(compare, run, (r) => applyRunEvent(r, event, payload))
}

interface CompareEventPayload { compare_id?: string; state?: string; selectable?: unknown }

/**
 * The turn-level `compare` event: the turn is over and is either waiting for
 * a choice (`pending`) or produced nothing selectable (`void`). Runs that
 * never reported `done` are closed as `incomplete` so no column spins forever.
 */
export function applyCompareResult(compare: AiCompare, payload: unknown): AiCompare {
  const p = (payload ?? {}) as CompareEventPayload
  const state: CompareState = p.state === "pending" ? "pending" : "void"
  const selectable = Array.isArray(p.selectable)
    ? p.selectable.filter((m): m is string => typeof m === "string")
    : []
  return {
    ...compare,
    compareId: p.compare_id || compare.compareId,
    state,
    selectable: state === "pending" ? selectable : [],
    detached: false,
    runs: compare.runs.map((r) =>
      r.status === "running"
        ? { ...r, status: "failed" as const, error: r.error ?? { code: "incomplete", message: "" } }
        : r,
    ),
  }
}

/** True while no run has produced anything — the turn was refused up front. */
export function compareIsUntouched(compare: AiCompare): boolean {
  return compare.runs.every((r) => r.text === "" && r.tools.length === 0 && !r.error && r.status === "running")
}

// ── selection ──────────────────────────────────────────────────────────────

/**
 * Whether the "continue with this answer" button is live for a model. Only a
 * finished turn can be chosen from (the server answers 409 `session busy`
 * until then), and only the models the server listed.
 */
export function isSelectable(compare: AiCompare, model: string): boolean {
  return compare.state === "pending" && compare.selectable.includes(model)
}

/** A compare that still owns the conversation: no new question until resolved. */
export function isOpenCompare(compare: AiCompare | undefined | null): boolean {
  return !!compare && (compare.state === "running" || compare.state === "pending")
}

// ── model strip / columns ──────────────────────────────────────────────────

/** How many 480px columns fit in `width` (never more than `n`, never fewer than 1). */
export function visibleColumnCount(width: number, n: number): number {
  if (n <= 0) return 0
  const fit = Math.floor((width + COMPARE_COLUMN_GAP_PX) / (COMPARE_COLUMN_MIN_PX + COMPARE_COLUMN_GAP_PX))
  return Math.max(1, Math.min(n, fit))
}

/**
 * Display order after the reader picks a model that is currently hidden: it
 * moves to the front of the visible set. The order is a most-recently-chosen
 * list and CSS shows its first `k` entries, so the entry pushed past `k` is
 * the least recently chosen one — whatever `k` the container allows.
 */
export function promoteModel(order: readonly string[], model: string): string[] {
  if (!order.includes(model)) return [...order]
  return [model, ...order.filter((m) => m !== model)]
}

/** Keep a stored display order valid for the current set of models. */
export function reconcileOrder(order: readonly string[], models: readonly string[]): string[] {
  const kept = order.filter((m) => models.includes(m))
  return [...kept, ...models.filter((m) => !kept.includes(m))]
}

/**
 * Tool names only ONE run called, per model. The badge of such a tool gets an
 * outline hint: a figure only one model went and fetched is the first place
 * to look when the answers disagree. Empty when fewer than two runs exist.
 */
export function uniqueToolNames(runs: readonly Pick<CompareRun, "model" | "tools">[]): Record<string, string[]> {
  const out: Record<string, string[]> = {}
  for (const r of runs) out[r.model] = []
  if (runs.length < 2) return out
  const callers = new Map<string, Set<string>>()
  for (const r of runs) {
    for (const t of r.tools) {
      const set = callers.get(t.name) ?? new Set<string>()
      set.add(r.model)
      callers.set(t.name, set)
    }
  }
  for (const [name, models] of callers) {
    if (models.size === 1) out[[...models][0]].push(name)
  }
  return out
}

// ── composer: which models to compare ──────────────────────────────────────

function knownDistinct(models: readonly unknown[], known: readonly string[]): string[] {
  const out: string[] = []
  for (const m of models) {
    if (typeof m === "string" && known.includes(m) && !out.includes(m)) out.push(m)
  }
  return out
}

/**
 * The default pair: `gpt-5.6-terra` plus the other model used last (falling
 * back to `grok-4.7`). Always two distinct known models.
 */
export function defaultCompareModels(lastOther: string | null | undefined, known: readonly string[]): string[] {
  const anchor = known.includes(COMPARE_ANCHOR_MODEL) ? COMPARE_ANCHOR_MODEL : known[0]
  const candidates = [lastOther, COMPARE_FALLBACK_MODEL, ...known]
  const other = candidates.find((m): m is string => typeof m === "string" && known.includes(m) && m !== anchor)
  return other ? [anchor, other] : [anchor]
}

/**
 * Sanitise a persisted model set: known, distinct, at most three, in the
 * canonical model order (= column order). Fewer than two left → the default
 * pair built around `lastOther`.
 */
export function normalizeCompareModels(
  stored: unknown,
  known: readonly string[],
  lastOther?: string | null,
): string[] {
  const picked = Array.isArray(stored) ? knownDistinct(stored, known) : []
  if (picked.length < COMPARE_MIN_MODELS) return defaultCompareModels(lastOther ?? picked[0], known)
  return known.filter((m) => picked.includes(m)).slice(0, COMPARE_MAX_MODELS)
}

/** Tick / untick one model in the multi-select; a fourth tick is ignored. */
export function toggleCompareModel(selected: readonly string[], model: string, known: readonly string[]): string[] {
  if (selected.includes(model)) return selected.filter((m) => m !== model)
  if (selected.length >= COMPARE_MAX_MODELS || !known.includes(model)) return [...selected]
  return known.filter((m) => m === model || selected.includes(m))
}

export function compareModelsValid(models: readonly string[]): boolean {
  return (
    models.length >= COMPARE_MIN_MODELS &&
    models.length <= COMPARE_MAX_MODELS &&
    new Set(models).size === models.length
  )
}

// ── totals / formatting ────────────────────────────────────────────────────

/** Sum of the runs' usage; null when no run has reported any yet. */
export function sumUsage(usages: readonly (TurnUsage | undefined | null)[]): TurnUsage | null {
  let any = false
  let cost: number | null = null
  const total: TurnUsage = { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cost_usd: null }
  for (const u of usages) {
    if (!u) continue
    any = true
    total.input_tokens += u.input_tokens
    total.output_tokens += u.output_tokens
    total.cache_read_input_tokens += u.cache_read_input_tokens
    if (u.cost_usd != null) cost = (cost ?? 0) + u.cost_usd
  }
  if (!any) return null
  total.cost_usd = cost
  return total
}

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

export function formatUsd(v: number): string {
  // Sub-cent costs are the normal case for one turn; two decimals would print
  // "$0.00" for most of them and read as free.
  const digits = v > 0 && v < 0.1 ? 3 : 2
  return `$${v.toFixed(digits)}`
}

export function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`
}
