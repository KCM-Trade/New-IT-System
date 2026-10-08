import { describe, expect, it } from "vitest"

import {
  mapPendingCompare,
  mapSessionMessages,
  sessionTranscript,
  sessionModel,
  sessionTitle,
  shouldPersistSessionId,
  type AiPendingCompare,
  type AiSessionDetail,
  type AiSessionMessageRow,
} from "./ai-session"

const rows: AiSessionMessageRow[] = [
  {
    seq: 2,
    role: "assistant",
    text: "Client 123 looks fine.",
    tools: [
      {
        name: "get_client_overview",
        ok: true,
        certified: true,
        source: { service: "svc", function: "net_gain_by_ids", as_of: "2026-09-27T00:00:00Z", certified: true },
        input: { subject: { kind: "client_id", value: "123" } },
      },
      { name: "get_risk_signals", ok: false, error_code: "scope_denied" },
      // A tool the turn died inside: no verdict on the wire.
      { name: "get_trade_activity", ok: null },
    ],
    usage: { input_tokens: 1000, output_tokens: 200, cost_usd: 0.01 },
    error_code: null,
    at: "2026-09-27T01:00:01Z",
  },
  {
    seq: 1,
    role: "user",
    text: "客户 123 怎么样？",
    tools: null,
    usage: null,
    error_code: null,
    at: "2026-09-27T01:00:00Z",
  },
  {
    seq: 3,
    role: "assistant",
    text: "",
    tools: [],
    usage: null,
    error_code: "quota_exceeded",
    at: "2026-09-27T01:00:02Z",
  },
]

describe("mapSessionMessages", () => {
  const out = mapSessionMessages(rows)

  it("orders by seq and keeps roles and text", () => {
    expect(out.map((m) => m.role)).toEqual(["user", "assistant", "assistant"])
    expect(out[0].text).toBe("客户 123 怎么样？")
    expect(out[1].text).toBe("Client 123 looks fine.")
  })

  it("gives every message a stable, unique id", () => {
    expect(new Set(out.map((m) => m.id)).size).toBe(3)
  })

  it("maps stored tool rows to the live ToolCall shape", () => {
    const [okCall, denied, unfinished] = out[1].tools
    expect(okCall).toMatchObject({ name: "get_client_overview", ok: true, certified: true })
    expect(okCall.source?.function).toBe("net_gain_by_ids")
    expect(okCall.errorCode).toBeUndefined()
    expect(denied).toMatchObject({ name: "get_risk_signals", ok: false, certified: false, errorCode: "scope_denied" })
    // Never a pending spinner for a stored row: an unfinished call renders as failed.
    expect(unfinished).toMatchObject({ ok: false, certified: false, errorCode: "error" })
    // Keys are unique within the message.
    expect(new Set(out[1].tools.map((t) => t.key)).size).toBe(3)
  })

  it("a null tools column is an empty list, never undefined", () => {
    expect(out[0].tools).toEqual([])
    expect(out[2].tools).toEqual([])
  })

  it("carries usage with defaults for missing counters", () => {
    expect(out[1].usage).toEqual({
      input_tokens: 1000,
      output_tokens: 200,
      cache_read_input_tokens: 0,
      cost_usd: 0.01,
    })
    expect(out[0].usage).toBeUndefined()
  })

  it("surfaces a stored error_code as the assistant message's error", () => {
    expect(out[2].error).toEqual({ code: "quota_exceeded", message: "" })
    expect(out[1].error).toBeUndefined()
  })

  it("does not mutate the input order", () => {
    expect(rows[0].seq).toBe(2)
  })
})

describe("sessionTitle / sessionModel", () => {
  it("falls back for null or blank titles", () => {
    expect(sessionTitle({ title: null }, "Untitled")).toBe("Untitled")
    expect(sessionTitle({ title: "   " }, "Untitled")).toBe("Untitled")
    expect(sessionTitle({ title: " 客户 123 " }, "Untitled")).toBe("客户 123")
  })

  it("only returns models the composer knows", () => {
    const known = ["gpt-5.6-terra", "gpt-5.6-sol"] as const
    expect(sessionModel("gpt-5.6-sol", known)).toBe("gpt-5.6-sol")
    expect(sessionModel("gpt-5.6-luna", known)).toBeNull()
    expect(sessionModel(null, known)).toBeNull()
  })
})

describe("shouldPersistSessionId", () => {
  it("never writes null from the change effect (mount would wipe the id resume needs)", () => {
    expect(shouldPersistSessionId(null)).toBe(false)
    expect(shouldPersistSessionId("")).toBe(false)
  })
  it("writes a real id", () => {
    expect(shouldPersistSessionId("b1348b47999f4d38beac9f92b5766bc4")).toBe(true)
  })
})

