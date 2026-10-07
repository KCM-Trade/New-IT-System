import { describe, expect, it } from "vitest"

import {
  appendRunText,
  applyCompareResult,
  applyCompareRunEvent,
  applyRunEvent,
  compareIsUntouched,
  compareModelsValid,
  defaultCompareModels,
  isOpenCompare,
  isSelectable,
  newCompare,
  normalizeCompareModels,
  promoteModel,
  reconcileOrder,
  runOf,
  sumUsage,
  toggleCompareModel,
  uniqueToolNames,
  visibleColumnCount,
} from "./ai-compare"

const KNOWN = ["gpt-5.6-terra", "gpt-5.6-sol", "gpt-6.1-sol", "grok-4.7", "DeepSeek-V4-Pro"]

describe("runOf", () => {
  it("returns the run of a compare event", () => {
    expect(runOf({ run: "grok-4.7", delta: "x" })).toBe("grok-4.7")
  })

  it("is null when `run` is missing — the single-model path", () => {
    expect(runOf({ delta: "x" })).toBeNull()
    expect(runOf({ terminal_reason: "compare_pending", num_turns: 0 })).toBeNull()
    expect(runOf(null)).toBeNull()
    expect(runOf({ run: "" })).toBeNull()
    expect(runOf({ run: 3 })).toBeNull()
  })
})

describe("run reduction", () => {
  const start = newCompare(["gpt-5.6-terra", "grok-4.7"], 1000)

  it("starts every run empty and running, in column order", () => {
    expect(start.state).toBe("running")
    expect(start.runs.map((r) => [r.model, r.status, r.text])).toEqual([
      ["gpt-5.6-terra", "running", ""],
      ["grok-4.7", "running", ""],
    ])
    expect(compareIsUntouched(start)).toBe(true)
  })

  it("routes an event to its run and leaves the other run's reference alone", () => {
    const next = applyCompareRunEvent(start, "grok-4.7", "text", { run: "grok-4.7", delta: "Hi" })
    expect(next.runs[1].text).toBe("Hi")
    expect(next.runs[0]).toBe(start.runs[0])
    expect(compareIsUntouched(next)).toBe(false)
  })

  it("ignores an unknown run and returns the same object", () => {
    expect(applyCompareRunEvent(start, "nope", "text", { delta: "x" })).toBe(start)
  })

  it("keeps a separate tool sequence per run and resolves the oldest pending call", () => {
    let c = start
    c = applyCompareRunEvent(c, "grok-4.7", "tool_use", { name: "get_economic_calendar", input: { m: 10 } })
    c = applyCompareRunEvent(c, "grok-4.7", "tool_use", { name: "get_economic_calendar" })
    c = applyCompareRunEvent(c, "gpt-5.6-terra", "tool_use", { name: "get_economic_calendar" })
    c = applyCompareRunEvent(c, "grok-4.7", "tool_done", {
      name: "get_economic_calendar",
      ok: true,
      certified: true,
      source: { service: "s", function: "f", as_of: null, certified: true },
    })
    const grok = c.runs[1].tools
    expect(grok.map((t) => t.key)).toEqual(["get_economic_calendar#0", "get_economic_calendar#1"])
    expect(grok[0]).toMatchObject({ ok: true, certified: true, input: { m: 10 } })
    expect(grok[1].ok).toBeNull()
    // The other run's identical tool is untouched and has its own key space.
    expect(c.runs[0].tools).toHaveLength(1)
    expect(c.runs[0].tools[0]).toMatchObject({ key: "get_economic_calendar#0", ok: null })
  })

  it("records a failed tool with its error code", () => {
    let run = start.runs[0]
    run = applyRunEvent(run, "tool_use", { name: "run_sql" })
    run = applyRunEvent(run, "tool_done", { name: "run_sql", ok: false, error_code: "scope_denied" })
    expect(run.tools[0]).toMatchObject({ ok: false, certified: false, errorCode: "scope_denied" })
  })

  it("usage, done and elapsed land on the run", () => {
    let run = appendRunText(start.runs[0], "answer")
    run = applyRunEvent(run, "usage", { input_tokens: 10, output_tokens: 5, cost_usd: 0.01 })
    run = applyRunEvent(run, "done", { terminal_reason: "end_turn", num_turns: 2, elapsed_ms: 12345 })
    expect(run).toMatchObject({ status: "done", elapsedMs: 12345 })
    expect(run.usage).toEqual({ input_tokens: 10, output_tokens: 5, cache_read_input_tokens: 0, cost_usd: 0.01 })
  })

  it("a run error fails only that run, and its later `done` does not un-fail it", () => {
    let c = applyCompareRunEvent(start, "grok-4.7", "error", { code: "agent_unavailable", message: "x", trace_id: "t1" })
    c = applyCompareRunEvent(c, "grok-4.7", "done", { terminal_reason: "error", elapsed_ms: 900 })
    expect(c.runs[1]).toMatchObject({ status: "failed", elapsedMs: 900 })
    expect(c.runs[1].error).toEqual({ code: "agent_unavailable", message: "x", traceId: "t1" })
    expect(c.runs[0].status).toBe("running")
  })

  it("`done` with terminal_reason error but no error event still fails the run", () => {
    const run = applyRunEvent(start.runs[0], "done", { terminal_reason: "error" })
    expect(run.status).toBe("failed")
    expect(run.error?.code).toBe("internal")
  })

  it("unknown events and empty deltas return the same run", () => {
    expect(applyRunEvent(start.runs[0], "session_state", {})).toBe(start.runs[0])
    expect(appendRunText(start.runs[0], "")).toBe(start.runs[0])
  })
})

