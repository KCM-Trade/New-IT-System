import { useCallback, useEffect, useRef, useState, type FocusEvent, type MouseEvent, type ReactNode } from "react"
import { IconCheck, IconPencil, IconPlus, IconTrash, IconX } from "@tabler/icons-react"

import { useI18n } from "@/components/i18n-provider"
import { Button } from "@/components/ui/button"
import { sessionTitle, type AiSessionSummary } from "@/lib/ai-session"
import { cn } from "@/lib/utils"

/**
 * The conversation history column of /ai/assistant (02 §8.7).
 *
 * Secondary content by design: the transcript is what the analyst is reading,
 * this list is how they get back to an earlier one. So it is a muted column
 * with no borders or cards — rows separated by spacing, the active row marked
 * by a soft background, and the row actions (rename / delete) only visible on
 * hover or when the row is active, so a dozen conversations do not read as
 * two dozen buttons.
 *
 * Delete is a two-step in place (trash → tick / cross) rather than a modal:
 * the cost of a mistake is one conversation, the row stays where the eye is,
 * and Esc / clicking away cancels.
 */

export interface SessionListProps {
  sessions: AiSessionSummary[]
  activeId: string | null
  /** Disables selection and actions while a turn is streaming. */
  busy: boolean
  onSelect: (id: string) => void
  onNew: () => void
  onRename: (id: string, title: string) => Promise<void>
  onDelete: (id: string) => Promise<void>
  className?: string
}

const TITLE_MAX = 120

export function SessionList({
  sessions,
  activeId,
  busy,
  onSelect,
  onNew,
  onRename,
  onDelete,
  className,
}: SessionListProps) {
  const { t } = useI18n()

  return (
    <nav aria-label={t("ai.history")} className={cn("flex h-full min-h-0 flex-col", className)}>
      <div className="flex items-center justify-between gap-2 px-2 pb-2">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {t("ai.history")}
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={onNew}
          disabled={busy}
          className="h-7 gap-1 px-2 text-xs text-muted-foreground"
        >
          <IconPlus className="h-3.5 w-3.5" />
          {t("ai.newConversation")}
        </Button>
      </div>
      <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-1">
        {sessions.length === 0 && (
          <li className="px-2 py-1 text-xs text-muted-foreground">{t("ai.historyEmpty")}</li>
        )}
        {sessions.map((s) => (
          <SessionRow
            key={s.session_id}
            session={s}
            active={s.session_id === activeId}
            busy={busy}
            onSelect={onSelect}
            onRename={onRename}
            onDelete={onDelete}
          />
        ))}
      </ul>
    </nav>
  )
}