// ── OPT-0076: model per answer, compare alternatives, pending compare ──────

const session = {
  session_id: "s1",
  title: "t",
  model: "grok-4.7",
  turns: 2,
  created_at: "2026-10-07T01:00:00Z",
  updated_at: "2026-10-07T01:00:00Z",
}

const compareRows: AiSessionMessageRow[] = [
  { seq: 1, role: "user", text: "10 月有哪些数据？", tools: null, usage: null, error_code: null, at: "x" },
  {
    seq: 2,
    role: "assistant",
    text: "chosen answer",
    tools: [],
    usage: null,
    error_code: null,
    at: "x",
    model: "grok-4.7",
    compare: {
      compare_id: "cmp1",
      reason: "numbers",
      alternatives: [
        {
          model: "gpt-5.6-terra",
          text: "other answer",
          tools: [{ name: "get_economic_calendar", ok: true, certified: true }],
          usage: { input_tokens: 5, output_tokens: 5, cost_usd: 0.001 },
          error_code: null,
          elapsed_ms: 12000,
        },
        { model: "DeepSeek-V4-Pro", text: "", tools: null, usage: null, error_code: "incomplete", elapsed_ms: null },
      ],
    },
  },
  // Plain single-model answer stored after OPT-0076: model, no compare.
  { seq: 3, role: "user", text: "next", tools: null, usage: null, error_code: null, at: "x" },
  { seq: 4, role: "assistant", text: "plain", tools: [], usage: null, error_code: null, at: "x", model: "gpt-5.6-sol" },
  // A turn where no run was selectable.
  { seq: 5, role: "user", text: "again", tools: null, usage: null, error_code: null, at: "x" },
  {
    seq: 6,
    role: "assistant",
    text: "",
    tools: [],
    usage: null,
    error_code: "compare_failed",
    at: "x",
    compare: {
      compare_id: "cmp2",
      reason: null,
      alternatives: [
        { model: "gpt-5.6-terra", text: "", tools: [], usage: null, error_code: "internal", elapsed_ms: 10 },
        { model: "grok-4.7", text: "", tools: [], usage: null, error_code: "agent_unavailable", elapsed_ms: 20 },
      ],
    },
  },
]

describe("mapSessionMessages — model and compare", () => {
  const out = mapSessionMessages(compareRows)

  it("puts the model on assistant answers, and leaves old rows without one", () => {
    expect(out[1].model).toBe("grok-4.7")
    expect(out[3].model).toBe("gpt-5.6-sol")
    expect(out[3].compare).toBeUndefined()
    // Rows stored before OPT-0076 omit the field entirely.
    expect(mapSessionMessages(rows)[1].model).toBeUndefined()
    expect(mapSessionMessages(rows)[1].compare).toBeUndefined()
  })

  it("maps a chosen answer: the row is the answer, the alternatives are the runs", () => {
    const c = out[1].compare!
    expect(c).toMatchObject({ compareId: "cmp1", state: "selected", selectedModel: "grok-4.7", reason: "numbers" })
    expect(c.models).toEqual(["grok-4.7", "gpt-5.6-terra", "DeepSeek-V4-Pro"])
    expect(c.runs.map((r) => [r.model, r.status])).toEqual([
      ["gpt-5.6-terra", "done"],
      ["DeepSeek-V4-Pro", "failed"],
    ])
    expect(c.runs[0]).toMatchObject({ text: "other answer", elapsedMs: 12000 })
    expect(c.runs[0].tools[0]).toMatchObject({ name: "get_economic_calendar", ok: true, certified: true })
    expect(c.runs[0].usage?.cost_usd).toBe(0.001)
    expect(c.runs[1].error).toEqual({ code: "incomplete", message: "" })
    expect(out[1].text).toBe("chosen answer")
  })

  it("maps a void turn: every run is an alternative and nothing is selected", () => {
    const c = out[5].compare!
    expect(c.state).toBe("void")
    expect(c.selectedModel).toBeUndefined()
    expect(c.runs).toHaveLength(2)
    expect(out[5].error).toEqual({ code: "compare_failed", message: "" })
  })

  it("an unknown reason string reads as no reason", () => {
    const odd = mapSessionMessages([
      { ...compareRows[1], compare: { ...compareRows[1].compare!, reason: "because" } },
    ])
    expect(odd[0].compare?.reason).toBeNull()
  })
})