describe("applyCompareResult", () => {
  const base = { ...newCompare(["a", "b"]), compareId: "c1" }

  it("pending: takes the server's selectable list", () => {
    const done = applyCompareRunEvent(base, "a", "done", { terminal_reason: "end_turn" })
    const c = applyCompareResult(done, { compare_id: "c1", state: "pending", selectable: ["a"] })
    expect(c.state).toBe("pending")
    expect(isOpenCompare(c)).toBe(true)
    expect(isSelectable(c, "a")).toBe(true)
    expect(isSelectable(c, "b")).toBe(false)
    // A run that never reported `done` is closed, not left spinning.
    expect(c.runs[1]).toMatchObject({ status: "failed", error: { code: "incomplete" } })
  })

  it("void: nothing is selectable and the compare is no longer open", () => {
    const c = applyCompareResult(base, { compare_id: "c1", state: "void", selectable: [] })
    expect(c.state).toBe("void")
    expect(isOpenCompare(c)).toBe(false)
    expect(isSelectable(c, "a")).toBe(false)
  })

  it("nothing is selectable while the turn is still running", () => {
    const c = { ...base, selectable: ["a"] }
    expect(isSelectable(c, "a")).toBe(false)
  })
})

describe("visibleColumnCount — one column needs 480px, gap 16px", () => {
  it("matches the thresholds the CSS container queries use", () => {
    expect(visibleColumnCount(975, 3)).toBe(1)
    expect(visibleColumnCount(976, 3)).toBe(2)
    expect(visibleColumnCount(1471, 3)).toBe(2)
    expect(visibleColumnCount(1472, 3)).toBe(3)
  })

  it("never exceeds the number of answers and never drops below one", () => {
    expect(visibleColumnCount(1857, 2)).toBe(2)
    expect(visibleColumnCount(320, 2)).toBe(1)
    expect(visibleColumnCount(2000, 0)).toBe(0)
  })

  it("reproduces the approved width matrix", () => {
    // [content width, k for 2 answers, k for 3 answers]
    const matrix = [
      [929, 1, 1],
      [1217, 2, 2],
      [1089, 2, 2],
      [1377, 2, 2],
      [1569, 2, 3],
      [1857, 2, 3],
    ]
    for (const [a, two, three] of matrix) {
      expect(visibleColumnCount(a, 2)).toBe(two)
      expect(visibleColumnCount(a, 3)).toBe(three)
    }
  })
})

describe("model strip display order", () => {
  it("a hidden model moves to the front; the least recently chosen falls out of the first k", () => {
    const order = promoteModel(["a", "b", "c"], "c")
    expect(order).toEqual(["c", "a", "b"])
    expect(order.slice(0, 2)).toEqual(["c", "a"]) // k = 2: b is pushed out
    expect(order.slice(0, 1)).toEqual(["c"]) // k = 1: plain tabs
    // Picking b next pushes out a — the one chosen longest ago.
    expect(promoteModel(order, "b").slice(0, 2)).toEqual(["b", "c"])
  })

  it("ignores a model that is not in the order", () => {
    expect(promoteModel(["a", "b"], "z")).toEqual(["a", "b"])
  })

  it("reconcileOrder drops unknown entries and appends new models", () => {
    expect(reconcileOrder(["c", "x", "a"], ["a", "b", "c"])).toEqual(["c", "a", "b"])
  })
})

