/**
 * Wire types and the pure mapping for the AI assistant's session history
 * (`GET /api/v1/ai/sessions*`, docs/ai-agent/02-contracts.md §8.4).
 *
 * The server keeps two things per conversation: the model's own context (an
 * opaque blob it never sends to the browser) and a transcript table meant only
 * for redisplay. This module turns that transcript into the same `AiMessage`
 * rows the streaming hook produces live, so a resumed conversation renders
 * exactly like one that was typed a minute ago — same badges, same error line.
 */

import type { AiMessage, AiModel, ToolCall, ToolSource, TurnUsage } from "@/hooks/useAiTurn"
import { webFields } from "@/lib/ai-tools"
import { COMPARE_REASONS, type AiCompare, type CompareReason, type CompareRun } from "@/lib/ai-compare"

export interface AiSessionSummary {
  session_id: string
  /** Null until the first turn has completed. */
  title: string | null
  model: string | null
  turns: number
  created_at: string
  updated_at: string
  /** True while a compare turn of this conversation waits for a choice (02 §23). */
  pending_compare?: boolean
}

export interface AiSessionToolRow {
  name: string
  ok: boolean | null
  certified?: boolean | null
  source?: ToolSource | null
  error_code?: string | null
  /** The model's arguments (subject, date range, later the SQL text). */
  input?: unknown
  /** Per-call id (OPT-0078); absent on rows stored before it. */
  call_id?: string | null
  /** `search_web` only; absent when empty. */
  citations?: { title: string; url: string }[] | null
  queries?: string[] | null
}

export interface AiSessionMessageRow {
  seq: number
  role: "user" | "assistant"
  text: string
  tools: AiSessionToolRow[] | null
  usage: Partial<TurnUsage> | null
  error_code: string | null
  at: string
  /** Which model gave this answer; null on rows stored before OPT-0076. */
  model?: string | null
  /** Present on an assistant row that came out of a compare turn (02 §23). */
  compare?: AiSessionCompareRow | null
}

/** One run of a compare turn as the server stores it (never the blob). */
export interface AiCompareCandidateRow {
  model: string
  text: string | null
  tools: AiSessionToolRow[] | null
  usage: Partial<TurnUsage> | null
  error_code: string | null
  elapsed_ms: number | null
  /** Only on `pending_compare.candidates`. */
  selectable?: boolean
}

export interface AiSessionCompareRow {
  compare_id: string
  reason: string | null
  /** Selected turn: the answers not chosen. Void turn: every run. */
  alternatives: AiCompareCandidateRow[] | null
}

export interface AiPendingCompare {
  compare_id: string
  state: "running" | "pending"
  question: string
  models: string[]
  created_at: string
  /** Empty while `running` — candidates are stored when the whole turn ends. */
  candidates: AiCompareCandidateRow[] | null
}

export interface AiSessionDetail {
  session: AiSessionSummary
  messages: AiSessionMessageRow[]
  pending_compare?: AiPendingCompare | null
}

export interface AiSessionListResponse {
  data: AiSessionSummary[]
  total: number
}

export const AI_SESSION_STORAGE_KEY = "AI_ASSISTANT_SESSION_ID"

function mapToolRows(rows: AiSessionToolRow[] | null | undefined, keyPrefix: string): ToolCall[] {
  return (rows ?? []).map((t, i) => {
    const ok = t.ok === true
    const call: ToolCall = {
      key: `${keyPrefix}#${i}`,
      name: t.name,
      ok: t.ok === null || t.ok === undefined ? false : t.ok,
      certified: ok && Boolean(t.certified),
      source: t.source ?? null,
      errorCode: ok ? undefined : t.error_code ?? "error",
      input: t.input,
      ...webFields(t.citations, t.queries),
    }
    if (t.call_id) call.callId = t.call_id
    return call
  })
}

function mapUsage(usage: Partial<TurnUsage> | null | undefined): TurnUsage | undefined {
  if (!usage) return undefined
  return {
    input_tokens: usage.input_tokens ?? 0,
    output_tokens: usage.output_tokens ?? 0,
    cache_read_input_tokens: usage.cache_read_input_tokens ?? 0,
    cost_usd: usage.cost_usd ?? null,
  }
}

function mapReason(reason: string | null | undefined): CompareReason | null {
  return (COMPARE_REASONS as readonly string[]).includes(reason ?? "") ? (reason as CompareReason) : null
}

/**
 * A stored candidate → the run shape a live column renders. A stored run is
 * always finished: an `error_code` means failed, anything else is done.
 */
export function mapCandidate(row: AiCompareCandidateRow, keyPrefix: string): CompareRun {
  const run: CompareRun = {
    model: row.model,
    text: row.text ?? "",
    tools: mapToolRows(row.tools, `${keyPrefix}:${row.model}`),
    status: row.error_code ? "failed" : "done",
  }
  const usage = mapUsage(row.usage)
  if (usage) run.usage = usage
  if (row.error_code) run.error = { code: row.error_code, message: "" }
  if (typeof row.elapsed_ms === "number") run.elapsedMs = row.elapsed_ms
  return run
}

/**
 * Transcript rows → the hook's message shape.
 *
 * A stored tool row is always finished (`ok` is a boolean); a null `ok` on the
 * wire would mean the turn died mid-call, and the closest honest rendering is
 * "failed" rather than an eternal spinner. `key` only needs to be unique
 * within the message, so `seq#index` is enough.
 *
 * An assistant row that came out of a compare turn carries `compare`: the
 * chosen answer is the row itself, the others are `alternatives`. A row with
 * an `error_code` and a `compare` is a turn where no run was selectable
 * (`void`) — there every run is an alternative.
 */
