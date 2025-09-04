import * as React from "react"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
  DrawerTrigger,
} from "@/components/ui/drawer"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Badge } from "@/components/ui/badge"
import { Calendar } from "@/components/ui/calendar"
import type { DateRange } from "react-day-picker"
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  Pie,
  PieChart,
  XAxis,
  YAxis,
} from "recharts"

// Page: 客户交易分析（静态原型）
// fresh grad: All data below are static mock data to show layout and style.

// 顶部指标卡静态数据
const kpis = [
  { label: "净收益 (PnL)", value: 5000, unit: "USD", trend: "+", tone: "pos" },
  { label: "盈亏因子", value: 1.6, trend: "=", tone: "neu" },
  { label: "胜率", value: 0.62, unit: "%", trend: "+", tone: "neu" },
  { label: "最大回撤", value: -25, unit: "%", trend: "-", tone: "neg" },
  { label: "Sharpe Ratio", value: 1.2, trend: "=", tone: "neu" },
]

// 资金曲线与回撤（静态）
const equityData = Array.from({ length: 24 }).map((_, i) => {
  const base = 10000
  const equity = base + i * 120 + (i % 5 === 0 ? -300 : 0)
  const peak = Math.max(...Array.from({ length: i + 1 }).map((__, j) => base + j * 120 + (j % 5 === 0 ? -300 : 0)))
  const drawdown = Math.min(0, ((equity - peak) / peak) * 100)
  return { t: `Day ${i + 1}`, equity, drawdown }
})

const equityChartConfig: ChartConfig = {
  equity: { label: "权益", color: "var(--primary)" },
  drawdown: { label: "回撤%", color: "hsl(0 80% 60%)" },
}

// 成本拆解（瀑布式条形图的近似，用堆叠柱）
const costData = [
  { label: "点差", cost: 1200 },
  { label: "佣金", cost: 800 },
  { label: "隔夜利息", cost: 300 },
  { label: "滑点", cost: 200 },
]

const costChartConfig: ChartConfig = {
  cost: { label: "成本", color: "hsl(220 80% 60%)" },
}

// 品种占比（饼图）
const symbolShare = [
  { name: "XAUUSD", value: 45, fill: "hsl(45 90% 55%)" },
  { name: "XAGUSD", value: 20, fill: "hsl(0 80% 60%)" },
  { name: "EURUSD", value: 18, fill: "hsl(210 80% 60%)" },
  { name: "GBPUSD", value: 10, fill: "hsl(280 70% 60%)" },
  { name: "US30", value: 7, fill: "hsl(150 70% 45%)" },
]

const symbolConfig: ChartConfig = {
  share: { label: "占比", color: "var(--primary)" },
}

