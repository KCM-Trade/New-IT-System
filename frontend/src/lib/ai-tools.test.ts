import { describe, expect, it } from "vitest"

import type { ToolCall } from "@/hooks/useAiTurn"
import {
  appendToolUse,
  parseLinkAllowlist,
  sessionLinkAllowlists,
  normalizeLinkUrl,
  resolveToolDone,
  safeHttpUrl,
  sanitizeCitations,
} from "./ai-tools"

const WEB = { service: "web", function: "search_web", as_of: null, certified: false }

function twoPending(withIds: boolean): ToolCall[] {
  let tools: ToolCall[] = []
  tools = appendToolUse(tools, { name: "search_web", input: { query: "A" }, ...(withIds ? { call_id: "c1" } : {}) }, "search_web#0")
  tools = appendToolUse(tools, { name: "search_web", input: { query: "B" }, ...(withIds ? { call_id: "c2" } : {}) }, "search_web#1")
  return tools
}

describe("resolveToolDone", () => {
  it("pairs by call_id when two concurrent searches finish in reverse order", () => {
    let tools = twoPending(true)
    tools = resolveToolDone(
      tools,
      { name: "search_web", ok: true, source: WEB, call_id: "c2", citations: [{ title: "B", url: "https://b.example/x" }], queries: ["B q"] },
      "k",
    )
    expect(tools[0].ok).toBeNull()
    expect(tools[1]).toMatchObject({ key: "search_web#1", ok: true, input: { query: "B" }, queries: ["B q"] })
    tools = resolveToolDone(
      tools,
      { name: "search_web", ok: true, source: WEB, call_id: "c1", citations: [{ title: "A", url: "https://a.example/x" }] },
      "k",
    )
    expect(tools[0]).toMatchObject({ key: "search_web#0", input: { query: "A" }, citations: [{ title: "A", url: "https://a.example/x" }] })
    expect(tools[1].citations).toEqual([{ title: "B", url: "https://b.example/x" }])
    expect(tools).toHaveLength(2)
  })

  it("falls back to the oldest pending call of that name when call_id is missing", () => {
    let tools = twoPending(false)
    tools = resolveToolDone(tools, { name: "search_web", ok: true, source: WEB }, "k")
    expect(tools[0].ok).toBe(true)
    expect(tools[1].ok).toBeNull()
    // tool_use had an id but tool_done does not (mixed versions): still resolves.
    let mixed = twoPending(true)
    mixed = resolveToolDone(mixed, { name: "search_web", ok: false, error_code: "web_search_timeout" }, "k")
    expect(mixed[0]).toMatchObject({ ok: false, errorCode: "web_search_timeout", callId: "c1" })
  })

  it("leaves the optional fields absent when they are empty or missing", () => {
    let tools = appendToolUse([], { name: "search_web", call_id: "c1" }, "search_web#0")
    tools = resolveToolDone(tools, { name: "search_web", ok: true, source: WEB, call_id: "c1", citations: [], queries: [] }, "k")
    expect("citations" in tools[0]).toBe(false)
    expect("queries" in tools[0]).toBe(false)
    const other = resolveToolDone([], { name: "run_sql", ok: true }, "run_sql#0")
    expect(other[0]).toMatchObject({ key: "run_sql#0", ok: true })
    expect("citations" in other[0]).toBe(false)
    expect("callId" in other[0]).toBe(false)
  })
})

describe("citation URLs", () => {
  it("accepts only http(s) within the length cap", () => {
    expect(safeHttpUrl(" https://a.example/x ")).toBe("https://a.example/x")
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull()
    expect(safeHttpUrl("data:text/html,x")).toBeNull()
    expect(safeHttpUrl("//a.example/x")).toBeNull()
    expect(safeHttpUrl("https://a.example/" + "x".repeat(600))).toBeNull()
    expect(sanitizeCitations([{ title: "t", url: "javascript:alert(1)" }, { title: "ok", url: "https://a.example" }, null])).toEqual([
      { title: "ok", url: "https://a.example" },
    ])
  })

  it("compares links ignoring fragment and encoding, but not the query string", () => {
    expect(normalizeLinkUrl("https://A.example/p#frag")).toBe(normalizeLinkUrl("https://a.example/p"))
    expect(normalizeLinkUrl("https://a.example/p?d=1")).not.toBe(normalizeLinkUrl("https://a.example/p"))
  })
})

describe("session link allow-list", () => {
  const search = (url: string, ok: boolean | null = true): ToolCall => ({
    key: `search_web#${url}`,
    name: "search_web",
    ok,
    certified: false,
    source: WEB,
    ...(ok ? { citations: [{ title: "t", url }] } : {}),
  })
  const sql: ToolCall = { key: "run_sql#0", name: "run_sql", ok: true, certified: false, source: null }
  const A = "https://a.example/x"
  const B = "https://b.example/y"

  it("is undefined until the conversation searches, then restricts every later message", () => {
    const lists = sessionLinkAllowlists([
      { tools: [] }, // user
      { tools: [sql] }, // before the first search: unchanged
      { tools: [] },
      { tools: [search(A)] },
      { tools: [] },
      { tools: [sql] }, // follow-up that searched nothing
    ])
    expect(lists.slice(0, 3)).toEqual([undefined, undefined, undefined])
    expect([...parseLinkAllowlist(lists[3])!]).toEqual([A])
    expect([...parseLinkAllowlist(lists[5])!]).toEqual([A])
    // Unchanged set → the very same string, so memoised rows do not re-render.
    expect(lists[5]).toBe(lists[3])
  })

  it("grows with later searches and never lets a message use citations from its future", () => {
    const lists = sessionLinkAllowlists([{ tools: [search(A)] }, { tools: [search(B)] }])
    expect(parseLinkAllowlist(lists[0])!.has(B)).toBe(false)
    expect([...parseLinkAllowlist(lists[1])!].sort()).toEqual([A, B])
  })

  it("is empty while the first search is still running, earlier citations only while a later one runs", () => {
    expect(parseLinkAllowlist(sessionLinkAllowlists([{ tools: twoPending(true) }])[0])?.size).toBe(0)
    const lists = sessionLinkAllowlists([{ tools: [search(A)] }, { tools: [search(B, null)] }])
    expect([...parseLinkAllowlist(lists[1])!]).toEqual([A])
  })

  it("restricts a compare turn after an earlier search, and counts searches inside compare runs", () => {
    const run = (tools: ToolCall[]) => ({ tools })
    const after = sessionLinkAllowlists([{ tools: [search(A)] }, { tools: [], compare: { runs: [run([sql]), run([])] } }])
    expect([...parseLinkAllowlist(after[1])!]).toEqual([A])
    const inside = sessionLinkAllowlists([{ tools: [], compare: { runs: [run([search(B)])] } }, { tools: [] }])
    expect([...parseLinkAllowlist(inside[1])!]).toEqual([B])
  })

  it("gives a replayed session the same lists as the live one", () => {
    // Replay rebuilds ToolCall rows from `tools_json`; keys differ, content does not.
    const live = [{ tools: [search(A)] }, { tools: [sql] }]
    const replayed = [{ tools: [{ ...search(A), key: "t0", callId: "tc_1" }] }, { tools: [{ ...sql, key: "t0" }] }]
    expect(sessionLinkAllowlists(replayed)).toEqual(sessionLinkAllowlists(live))
  })

  it("round-trips: undefined stays unrestricted, an empty key allows nothing", () => {
    expect(parseLinkAllowlist(undefined)).toBeUndefined()
    expect(parseLinkAllowlist("")?.size).toBe(0)
  })
})
