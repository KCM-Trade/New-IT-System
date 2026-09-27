import { describe, expect, it } from "vitest"

import {
  mapSessionMessages,
  sessionModel,
  sessionTitle,
  shouldPersistSessionId,
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