const pending: AiPendingCompare = {
  compare_id: "cmp9",
  state: "pending",
  question: "哪个更好？",
  models: ["gpt-5.6-terra", "grok-4.7", "DeepSeek-V4-Pro"],
  created_at: "2026-10-07T02:00:00Z",
  candidates: [
    // Deliberately out of column order: `models` decides the order.
    { model: "grok-4.7", text: "g", tools: [], usage: null, error_code: null, elapsed_ms: 9000, selectable: true },
    { model: "gpt-5.6-terra", text: "t", tools: [], usage: null, error_code: null, elapsed_ms: 8000, selectable: true },
    { model: "DeepSeek-V4-Pro", text: "", tools: [], usage: null, error_code: "incomplete", elapsed_ms: null, selectable: false },
  ],
}

describe("mapPendingCompare", () => {
  it("builds the question and a pending compare message, like a live turn", () => {
    const [q, a] = mapPendingCompare(pending)
    expect(q).toMatchObject({ role: "user", text: "哪个更好？" })
    expect(a.role).toBe("assistant")
    const c = a.compare!
    expect(c).toMatchObject({ compareId: "cmp9", state: "pending", detached: false })
    expect(c.runs.map((r) => r.model)).toEqual(pending.models)
    expect(c.selectable).toEqual(["grok-4.7", "gpt-5.6-terra"])
    expect(c.runs[2]).toMatchObject({ status: "failed", error: { code: "incomplete" } })
    expect(c.startedAt).toBe(Date.parse("2026-10-07T02:00:00Z"))
  })

  it("running: no candidates yet, every column is a placeholder and nothing is selectable", () => {
    const [, a] = mapPendingCompare({ ...pending, state: "running", candidates: [] })
    const c = a.compare!
    expect(c).toMatchObject({ state: "running", detached: true, selectable: [] })
    expect(c.runs.map((r) => r.status)).toEqual(["running", "running", "running"])
  })
})

describe("sessionTranscript", () => {
  const detail = (pc?: AiPendingCompare | null): AiSessionDetail => ({
    session,
    messages: compareRows.slice(0, 2),
    ...(pc === undefined ? {} : { pending_compare: pc }),
  })

  it("is just the stored messages when the field is omitted or null", () => {
    expect(sessionTranscript(detail())).toHaveLength(2)
    expect(sessionTranscript(detail(null))).toHaveLength(2)
  })

  it("appends an open compare after the stored messages", () => {
    const out = sessionTranscript(detail(pending))
    expect(out).toHaveLength(4)
    expect(out[3].compare?.state).toBe("pending")
  })

  it("keeps the on-screen runs while the server is still finishing that same compare", () => {
    const [q, a] = mapPendingCompare({ ...pending, state: "running", candidates: [] })
    const live = {
      ...a,
      stopped: true,
      compare: { ...a.compare!, detached: false, runs: a.compare!.runs.map((r) => ({ ...r, text: "partial" })) },
    }
    const out = sessionTranscript(detail({ ...pending, state: "running", candidates: [] }), [q, live])
    expect(out).toHaveLength(4)
    expect(out[3].compare?.runs[0].text).toBe("partial")
    expect(out[3].compare?.detached).toBe(true)
    // Once it is pending, the server's candidates replace the snapshot.
    const settled = sessionTranscript(detail(pending), [q, live])
    expect(settled[3].compare?.runs[0].text).toBe("t")
    // A different compare id on screen is never kept.
    const other = sessionTranscript(detail({ ...pending, compare_id: "zzz", state: "running", candidates: [] }), [q, live])
    expect(other[3].compare?.runs[0].text).toBe("")
  })
})

describe("mapSessionMessages — web search rows (OPT-0078)", () => {
  const web = { service: "web", function: "search_web", as_of: null, certified: false }
  const out = mapSessionMessages([
    {
      seq: 1,
      role: "assistant",
      text: "see [Fed](https://www.federalreserve.gov/x)",
      tools: [
        {
          name: "search_web",
          ok: true,
          certified: false,
          source: web,
          input: { query: "fomc decision" },
          call_id: "c1",
          queries: ["fomc decision october"],
          citations: [
            { title: "Fed", url: "https://www.federalreserve.gov/x" },
            { title: "bad", url: "javascript:alert(1)" },
          ],
        },
        { name: "get_client_overview", ok: true, certified: true, source: null },
      ],
      usage: null,
      error_code: null,
      at: "2026-10-08T00:00:00Z",
    },
  ])

  it("a reloaded search keeps its queries and sources, minus non-http(s) URLs", () => {
    const [search, other] = out[0].tools
    expect(search.callId).toBe("c1")
    expect(search.queries).toEqual(["fomc decision october"])
    expect(search.citations).toEqual([{ title: "Fed", url: "https://www.federalreserve.gov/x" }])
    expect(search.source?.service).toBe("web")
    expect("citations" in other).toBe(false)
    expect("queries" in other).toBe(false)
    expect("callId" in other).toBe(false)
  })
})
