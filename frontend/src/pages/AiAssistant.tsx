import { memo, useCallback, useEffect, useRef, useState } from "react"
import {
  IconArrowUp,
  IconHistory,
  IconMessageChatbot,
  IconPlayerStopFilled,
} from "@tabler/icons-react"

import { AiStatusBar, type TodayUsage } from "@/components/ai/AiStatusBar"
import { SessionList } from "@/components/ai/SessionList"
import { SourceBadge } from "@/components/ai/SourceBadge"
import { useI18n } from "@/components/i18n-provider"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { Textarea } from "@/components/ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  AI_MODELS,
  DEFAULT_AI_MODEL,
  useAiTurn,
  type AiMessage,
  type AiModel,
  type TurnError,
} from "@/hooks/useAiTurn"
import {
  readStoredSessionId,
  sessionModel,
  writeStoredSessionId,
  type AiSessionListResponse,
  type AiSessionSummary,
} from "@/lib/ai-session"
import { apiFetch } from "@/lib/fetch"
import { cn } from "@/lib/utils"

/**
 * /ai/assistant — the risk-team analyst agent (slice 1, Preview).
 *
 * Second UI pass (2026-09-27): modelled on the plain ChatGPT / Gemini chat
 * window. The page owns the viewport below the site header; before the first
 * question there is nothing but the product name, the composer and the muted
 * status line (model · tokens · cost · today's quota — the user asked to keep
 * it visible at all times). Once a conversation exists the transcript scrolls
 * in the middle and the composer stays pinned at the bottom. Everything that
 * used to explain the page (title, Preview badge, stateless note, badge
 * legend, keyboard hint, example chips) is gone — the site header already
 * names the page, and the rest was reading material nobody asked for.
 *
 * Slice 2 adds memory (docs/ai-agent/02 §8): the server keeps every
 * conversation, so a history column sits to the left on wide screens (a
 * sheet behind one icon on narrow ones) with rename / delete per row and a
 * single "new conversation" entry point at its top. The id of the open
 * conversation lives in sessionStorage — tab-scoped on purpose: a refresh
 * comes back to the same investigation, a new tab starts clean, and nothing
 * about *what* was being investigated is stored as a preference.
 */

// The DashboardLayout wrapper is a plain block with `pt-4` (1rem) under a
// 3.5rem header, so the page takes the rest of the viewport itself.
const PAGE_HEIGHT = "h-[calc(100svh-var(--header-height)-1rem)]"
const COLUMN = "mx-auto w-full max-w-3xl"

