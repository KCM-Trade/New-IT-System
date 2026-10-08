import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  IconArrowUp,
  IconChevronRight,
  IconHistory,
  IconMessageChatbot,
  IconPlayerStopFilled,
} from "@tabler/icons-react"

import { AiStatusBar, type TodayUsage } from "@/components/ai/AiStatusBar"
import { CompareBlock } from "@/components/ai/CompareBlock"
import { CompareModelPicker } from "@/components/ai/CompareModelPicker"
import { ErrorLine } from "@/components/ai/ErrorLine"
import { SessionList } from "@/components/ai/SessionList"
import { SourceBadge } from "@/components/ai/SourceBadge"
import { MarkdownMessage } from "@/components/ai/MarkdownMessage"
import { useI18n } from "@/components/i18n-provider"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  AI_MODELS,
  DEFAULT_AI_MODEL,
  useAiTurn,
  type AiMessage,
  type AiModel,
} from "@/hooks/useAiTurn"
import { readFilterState, useFilterPersist } from "@/hooks/useFilterPersist"
import {
  COMPARE_REASONS,
  compareModelsValid,
  isOpenCompare,
  normalizeCompareModels,
  sumUsage,
  type AiCompare,
  type CompareReason,
} from "@/lib/ai-compare"
import { sessionLinkAllowlists } from "@/lib/ai-tools"
import {
  readStoredSessionId,
  sessionModel,
  shouldPersistSessionId,
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
 *
 * Compare mode (OPT-0076): a labelled switch in the composer, off by default.
 * Off, the page is the one described above. On, the model picker becomes a
 * 2–3 model multi-select and a question fans out to all of them; the answers
 * render side by side in a block that uses the full content width (see
 * components/ai/CompareBlock for the one layout rule). Two consequences live
 * here:
 *   - the history column collapses to its icon whenever the switch is on or
 *     the open conversation has an unresolved compare turn — at 1280 / 1440
 *     its 256px are the difference between one column and two;
 *   - while a compare turn is unresolved the composer is replaced by a notice
 *     with one button per selectable answer. The server refuses any question
 *     until one is chosen, so that holds even with the switch turned off.
 * The switch and the model set are a "how I look at data" preference and are
 * kept in localStorage (`AI_ASSISTANT_MAIN_FILTERS_V1`). They are deliberately
 * NOT in the view-profiles manifest: a profile can be claimed by a colleague,
 * and claiming one must not silently double their quota spend.
 */

// The DashboardLayout wrapper is a plain block with `pt-4` (1rem) under a
// 3.5rem header, so the page takes the rest of the viewport itself.
const PAGE_HEIGHT = "h-[calc(100svh-var(--header-height)-1rem)]"
const COLUMN = "mx-auto w-full max-w-3xl"

const FILTERS_KEY = "AI_ASSISTANT_MAIN_FILTERS_V1"
type AiAssistantFilters = { compare: boolean; compareModels: string[] }
const FILTER_DEFAULTS: AiAssistantFilters = { compare: false, compareModels: [] }

/**
 * The stored model set, sanitised. Someone who never used compare mode has
 * nothing stored; their pair is decided at the first switch-on, when the
 * single model they are using can stand in for "the other model used last".
 */
function loadCompareModels(stored: AiAssistantFilters): AiModel[] {
  const raw: unknown = stored.compareModels
  if (!stored.compare && !(Array.isArray(raw) && raw.length > 0)) return []
  return normalizeCompareModels(raw, AI_MODELS) as AiModel[]
}

export default function AiAssistantPage() {
  const { t } = useI18n()
  const [draft, setDraft] = useState("")
  const [model, setModel] = useState<AiModel>(DEFAULT_AI_MODEL)
  const [storedFilters] = useState(() => readFilterState(FILTERS_KEY, FILTER_DEFAULTS))
  const [compareOn, setCompareOn] = useState(() => storedFilters.compare === true)
  const [compareModels, setCompareModels] = useState<AiModel[]>(() => loadCompareModels(storedFilters))
  useFilterPersist<AiAssistantFilters>(FILTERS_KEY, FILTER_DEFAULTS, { compare: compareOn, compareModels })
  const compareReady = compareModelsValid(compareModels)
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

  const {
    messages,
    streaming,
    usage,
    sessionId,
    loadingSession,
    send,
    stop,
    pendingCompare,
    selecting,
    selectError,
    select,
    resumeSession,
    newConversation,
  } = useAiTurn({
      onTurnEnd: () => {
        fetchToday()
        // The first turn gives the row its title; later turns move it to the top.
        fetchSessions()
      },
    })
  const hasConversation = messages.length > 0
  // Which links each message may render as clickable: session-scoped once any
  // message has searched the web (see `sessionLinkAllowlists`).
  const linkAllowlists = useMemo(() => sessionLinkAllowlists(messages), [messages])
  // An unresolved compare turn owns the conversation: nothing can be asked
  // until an answer is chosen. Independent of the switch on purpose.
  const awaitingChoice = pendingCompare !== null && !streaming
  // Compare needs the width: the history column gives way to its icon.
  const wideLayout = compareOn || pendingCompare !== null

  // Keep the tab's "current conversation" in step with the hook. Only real
  // ids are written here; clearing is explicit (startNew / delete-active) —
  // see shouldPersistSessionId for why the mount-time null must not write.
  useEffect(() => {
    if (shouldPersistSessionId(sessionId)) writeStoredSessionId(sessionId)
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
        if (id === sessionId) {
          newConversation()
          writeStoredSessionId(null)
        }
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
    if (!q || streaming || pendingCompare) return
    if (compareOn && !compareReady) return
    setDraft("")
    if (compareOn) void send(q, compareModels[0], compareModels)
    else void send(q, model)
  }, [draft, streaming, pendingCompare, compareOn, compareReady, compareModels, send, model])

  const toggleCompare = useCallback(
    (on: boolean) => {
      setCompareOn(on)
      // First switch-on (or a set left below two): gpt-5.6-terra plus the
      // model in use, falling back to grok-4.7.
      if (on && !compareModelsValid(compareModels)) {
        setCompareModels(normalizeCompareModels(compareModels, AI_MODELS, model) as AiModel[])
      }
    },
    [compareModels, model],
  )

  const chooseAnswer = useCallback(
    (compareId: string, chosen: string) => void select(compareId, chosen),
    [select],
  )
  const giveReason = useCallback(
    (compareId: string, chosen: string, reason: CompareReason) => void select(compareId, chosen, reason),
    [select],
  )

  // "This turn" in the status line: a compare turn's cost is the sum of its runs.
  const lastCompare = useMemo<AiCompare | null>(() => {
    const last = messages.length ? messages[messages.length - 1] : null
    return last?.compare && last.compare.state !== "selected" ? last.compare : null
  }, [messages])
  const compareUsage = useMemo(
    () => (lastCompare ? sumUsage(lastCompare.runs.map((r) => r.usage)) : null),
    [lastCompare],
  )
  const statusBar = (
    <AiStatusBar
      model={model}
      lead={
        compareOn
          ? compareReady
            ? t("ai.compare.statusLine", { n: compareModels.length })
            : t("ai.compare.needTwo")
          : undefined
      }
      turnUsage={lastCompare ? compareUsage : usage}
      turnRuns={lastCompare ? lastCompare.runs.length : 0}
      today={today}
    />
  )

  const startNew = useCallback(() => {
    newConversation()
    writeStoredSessionId(null)
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
  // The same list behind one icon, in a sheet: on narrow screens always, and
  // at every width while compare mode needs the room (`wideLayout`), where the
  // icon moves from the composer to the top-left of the conversation.
  const historyToggle = showHistory ? (
    <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("ai.history")}
          title={t("ai.history")}
          className={cn("size-8 text-muted-foreground", !wideLayout && "md:hidden")}
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

  const compareSwitch = (
    <label
      title={t("ai.compare.switchHint")}
      className="flex shrink-0 cursor-pointer items-center gap-1.5 pl-1.5 text-xs text-muted-foreground"
    >
      <Switch
        checked={compareOn}
        onCheckedChange={toggleCompare}
        disabled={streaming}
        aria-label={t("ai.compare.switchHint")}
      />
      <span className={cn(compareOn && "text-foreground")}>{t("ai.compare.switchLabel")}</span>
    </label>
  )

  // Replaces the composer while a compare turn is unresolved (design sketch
  // "待选择态"). The buttons are outline: the filled one is in each column.
  const pendingNotice = pendingCompare ? (
    <div className="rounded-2xl border bg-card px-4 py-3 shadow-sm" role="status">
      <p className="text-sm">
        {pendingCompare.state === "pending"
          ? t("ai.compare.pendingNotice", { n: pendingCompare.selectable.length })
          : t("ai.compare.pendingRunning")}
      </p>
      {pendingCompare.state === "pending" && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">{t("ai.compare.continueUsing")}</span>
          {pendingCompare.selectable.map((m) => (
            <Button
              key={m}
              variant="outline"
              size="sm"
              disabled={selecting !== null}
              onClick={() => chooseAnswer(pendingCompare.compareId, m)}
              className="h-7 font-mono text-xs"
            >
              {m}
            </Button>
          ))}
        </div>
      )}
      {selectError && (
        <div className="mt-2">
          <ErrorLine error={selectError} />
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">{t("ai.compare.switchNoEffect")}</span>
        {compareSwitch}
      </div>
    </div>
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
        <div className="flex min-w-0 items-center gap-1">
        {!wideLayout && historyToggle}
        {compareOn ? (
          <CompareModelPicker
            models={AI_MODELS}
            value={compareModels}
            onChange={(next) => setCompareModels(next as AiModel[])}
            disabled={streaming}
          />
        ) : (
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
            <SelectItem value="gpt-6.1-sol" className="items-start py-2">
              <ModelOption label={t("ai.modelFrontier")} desc={t("ai.modelFrontierDesc")} />
            </SelectItem>
            <SelectItem value="grok-4.7" className="items-start py-2">
              <ModelOption label={t("ai.modelGrok")} desc={t("ai.modelGrokDesc")} />
            </SelectItem>
            <SelectItem value="DeepSeek-V4-Pro" className="items-start py-2">
              <ModelOption label={t("ai.modelDeepSeek")} desc={t("ai.modelDeepSeekDesc")} />
            </SelectItem>
          </SelectContent>
        </Select>
        )}
        {compareSwitch}
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
            disabled={!draft.trim() || (compareOn && !compareReady)}
            aria-label={t("ai.send")}
            title={compareOn && !compareReady ? t("ai.compare.needTwo") : t("ai.send")}
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
  const historyColumn =
    showHistory && !wideLayout ? (
      <aside className="hidden w-60 shrink-0 py-3 md:block">{historyList}</aside>
    ) : null

  const main = !hasConversation ? (
    // Empty state: the composer alone, vertically centred, the way a fresh
    // ChatGPT / Gemini window opens — product name above, status line below.
    <div className="relative flex min-w-0 flex-1 flex-col items-center justify-center pb-16">
      {wideLayout && historyToggle && <div className="absolute left-0 top-0">{historyToggle}</div>}
      <div className={cn(COLUMN, "flex flex-col gap-5")}>
        <h1 className="text-center text-2xl font-medium tracking-tight">{t("ai.greeting")}</h1>
        {composer}
        <div className="flex justify-center px-1">{statusBar}</div>
      </div>
    </div>
  ) : (
    <div className="flex min-w-0 flex-1 flex-col">
      {wideLayout && historyToggle && <div className="flex h-9 shrink-0 items-center">{historyToggle}</div>}
      {/* Transcript — scrolls on its own; the composer below never moves.
          Rows centre themselves in the reading column; a compare block is the
          one thing that takes the full width. */}
      <div
        ref={transcriptRef}
        onScroll={onTranscriptScroll}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        <div className="flex flex-col gap-6 py-4">
          {messages.map((m, i) => (
            <MessageRow
              key={m.id}
              message={m}
              allowedLinks={linkAllowlists[i]}
              streaming={streaming}
              selecting={selecting}
              onSelect={chooseAnswer}
              onReason={giveReason}
            />
          ))}
        </div>
      </div>

      {/* Composer (or the pending-choice notice) + one muted status line */}
      <div className={cn(COLUMN, "flex flex-col gap-2 pb-4 pt-2")}>
        {awaitingChoice ? pendingNotice : composer}
        {/* A refused choice (stale / no longer selectable) ends the pending
            state, so its explanation has to outlive the notice. */}
        {selectError && !awaitingChoice && (
          <div className="px-1">
            <ErrorLine error={selectError} />
          </div>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2 px-1">{statusBar}</div>
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
// must not re-render. `streaming` flips only at turn boundaries, `selecting`
// only around a choice, and the two callbacks are stable.
const MessageRow = memo(function MessageRow({
  message,
  allowedLinks,
  streaming,
  selecting,
  onSelect,
  onReason,
}: {
  message: AiMessage
  /** The session's clickable-link set at this message (a string, so the memo holds). */
  allowedLinks: string | undefined
  streaming: boolean
  selecting: string | null
  onSelect: (compareId: string, model: string) => void
  onReason: (compareId: string, model: string, reason: CompareReason) => void
}) {
  const { t } = useI18n()
  const compare = message.compare
  // A turn where no run was selectable has nothing but its runs to show, so
  // they start expanded; the alternatives of a chosen answer start folded.
  const [othersOpen, setOthersOpen] = useState(compare?.state === "void")

  if (message.role === "user") {
    return (
      <div className={cn(COLUMN, "flex justify-end")}>
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-muted px-4 py-2 text-sm">
          {message.text}
        </div>
      </div>
    )
  }

  // Still generating, or waiting for a choice: the runs are the answer.
  if (compare && isOpenCompare(compare)) {
    return <CompareBlock compare={compare} mode="choose" selecting={selecting} allowedLinks={allowedLinks} onSelect={onSelect} />
  }

  const isLive = streaming && !message.error && !message.stopped
  const showThinking = isLive && message.text === "" && message.tools.every((tl) => tl.ok !== null)
  const chosen = compare?.state === "selected" ? compare : null
  const others = compare && compare.runs.length > 0 ? compare : null

  return (
    <div>
      <div className={cn(COLUMN, "flex gap-3")}>
        <div className="mt-0.5 shrink-0 rounded-lg bg-muted p-1.5">
          <IconMessageChatbot className="h-4 w-4 text-muted-foreground" />
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          {message.text && (
            <MarkdownMessage text={message.text} allowedLinks={allowedLinks} />
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
          {(message.model || chosen) && (
            // Which model said this — on every answer, live or from history.
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span>
                {message.model && <span className="font-mono">{message.model}</span>}
                {chosen && (
                  <>
                    {message.model ? " · " : ""}
                    {t("ai.compare.chosenFrom", { n: chosen.runs.length + 1 })}
                  </>
                )}
              </span>
              {chosen && message.model && (
                <ReasonRow
                  compare={chosen}
                  model={message.model}
                  disabled={selecting !== null || streaming}
                  onReason={onReason}
                />
              )}
            </div>
          )}
          {others && (
            <button
              type="button"
              onClick={() => setOthersOpen((v) => !v)}
              aria-expanded={othersOpen}
              className="inline-flex items-center gap-1 rounded text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
            >
              <IconChevronRight className={cn("size-3.5 transition-transform", othersOpen && "rotate-90")} />
              {othersOpen
                ? t("ai.compare.hideOthers")
                : t(chosen ? "ai.compare.showOthers" : "ai.compare.showRuns", { n: others.runs.length })}
            </button>
          )}
        </div>
      </div>
      {others && othersOpen && (
        // Same k-column rule as the live block; read-only, no select buttons.
        <div className="mt-3">
          <CompareBlock compare={others} mode="alternatives" allowedLinks={allowedLinks} />
        </div>
      )}
    </div>
  )
})

/**
 * "Why this one?" — optional, never blocks. A click re-sends the same choice
 * with the reason attached (the select endpoint is idempotent, 02 §22). Once
 * a reason is stored the row reads back as plain text on later visits.
 */
function ReasonRow({
  compare,
  model,
  disabled,
  onReason,
}: {
  compare: AiCompare
  model: string
  disabled: boolean
  onReason: (compareId: string, model: string, reason: CompareReason) => void
}) {
  const { t } = useI18n()
  const [stored] = useState<CompareReason | null>(compare.reason ?? null)
  const [picked, setPicked] = useState<CompareReason | null>(compare.reason ?? null)

  if (stored) {
    return (
      <span>
        {t("ai.compare.reasonLabel")}
        {t(`ai.compare.reasons.${stored}`)}
      </span>
    )
  }
  return (
    <span className="flex flex-wrap items-center gap-1">
      <span>{t("ai.compare.whyChosen")}</span>
      {COMPARE_REASONS.map((r) => (
        <button
          key={r}
          type="button"
          disabled={disabled}
          aria-pressed={picked === r}
          onClick={() => {
            if (picked === r) return
            setPicked(r)
            onReason(compare.compareId, model, r)
          }}
          className={cn(
            "rounded-full border px-2 py-0.5 hover:text-foreground focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-60",
            picked === r ? "border-transparent bg-secondary text-foreground" : "border-border",
          )}
        >
          {t(`ai.compare.reasons.${r}`)}
        </button>
      ))}
    </span>
  )
}
