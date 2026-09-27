/**
 * Render a backend UTC ISO8601 timestamp as Hong Kong wall time.
 *
 * The backend normalises every time to UTC (`...Z`) and the desk reads
 * Asia/Hong_Kong (CLAUDE.md "Timezones"). Several pages had grown their own
 * copy of this one-liner; new code should import this one.
 */
export function formatHk(iso: string | null | undefined): string {
  if (!iso) return "—"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString("zh-CN", { timeZone: "Asia/Hong_Kong", hour12: false })
}