export default function AiAssistantPage() {
  const { t } = useI18n()
  const [draft, setDraft] = useState("")
  const [model, setModel] = useState<AiModel>(DEFAULT_AI_MODEL)
  const [today, setToday] = useState<TodayUsage | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const transcriptRef = useRef<HTMLDivElement>(null)
  // Whether the reader was at (or near) the bottom before the latest render.
  // Sampled on scroll so a person who scrolled up to re-read an earlier
  // answer is not dragged back down on every streamed frame.
  const stickToBottomRef = useRef(true)

  const fetchToday = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await apiFetch("/api/v1/ai/usage/today", { signal })
      if (!res.ok) return
      setToday((await res.json()) as TodayUsage)
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return
      // The status line simply keeps its last value; the quota is enforced
      // server-side regardless of whether the browser can display it.
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    fetchToday(controller.signal)
    return () => controller.abort()
  }, [fetchToday])

  // ── history ──────────────────────────────────────────────────────────────
  const [sessions, setSessions] = useState<AiSessionSummary[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)

  const fetchSessions = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await apiFetch("/api/v1/ai/sessions?limit=50", { signal })
      if (!res.ok) return
      const body = (await res.json()) as AiSessionListResponse
      setSessions(Array.isArray(body.data) ? body.data : [])
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return
      // The list keeps its last value; the conversation itself is unaffected.
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    fetchSessions(controller.signal)
    return () => controller.abort()
  }, [fetchSessions])

  const { messages, streaming, usage, sessionId, loadingSession, send, stop, resumeSession, newConversation } =
    useAiTurn({
      onTurnEnd: () => {
        fetchToday()
        // The first turn gives the row its title; later turns move it to the top.
        fetchSessions()
      },
    })
  const hasConversation = messages.length > 0

  // Keep the tab's "current conversation" in step with the hook.
  useEffect(() => {
    writeStoredSessionId(sessionId)
  }, [sessionId])

  // Refresh-resume: reopen the conversation this tab had before the reload.
  // A 404 (deleted elsewhere, or a stale id) is silently dropped.
  useEffect(() => {
    const stored = readStoredSessionId()
    if (!stored) return
    const controller = new AbortController()
    void resumeSession(stored, controller.signal).then((detail) => {
      if (controller.signal.aborted) return
      if (!detail) {
        writeStoredSessionId(null)
        return
      }
      const m = sessionModel(detail.session.model, AI_MODELS)
      if (m) setModel(m)
    })
    return () => controller.abort()
    // Mount-only on purpose: `resumeSession` is stable (useCallback with no deps).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const openSession = useCallback(
    async (id: string) => {
      if (streaming || id === sessionId) {
        setHistoryOpen(false)
        return
      }
      setHistoryOpen(false)
      const detail = await resumeSession(id)
      if (!detail) {
        // Gone since the list was fetched — drop it from the list too.
        setSessions((prev) => prev.filter((s) => s.session_id !== id))
        return
      }
      const m = sessionModel(detail.session.model, AI_MODELS)
      if (m) setModel(m)
    },
    [streaming, sessionId, resumeSession],
  )

  const renameSession = useCallback(async (id: string, title: string) => {
    // Optimistic: the row reads the new name at once; a failed PATCH is
    // corrected by the refetch.
    setSessions((prev) => prev.map((s) => (s.session_id === id ? { ...s, title } : s)))
    try {
      await apiFetch(`/api/v1/ai/sessions/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      })
    } finally {
      fetchSessions()
    }
  }, [fetchSessions])

  const deleteSession = useCallback(
    async (id: string) => {
      try {
        await apiFetch(`/api/v1/ai/sessions/${encodeURIComponent(id)}`, { method: "DELETE" })
      } finally {
        setSessions((prev) => prev.filter((s) => s.session_id !== id))
        if (id === sessionId) newConversation()
        fetchSessions()
      }
    },
    [sessionId, newConversation, fetchSessions],
  )

  const onTranscriptScroll = useCallback(() => {
    const el = transcriptRef.current
    if (!el) return
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }, [])

  // Follow the stream only while the reader is already at the bottom. A new
  // turn (messages.length changes) always scrolls: the user just sent it.
  const lastText = messages.length ? messages[messages.length - 1].text : ""
  useEffect(() => {
    const el = transcriptRef.current
    if (!el) return
    if (stickToBottomRef.current) el.scrollTop = el.scrollHeight
  }, [lastText])
  useEffect(() => {
    const el = transcriptRef.current
    if (!el) return
    stickToBottomRef.current = true
    el.scrollTop = el.scrollHeight
  }, [messages.length])

  const submit = useCallback(() => {
    const q = draft.trim()
    if (!q || streaming) return
    setDraft("")
    void send(q, model)
  }, [draft, streaming, send, model])

  const startNew = useCallback(() => {
    newConversation()
    setDraft("")
    setHistoryOpen(false)
    textareaRef.current?.focus()
  }, [newConversation])

  const showHistory = sessions.length > 0 || hasConversation
  const historyList = (
    <SessionList
      sessions={sessions}
      activeId={sessionId}
      busy={streaming || loadingSession}
      onSelect={(id) => void openSession(id)}
      onNew={startNew}
      onRename={renameSession}
      onDelete={deleteSession}
    />
  )
  // Narrow screens: the same list behind one icon, in a sheet.
  const historyToggle = showHistory ? (
    <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("ai.history")}
          title={t("ai.history")}
          className="size-8 text-muted-foreground md:hidden"
        >
          <IconHistory className="h-4 w-4" />
        </Button>
      </SheetTrigger>
      <SheetContent side="left" className="w-72 p-3 pt-10">
        <SheetTitle className="sr-only">{t("ai.history")}</SheetTitle>
        {historyList}
      </SheetContent>
    </Sheet>
  ) : null

  const composer = (
    <div className="rounded-2xl border bg-card px-4 pb-2.5 pt-3 shadow-sm">
      <Textarea
        ref={textareaRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          }
        }}
        placeholder={t("ai.placeholder")}
        rows={1}
        maxLength={4000}
        disabled={streaming}
        autoFocus
        className="max-h-48 min-h-6 resize-none overflow-y-auto border-0 bg-transparent p-0 text-sm shadow-none focus-visible:ring-0 md:text-sm"
      />
      <div className="mt-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1">
        {historyToggle}
        <Select value={model} onValueChange={(v) => setModel(v as AiModel)} disabled={streaming}>
          <SelectTrigger
            size="sm"
            aria-label={t("ai.modelLabel")}
            className="h-7 w-auto gap-1 rounded-full border-0 bg-muted px-2.5 text-xs shadow-none focus-visible:ring-0 [&_[data-desc]]:hidden"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent align="start">
            <SelectItem value="gpt-5.6-terra" className="items-start py-2">
              <ModelOption label={t("ai.modelStandard")} desc={t("ai.modelStandardDesc")} />
            </SelectItem>
            <SelectItem value="gpt-5.6-sol" className="items-start py-2">
              <ModelOption label={t("ai.modelDeep")} desc={t("ai.modelDeepDesc")} />
            </SelectItem>
          </SelectContent>
        </Select>
        </div>
        {streaming ? (
          <Button
            size="icon"
            variant="secondary"
            onClick={stop}
            aria-label={t("ai.stop")}
            title={t("ai.stop")}
            className="size-8 rounded-full"
          >
            <IconPlayerStopFilled className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            size="icon"
            onClick={submit}
            disabled={!draft.trim()}
            aria-label={t("ai.send")}
            title={t("ai.send")}
            className="size-8 rounded-full"
          >
            <IconArrowUp className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  )

  // Wide screens: the history column sits left of the conversation once there
  // is anything to list. With no sessions the page is exactly the slice-1
  // empty state — nothing to explain, nothing to navigate.
  const historyColumn = showHistory ? (
    <aside className="hidden w-60 shrink-0 py-3 md:block">{historyList}</aside>
  ) : null

  const main = !hasConversation ? (
    // Empty state: the composer alone, vertically centred, the way a fresh
    // ChatGPT / Gemini window opens — product name above, status line below.
    <div className="flex min-w-0 flex-1 flex-col items-center justify-center pb-16">
      <div className={cn(COLUMN, "flex flex-col gap-5")}>
        <h1 className="text-center text-2xl font-medium tracking-tight">{t("ai.greeting")}</h1>
        {composer}
        <div className="flex justify-center px-1">
          <AiStatusBar model={model} turnUsage={usage} today={today} />
        </div>
      </div>
    </div>
  ) : (
    <div className="flex min-w-0 flex-1 flex-col">
      {/* Transcript — scrolls on its own; the composer below never moves. */}
      <div
        ref={transcriptRef}
        onScroll={onTranscriptScroll}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        <div className={cn(COLUMN, "flex flex-col gap-6 py-4")}>
          {messages.map((m) => (
            <MessageRow key={m.id} message={m} streaming={streaming} />
          ))}
        </div>
      </div>

      {/* Composer + one muted status line */}
      <div className={cn(COLUMN, "flex flex-col gap-2 pb-4 pt-2")}>
        {composer}
        <div className="flex flex-wrap items-center justify-between gap-2 px-1">
          <AiStatusBar model={model} turnUsage={usage} today={today} />
        </div>
      </div>
    </div>
  )

  return (
    <div className={cn(PAGE_HEIGHT, "flex gap-4")}>
      {historyColumn}
      {main}
    </div>
  )
}

function ModelOption({ label, desc }: { label: string; desc: string }) {
  // The closed trigger renders the same children, so keep the label first and
  // hide the description there via the `[&_[data-desc]]:hidden` rule below.
  return (
    <span className="flex flex-col gap-0.5">
      <span>{label}</span>
      <span data-desc className="text-xs text-muted-foreground">
        {desc}
      </span>
    </span>
  )
}

// Memoised on purpose: while streaming, only the last assistant message object
// changes per frame; every earlier row keeps the same `message` reference and
// must not re-render. `streaming` flips only at turn boundaries.
const MessageRow = memo(function MessageRow({
  message,
  streaming,
}: {
  message: AiMessage
  streaming: boolean
}) {
  const { t } = useI18n()

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-muted px-4 py-2 text-sm">
          {message.text}
        </div>
      </div>
    )
  }

  const isLive = streaming && !message.error && !message.stopped
  const showThinking = isLive && message.text === "" && message.tools.every((tl) => tl.ok !== null)

  return (
    <div className="flex gap-3">
      <div className="mt-0.5 shrink-0 rounded-lg bg-muted p-1.5">
        <IconMessageChatbot className="h-4 w-4 text-muted-foreground" />
      </div>
      <div className="min-w-0 flex-1 space-y-2">
        {message.text && (
          <div className="whitespace-pre-wrap text-sm leading-relaxed">{message.text}</div>
        )}
        {showThinking && <p className="text-sm text-muted-foreground">{t("ai.thinking")}</p>}
        {message.tools.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {message.tools.map((tl) => (
              <SourceBadge key={tl.key} tool={tl} />
            ))}
          </div>
        )}
        {message.stopped && <p className="text-xs text-muted-foreground">{t("ai.stopped")}</p>}
        {message.error && <ErrorLine error={message.error} />}
      </div>
    </div>
  )
})

function ErrorLine({ error }: { error: TurnError }) {
  const { t } = useI18n()
  // The locale table is the whitelist: `t()` returns the key path itself for
  // a code it does not know, so a new backend code needs an i18n entry and
  // nothing else. Unknown codes fall back to the server's message plus the
  // raw code (always shown below) and the trace id.
  const key = `ai.errors.${error.code}`
  const translated = t(key)
  const text = translated !== key ? translated : error.message || error.code
  return (
    <p className={cn("text-xs", "text-destructive/80")}>
      {text}
      <span className="ml-2 font-mono text-muted-foreground">
        {error.code}
        {error.traceId ? ` · trace ${error.traceId}` : ""}
      </span>
    </p>
  )
}