export function mapSessionMessages(rows: AiSessionMessageRow[]): AiMessage[] {
  return [...rows]
    .sort((a, b) => a.seq - b.seq)
    .map((row) => {
      const message: AiMessage = {
        id: `s${row.seq}`,
        role: row.role,
        text: row.text ?? "",
        tools: mapToolRows(row.tools, String(row.seq)),
      }
      const usage = mapUsage(row.usage)
      if (usage) message.usage = usage
      if (row.role === "assistant" && row.error_code) {
        message.error = { code: row.error_code, message: "" }
      }
      if (row.role === "assistant" && row.model) message.model = row.model as AiModel
      if (row.role === "assistant" && row.compare) {
        const runs = (row.compare.alternatives ?? []).map((c) => mapCandidate(c, `s${row.seq}`))
        const isVoid = Boolean(row.error_code)
        const compare: AiCompare = {
          compareId: row.compare.compare_id,
          state: isVoid ? "void" : "selected",
          models: isVoid || !row.model ? runs.map((r) => r.model) : [row.model, ...runs.map((r) => r.model)],
          runs,
          selectable: [],
          reason: mapReason(row.compare.reason),
        }
        if (!isVoid && row.model) compare.selectedModel = row.model
        message.compare = compare
      }
      return message
    })
}

/**
 * A compare turn that is still open → the two rows a live turn would show.
 *
 * While a turn waits for a choice the server writes nothing to the transcript
 * table (02 §21), so the question and its candidates only exist here. They are
 * mapped to exactly what the streaming hook builds, so a reloaded pending turn
 * renders through the same components as one that just finished.
 *
 * `running` means the server is still finishing the runs in the background:
 * no candidates yet, every column is a placeholder.
 */
export function mapPendingCompare(pending: AiPendingCompare): AiMessage[] {
  const candidates = pending.candidates ?? []
  const byModel = new Map(candidates.map((c) => [c.model, c]))
  const models = pending.models?.length ? pending.models : candidates.map((c) => c.model)
  const running = pending.state === "running"
  const prefix = `c${pending.compare_id}`
  const runs: CompareRun[] = models.map((model) => {
    const row = byModel.get(model)
    if (row) return mapCandidate(row, prefix)
    return running
      ? { model, text: "", tools: [], status: "running" }
      : { model, text: "", tools: [], status: "failed", error: { code: "incomplete", message: "" } }
  })
  const startedAt = Date.parse(pending.created_at)
  const compare: AiCompare = {
    compareId: pending.compare_id,
    state: running ? "running" : "pending",
    models: [...models],
    runs,
    selectable: running ? [] : candidates.filter((c) => c.selectable === true).map((c) => c.model),
    detached: running,
  }
  if (Number.isFinite(startedAt)) compare.startedAt = startedAt
  return [
    { id: `${prefix}:q`, role: "user", text: pending.question ?? "", tools: [] },
    { id: `${prefix}:a`, role: "assistant", text: "", tools: [], compare },
  ]
}

/**
 * The whole visible transcript of a stored conversation: its messages, then
 * the open compare turn if there is one.
 *
 * `local` is the transcript currently on screen. When the server says the
 * compare is still `running` and the screen already shows that same compare
 * (the reader pressed Stop on it), the on-screen rows are kept — they hold the
 * text streamed so far, which the server will only hand back once the turn
 * has finished.
 */
export function sessionTranscript(detail: AiSessionDetail, local: readonly AiMessage[] = []): AiMessage[] {
  const stored = mapSessionMessages(detail.messages ?? [])
  const pending = detail.pending_compare
  if (!pending) return stored
  if (pending.state === "running") {
    const idx = local.findIndex(
      (m) => m.role === "assistant" && m.compare?.compareId === pending.compare_id && m.compare.state === "running",
    )
    if (idx !== -1) {
      const kept = local.slice(Math.max(0, idx - 1), idx + 1).map((m) =>
        m.compare ? { ...m, stopped: false, compare: { ...m.compare, detached: true } } : m,
      )
      return [...stored, ...kept]
    }
  }
  return [...stored, ...mapPendingCompare(pending)]
}

/** The list row's display title; `fallback` is the localised "Untitled". */
export function sessionTitle(session: Pick<AiSessionSummary, "title">, fallback: string): string {
  const title = session.title?.trim()
  return title ? title : fallback
}

/** Model id → a value the composer's Select accepts, or null when unknown. */
export function sessionModel(model: string | null, known: readonly AiModel[]): AiModel | null {
  return model && (known as readonly string[]).includes(model) ? (model as AiModel) : null
}

/**
 * Whether a `sessionId` change should be written to sessionStorage.
 *
 * Only a real id is ever written by the change effect. `null` is NOT written
 * there, because the very first run of that effect (mount, before anything
 * was resumed) has `sessionId === null` and would wipe the id the
 * refresh-resume effect is about to read — the page then always started
 * empty after a reload (cold review #8; React StrictMode re-runs effects, so
 * a "skip the first call" flag is not enough). Clearing is an explicit act:
 * `newConversation` / delete-active call `writeStoredSessionId(null)`.
 */
export function shouldPersistSessionId(next: string | null): boolean {
  return typeof next === "string" && next.length > 0
}

export function readStoredSessionId(): string | null {
  try {
    return window.sessionStorage.getItem(AI_SESSION_STORAGE_KEY)
  } catch {
    return null
  }
}

export function writeStoredSessionId(id: string | null): void {
  try {
    if (id) window.sessionStorage.setItem(AI_SESSION_STORAGE_KEY, id)
    else window.sessionStorage.removeItem(AI_SESSION_STORAGE_KEY)
  } catch {
    /* private window / blocked storage: resume simply does not survive a refresh */
  }
}
