import { describe, expect, it } from "vitest"

import { formatHk } from "./hk-time"

describe("formatHk", () => {
  it("shifts a UTC instant to Hong Kong (+8) wall time", () => {
    // 2026-09-27T05:34:29Z is 13:34:29 in Hong Kong.
    expect(formatHk("2026-09-27T05:34:29Z")).toMatch(/2026\/9\/27 13:34:29/)
  })
  it("returns a dash for null and the raw text for garbage", () => {
    expect(formatHk(null)).toBe("—")
    expect(formatHk("not-a-date")).toBe("not-a-date")
  })
})
