import { useState, useCallback, useEffect, useMemo } from "react"
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
  FilterOperator,
  ColumnMeta,
} from "@/types/filter"
import { format } from "date-fns"
import { cn } from "@/lib/utils"
import { useI18n } from "@/components/i18n-provider"

// fresh grad note: This component uses Dialog for desktop, Drawer for mobile
// Responsive breakpoint: 640px (sm)

interface FilterBuilderProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  initialFilters?: FilterGroup
  onApply: (filters: FilterGroup) => void
  // Optional override columns for current page (fallbacks to global config)
  columns?: ColumnMeta[]
}

export function FilterBuilder({ open, onOpenChange, initialFilters, onApply, columns }: FilterBuilderProps) {
  // Detect mobile viewport
  const [isMobile, setIsMobile] = useState(false)
  // i18n helpers
  const { t } = useI18n()
  const isZh = useMemo(() => {
    try {
      const sep = (t as any)('common.comma')
      return typeof sep === 'string' && sep !== ', '
    } catch {
      return true
    }
  }, [t])
  const tz = useCallback((key: string, zhFallback: string, enFallback: string) => {
    try {
      const v = (t as any)(key)
      if (typeof v === 'string' && v && v !== key) return v
    } catch {}
    return isZh ? zhFallback : enFallback
  }, [t, isZh])

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

  // Resolve columns/meta depending on optional override
  const resolveFilterableColumns = useCallback((): ColumnMeta[] => {
    if (Array.isArray(columns) && columns.length > 0) {
      return columns.filter(c => c.filterable)
    }
    return getFilterableColumns()
  }, [columns])

  const resolveColumnMeta = useCallback((id: string): ColumnMeta | undefined => {
    if (Array.isArray(columns) && columns.length > 0) {
      return columns.find(c => c.id === id)
    }
    return getColumnMeta(id)
  }, [columns])

  // Add new rule (default: first filterable column + first operator)
  const handleAddRule = useCallback(() => {
    const filterableColumns = resolveFilterableColumns()
    if (filterableColumns.length === 0) return
    
    const firstCol = filterableColumns[0]
    const ops = getOperatorsForType(firstCol.type)
    
    setRules(prev => [...prev, {
      field: firstCol.id,
      op: ops[0],
      value: undefined,
      value2: undefined,
    }])
  }, [resolveFilterableColumns])

  // Remove rule
  const handleRemoveRule = useCallback((index: number) => {
    setRules(prev => prev.filter((_, i) => i !== index))
  }, [])

  // Update rule field
  const handleRuleFieldChange = useCallback((index: number, field: string) => {
    setRules(prev => {
      const next = [...prev]
      const colMeta = resolveColumnMeta(field)
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
  }, [resolveColumnMeta])

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
        <span className="text-sm text-muted-foreground">{tz('filter.logic', '逻辑关系', 'Logic')}</span>
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
            availableColumns={resolveFilterableColumns()}
            getColMeta={resolveColumnMeta}
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
        {tz('filter.addRule', '新增条件', 'Add Rule')}
      </Button>
    </div>
  )

  const footer = (
    <>
      <Button variant="outline" onClick={handleReset} className="h-9">
        {tz('filter.reset', '重置', 'Reset')}
      </Button>
      <Button variant="outline" onClick={() => onOpenChange(false)} className="h-9">
        {tz('filter.cancel', '取消', 'Cancel')}
      </Button>
      <Button onClick={handleApply} className="h-9">
        {tz('filter.apply', '应用', 'Apply')}
      </Button>
    </>
  )

  // Mobile: use Drawer
  if (isMobile) {
    return (
      <Drawer open={open} onOpenChange={onOpenChange}>
        <DrawerContent>
          <DrawerHeader>
            <DrawerTitle>{tz('filter.title', '筛选条件', 'Filter')}</DrawerTitle>
            <DrawerDescription>{tz('filter.description', '设置筛选规则并应用到数据表格', 'Set filter rules and apply to the table')}</DrawerDescription>
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
          <DialogTitle>{tz('filter.title', '筛选条件', 'Filter')}</DialogTitle>
          <DialogDescription>{tz('filter.description', '设置筛选规则并应用到数据表格', 'Set filter rules and apply to the table')}</DialogDescription>
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
  availableColumns: ColumnMeta[]
  getColMeta: (id: string) => ColumnMeta | undefined
}

function FilterRuleRow({ rule, index, onFieldChange, onOpChange, onValueChange, onRemove, availableColumns, getColMeta }: FilterRuleRowProps) {
  const filterableColumns = availableColumns
  const colMeta = getColMeta(rule.field)
  // fresh grad note: allow per-column operator whitelist to match product requirements
  const operators = useMemo(() => {
    if (!colMeta) return []
    if (Array.isArray((colMeta as any).operators) && (colMeta as any).operators.length > 0) {
      return (colMeta as any).operators as FilterOperator[]
    }
    return getOperatorsForType(colMeta.type)
  }, [colMeta])
  const needsValue = operatorNeedsValue(rule.op)
  const needsTwoValues = operatorNeedsTwoValues(rule.op)
  const isIsEnabled = rule.field === 'is_enabled'
  // i18n helpers inside row
  const { t } = useI18n()
  const isZh = useMemo(() => {
    try {
      const sep = (t as any)('common.comma')
      return typeof sep === 'string' && sep !== ', '
    } catch {
      return true
    }
  }, [t])
  const tz = useCallback((key: string, zhFallback: string, enFallback: string) => {
    try {
      const v = (t as any)(key)
      if (typeof v === 'string' && v && v !== key) return v
    } catch {}
    return isZh ? zhFallback : enFallback
  }, [t, isZh])
  const getOperatorLabel = useCallback((op: FilterOperator) => {
    // NOTE: Per product request, operator labels are ALWAYS English (do not localize).
    return (OPERATOR_LABELS as any)[op] || String(op)
  }, [])

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
      {isIsEnabled ? (
        <Select value={'='} onValueChange={() => { /* locked to '=' */ }} disabled>
          <SelectTrigger className="h-9 w-full sm:w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="=">{getOperatorLabel('=' as any)}</SelectItem>
          </SelectContent>
        </Select>
      ) : (
        <Select value={rule.op} onValueChange={(v) => onOpChange(index, v as FilterOperator)}>
          <SelectTrigger className="h-9 w-full sm:w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {operators.map(op => (
              <SelectItem key={op} value={op}>
                {getOperatorLabel(op)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}

      {/* Value Input(s) */}
      {isIsEnabled ? (
        <div className="flex gap-2 flex-1">
          <Select
            value={rule.op === 'blank' ? 'NULL' : (rule.value === 1 ? '1' : (rule.value === 0 ? '0' : 'NULL'))}
            onValueChange={(v) => {
              if (v === 'NULL') {
                onOpChange(index, 'blank')
                onValueChange(index, undefined, false)
              } else {
                onOpChange(index, '=' as any)
                onValueChange(index, Number(v), false)
              }
            }}
          >
            <SelectTrigger className="h-9 w-full sm:w-[200px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="1">{tz('filter.enabled', '启用', 'Enabled')}</SelectItem>
              <SelectItem value="0">{tz('filter.disabled', '禁用', 'Disabled')}</SelectItem>
              <SelectItem value="NULL">{tz('filter.otherEmpty', '其他（空值）', 'Other (Empty)')}</SelectItem>
            </SelectContent>
          </Select>
        </div>
      ) : (
        needsValue && (
          <div className="flex gap-2 flex-1">
            <ValueInput
              type={colMeta?.type || 'text'}
              value={rule.value}
              onChange={(v) => onValueChange(index, v, false)}
              options={colMeta?.options}
            />
            {needsTwoValues && (
              <>
                <span className="text-muted-foreground self-center">~</span>
                <ValueInput
                  type={colMeta?.type || 'text'}
                  value={rule.value2}
                  onChange={(v) => onValueChange(index, v, true)}
                  options={colMeta?.options}
                />
              </>
            )}
          </div>
        )
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
  options?: Array<{ label: string; value: string | number }>
}

function ValueInput({ type, value, onChange, options }: ValueInputProps) {
  // Date picker state
  const [dateOpen, setDateOpen] = useState(false)
  // i18n helpers
  const { t } = useI18n()
  const isZh = useMemo(() => {
    try {
      const sep = (t as any)('common.comma')
      return typeof sep === 'string' && sep !== ', '
    } catch {
      return true
    }
  }, [t])
  const tz = useCallback((key: string, zhFallback: string, enFallback: string) => {
    try {
      const v = (t as any)(key)
      if (typeof v === 'string' && v && v !== key) return v
    } catch {}
    return isZh ? zhFallback : enFallback
  }, [t, isZh])

  // Enum options (Select) - used for cases like server id -> server name
  if (Array.isArray(options) && options.length > 0) {
    const current = value === undefined || value === null ? '' : String(value)
    return (
      <Select
        value={current}
        onValueChange={(v) => {
          const matched = options.find(o => String(o.value) === String(v))
          if (!matched) return
          // fresh grad note: keep numeric values as numbers when possible
          if (typeof matched.value === 'number') {
            onChange(matched.value)
          } else {
            onChange(matched.value)
          }
        }}
      >
        <SelectTrigger className="h-9 w-full sm:w-[200px]">
          <SelectValue placeholder={tz('filter.selectOption', '请选择', 'Select')} />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={String(o.value)} value={String(o.value)}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    )
  }

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
            {value ? format(new Date(value), "yyyy-MM-dd") : tz('filter.chooseDate', '选择日期', 'Choose Date')}
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
      placeholder={tz('filter.enterText', '输入文本', 'Enter text')}
      className="h-9"
    />
  )
}

