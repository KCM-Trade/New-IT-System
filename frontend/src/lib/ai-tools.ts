/**
 * Pure helpers for the AI assistant's tool calls: pairing `tool_done` with its
 * `tool_use`, and the web-search (`search_web`) citation handling.
 *
 * Shared by the single-model hook and the compare reducer so both pair events
 * the same way. Nothing here touches React or the DOM.
 */

import type { ToolCall, ToolSource } from "@/hooks/useAiTurn"

export const WEB_SEARCH_TOOL = "search_web"
export const MAX_CITATIONS = 10
export const MAX_CITATION_URL_LENGTH = 500
export const MAX_CITATION_TITLE_LENGTH = 200
export const MAX_QUERIES = 6
export const MAX_QUERY_LENGTH = 200

export interface ToolCitation {
  title: string
  url: string
}

export interface ToolUsePayload {
  name?: string
  input?: unknown
  call_id?: string
}

export interface ToolDonePayload {
  name?: string
  ok?: boolean
  source?: ToolSource | null
  certified?: boolean
  error_code?: string
  call_id?: string
  citations?: unknown
  queries?: unknown
}

/**
 * `url` when it is an absolute http(s) URL within the length cap, else null.
 * Citation cards build their own `<a href>` (they do not go through
 * react-markdown's urlTransform), so every href passes through here first.
 */
export function safeHttpUrl(url: unknown): string | null {
  if (typeof url !== "string") return null
  const trimmed = url.trim()
  if (!trimmed || trimmed.length > MAX_CITATION_URL_LENGTH) return null
  if (!/^https?:\/\//i.test(trimmed)) return null
  try {
    const parsed = new URL(trimmed)
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? trimmed : null
  } catch {
    return null
  }
}

/**
 * Comparison form of a link: parsed and re-serialised (so percent-encoding
 * and host case do not matter), fragment dropped (it never reaches a server).
 * The query string is kept on purpose — it is the exfiltration channel the
 * allow-list exists to close. Null for anything that is not http(s).
 */
export function normalizeLinkUrl(url: unknown): string | null {
  const safe = safeHttpUrl(url)
  if (!safe) return null
  const parsed = new URL(safe)
  parsed.hash = ""
  return parsed.href
}

/** Hostname for display ("www." dropped); empty when the URL is not usable. */
export function citationDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./i, "")
  } catch {
    return ""
  }
}

/** Wire `citations` → validated list; entries with a non-http(s) URL are dropped. */
export function sanitizeCitations(raw: unknown): ToolCitation[] {
  if (!Array.isArray(raw)) return []
  const out: ToolCitation[] = []
  const seen = new Set<string>()
  for (const item of raw) {
    if (!item || typeof item !== "object") continue
    const url = safeHttpUrl((item as { url?: unknown }).url)
    if (!url || seen.has(url)) continue
    seen.add(url)
    const title = (item as { title?: unknown }).title
    out.push({ url, title: typeof title === "string" ? title.trim().slice(0, MAX_CITATION_TITLE_LENGTH) : "" })
    if (out.length >= MAX_CITATIONS) break
  }
  return out
}

export function sanitizeQueries(raw: unknown): string[] {
  if (!Array.isArray(raw)) return []
  return raw
    .filter((q): q is string => typeof q === "string" && q.trim().length > 0)
    .slice(0, MAX_QUERIES)
    .map((q) => q.trim().slice(0, MAX_QUERY_LENGTH))
}

/** `search_web`'s `input.query`, when the tool input has one; null otherwise. */
export function queryOf(input: unknown): string | null {
  if (!input || typeof input !== "object") return null
  const query = (input as { query?: unknown }).query
  return typeof query === "string" && query.trim() ? query.trim() : null
}

/** The optional web-search fields of a finished call; absent when empty. */
export function webFields(citations: unknown, queries: unknown): Pick<ToolCall, "citations" | "queries"> {
  const out: Pick<ToolCall, "citations" | "queries"> = {}
  const c = sanitizeCitations(citations)
  const q = sanitizeQueries(queries)
  if (c.length) out.citations = c
  if (q.length) out.queries = q
  return out
}

/** Append a started call. `key` must be unique within the message. */
export function appendToolUse(tools: readonly ToolCall[], payload: ToolUsePayload, key: string): ToolCall[] {
  if (!payload.name) return [...tools]
  const call: ToolCall = { key, name: payload.name, ok: null, certified: false, source: null, input: payload.input }
  if (typeof payload.call_id === "string" && payload.call_id) call.callId = payload.call_id
  return [...tools, call]
}

/**
 * Resolve a finished call. With a `call_id` the pending call carrying the same
 * id is resolved — concurrent `search_web` calls finish in any order, and each
 * `tool_done` carries that call's own citations. Without one (stored sessions,
 * an agent from before OPT-0078) the oldest still-pending call of that name is
 * taken, as before. A `tool_done` that matches nothing is appended under
 * `fallbackKey`.
 */
export function resolveToolDone(tools: readonly ToolCall[], payload: ToolDonePayload, fallbackKey: string): ToolCall[] {
  if (!payload.name) return [...tools]
  const callId = typeof payload.call_id === "string" && payload.call_id ? payload.call_id : undefined
  let idx = callId ? tools.findIndex((t) => t.callId === callId && t.ok === null) : -1
  if (idx === -1) {
    // Only calls without an id of their own: one that has an id is waiting
    // for its own `tool_done`.
    idx = tools.findIndex((t) => t.name === payload.name && t.ok === null && (!callId || !t.callId))
  }
  const ok = payload.ok === true
  const done: ToolCall = {
    key: idx === -1 ? fallbackKey : tools[idx].key,
    name: payload.name,
    ok,
    certified: Boolean(payload.certified),
    source: payload.source ?? null,
    errorCode: ok ? undefined : payload.error_code ?? "error",
    input: idx === -1 ? undefined : tools[idx].input,
    ...webFields(payload.citations, payload.queries),
  }
  const id = callId ?? (idx === -1 ? undefined : tools[idx].callId)
  if (id) done.callId = id
  const next = [...tools]
  if (idx === -1) next.push(done)
  else next[idx] = done
  return next
}

/**
 * The links a message may render as clickable, or `undefined` when the message
 * made no web search (then links behave as they always did).
 *
 * A message that searched the web has read attacker-controllable text; an
 * injected instruction can make the model write a link whose query string
 * carries data from its context. Only URLs the search itself returned as
 * citations stay clickable; everything else is shown as text.
 */
export function linkAllowlist(tools: readonly ToolCall[] | undefined): ReadonlySet<string> | undefined {
  if (!tools?.some((t) => t.name === WEB_SEARCH_TOOL)) return undefined
  const allowed = new Set<string>()
  for (const t of tools) {
    for (const c of t.citations ?? []) {
      const n = normalizeLinkUrl(c.url)
      if (n) allowed.add(n)
    }
  }
  return allowed
}

export function isWebSource(tool: Pick<ToolCall, "source">): boolean {
  return tool.source?.service === "web"
}
