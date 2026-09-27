/**
 * Coalesce a stream of text deltas into at most one flush per scheduler tick.
 *
 * An SSE `text` event arrives per token — a long answer is hundreds of them in
 * a few seconds. Pushing each one straight into React state re-renders the
 * whole transcript per token (measured as visible frame drops on long answers
 * during the OPT-0064 review). Buffering here and flushing once per animation
 * frame keeps the UI at the display's refresh rate no matter how fast the
 * model streams.
 *
 * Kept as a pure helper so the timing can be unit-tested with a fake
 * scheduler; the hook wires it to `requestAnimationFrame`.
 */

export type Scheduler = (cb: () => void) => void

export interface DeltaBuffer {
  /** Queue a delta; a flush is scheduled if none is pending. */
  push: (delta: string) => void
  /** Deliver whatever is buffered right now, synchronously. Safe to call when empty. */
  flush: () => void
}

const rafScheduler: Scheduler = (cb) => {
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => cb())
  else setTimeout(cb, 16)
}

export function createDeltaBuffer(
  onFlush: (text: string) => void,
  schedule: Scheduler = rafScheduler,
): DeltaBuffer {
  let pending = ""
  let scheduled = false

  const flush = () => {
    scheduled = false
    if (!pending) return
    const text = pending
    pending = ""
    onFlush(text)
  }

  const push = (delta: string) => {
    if (!delta) return
    pending += delta
    if (scheduled) return
    scheduled = true
    schedule(flush)
  }

  return { push, flush }
}