export default function ClientTradingAnalyticsPage() {
  // --- Static filters state (for demo only) ---
  type Rule =
    | { type: "customer_ids"; ids: number[]; include: boolean }
    | { type: "customer_tags"; source: "local" | "crm"; tags: string[]; operator: "ANY" | "ALL"; include: boolean }
    | { type: "account_ids"; ids: string[]; include: boolean }
    | { type: "ib_id"; id: number; depth: "all" | "level1" | "level1_2"; include: boolean }

  // sample datasets (static)
  const sampleAccountsByCustomer: Record<number, string[]> = {
    1001: ["A-1001", "A-1002"],
    1002: ["A-2001"],
    1003: ["A-3001", "A-3002", "A-3003"],
  }
  const sampleTagsLocal = ["VIP", "HighTurnover", "NewUser"]
  const sampleTagsCRM = ["XAU-Focus", "Scalper", "Asia-Desk"]
  const sampleAccountsByTag: Record<string, string[]> = {
    VIP: ["A-1001", "A-3001"],
    HighTurnover: ["A-3002"],
    NewUser: ["A-2001"],
    "XAU-Focus": ["A-1002", "A-3003"],
    Scalper: ["A-3001", "A-3002"],
    "Asia-Desk": ["A-2001"],
  }
  const sampleSymbols = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "US30"]
  const sampleIbTree: Record<string, string[]> = {
    // key format: `${ibId}:${depth}` → accounts
    "137016:all": ["A-1001", "A-1002", "A-2001", "A-3001", "A-3002"],
    "137016:level1": ["A-1001", "A-1002"],
    "137016:level1_2": ["A-1001", "A-1002", "A-2001"],
  }

  const [rules, setRules] = React.useState<Rule[]>([])
  // removed: dedicated preview drawer state in compact mode

  // ephemeral inputs for adding rules
  const [inputCustomerId, setInputCustomerId] = React.useState("")
  const [inputAccountId, setInputAccountId] = React.useState("")
  const [inputIbId, setInputIbId] = React.useState("")
  const [ibDepth, setIbDepth] = React.useState<"all" | "level1" | "level1_2">("all")
  const [tagSource, setTagSource] = React.useState<"local" | "crm">("local")
  const [selectedLocalTags, setSelectedLocalTags] = React.useState<string[]>([])
  const [selectedCrmTags, setSelectedCrmTags] = React.useState<string[]>([])
  const [tagOperator, setTagOperator] = React.useState<"ANY" | "ALL">("ANY")

  // time filters (static)
  const [range, setRange] = React.useState<DateRange | undefined>(undefined)
  const [displayTz, setDisplayTz] = React.useState<"UTC+3" | "UTC+8">("UTC+8")
  const rangeLabel = React.useMemo(() => {
    if (!range?.from || !range?.to) return "选择日期范围"
    const opts: Intl.DateTimeFormatOptions = { month: "short", day: "2-digit", year: "numeric" }
    return `${range.from.toLocaleDateString("en-US", opts)} - ${range.to.toLocaleDateString("en-US", opts)}`
  }, [range])

  // symbols (static multi-select)
  const [selectedSymbols, setSelectedSymbols] = React.useState<string[]>([])
  const [ruleType, setRuleType] = React.useState<"customer_ids" | "customer_tags" | "account_ids" | "ib_id">("customer_ids")
  const [symbolsMode, setSymbolsMode] = React.useState<"all" | "custom">("all")
  React.useEffect(() => {
    if (symbolsMode === "all") setSelectedSymbols([])
  }, [symbolsMode])

  // derive accounts from rules (include/exclude with OR semantics)
  const derivedAccounts = React.useMemo(() => {
    const includeSet = new Set<string>()
    const excludeSet = new Set<string>()

    const addAccounts = (arr: string[] | undefined, to: Set<string>) => {
      if (!arr) return
      for (const a of arr) to.add(a)
    }

    for (const r of rules) {
      if (r.type === "customer_ids") {
        const accounts = r.ids.flatMap((cid) => sampleAccountsByCustomer[cid] ?? [])
        ;(r.include ? addAccounts : (arr: string[] | undefined) => addAccounts(arr, excludeSet))(accounts, r.include ? includeSet : excludeSet)
      } else if (r.type === "account_ids") {
        ;(r.include ? addAccounts : (arr: string[] | undefined) => addAccounts(arr, excludeSet))(r.ids, r.include ? includeSet : excludeSet)
      } else if (r.type === "customer_tags") {
        const allTags = r.tags
        if (r.operator === "ANY") {
          const accounts = allTags.flatMap((t) => sampleAccountsByTag[t] ?? [])
          ;(r.include ? addAccounts : (arr: string[] | undefined) => addAccounts(arr, excludeSet))(accounts, r.include ? includeSet : excludeSet)
        } else {
          // ALL: intersection of tag account sets
          const sets = allTags.map((t) => new Set(sampleAccountsByTag[t] ?? []))
          const inter = sets.reduce<string[]>((acc, s, idx) => {
            if (idx === 0) return Array.from(s)
            return acc.filter((x) => s.has(x))
          }, [])
          ;(r.include ? addAccounts : (arr: string[] | undefined) => addAccounts(arr, excludeSet))(inter, r.include ? includeSet : excludeSet)
        }
      } else if (r.type === "ib_id") {
        const key = `${r.id}:${r.depth}`
        const accounts = sampleIbTree[key] ?? []
        ;(r.include ? addAccounts : (arr: string[] | undefined) => addAccounts(arr, excludeSet))(accounts, r.include ? includeSet : excludeSet)
      }
    }

    // OR include then subtract exclude
    for (const ex of excludeSet) includeSet.delete(ex)
    return Array.from(includeSet)
  }, [rules])

  // helpers to add rules
  function addCustomerIdRule() {
    const id = parseInt(inputCustomerId, 10)
    if (!id || Number.isNaN(id)) return
    setRules((prev) => [...prev, { type: "customer_ids", ids: [id], include: true }])
    setInputCustomerId("")
  }
  function addAccountIdRule() {
    const v = inputAccountId.trim()
    if (!v) return
    setRules((prev) => [...prev, { type: "account_ids", ids: [v], include: true }])
    setInputAccountId("")
  }
  function addIbRule() {
    const id = parseInt(inputIbId, 10)
    if (!id || Number.isNaN(id)) return
    setRules((prev) => [...prev, { type: "ib_id", id, depth: ibDepth, include: true }])
    setInputIbId("")
  }
  function addTagRule() {
    const tags = tagSource === "local" ? selectedLocalTags : selectedCrmTags
    if (!tags.length) return
    setRules((prev) => [
      ...prev,
      { type: "customer_tags", source: tagSource, tags, operator: tagOperator, include: true },
    ])
    setSelectedLocalTags([])
    setSelectedCrmTags([])
  }
  function removeRuleAt(idx: number) {
    setRules((prev) => prev.filter((_, i) => i !== idx))
  }
  function clearRules() {
    setRules([])
  }

  function toggleSymbol(sym: string) {
    setSelectedSymbols((prev) => (prev.includes(sym) ? prev.filter((s) => s !== sym) : [...prev, sym]))
  }

  return (
    <div className="space-y-4 px-4 pb-6 lg:px-6">
      {/* 筛选卡片（与 Profit 风格一致） */}
      <Card>
        <CardHeader>
          <CardTitle className="text-2xl font-bold">筛选</CardTitle>
          
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm text-muted-foreground">选择对象：</span>
        {/* 对象（Responsive Dialog: desktop=Dialog, mobile=Drawer） */}
        <div className="block sm:hidden">
          <Drawer>
            <DrawerTrigger asChild>
              <Button variant="outline" size="sm">选择对象</Button>
            </DrawerTrigger>
            <DrawerContent>
              <DrawerHeader>
                <DrawerTitle>对象选择</DrawerTitle>
                <DrawerDescription>通过不同来源添加到对象池，确认后生效（静态演示）</DrawerDescription>
              </DrawerHeader>
              <div className="px-4 pb-4 space-y-4">
              {/* 规则类型选择 + 动态输入（紧凑） */}
              <div className="space-y-2">
                <div className="grid w-full grid-cols-[6rem_1fr_auto_auto] items-center gap-2">
                  <Label className="text-sm text-muted-foreground">类型</Label>
                  <Select value={ruleType} onValueChange={(v) => setRuleType(v as typeof ruleType)}>
                    <SelectTrigger className="w-48"><SelectValue placeholder="选择类型" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="customer_ids">客户ID</SelectItem>
                      <SelectItem value="account_ids">账户号</SelectItem>
                      <SelectItem value="ib_id">IB ID</SelectItem>
                      <SelectItem value="customer_tags">客户Tag</SelectItem>
                    </SelectContent>
                  </Select>
                  <div />
                  <div />
                </div>

                {/* customer_ids */}
                {ruleType === "customer_ids" && (
                  <div className="grid w-full grid-cols-[6rem_1fr_auto_auto] items-center gap-2">
                    <Label htmlFor="cid_drawer" className="text-sm text-muted-foreground">客户ID</Label>
                    <Input id="cid_drawer" value={inputCustomerId} onChange={(e) => setInputCustomerId(e.target.value)} placeholder="如 1001" className="w-full" />
                    <div />
                    <Button variant="secondary" onClick={addCustomerIdRule}>加入对象池</Button>
                  </div>
                )}

                {/* account_ids */}
                {ruleType === "account_ids" && (
                  <div className="grid w-full grid-cols-[6rem_1fr_auto_auto] items-center gap-2">
                    <Label htmlFor="acc_drawer" className="text-sm text-muted-foreground">账户号</Label>
                    <Input id="acc_drawer" value={inputAccountId} onChange={(e) => setInputAccountId(e.target.value)} placeholder="如 A-1001" className="w-full" />
                    <div />
                    <Button variant="secondary" onClick={addAccountIdRule}>加入对象池</Button>
                  </div>
                )}

                {/* ib_id */}
                {ruleType === "ib_id" && (
                  <div className="grid w-full grid-cols-[6rem_1fr_auto_auto] items-center gap-2">
                    <Label htmlFor="ib_drawer" className="text-sm text-muted-foreground">IB ID</Label>
                    <Input id="ib_drawer" inputMode="numeric" value={inputIbId} onChange={(e) => setInputIbId(e.target.value)} placeholder="如 137016" className="w-full" />
                    <Select value={ibDepth} onValueChange={(v) => setIbDepth(v as typeof ibDepth)}>
                      <SelectTrigger className="w-32"><SelectValue placeholder="层级" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">所有层级</SelectItem>
                        <SelectItem value="level1">仅一级</SelectItem>
                        <SelectItem value="level1_2">一级+二级</SelectItem>
                      </SelectContent>
                    </Select>
                    <Button variant="secondary" onClick={addIbRule}>加入对象池</Button>
                  </div>
                )}

                {/* customer_tags */}
                {ruleType === "customer_tags" && (
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="flex items-center gap-2">
                      <Label className="w-24">Tag来源</Label>
                      <Select value={tagSource} onValueChange={(v) => setTagSource(v as typeof tagSource)}>
                        <SelectTrigger className="w-28"><SelectValue placeholder="来源" /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="local">本地Tag</SelectItem>
                          <SelectItem value="crm">CRM Tag</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">逻辑</span>
                      <Select value={tagOperator} onValueChange={(v) => setTagOperator(v as typeof tagOperator)}>
                        <SelectTrigger className="w-28"><SelectValue placeholder="逻辑" /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="ANY">ANY(并集)</SelectItem>
                          <SelectItem value="ALL">ALL(交集)</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <Popover>
                      <PopoverTrigger asChild>
                        <Button variant="outline" size="sm">选择Tag</Button>
                      </PopoverTrigger>
                      <PopoverContent className="w-56 p-2">
                        <div className="grid gap-2">
                          {(tagSource === "local" ? sampleTagsLocal : sampleTagsCRM).map((t) => {
                            const selected = (tagSource === "local" ? selectedLocalTags : selectedCrmTags).includes(t)
                            return (
                              <label key={t} className="flex items-center gap-2">
                                <Checkbox
                                  checked={selected}
                                  onCheckedChange={(ck) => {
                                    const upd = (tagSource === "local" ? selectedLocalTags : selectedCrmTags)
                                    const setUpd = (tagSource === "local" ? setSelectedLocalTags : setSelectedCrmTags)
                                    if (ck) setUpd([...upd, t])
                                    else setUpd(upd.filter((x) => x !== t))
                                  }}
                                />
                                <span className="text-sm">{t}</span>
                              </label>
                            )
                          })}
                          <Button size="sm" onClick={addTagRule}>添加所选</Button>
                        </div>
                      </PopoverContent>
                    </Popover>
                  </div>
                )}
              </div>
              {/* 规则列表 */}
              <div>
                <div className="mb-2 text-sm font-medium">已选对象规则</div>
                <div className="flex flex-wrap gap-2">
                  {rules.map((r, idx) => (
                    <Badge key={idx} variant="secondary" className="flex items-center gap-2">
                      <span className="text-xs">
                        {r.type === "customer_ids" && `客户ID:${r.ids.join(',')}`}
                        {r.type === "account_ids" && `账户:${r.ids.join(',')}`}
                        {r.type === "customer_tags" && `${r.source} Tags:${r.tags.join(',')}(${r.operator})`}
                        {r.type === "ib_id" && `IB:${r.id}(${r.depth})`}
                      </span>
                      <button onClick={() => removeRuleAt(idx)} className="text-muted-foreground hover:text-foreground">×</button>
                    </Badge>
                  ))}
                  {rules.length === 0 && <span className="text-xs text-muted-foreground">暂无规则</span>}
                </div>
                <div className="mt-2">
                  <Button variant="ghost" size="sm" onClick={clearRules}>清空对象池</Button>
                </div>
              </div>
              {/* 账户预览 */}
              <div>
                <div className="mb-2 text-sm font-medium">命中账户（示例）{derivedAccounts.length} 个</div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
                  {derivedAccounts.length > 0 ? (
                    derivedAccounts.map((a) => (
                      <Badge key={a} variant="outline">{a}</Badge>
                    ))
                  ) : (
                    <span className="text-xs text-muted-foreground">无账户</span>
                  )}
                </div>
              </div>
              </div>
              <DrawerFooter>
                <DrawerClose asChild>
                  <Button>确认</Button>
                </DrawerClose>
                <DrawerClose asChild>
                  <Button variant="outline">取消</Button>
                </DrawerClose>
              </DrawerFooter>
            </DrawerContent>
          </Drawer>
            </div>
            <div className="hidden sm:block">
          <Dialog>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm">选择对象</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>对象选择</DialogTitle>
                <DialogDescription>通过不同来源添加到对象池，确认后生效（静态演示）</DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                {/* 规则类型选择 + 动态输入（紧凑） */}
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <Label className="w-24">类型</Label>
                    <Select value={ruleType} onValueChange={(v) => setRuleType(v as typeof ruleType)}>
                      <SelectTrigger className="w-40"><SelectValue placeholder="选择类型" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="customer_ids">客户ID</SelectItem>
                        <SelectItem value="account_ids">账户号</SelectItem>
                        <SelectItem value="ib_id">IB ID</SelectItem>
                        <SelectItem value="customer_tags">客户Tag</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {/* customer_ids */}
                  {ruleType === "customer_ids" && (
                    <div className="grid w-full grid-cols-[6rem_1fr_auto_auto] items-center gap-2">
                      <Label htmlFor="cid_dialog" className="text-sm text-muted-foreground">客户ID</Label>
                      <Input id="cid_dialog" value={inputCustomerId} onChange={(e) => setInputCustomerId(e.target.value)} placeholder="如 1001" className="w-full" />
                      <div />
                      <Button variant="secondary" onClick={addCustomerIdRule}>加入对象池</Button>
                    </div>
                  )}

                  {/* account_ids */}
                  {ruleType === "account_ids" && (
                    <div className="grid w-full grid-cols-[6rem_1fr_auto_auto] items-center gap-2">
                      <Label htmlFor="acc_dialog" className="text-sm text-muted-foreground">账户号</Label>
                      <Input id="acc_dialog" value={inputAccountId} onChange={(e) => setInputAccountId(e.target.value)} placeholder="如 A-1001" className="w-full" />
                      <div />
                      <Button variant="secondary" onClick={addAccountIdRule}>加入对象池</Button>
                    </div>
                  )}

                  {/* ib_id */}
                  {ruleType === "ib_id" && (
                    <div className="grid w-full grid-cols-[6rem_1fr_auto_auto] items-center gap-2">
                      <Label htmlFor="ib_dialog" className="text-sm text-muted-foreground">IB ID</Label>
                      <Input id="ib_dialog" inputMode="numeric" value={inputIbId} onChange={(e) => setInputIbId(e.target.value)} placeholder="如 137016" className="w-full" />
                      <Select value={ibDepth} onValueChange={(v) => setIbDepth(v as typeof ibDepth)}>
                        <SelectTrigger className="w-32"><SelectValue placeholder="层级" /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">所有层级</SelectItem>
                          <SelectItem value="level1">仅一级</SelectItem>
                          <SelectItem value="level1_2">一级+二级</SelectItem>
                        </SelectContent>
                      </Select>
                      <Button variant="secondary" onClick={addIbRule}>加入对象池</Button>
                    </div>
                  )}

                  {/* customer_tags */}
                  {ruleType === "customer_tags" && (
                    <div className="flex flex-wrap items-center gap-3">
                      <div className="flex items-center gap-2">
                        <Label className="w-24">Tag来源</Label>
                        <Select value={tagSource} onValueChange={(v) => setTagSource(v as typeof tagSource)}>
                          <SelectTrigger className="w-28"><SelectValue placeholder="来源" /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="local">本地Tag</SelectItem>
                            <SelectItem value="crm">CRM Tag</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">逻辑</span>
                        <Select value={tagOperator} onValueChange={(v) => setTagOperator(v as typeof tagOperator)}>
                          <SelectTrigger className="w-28"><SelectValue placeholder="逻辑" /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="ANY">ANY(并集)</SelectItem>
                            <SelectItem value="ALL">ALL(交集)</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <Popover>
                        <PopoverTrigger asChild>
                          <Button variant="outline" size="sm">选择Tag</Button>
                        </PopoverTrigger>
                        <PopoverContent className="w-56 p-2">
                          <div className="grid gap-2">
                            {(tagSource === "local" ? sampleTagsLocal : sampleTagsCRM).map((t) => {
                              const selected = (tagSource === "local" ? selectedLocalTags : selectedCrmTags).includes(t)
                              return (
                                <label key={t} className="flex items-center gap-2">
                                  <Checkbox
                                    checked={selected}
                                    onCheckedChange={(ck) => {
                                      const upd = (tagSource === "local" ? selectedLocalTags : selectedCrmTags)
                                      const setUpd = (tagSource === "local" ? setSelectedLocalTags : setSelectedCrmTags)
                                      if (ck) setUpd([...upd, t])
                                      else setUpd(upd.filter((x) => x !== t))
                                    }}
                                  />
                                  <span className="text-sm">{t}</span>
                                </label>
                              )
                            })}
                            <Button size="sm" onClick={addTagRule}>添加所选</Button>
                          </div>
                        </PopoverContent>
                      </Popover>
                    </div>
                  )}
                </div>
                {/* 规则列表 */}
                <div>
                  <div className="mb-2 text-sm font-medium">已选对象规则</div>
                  <div className="flex flex-wrap gap-2">
                    {rules.map((r, idx) => (
                      <Badge key={idx} variant="secondary" className="flex items-center gap-2">
                        <span className="text-xs">
                          {r.type === "customer_ids" && `客户ID:${r.ids.join(',')}`}
                          {r.type === "account_ids" && `账户:${r.ids.join(',')}`}
                          {r.type === "customer_tags" && `${r.source} Tags:${r.tags.join(',')}(${r.operator})`}
                          {r.type === "ib_id" && `IB:${r.id}(${r.depth})`}
                        </span>
                        <button onClick={() => removeRuleAt(idx)} className="text-muted-foreground hover:text-foreground">×</button>
                      </Badge>
                    ))}
                    {rules.length === 0 && <span className="text-xs text-muted-foreground">暂无规则</span>}
                  </div>
                </div>
              </div>
              <DialogFooter className="pt-2">
                <DialogClose asChild>
                  <Button>确认</Button>
                </DialogClose>
                <DialogClose asChild>
                  <Button variant="outline">取消</Button>
                </DialogClose>
              </DialogFooter>
            </DialogContent>
          </Dialog>
            </div>

            {/* 时间（Profit 风格：按钮显示范围 + 日历弹层 + 时区选择） */}
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">时间范围：</span>
              <Popover>
                <PopoverTrigger asChild>
                  <Button variant="outline" size="sm" className="justify-start gap-2 font-normal">
                    <span>{rangeLabel}</span>
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-2" align="start">
                  <Calendar mode="range" selected={range} onSelect={setRange} numberOfMonths={2} initialFocus />
                </PopoverContent>
              </Popover>
              <Select value={displayTz} onValueChange={(v) => setDisplayTz(v as typeof displayTz)}>
                <SelectTrigger className="w-28"><SelectValue placeholder="时区" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="UTC+3">UTC+3</SelectItem>
                  <SelectItem value="UTC+8">UTC+8</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* 交易品种 */}
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">交易品种：</span>
              <Select value={symbolsMode} onValueChange={(v) => setSymbolsMode(v as typeof symbolsMode)}>
                <SelectTrigger className="w-36"><SelectValue placeholder="选择方式" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全选（默认）</SelectItem>
                  <SelectItem value="custom">其他（自定义）</SelectItem>
                </SelectContent>
              </Select>
              {symbolsMode === "custom" && (
                <Popover>
                  <PopoverTrigger asChild>
                    <Button variant="outline" size="sm">选择品种</Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-56 p-2">
                    <div className="grid gap-2">
                      {sampleSymbols.map((s) => (
                        <label key={s} className="flex items-center gap-2">
                          <Checkbox checked={selectedSymbols.includes(s)} onCheckedChange={() => toggleSymbol(s)} />
                          <span className="text-sm">{s}</span>
                        </label>
                      ))}
                      <div className="flex items-center gap-2 pt-1">
                        <Button size="sm" variant="secondary" onClick={() => setSelectedSymbols(sampleSymbols)}>全选</Button>
                        <Button size="sm" variant="ghost" onClick={() => setSelectedSymbols([])}>清空</Button>
                      </div>
                    </div>
                  </PopoverContent>
                </Popover>
              )}
            </div>
          </div>
          {/* 简要提示 */}
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span>规则 {rules.length} 条</span>
            <span>账户 {derivedAccounts.length} 个</span>
            {symbolsMode === "all" ? <span>品种 全部</span> : (selectedSymbols.length > 0 ? <span>品种 {selectedSymbols.length} 个</span> : <span>品种 未选择</span>)}
          </div>
        </CardContent>
      </Card>

      {/* 指标卡片区 */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3 lg:grid-cols-5 *:data-[slot=card]:bg-gradient-to-t *:data-[slot=card]:from-primary/5 *:data-[slot=card]:to-card *:data-[slot=card]:shadow-xs dark:*:data-[slot=card]:bg-card">
        {kpis.map((k) => (
          <Card key={k.label} className="@container/card">
            <CardHeader>
              <CardDescription>{k.label}</CardDescription>
              <CardTitle className={`text-2xl font-semibold tabular-nums @[250px]/card:text-3xl ${
                k.tone === "pos" ? "text-green-600" : k.tone === "neg" ? "text-red-600" : ""
              }`}>
                {k.trend}{
                  typeof k.value === "number" && k.unit === "%"
                    ? `${Math.round(k.value * 100) / 1}%`
                    : typeof k.value === "number" && k.unit === "USD"
                    ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(k.value)
                    : String(k.value)
                }
              </CardTitle>
            </CardHeader>
          </Card>
        ))}
      </div>

      {/* 第一行：资金曲线 + 回撤阴影图 */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>资金曲线与回撤</CardTitle>
            <CardDescription>静态样例（日维度）</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={equityChartConfig} className="aspect-auto h-[300px] w-full">
              <LineChart data={equityData}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="t" tickLine={false} axisLine={false} tickMargin={8} minTickGap={24} />
                <YAxis yAxisId="left" tickFormatter={(v) => new Intl.NumberFormat().format(v)} />
                <YAxis yAxisId="right" orientation="right" tickFormatter={(v) => `${v}%`} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Line yAxisId="left" type="monotone" dataKey="equity" stroke="var(--color-equity)" dot={false} />
                <Area yAxisId="right" type="monotone" dataKey="drawdown" stroke="var(--color-drawdown)" fill="var(--color-drawdown)" />
              </LineChart>
            </ChartContainer>
          </CardContent>
        </Card>

        {/* 成本拆解（柱状模拟瀑布） */}
        <Card>
          <CardHeader>
            <CardTitle>成本拆解</CardTitle>
            <CardDescription>点差、佣金、隔夜利息、滑点</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={costChartConfig} className="aspect-auto h-[300px] w-full">
              <BarChart data={costData}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} />
                <YAxis tickFormatter={(v) => new Intl.NumberFormat().format(v)} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Bar dataKey="cost" fill="var(--color-cost)" />
              </BarChart>
            </ChartContainer>
          </CardContent>
        </Card>
      </div>

      {/* 第二行：品种占比饼图 + 多空占比条形图（静态） */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>交易品种占比</CardTitle>
            <CardDescription>静态样例</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={symbolConfig} className="aspect-auto h-[280px] w-full">
              <PieChart>
                <ChartLegend verticalAlign="top" content={<ChartLegendContent />} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Pie
                  data={symbolShare}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={50}
                  outerRadius={100}
                  strokeWidth={2}
                />
              </PieChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>多空方向占比</CardTitle>
            <CardDescription>静态样例</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={{ long: { label: "多" }, short: { label: "空" } }} className="aspect-auto h-[280px] w-full">
              <BarChart data={[{ k: "方向", long: 62, short: 38 }]}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="k" tickLine={false} axisLine={false} tickMargin={8} />
                <YAxis tickFormatter={(v) => `${v}%`} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Bar dataKey="long" stackId="a" fill="hsl(150 70% 45%)" />
                <Bar dataKey="short" stackId="a" fill="hsl(0 80% 60%)" />
              </BarChart>
            </ChartContainer>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}