describe("uniqueToolNames", () => {
  const tool = (name: string) => ({ key: name, name, ok: true, certified: true, source: null })

  it("flags a tool only one model called", () => {
    const out = uniqueToolNames([
      { model: "a", tools: [tool("get_economic_calendar")] },
      { model: "b", tools: [tool("get_economic_calendar"), tool("load_skill"), tool("load_skill")] },
    ])
    expect(out).toEqual({ a: [], b: ["load_skill"] })
  })

  it("flags nothing with a single run", () => {
    expect(uniqueToolNames([{ model: "a", tools: [tool("x")] }])).toEqual({ a: [] })
  })
})

describe("which models to compare", () => {
  it("defaults to gpt-5.6-terra plus the other model used last", () => {
    expect(defaultCompareModels("DeepSeek-V4-Pro", KNOWN)).toEqual(["gpt-5.6-terra", "DeepSeek-V4-Pro"])
  })

  it("falls back to grok-4.7 with no history, an unknown model, or terra itself", () => {
    expect(defaultCompareModels(null, KNOWN)).toEqual(["gpt-5.6-terra", "grok-4.7"])
    expect(defaultCompareModels("retired-model", KNOWN)).toEqual(["gpt-5.6-terra", "grok-4.7"])
    expect(defaultCompareModels("gpt-5.6-terra", KNOWN)).toEqual(["gpt-5.6-terra", "grok-4.7"])
  })

  it("normalizes a stored set: known, distinct, canonical order, at most three", () => {
    expect(normalizeCompareModels(["grok-4.7", "gpt-5.6-terra", "grok-4.7"], KNOWN)).toEqual([
      "gpt-5.6-terra",
      "grok-4.7",
    ])
    expect(normalizeCompareModels(KNOWN, KNOWN)).toHaveLength(3)
    expect(normalizeCompareModels(["grok-4.7", "bogus", 7], KNOWN)).toEqual(["gpt-5.6-terra", "grok-4.7"])
    expect(normalizeCompareModels("garbage", KNOWN)).toEqual(["gpt-5.6-terra", "grok-4.7"])
    expect(normalizeCompareModels([], KNOWN, "gpt-6.1-sol")).toEqual(["gpt-5.6-terra", "gpt-6.1-sol"])
  })

  it("every default / normalized set is a valid 2–3 distinct request", () => {
    expect(compareModelsValid(defaultCompareModels(null, KNOWN))).toBe(true)
    expect(compareModelsValid(normalizeCompareModels(KNOWN, KNOWN))).toBe(true)
    expect(compareModelsValid(["a"])).toBe(false)
    expect(compareModelsValid(["a", "a"])).toBe(false)
    expect(compareModelsValid(["a", "b", "c", "d"])).toBe(false)
  })

  it("toggle: adds in canonical order, removes, and refuses a fourth", () => {
    expect(toggleCompareModel(["grok-4.7"], "gpt-5.6-terra", KNOWN)).toEqual(["gpt-5.6-terra", "grok-4.7"])
    expect(toggleCompareModel(["gpt-5.6-terra", "grok-4.7"], "grok-4.7", KNOWN)).toEqual(["gpt-5.6-terra"])
    const three = ["gpt-5.6-terra", "gpt-5.6-sol", "grok-4.7"]
    expect(toggleCompareModel(three, "DeepSeek-V4-Pro", KNOWN)).toEqual(three)
  })
})

describe("sumUsage", () => {
  it("adds tokens and cost across runs, skipping runs without usage", () => {
    expect(
      sumUsage([
        { input_tokens: 100, output_tokens: 10, cache_read_input_tokens: 1, cost_usd: 0.01 },
        undefined,
        { input_tokens: 200, output_tokens: 20, cache_read_input_tokens: 2, cost_usd: 0.02 },
      ]),
    ).toEqual({ input_tokens: 300, output_tokens: 30, cache_read_input_tokens: 3, cost_usd: 0.03 })
  })

  it("is null with no usage at all, and keeps a null cost null", () => {
    expect(sumUsage([undefined, null])).toBeNull()
    expect(
      sumUsage([{ input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cost_usd: null }])?.cost_usd,
    ).toBeNull()
  })
})
