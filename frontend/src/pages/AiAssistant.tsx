import { memo, useCallback, useEffect, useRef, useState } from "react"
import {
  IconMessageChatbot,
  IconPlayerStop,
  IconSend,
  IconSparkles,
  IconTrash,
} from "@tabler/icons-react"

import { AiStatusBar, type TodayUsage } from "@/components/ai/AiStatusBar"
import { SourceBadge } from "@/components/ai/SourceBadge"
import { useI18n } from "@/components/i18n-provider"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import {
  DEFAULT_AI_MODEL,
  useAiTurn,
  type AiMessage,
  type AiModel,
  type TurnError,
} from "@/hooks/useAiTurn"
import { apiFetch } from "@/lib/fetch"
import { cn } from "@/lib/utils"

/**
 * /ai/assistant — the risk-team analyst agent (slice 1, Preview).
 *
 * Layout is a single column: transcript on top, composer pinned below, a
 * one-line status bar in the footer. The hierarchy is deliberately flat —
 * the assistant's text is the primary content, the provenance badges under it
 * are the one accented element, and everything about cost / quota / model is
 * tertiary and muted (Refactoring UI: emphasise by de-emphasising).
 *
 * Slice 1 has no history: every turn is independent (docs/ai-agent/05 §1),
 * and the page says so instead of implying a memory it does not have.
 */

const PILL_GROUP = "inline-flex items-center rounded-full bg-muted p-0.5"
const PILL_ITEM =
  "flex-1 rounded-full px-3 py-1 text-center text-xs text-muted-foreground data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow"

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
      // The status bar simply keeps its last value; the quota is enforced
      // server-side regardless of whether the browser can display it.
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    fetchToday(controller.signal)
    return () => controller.abort()
  }, [fetchToday])

  const { messages, streaming, usage, send, stop, clear } = useAiTurn({
    onTurnEnd: () => {
      fetchToday()
    },
  })

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

  const fillExample = useCallback((text: string) => {
    setDraft(text)
    textareaRef.current?.focus()
  }, [])

  const examples = [t("ai.exampleOverview"), t("ai.exampleActivity"), t("ai.exampleSignals")]

  return (
    <div className="flex flex-1 flex-col gap-4 p-4 md:p-6">
      {/* Header: title + Preview badge on one line, the no-history note under it. */}
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold leading-tight">{t("ai.title")}</h1>
            <Badge variant="secondary" className="font-normal">
              {t("ai.preview")}
            </Badge>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{t("ai.statelessNote")}</p>
        </div>
        {messages.length > 0 && (
          <Button variant="ghost" size="sm" onClick={clear} disabled={streaming} className="gap-1.5">
            <IconTrash className="h-4 w-4" />
            {t("ai.clear")}
          </Button>
        )}
      </div>

      {/* Transcript */}
      <div
        ref={transcriptRef}
        onScroll={onTranscriptScroll}
        className="flex min-h-[320px] flex-1 flex-col overflow-y-auto rounded-xl border bg-card px-4 py-4 md:px-6"
      >
        {messages.length === 0 ? (
          <EmptyState examples={examples} onPick={fillExample} />
        ) : (
          <div className="flex flex-col gap-5">
            {messages.map((m) => (
              <MessageRow key={m.id} message={m} streaming={streaming} />
            ))}
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="rounded-xl border bg-card px-4 py-3 md:px-6">
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
          rows={3}
          maxLength={4000}
          disabled={streaming}
          className="min-h-[72px] resize-y border-0 bg-transparent p-0 shadow-none focus-visible:ring-0"
        />
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <ToggleGroup
            type="single"
            value={model}
            onValueChange={(v) => v && setModel(v as AiModel)}
            className={PILL_GROUP}
            aria-label={t("ai.modelLabel")}
          >
            <ToggleGroupItem value="gpt-5.6-terra" className={PILL_ITEM} disabled={streaming}>
              {t("ai.modelStandard")}
            </ToggleGroupItem>
            <ToggleGroupItem value="gpt-5.6-sol" className={PILL_ITEM} disabled={streaming}>
              {t("ai.modelDeep")}
            </ToggleGroupItem>
          </ToggleGroup>
          <div className="flex items-center gap-2">
            <span className="hidden text-xs text-muted-foreground sm:inline">{t("ai.enterHint")}</span>
            {streaming ? (
              <Button variant="outline" size="sm" onClick={stop} className="gap-1.5">
                <IconPlayerStop className="h-4 w-4" />
                {t("ai.stop")}
              </Button>
            ) : (
              <Button size="sm" onClick={submit} disabled={!draft.trim()} className="gap-1.5">
                <IconSend className="h-4 w-4" />
                {t("ai.send")}
              </Button>
            )}
          </div>
        </div>
      </div>

      <AiStatusBar model={model} turnUsage={usage} today={today} />
    </div>
  )
}

function EmptyState({ examples, onPick }: { examples: string[]; onPick: (text: string) => void }) {
  const { t } = useI18n()
  return (
    <div className="m-auto flex max-w-lg flex-col items-center gap-4 py-8 text-center">
      <div className="rounded-lg bg-muted p-2">
        <IconSparkles className="h-5 w-5 text-muted-foreground" />
      </div>
      <div className="space-y-1">
        <p className="text-sm font-medium">{t("ai.emptyTitle")}</p>
        <p className="text-sm text-muted-foreground">{t("ai.emptySub")}</p>
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        {examples.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => onPick(ex)}
            className="rounded-full border bg-background px-3 py-1 text-xs text-foreground transition-colors hover:bg-accent"
          >
            {ex}
          </button>
        ))}
      </div>
    </div>
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
