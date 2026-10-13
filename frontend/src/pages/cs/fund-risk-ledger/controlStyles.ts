/**
 * Sizing shared by every control of the entry tab, so an input, a dropdown, a
 * date trigger and a button placed in one row are the same box.
 *
 * Values come from the shadcn primitives in components/ui: Input, the default
 * Button and the default SelectTrigger are all h-9 / rounded-md / px-3 / border
 * / shadow-xs. The one thing that differs between them is the font size:
 * Input and Textarea are `text-base md:text-sm` while Button and SelectTrigger
 * are `text-sm`, so the text size is pinned here.
 */

/** Input / SelectTrigger in a form row: full width of the control column. */
export const CONTROL_CLASS = "h-9 w-full min-w-0 text-sm";

/** Textarea in a form row. */
export const TEXTAREA_CLASS = "w-full min-w-0 text-sm";

/**
 * A plain <button> that looks exactly like a SelectTrigger (same classes as
 * components/ui/select.tsx). Used by the two popover-based pickers — the
 * searchable province list and the date calendar — which cannot be a Select.
 */
export const SELECT_LIKE_TRIGGER_CLASS =
  "border-input flex h-9 w-full min-w-0 items-center justify-between gap-2 rounded-md border bg-transparent px-3 py-2 text-left text-sm whitespace-nowrap shadow-xs transition-[color,box-shadow] outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50";

/** Trailing icon of a select-like trigger (matches the Select chevron). */
export const TRIGGER_ICON_CLASS = "size-4 shrink-0 text-muted-foreground opacity-50";

/**
 * The transaction-id bar (input + button as one unit). `hero` is the centred
 * idle view (Button size="lg" height), `compact` the bar above an open form.
 */
export const TXN_BAR_CLASS = {
  hero: "h-10 text-sm",
  compact: "h-9 text-sm",
} as const;
