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

export interface AiSessionSummary {
  session_id: string
  /** Null until the first turn has completed. */
  title: string | null
  model: string | null
  turns: number
  created_at: string
  updated_at: string
}

export interface AiSessionToolRow {
  name: string
  ok: boolean | null
  certified?: boolean | null
  source?: ToolSource | null
  error_code?: string | null
  /** The model's arguments (subject, date range, later the SQL text). */
  input?: unknown
}

export interface AiSessionMessageRow {
  seq: number
  role: "user" | "assistant"
  text: string
  tools: AiSessionToolRow[] | null
  usage: Partial<TurnUsage> | null
  error_code: string | null
  at: string
}

export interface AiSessionDetail {
  session: AiSessionSummary
  messages: AiSessionMessageRow[]
}

export interface AiSessionListResponse {
  data: AiSessionSummary[]
  total: number
}

export const AI_SESSION_STORAGE_KEY = "AI_ASSISTANT_SESSION_ID"

/**
 * Transcript rows → the hook's message shape.
 *
 * A stored tool row is always finished (`ok` is a boolean); a null `ok` on the
 * wire would mean the turn died mid-call, and the closest honest rendering is
 * "failed" rather than an eternal spinner. `key` only needs to be unique
 * within the message, so `seq#index` is enough.
 */
export function mapSessionMessages(rows: AiSessionMessageRow[]): AiMessage[] {
  return [...rows]
    .sort((a, b) => a.seq - b.seq)
    .map((row) => {
      const tools: ToolCall[] = (row.tools ?? []).map((t, i) => {
        const ok = t.ok === true
        return {
          key: `${row.seq}#${i}`,
          name: t.name,
          ok: t.ok === null || t.ok === undefined ? false : t.ok,
          certified: ok && Boolean(t.certified),
          source: t.source ?? null,
          errorCode: ok ? undefined : t.error_code ?? "error",
        }
      })
      const message: AiMessage = {
        id: `s${row.seq}`,
        role: row.role,
        text: row.text ?? "",
        tools,
      }
      if (row.usage) {
        message.usage = {
          input_tokens: row.usage.input_tokens ?? 0,
          output_tokens: row.usage.output_tokens ?? 0,
          cache_read_input_tokens: row.usage.cache_read_input_tokens ?? 0,
          cost_usd: row.usage.cost_usd ?? null,
        }
      }
      if (row.role === "assistant" && row.error_code) {
        message.error = { code: row.error_code, message: "" }
      }
      return message
    })
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
