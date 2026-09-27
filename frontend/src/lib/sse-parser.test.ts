import { describe, expect, it } from "vitest"

import { SseParser, parseFrameJson } from "./sse-parser"

describe("SseParser", () => {
  it("parses a complete frame", () => {
    const p = new SseParser()
    const frames = p.push('event: text\ndata: {"delta":"hi"}\n\n')
    expect(frames).toEqual([{ event: "text", data: '{"delta":"hi"}' }])
  })

  it("reassembles frames split across chunk boundaries, mid-line included", () => {
    const p = new SseParser()
    const all = [
      ...p.push("event: te"),
      ...p.push('xt\ndata: {"del'),
      ...p.push('ta":"a"}\n'),
      ...p.push("\nevent: done\n"),
      ...p.push('data: {"terminal_reason":"end_turn"}\n\n'),
    ]
    expect(all).toEqual([
      { event: "text", data: '{"delta":"a"}' },
      { event: "done", data: '{"terminal_reason":"end_turn"}' },
    ])
  })

  it("ignores comment lines such as keepalive pings", () => {
    const p = new SseParser()
    const frames = p.push(': connected\n\n: ping\n\nevent: text\ndata: {"delta":"x"}\n\n')
    expect(frames).toEqual([{ event: "text", data: '{"delta":"x"}' }])
  })

  it("joins multi-line data with newlines and defaults the event name", () => {
    const p = new SseParser()
    const frames = p.push("data: line one\ndata: line two\n\n")
    expect(frames).toEqual([{ event: "message", data: "line one\nline two" }])
  })

  it("handles CRLF line endings", () => {
    const p = new SseParser()
    const frames = p.push("event: usage\r\ndata: {}\r\n\r\n")
    expect(frames).toEqual([{ event: "usage", data: "{}" }])
  })

  it("does not leak the previous frame's event name into the next", () => {
    const p = new SseParser()
    const frames = p.push("event: tool_use\ndata: {}\n\ndata: plain\n\n")
    expect(frames.map((f) => f.event)).toEqual(["tool_use", "message"])
  })

  it("flushes a trailing frame the stream ended without terminating", () => {
    const p = new SseParser()
    expect(p.push("event: error\ndata: {\"code\":\"internal\"}")).toEqual([])
    expect(p.flush()).toEqual([{ event: "error", data: '{"code":"internal"}' }])
    // Flushing again yields nothing: state was reset.
    expect(p.flush()).toEqual([])
  })

  it("strips exactly one leading space after the colon", () => {
    const p = new SseParser()
    expect(p.push("data:  two spaces\n\n")[0].data).toBe(" two spaces")
    expect(p.push("data:nospace\n\n")[0].data).toBe("nospace")
  })
})

describe("parseFrameJson", () => {
  it("returns null on malformed JSON instead of throwing", () => {
    expect(parseFrameJson({ event: "text", data: "{not json" })).toBeNull()
    expect(parseFrameJson<{ a: number }>({ event: "text", data: '{"a":1}' })).toEqual({ a: 1 })
  })
})
