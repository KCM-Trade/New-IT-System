import { describe, expect, it } from "vitest"

import { createDeltaBuffer, type Scheduler } from "./delta-buffer"

/** A scheduler that only fires when the test says so. */
function manualScheduler() {
  const queue: Array<() => void> = []
  const schedule: Scheduler = (cb) => {
    queue.push(cb)
  }
  const tick = () => {
    const cbs = queue.splice(0)
    for (const cb of cbs) cb()
  }
  return { schedule, tick, size: () => queue.length }
}

describe("createDeltaBuffer", () => {
  it("coalesces every delta pushed before the tick into one flush", () => {
    const out: string[] = []
    const s = manualScheduler()
    const buf = createDeltaBuffer((t) => out.push(t), s.schedule)

    buf.push("客户 ")
    buf.push("146530")
    buf.push(" 概况")
    expect(out).toEqual([])
    expect(s.size()).toBe(1) // one scheduled flush, not three

    s.tick()
    expect(out).toEqual(["客户 146530 概况"])
  })

  it("schedules again after a flush, so later deltas are not lost", () => {
    const out: string[] = []
    const s = manualScheduler()
    const buf = createDeltaBuffer((t) => out.push(t), s.schedule)

    buf.push("a")
    s.tick()
    buf.push("b")
    buf.push("c")
    s.tick()
    expect(out).toEqual(["a", "bc"])
  })

  it("flush() delivers synchronously and the pending tick then does nothing", () => {
    const out: string[] = []
    const s = manualScheduler()
    const buf = createDeltaBuffer((t) => out.push(t), s.schedule)

    buf.push("tail")
    buf.flush() // terminal event: nothing may be left behind
    expect(out).toEqual(["tail"])
    s.tick()
    expect(out).toEqual(["tail"]) // no empty flush
  })

  it("ignores empty deltas and never flushes empty text", () => {
    const out: string[] = []
    const s = manualScheduler()
    const buf = createDeltaBuffer((t) => out.push(t), s.schedule)

    buf.push("")
    buf.flush()
    s.tick()
    expect(out).toEqual([])
    expect(s.size()).toBe(0)
  })
})