function SessionRow({
  session,
  active,
  busy,
  onSelect,
  onRename,
  onDelete,
}: {
  session: AiSessionSummary
  active: boolean
  busy: boolean
  onSelect: (id: string) => void
  onRename: (id: string, title: string) => Promise<void>
  onDelete: (id: string) => Promise<void>
}) {
  const { t } = useI18n()
  const [mode, setMode] = useState<"idle" | "rename" | "confirmDelete">("idle")
  const [draft, setDraft] = useState("")
  const [pending, setPending] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  // Escape during a rename: the input unmounts and (browser-dependent) fires
  // blur, which would commit the draft anyway. The flag makes the cancel win.
  const renameCancelledRef = useRef(false)
  const fallback = t("ai.untitled")
  const title = sessionTitle(session, fallback)

  useEffect(() => {
    if (mode === "rename") inputRef.current?.select()
  }, [mode])

  // The delete confirmation cancels itself when the row loses focus, so a
  // stray click elsewhere never leaves a red tick waiting to be hit later.
  const onBlurCapture = useCallback(
    (e: FocusEvent<HTMLLIElement>) => {
      if (mode === "confirmDelete" && !e.currentTarget.contains(e.relatedTarget as Node | null)) {
        setMode("idle")
      }
    },
    [mode],
  )

  const startRename = (e: MouseEvent) => {
    e.stopPropagation()
    renameCancelledRef.current = false
    setDraft(session.title ?? "")
    setMode("rename")
  }

  const cancelRename = () => {
    renameCancelledRef.current = true
    setMode("idle")
  }

  const commitRename = async () => {
    if (renameCancelledRef.current) {
      renameCancelledRef.current = false
      return
    }
    const next = draft.trim().slice(0, TITLE_MAX)
    setMode("idle")
    if (!next || next === (session.title ?? "")) return
    setPending(true)
    try {
      await onRename(session.session_id, next)
    } finally {
      setPending(false)
    }
  }

  const commitDelete = async (e: MouseEvent) => {
    e.stopPropagation()
    setPending(true)
    try {
      await onDelete(session.session_id)
    } finally {
      setPending(false)
      setMode("idle")
    }
  }

  const disabled = busy || pending

  return (
    <li
      onBlurCapture={onBlurCapture}
      onKeyDown={(e) => {
        // Escape backs out of the delete confirmation from either of its
        // buttons; the rename input handles its own Escape above.
        if (e.key === "Escape" && mode === "confirmDelete") {
          e.preventDefault()
          setMode("idle")
        }
      }}
      className={cn(
        "group relative flex items-center gap-1 rounded-md text-sm",
        active ? "bg-muted" : "hover:bg-muted/60",
        disabled && "opacity-60",
      )}
    >
      {mode === "rename" ? (
        <input
          ref={inputRef}
          value={draft}
          maxLength={TITLE_MAX}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => void commitRename()}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault()
              void commitRename()
            } else if (e.key === "Escape") {
              e.preventDefault()
              cancelRename()
            }
          }}
          aria-label={t("ai.rename")}
          className="h-8 min-w-0 flex-1 rounded-md bg-transparent px-2 text-sm outline-none ring-1 ring-ring/40"
        />
      ) : (
        <button
          type="button"
          disabled={disabled}
          onClick={() => onSelect(session.session_id)}
          title={title}
          className="flex h-8 min-w-0 flex-1 items-center px-2 text-left disabled:cursor-default"
        >
          <span className={cn("truncate", !session.title && "text-muted-foreground")}>{title}</span>
        </button>
      )}

      {mode === "confirmDelete" ? (
        <span className="flex shrink-0 items-center gap-0.5 pr-1">
          <span className="px-1 text-xs text-muted-foreground">{t("ai.deleteConfirm")}</span>
          <RowIconButton label={t("ai.confirm")} onClick={commitDelete} disabled={disabled} destructive>
            <IconCheck className="h-3.5 w-3.5" />
          </RowIconButton>
          <RowIconButton
            label={t("ai.cancel")}
            onClick={(e) => {
              e.stopPropagation()
              setMode("idle")
            }}
          >
            <IconX className="h-3.5 w-3.5" />
          </RowIconButton>
        </span>
      ) : (
        mode === "idle" && (
          <span
            className={cn(
              "flex shrink-0 items-center gap-0.5 pr-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100",
              active && "opacity-100",
            )}
          >
            <RowIconButton label={t("ai.rename")} onClick={startRename} disabled={disabled}>
              <IconPencil className="h-3.5 w-3.5" />
            </RowIconButton>
            <RowIconButton
              label={t("ai.delete")}
              onClick={(e) => {
                e.stopPropagation()
                setMode("confirmDelete")
              }}
              disabled={disabled}
            >
              <IconTrash className="h-3.5 w-3.5" />
            </RowIconButton>
          </span>
        )
      )}
    </li>
  )
}

function RowIconButton({
  label,
  onClick,
  disabled,
  destructive,
  children,
}: {
  label: string
  onClick: (e: MouseEvent) => void
  disabled?: boolean
  destructive?: boolean
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "rounded p-1 text-muted-foreground hover:bg-background hover:text-foreground focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none",
        destructive && "hover:text-destructive",
      )}
    >
      {children}
    </button>
  )
}
