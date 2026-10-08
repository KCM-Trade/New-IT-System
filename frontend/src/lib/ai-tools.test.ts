import { describe, expect, it } from "vitest"

import type { ToolCall } from "@/hooks/useAiTurn"
import {
  appendToolUse,
  linkAllowlist,
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

  it("has no allow-list for a message without a web search, an empty one before results arrive", () => {
    expect(linkAllowlist([])).toBeUndefined()
    expect(linkAllowlist(appendToolUse([], { name: "run_sql" }, "k"))).toBeUndefined()
    expect(linkAllowlist(twoPending(true))?.size).toBe(0)
  })
})
