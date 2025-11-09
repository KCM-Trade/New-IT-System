import { useEffect, useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Calendar } from "@/components/ui/calendar"
import { Calendar as CalendarIcon } from "lucide-react"
import type { DateRange } from "react-day-picker"

// Swap Free Control page component
export default function SwapFreeControlPage() {
  // fresh grad: mobile detection to adjust calendar months & stacking
  const [isMobile, setIsMobile] = useState(false)
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

  return (
    <div className="px-3 py-4 sm:px-4 lg:px-6">
      {/* Layout: three cards in a row on large screens; stack on narrow screens.
         Mobile spacing reduced to maximize content area */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3 lg:gap-6">
        {/* Zipcode Change Logs */}
        <Card className="h-[600px] flex flex-col">
          <CardHeader>
            <CardTitle>Zipcode Change Logs</CardTitle>
            <CardDescription>
              Browse change logs within selected date range
            </CardDescription>
          </CardHeader>
          <CardContent className="flex-1 flex flex-col">
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
                <Button className="w-full">Apply</Button>
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
                  {/* Use odd:bg-muted/50 for zebra striping */}
                  <TableRow className="odd:bg-muted/50">
                    <TableCell>100001</TableCell>
                    <TableCell>10</TableCell>
                    <TableCell>90</TableCell>
                    <TableCell>AUTO_VOLUME</TableCell>
                    <TableCell>2025-01-08 05:52:01</TableCell>
                  </TableRow>
                  <TableRow className="odd:bg-muted/50">
                    <TableCell>100002</TableCell>
                    <TableCell>90</TableCell>
                    <TableCell>10</TableCell>
                    <TableCell>MANUAL_OVERRIDE</TableCell>
                    <TableCell>2025-01-08 06:02:17</TableCell>
                  </TableRow>
                  <TableRow className="odd:bg-muted/50">
                    <TableCell>100003</TableCell>
                    <TableCell>20</TableCell>
                    <TableCell>90</TableCell>
                    <TableCell>AUTO_LOSS</TableCell>
                    <TableCell>2025-01-08 06:10:45</TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>

        {/* Exclude Group */}
        <Card className="h-[600px] flex flex-col">
          <CardHeader>
            <CardTitle>Exclude Group</CardTitle>
            <CardDescription>
              Manage permanent/temporary exclusions that always keep zipcode=90
            </CardDescription>
          </CardHeader>
          <CardContent className="flex-1 flex flex-col">
            {/* Controls (Client ID, Reason, Add) - stack on mobile */}
            <div className="grid grid-cols-1 sm:grid-cols-12 items-end gap-3">
              <div className="sm:col-span-4">
                <label className="mb-1 block text-sm font-medium">Client ID</label>
                <Input placeholder="e.g. 100001" />
              </div>
              <div className="sm:col-span-6">
                <label className="mb-1 block text-sm font-medium">Reason</label>
                <Input placeholder="PERM_LOSS / MANUAL / OTHER" />
              </div>
              <div className="sm:col-span-2">
                <Button className="w-full">Add</Button>
              </div>
            </div>
            <Separator className="my-4" />
            {/* Table area with zebra stripes */}
            <div className="min-h-0 flex-1 overflow-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[120px]">Client ID</TableHead>
                    <TableHead>Reason</TableHead>
                    <TableHead className="w-[120px]">Added By</TableHead>
                    <TableHead className="w-[180px]">Added At</TableHead>
                    <TableHead className="w-[100px]">Active</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow className="odd:bg-muted/50">
                    <TableCell>100010</TableCell>
                    <TableCell>PERM_LOSS</TableCell>
                    <TableCell>system</TableCell>
                    <TableCell>2025-01-07 10:05:00</TableCell>
                    <TableCell>true</TableCell>
                  </TableRow>
                  <TableRow className="odd:bg-muted/50">
                    <TableCell>100011</TableCell>
                    <TableCell>MANUAL</TableCell>
                    <TableCell>ops_user</TableCell>
                    <TableCell>2025-01-07 11:12:31</TableCell>
                    <TableCell>true</TableCell>
                  </TableRow>
                  <TableRow className="odd:bg-muted/50">
                    <TableCell>100012</TableCell>
                    <TableCell>OTHER</TableCell>
                    <TableCell>system</TableCell>
                    <TableCell>2025-01-08 08:40:22</TableCell>
                    <TableCell>false</TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>

        {/* Client Change Frequency */}
        <Card className="h-[600px] flex flex-col">
          <CardHeader>
            <CardTitle>Client Change Frequency</CardTitle>
            <CardDescription>
              Overview of how often clients enter/exit swap-free eligibility
            </CardDescription>
          </CardHeader>
          <CardContent className="flex-1 flex flex-col">
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
