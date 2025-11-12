import { useEffect, useState, useCallback } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Calendar } from "@/components/ui/calendar"
import { Calendar as CalendarIcon, ArrowUp, ArrowDown } from "lucide-react"
import type { DateRange } from "react-day-picker"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

// Swap Free Control page component
export default function SwapFreeControlPage() {
  // fresh grad: mobile detection to adjust calendar months & stacking
  const [isMobile, setIsMobile] = useState(false)
  // fresh grad: zipcode distribution rows from backend API
  type DistRow = { zipcode: string; client_count: number; client_ids?: number[] }
  const [distRows, setDistRows] = useState<DistRow[]>([])
  const [distLoading, setDistLoading] = useState(false)
  const [distError, setDistError] = useState<string | null>(null)
  // fresh grad: zipcode change logs state
  type ChangeLogRow = {
    client_id: number
    zipcode_before: string
    zipcode_after: string
    change_reason: string
    change_time: string
  }
  const [logRows, setLogRows] = useState<ChangeLogRow[]>([])
  const [logsLoading, setLogsLoading] = useState(false)
  const [logsError, setLogsError] = useState<string | null>(null)
  // fresh grad: exclusions list state
  type ExclusionRow = {
    id: number
    client_id: number
    reason_code: string
    // fresh grad: manual note from UI for MANUAL entries
    note: string | null
    added_by: string
    added_at: string
    expires_at: string | null
    is_active: boolean
  }
  const [exRows, setExRows] = useState<ExclusionRow[]>([])
  const [exLoading, setExLoading] = useState(false)
  const [exError, setExError] = useState<string | null>(null)
  // fresh grad: exclusions filter/sort
  type ReasonKey = "ALL" | "PERM_LOSS" | "MANUAL" | "OTHER"
  const [exReasonFilter, setExReasonFilter] = useState<ReasonKey>("MANUAL")
  const [exAddedAtSort, setExAddedAtSort] = useState<"desc" | "asc">("desc")
  // fresh grad: manual add form state
  const [exClientIdInput, setExClientIdInput] = useState("")
  const [exNoteInput, setExNoteInput] = useState("")
  const [exAddLoading, setExAddLoading] = useState(false)
  const [exAddError, setExAddError] = useState<string | null>(null)
  const [exAddSuccess, setExAddSuccess] = useState<string | null>(null)
  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < 640) // sm breakpoint
    onResize()
    window.addEventListener("resize", onResize)
    return () => window.removeEventListener("resize", onResize)
  }, [])

  // fresh grad: Zipcode logs date range (Popover + Calendar, similar to Profit.tsx)
  const [range, setRange] = useState<DateRange | undefined>(undefined)
  const rangeLabel = (() => {
    if (!range?.from || !range?.to) return "Select date range"
    const opts: Intl.DateTimeFormatOptions = { month: "short", day: "2-digit", year: "numeric" }
    return `${range.from.toLocaleDateString("en-US", opts)} - ${range.to.toLocaleDateString("en-US", opts)}`
  })()

  // fresh grad: fetch zipcode distribution on mount
  useEffect(() => {
    ;(async () => {
      setDistLoading(true)
      setDistError(null)
      try {
        // Backend is expected to provide this API; on failure we gracefully fallback to empty table
        const res = await fetch("/api/v1/zipcode/distribution")
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const payload = await res.json()
        const data: DistRow[] = Array.isArray(payload) ? payload : (payload?.data ?? [])
        data.sort((a, b) => (b.client_count ?? 0) - (a.client_count ?? 0))
        setDistRows(data)
      } catch (e: any) {
        setDistRows([])
        setDistError(e?.message ?? "Failed to load distribution")
      } finally {
        setDistLoading(false)
      }
    })()
  }, [])
  
  // fresh grad: helper to format Date -> "YYYY-MM-DD HH:MM:SS" in UTC (backend uses UTC+0)
  const formatDateTime = (d: Date, endOfDay: boolean) => {
    const copy = new Date(d)
    if (endOfDay) {
      copy.setHours(23, 59, 59, 999)
    } else {
      copy.setHours(0, 0, 0, 0)
    }
    const iso = copy.toISOString()
    return iso.replace("T", " ").slice(0, 19)
  }

  // fresh grad: format ISO timestamptz to UTC+8 string "YYYY-MM-DD HH:mm:ss"
  const formatIsoToUtc8 = (iso: string | null | undefined) => {
    if (!iso) return ""
    try {
      // use fixed timezone Asia/Shanghai for UTC+8; sv-SE gives ISO-like format
      return new Date(iso).toLocaleString("sv-SE", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      }).replace(",", "")
    } catch {
      return iso
    }
  }

  // fresh grad: load zipcode change logs with optional time window, limit 100
  const loadLogs = async (start?: string, end?: string) => {
    setLogsLoading(true)
    setLogsError(null)
    try {
      const params = new URLSearchParams()
      params.set("page", "1")
      params.set("page_size", "100")
      if (start) params.set("start", start)
      if (end) params.set("end", end)
      const res = await fetch(`/api/v1/zipcode/changes?${params.toString()}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const payload = await res.json()
      const data: ChangeLogRow[] = Array.isArray(payload) ? payload : (payload?.data ?? [])
      setLogRows(data)
    } catch (e: any) {
      setLogRows([])
      setLogsError(e?.message ?? "Failed to load change logs")
    } finally {
      setLogsLoading(false)
    }
  }

  // fresh grad: on mount, load default logs (backend defaults to last 25h)
  useEffect(() => {
    loadLogs()
  }, [])

  // fresh grad: apply date range for logs
  const onApplyLogs = () => {
    if (range?.from && range?.to) {
      const start = formatDateTime(range.from, false)
      const end = formatDateTime(range.to, true)
      loadLogs(start, end)
    } else {
      loadLogs()
    }
  }

  // fresh grad: load exclusions (active only)
  // fresh grad: fetch exclusions (active only) so we can reuse after manual add
  const loadExclusions = useCallback(async () => {
    setExLoading(true)
    setExError(null)
    try {
      const res = await fetch(`/api/v1/zipcode/exclusions?is_active=true`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const payload = await res.json()
      const data: ExclusionRow[] = Array.isArray(payload) ? payload : (payload?.data ?? [])
      setExRows(data)
    } catch (e: any) {
      setExRows([])
      setExError(e?.message ?? "Failed to load exclusions")
    } finally {
      setExLoading(false)
    }
  }, [])

  useEffect(() => {
    loadExclusions()
  }, [loadExclusions])

  // fresh grad: manual add handler (validates input then POST to backend)
  const onAddExclusion = useCallback(async () => {
    setExAddError(null)
    setExAddSuccess(null)

    const clientIdRaw = exClientIdInput.trim()
    if (!clientIdRaw) {
      setExAddError("Client ID is required")
      return
    }
    const clientId = Number(clientIdRaw)
    if (!Number.isInteger(clientId) || clientId <= 0) {
      setExAddError("Client ID must be a positive integer")
      return
    }

    const note = exNoteInput.trim()
    if (!note) {
      setExAddError("Reason is required")
      return
    }

    setExAddLoading(true)
    try {
      const res = await fetch(`/api/v1/zipcode/exclusions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: clientId, note }),
      })
      if (!res.ok) {
        const payload = await res.json().catch(() => null)
        const message =
          typeof payload?.detail === "string"
            ? payload.detail
            : `HTTP ${res.status}`
        setExAddError(message)
        return
      }
      await loadExclusions()
      setExClientIdInput("")
      setExNoteInput("")
      setExAddSuccess("Client added to exclusion list")
    } catch (e: any) {
      setExAddError(e?.message ?? "Failed to add exclusion")
    } finally {
      setExAddLoading(false)
    }
  }, [exClientIdInput, exNoteInput, loadExclusions])
  
  // fresh grad: derived exclusions view (filter by reason, sort by added_at)
  const displayedExRows = (() => {
    const filtered =
      exReasonFilter === "ALL"
        ? exRows
        : exRows.filter((r) => r.reason_code === exReasonFilter)
    const sorted = [...filtered].sort((a, b) => {
      const ta = new Date(a.added_at).getTime()
      const tb = new Date(b.added_at).getTime()
      return exAddedAtSort === "desc" ? tb - ta : ta - tb
    })
    return sorted
  })()
  
  return (
    <div className="px-3 py-4 sm:px-4 lg:px-6">
      {/* Layout: 2x2 grid on desktop, stacked on mobile */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:gap-6">
        {/* Zipcode Distribution (borderless, blended with background) */}
        <div className="h-[600px] rounded-lg bg-transparent shadow-none border-0 overflow-hidden">
          <div className="px-4 pt-4">
            <div className="text-xl font-semibold">Current Zipcode Distribution</div>
            <div className="text-sm text-muted-foreground">
              Count of enabled clients by zipcode
            </div>
          </div>
          <div className="px-4 pt-2">
            {/* Table area */}
            <div className="min-h-0 mt-2 h-[520px] overflow-auto rounded-md">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[160px]">Zipcode</TableHead>
                    <TableHead className="w-[160px]">Client count</TableHead>
                    <TableHead>
                      {distRows.some((r) => Array.isArray(r.client_ids) && r.client_ids.length > 0)
                        ? "Client IDs (<10)"
                        : "Notes"
                      }
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {distLoading ? (
                    <TableRow>
                      <TableCell colSpan={3} className="text-center text-muted-foreground py-8">
                        Loading...
                      </TableCell>
                    </TableRow>
                  ) : distError ? (
                    <TableRow>
                      <TableCell colSpan={3} className="text-center text-destructive py-8">
                        {distError}
                      </TableCell>
                    </TableRow>
                  ) : distRows.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={3} className="text-center text-muted-foreground py-8">
                        No data
                      </TableCell>
                    </TableRow>
                  ) : (
                    distRows.map((r) => {
                      const ids = Array.isArray(r.client_ids) ? r.client_ids : []
                      return (
                        <TableRow key={r.zipcode} className="odd:bg-muted/50">
                          <TableCell>{r.zipcode}</TableCell>
                          <TableCell>{r.client_count}</TableCell>
                          <TableCell className="text-sm">
                            {ids.length > 0 ? ids.join(", ") : ""}
                          </TableCell>
                        </TableRow>
                      )
                    })
                  )}
                </TableBody>
              </Table>
            </div>
          </div>
        </div>

        {/* Zipcode Change Logs */}
        <Card className="h-[600px] flex flex-col overflow-hidden">
          <CardHeader>
            <CardTitle>Zipcode Change Logs</CardTitle>
            <CardDescription>
              Only display 100 records of zipcode changes (for more details, plz contact Kieran)
            </CardDescription>
          </CardHeader>
          <CardContent className="flex-1 flex flex-col min-h-0 overflow-hidden">
            {/* Controls (date range via Popover + Calendar; stack on mobile) */}
            <div className="grid grid-cols-1 sm:grid-cols-12 items-end gap-3">
              <div className="sm:col-span-10">
                <label className="mb-1 block text-sm font-medium">Date range</label>
                <Popover>
                  <PopoverTrigger asChild>
                    <Button variant="outline" className="w-full justify-start gap-2 font-normal">
                      <CalendarIcon className="h-4 w-4" />
                      <span className="truncate">{rangeLabel}</span>
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto p-0" align="start">
                    <Calendar
                      mode="range"
                      selected={range}
                      onSelect={setRange}
                      numberOfMonths={isMobile ? 1 : 2}
                      initialFocus
                    />
                  </PopoverContent>
                </Popover>
              </div>
              <div className="sm:col-span-2">
                <Button className="w-full" onClick={onApplyLogs}>Apply</Button>
              </div>
            </div>
            <Separator className="my-4" />
            {/* Table area with zebra stripes */}
            <div className="min-h-0 flex-1 overflow-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[120px]">Client ID</TableHead>
                    <TableHead>Zipcode Before</TableHead>
                    <TableHead>Zipcode After</TableHead>
                    <TableHead>Reason</TableHead>
                    <TableHead className="w-[180px]">Change Time</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {logsLoading ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-center text-muted-foreground py-8">
                        Loading...
                      </TableCell>
                    </TableRow>
                  ) : logsError ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-center text-destructive py-8">
                        {logsError}
                      </TableCell>
                    </TableRow>
                  ) : logRows.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-center text-muted-foreground py-8">
                        No data
                      </TableCell>
                    </TableRow>
                  ) : (
                    logRows.map((r, idx) => (
                      <TableRow key={`${r.client_id}-${r.change_time}-${idx}`} className="odd:bg-muted/50">
                        <TableCell>{r.client_id}</TableCell>
                        <TableCell>{r.zipcode_before}</TableCell>
                        <TableCell>{r.zipcode_after}</TableCell>
                        <TableCell>{r.change_reason}</TableCell>
                        <TableCell>{formatIsoToUtc8(r.change_time)}</TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>

        {/* Exclude Group */}
        <Card className="h-[600px] flex flex-col overflow-hidden">
          <CardHeader>
            <CardTitle>Exclude Group</CardTitle>
            <CardDescription>
              PERM_LOSS means cumulative net loss exceeds 5000 USD; clients remain swap-free (zipcode=90)
            </CardDescription>
          </CardHeader>
          <CardContent className="flex-1 flex flex-col min-h-0 overflow-hidden">
            {/* Controls (Client ID, Reason, Add) - stack on mobile */}
            <div className="grid grid-cols-1 sm:grid-cols-12 items-end gap-3">
              <div className="sm:col-span-4">
                <label className="mb-1 block text-sm font-medium">Client ID</label>
                <Input
                  placeholder="e.g. 100001"
                  value={exClientIdInput}
                  onChange={(e) => setExClientIdInput(e.target.value)}
                  disabled={exAddLoading}
                  inputMode="numeric"
                />
              </div>
              <div className="sm:col-span-6">
                <label className="mb-1 block text-sm font-medium">Reason</label>
                <Input
                  placeholder="Add context for MANUAL exclusion"
                  value={exNoteInput}
                  onChange={(e) => setExNoteInput(e.target.value)}
                  disabled={exAddLoading}
                  maxLength={500}
                />
              </div>
              <div className="sm:col-span-2">
                <Button className="w-full" onClick={onAddExclusion} disabled={exAddLoading}>
                  {exAddLoading ? "Adding..." : "Add"}
                </Button>
              </div>
            </div>
            {exAddError && (
              <p className="mt-2 text-sm text-destructive">{exAddError}</p>
            )}
            {exAddSuccess && (
              <p className="mt-2 text-sm text-emerald-600">{exAddSuccess}</p>
            )}
            {/* Filters & sort for display */}
            <div className="grid grid-cols-1 sm:grid-cols-12 items-end gap-3 mt-3">
              <div className="sm:col-span-6">
                <label className="mb-1 block text-sm font-medium">Filter by Reason</label>
                <Select value={exReasonFilter} onValueChange={(v) => setExReasonFilter(v as ReasonKey)}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select reason" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ALL">ALL</SelectItem>
                    <SelectItem value="PERM_LOSS">PERM_LOSS</SelectItem>
                    <SelectItem value="MANUAL">MANUAL</SelectItem>
                    <SelectItem value="OTHER">OTHER</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="sm:col-span-6">
                <label className="mb-1 block text-sm font-medium">Sort by Added At</label>
                <Button
                  variant="outline"
                  className="w-full justify-between"
                  onClick={() => setExAddedAtSort((p) => (p === "desc" ? "asc" : "desc"))}
                >
                  {exAddedAtSort === "desc" ? "Newest first" : "Oldest first"}
                  {exAddedAtSort === "desc" ? <ArrowDown className="ml-1 h-4 w-4" /> : <ArrowUp className="ml-1 h-4 w-4" />}
                </Button>
              </div>
            </div>
            <Separator className="my-4" />
            {/* Table area with zebra stripes */}
            <div className="min-h-0 flex-1 overflow-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[120px]">Client ID</TableHead>
                    <TableHead className="w-[110px]">Reason</TableHead>
                    <TableHead>Note</TableHead>
                    <TableHead className="w-[180px]">Added At</TableHead>
                    <TableHead className="w-[100px]">Active</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {exLoading ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-center text-muted-foreground py-8">
                        Loading...
                      </TableCell>
                    </TableRow>
                  ) : exError ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-center text-destructive py-8">
                        {exError}
                      </TableCell>
                    </TableRow>
                  ) : exRows.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-center text-muted-foreground py-8">
                        No data
                      </TableCell>
                    </TableRow>
                  ) : (
                    displayedExRows.map((r) => (
                      <TableRow key={r.id} className="odd:bg-muted/50">
                        <TableCell>{r.client_id}</TableCell>
                        <TableCell>{r.reason_code}</TableCell>
                        <TableCell>{r.note ?? ""}</TableCell>
                        <TableCell>{formatIsoToUtc8(r.added_at)}</TableCell>
                        <TableCell>{String(r.is_active)}</TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>

        {/* Client Change Frequency */}
        <Card className="h-[600px] flex flex-col overflow-hidden">
          <CardHeader>
            <CardTitle>Client Change Frequency</CardTitle>
            <CardDescription>
              Overview of how often clients enter/exit swap-free eligibility · in development (demo)
            </CardDescription>
          </CardHeader>
          <CardContent className="flex-1 flex flex-col min-h-0 overflow-hidden">
            {/* Table area with zebra stripes */}
            <div className="min-h-0 flex-1 overflow-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[120px]">Client ID</TableHead>
                    <TableHead className="w-[120px]">Changes</TableHead>
                    <TableHead>Window</TableHead>
                    <TableHead className="w-[180px]">Last Change</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow className="odd:bg-muted/50">
                    <TableCell>100021</TableCell>
                    <TableCell>5</TableCell>
                    <TableCell>Last 30 days</TableCell>
                    <TableCell>2025-01-08 07:02:10</TableCell>
                  </TableRow>
                  <TableRow className="odd:bg-muted/50">
                    <TableCell>100022</TableCell>
                    <TableCell>2</TableCell>
                    <TableCell>Last 30 days</TableCell>
                    <TableCell>2025-01-07 16:23:44</TableCell>
                  </TableRow>
                  <TableRow className="odd:bg-muted/50">
                    <TableCell>100023</TableCell>
                    <TableCell>0</TableCell>
                    <TableCell>Last 30 days</TableCell>
                    <TableCell>-</TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
