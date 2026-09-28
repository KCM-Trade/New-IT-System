import { memo } from "react"
import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"

import { cn } from "@/lib/utils"

// The analyst agent answers in Markdown (bold figures, GFM tables, `code` for
// ids / function names). Rendered here instead of shown raw.
//
// Safety posture — the text is model output, i.e. untrusted:
//   * no raw HTML: react-markdown escapes it by default and no rehype-raw is
//     added, so a `<script>` or `<img onerror>` in an answer stays text;
//   * NO images: a Markdown image pointing at an external URL is the classic
//     exfiltration channel for injected instructions (the browser fetches it
//     with data in the query string, no click needed). `img` renders as its
//     alt text only — no <img> element, so nothing is ever fetched;
//   * links render, but open in a new tab without referrer, and react-markdown's
//     default urlTransform already neutralises `javascript:` URLs.
const components: Components = {
  p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="pl-0.5">{children}</li>,
  h1: ({ children }) => <h3 className="mb-1 mt-3 text-base font-semibold first:mt-0">{children}</h3>,
  h2: ({ children }) => <h3 className="mb-1 mt-3 text-base font-semibold first:mt-0">{children}</h3>,
  h3: ({ children }) => <h4 className="mb-1 mt-3 text-sm font-semibold first:mt-0">{children}</h4>,
  h4: ({ children }) => <h4 className="mb-1 mt-3 text-sm font-semibold first:mt-0">{children}</h4>,
  img: ({ alt }) => <span className="text-muted-foreground">{alt ? `[${alt}]` : ""}</span>,
  hr: () => <hr className="my-3 border-border" />,
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-border pl-3 text-muted-foreground">{children}</blockquote>
  ),
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-primary underline underline-offset-2">
      {children}
    </a>
  ),
  code: ({ className, children }) => {
    // Fenced blocks carry a language-* class (or contain a newline); inline
    // code does not. Blocks are styled by the enclosing <pre>.
    const block = /language-/.test(className ?? "") || String(children).includes("\n")
    return block ? (
      <code className={cn("font-mono text-xs", className)}>{children}</code>
    ) : (
      <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">{children}</code>
    )
  },
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded-md bg-muted p-3 text-xs leading-relaxed">{children}</pre>
  ),
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto rounded-md border">
      <table className="w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-muted/60">{children}</thead>,
  th: ({ children, style }) => (
    <th className="whitespace-nowrap border-b px-2.5 py-1.5 text-left font-medium" style={style}>
      {children}
    </th>
  ),
  td: ({ children, style }) => (
    <td className="border-b px-2.5 py-1.5 align-top tabular-nums [tr:last-child_&]:border-b-0" style={style}>
      {children}
    </td>
  ),
}

const remarkPlugins = [remarkGfm]

export const MarkdownMessage = memo(function MarkdownMessage({ text }: { text: string }) {
  return (
    <div className="min-w-0 break-words text-sm leading-relaxed">
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
})
