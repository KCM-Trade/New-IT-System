import { useState, useCallback, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Drawer, DrawerContent, DrawerDescription, DrawerFooter, DrawerHeader, DrawerTitle } from "@/components/ui/drawer"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Calendar } from "@/components/ui/calendar"
import { X, Plus, CalendarIcon } from "lucide-react"
import { getFilterableColumns, getColumnMeta } from "@/config/filterColumns"
import { 
  FilterRule, 
  FilterGroup, 
  getOperatorsForType, 
  operatorNeedsValue, 
  operatorNeedsTwoValues,
  OPERATOR_LABELS,
  FilterOperator
} from "@/types/filter"
import { format } from "date-fns"
import { cn } from "@/lib/utils"

// fresh grad note: This component uses Dialog for desktop, Drawer for mobile
// Responsive breakpoint: 640px (sm)

interface FilterBuilderProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  initialFilters?: FilterGroup
  onApply: (filters: FilterGroup) => void
}

export function FilterBuilder({ open, onOpenChange, initialFilters, onApply }: FilterBuilderProps) {
  // Detect mobile viewport
  const [isMobile, setIsMobile] = useState(false)

  useEffect(() => {
    const checkMobile = () => setIsMobile(window.innerWidth < 640)
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  // Filter state
  const [join, setJoin] = useState<'AND' | 'OR'>(initialFilters?.join || 'AND')
  const [rules, setRules] = useState<FilterRule[]>(initialFilters?.rules || [])

  // Sync with initialFilters when dialog opens
  useEffect(() => {
    if (open && initialFilters) {
      setJoin(initialFilters.join)
      setRules(initialFilters.rules.length > 0 ? initialFilters.rules : [])
    }
  }, [open, initialFilters])

  // Add new rule (default: first filterable column + first operator)
  const handleAddRule = useCallback(() => {
    const filterableColumns = getFilterableColumns()
    if (filterableColumns.length === 0) return
    
    const firstCol = filterableColumns[0]
    const ops = getOperatorsForType(firstCol.type)
    
    setRules(prev => [...prev, {
      field: firstCol.id,
      op: ops[0],
      value: undefined,
      value2: undefined,
    }])
  }, [])

  // Remove rule
  const handleRemoveRule = useCallback((index: number) => {
    setRules(prev => prev.filter((_, i) => i !== index))
  }, [])

  // Update rule field
  const handleRuleFieldChange = useCallback((index: number, field: string) => {
    setRules(prev => {
      const next = [...prev]
      const colMeta = getColumnMeta(field)
      if (!colMeta) return prev
      
      const ops = getOperatorsForType(colMeta.type)
      next[index] = {
        field,
        op: ops[0],
        value: undefined,
        value2: undefined,
      }
      return next
    })
  }, [])

  // Update rule operator
  const handleRuleOpChange = useCallback((index: number, op: FilterOperator) => {
    setRules(prev => {
      const next = [...prev]
      next[index] = {
        ...next[index],
        op,
        value: undefined,
        value2: undefined,
      }
      return next
    })
  }, [])

  // Update rule value
  const handleRuleValueChange = useCallback((index: number, value: any, isSecondary = false) => {
    setRules(prev => {
      const next = [...prev]
      if (isSecondary) {
        next[index] = { ...next[index], value2: value }
      } else {
        next[index] = { ...next[index], value }
      }
      return next
    })
  }, [])

  // Reset to initial state
  const handleReset = useCallback(() => {
    setJoin('AND')
    setRules([])
  }, [])

  // Apply filters
  const handleApply = useCallback(() => {
    const filterGroup: FilterGroup = { join, rules }
    console.log('Applied Filters (静态 JSON):', JSON.stringify(filterGroup, null, 2))
    onApply(filterGroup)
    onOpenChange(false)
  }, [join, rules, onApply, onOpenChange])

  // Render content (shared between Dialog and Drawer)
  const content = (
    <div className="space-y-4">
      {/* AND/OR Join Toggle */}
      <div className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">逻辑关系:</span>
        <div className="flex gap-1">
          <Button
            variant={join === 'AND' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setJoin('AND')}
            className="h-9"
          >
            AND
          </Button>
          <Button
            variant={join === 'OR' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setJoin('OR')}
            className="h-9"
          >
            OR
          </Button>
        </div>
      </div>

      {/* Rules List */}
      <div className="space-y-3 max-h-[400px] overflow-y-auto">
        {rules.length === 0 && (
          <p className="text-sm text-muted-foreground py-4 text-center">暂无筛选条件，点击下方按钮添加</p>
        )}
        {rules.map((rule, index) => (
          <FilterRuleRow
            key={index}
            rule={rule}
            index={index}
            onFieldChange={handleRuleFieldChange}
            onOpChange={handleRuleOpChange}
            onValueChange={handleRuleValueChange}
            onRemove={handleRemoveRule}
          />
        ))}
      </div>

      {/* Add Rule Button */}
      <Button
        variant="outline"
        size="sm"
        onClick={handleAddRule}
        className="w-full h-9"
      >
        <Plus className="h-4 w-4 mr-2" />
        新增条件
      </Button>
    </div>
  )

  const footer = (
    <>
      <Button variant="outline" onClick={handleReset} className="h-9">
        重置
      </Button>
      <Button variant="outline" onClick={() => onOpenChange(false)} className="h-9">
        取消
      </Button>
      <Button onClick={handleApply} className="h-9">
        应用
      </Button>
    </>
  )

  // Mobile: use Drawer
  if (isMobile) {
    return (
      <Drawer open={open} onOpenChange={onOpenChange}>
        <DrawerContent>
          <DrawerHeader>
            <DrawerTitle>筛选条件</DrawerTitle>
            <DrawerDescription>设置筛选规则并应用到数据表格</DrawerDescription>
          </DrawerHeader>
          <div className="px-4 pb-4">
            {content}
          </div>
          <DrawerFooter className="flex-row gap-2">
            {footer}
          </DrawerFooter>
        </DrawerContent>
      </Drawer>
    )
  }

  // Desktop: use Dialog
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[720px] lg:max-w-[860px]">
        <DialogHeader>
          <DialogTitle>筛选条件</DialogTitle>
          <DialogDescription>设置筛选规则并应用到数据表格</DialogDescription>
        </DialogHeader>
        {content}
        <DialogFooter className="gap-2">
          {footer}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// Single rule row component
// fresh grad note: This row contains column selector, operator selector, value input(s), and delete button
interface FilterRuleRowProps {
  rule: FilterRule
  index: number
  onFieldChange: (index: number, field: string) => void
  onOpChange: (index: number, op: FilterOperator) => void
  onValueChange: (index: number, value: any, isSecondary?: boolean) => void
  onRemove: (index: number) => void
}

function FilterRuleRow({ rule, index, onFieldChange, onOpChange, onValueChange, onRemove }: FilterRuleRowProps) {
  const filterableColumns = getFilterableColumns()
  const colMeta = getColumnMeta(rule.field)
  const operators = colMeta ? getOperatorsForType(colMeta.type) : []
  const needsValue = operatorNeedsValue(rule.op)
  const needsTwoValues = operatorNeedsTwoValues(rule.op)

  return (
    <div className="flex flex-col sm:flex-row gap-2 p-3 border rounded-lg bg-muted/30">
      {/* Column Selector */}
      <Select value={rule.field} onValueChange={(v) => onFieldChange(index, v)}>
        <SelectTrigger className="h-9 w-full sm:w-[200px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {filterableColumns.map(col => (
            <SelectItem key={col.id} value={col.id}>
              {col.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Operator Selector */}
      <Select value={rule.op} onValueChange={(v) => onOpChange(index, v as FilterOperator)}>
        <SelectTrigger className="h-9 w-full sm:w-[140px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {operators.map(op => (
            <SelectItem key={op} value={op}>
              {OPERATOR_LABELS[op]}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Value Input(s) */}
      {needsValue && (
        <div className="flex gap-2 flex-1">
          <ValueInput
            type={colMeta?.type || 'text'}
            value={rule.value}
            onChange={(v) => onValueChange(index, v, false)}
          />
          {needsTwoValues && (
            <>
              <span className="text-muted-foreground self-center">~</span>
              <ValueInput
                type={colMeta?.type || 'text'}
                value={rule.value2}
                onChange={(v) => onValueChange(index, v, true)}
              />
            </>
          )}
        </div>
      )}

      {/* Delete Button */}
      <Button
        variant="ghost"
        size="sm"
        onClick={() => onRemove(index)}
        className="h-9 w-9 p-0 shrink-0"
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  )
}

// Value input component (adapts to column type)
interface ValueInputProps {
  type: 'text' | 'number' | 'date' | 'percent'
  value: any
  onChange: (value: any) => void
}

function ValueInput({ type, value, onChange }: ValueInputProps) {
  // Date picker state
  const [dateOpen, setDateOpen] = useState(false)

  if (type === 'date') {
    return (
      <Popover open={dateOpen} onOpenChange={setDateOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            className={cn(
              "h-9 w-full justify-start text-left font-normal",
              !value && "text-muted-foreground"
            )}
          >
            <CalendarIcon className="mr-2 h-4 w-4" />
            {value ? format(new Date(value), "yyyy-MM-dd") : "选择日期"}
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0" align="start">
          <Calendar
            mode="single"
            selected={value ? new Date(value) : undefined}
            onSelect={(date) => {
              if (date) {
                onChange(format(date, "yyyy-MM-dd"))
                setDateOpen(false)
              }
            }}
            initialFocus
          />
        </PopoverContent>
      </Popover>
    )
  }

  if (type === 'number' || type === 'percent') {
    return (
      <Input
        type="number"
        value={value ?? ''}
        onChange={(e) => {
          const v = e.target.value
          onChange(v === '' ? undefined : Number(v))
        }}
        placeholder="输入数值"
        className="h-9"
      />
    )
  }

  // Default: text
  return (
    <Input
      type="text"
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value)}
      placeholder="输入文本"
      className="h-9"
    />
  )
}

