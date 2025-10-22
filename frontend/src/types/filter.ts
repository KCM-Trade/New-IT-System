// Filter module data models for CustomerPnLMonitorV2
// fresh grad note: This defines the structure for advanced filtering

// Column metadata for filter builder
export interface ColumnMeta {
  id: string // field name in backend API
  label: string // display label in UI
  type: 'text' | 'number' | 'date' | 'percent' // data type
  filterable: boolean // whether this column can be filtered
  note?: string // optional tooltip or help text
}

// Filter operators by type
export type TextOperator = 
  | 'contains' 
  | 'not_contains' 
  | 'equals' 
  | 'not_equals' 
  | 'starts_with' 
  | 'ends_with' 
  | 'blank' 
  | 'not_blank'

export type NumberOperator = 
  | '=' 
  | '!=' 
  | '>' 
  | '>=' 
  | '<' 
  | '<=' 
  | 'between' 
  | 'blank' 
  | 'not_blank'

export type DateOperator = 
  | 'on' 
  | 'before' 
  | 'after' 
  | 'between' 
  | 'blank' 
  | 'not_blank'

export type FilterOperator = TextOperator | NumberOperator | DateOperator

// Single filter rule
export interface FilterRule {
  field: string // column id
  op: FilterOperator
  value?: any // primary value
  value2?: any // secondary value (for 'between')
}

// Filter group with logical join
export interface FilterGroup {
  join: 'AND' | 'OR'
  rules: FilterRule[]
}

// Operator labels for UI display
export const OPERATOR_LABELS: Record<FilterOperator, string> = {
  // Text
  contains: '包含',
  not_contains: '不包含',
  equals: '等于',
  not_equals: '不等于',
  starts_with: '开头是',
  ends_with: '结尾是',
  blank: '为空',
  not_blank: '不为空',
  // Number
  '=': '等于',
  '!=': '不等于',
  '>': '大于',
  '>=': '大于等于',
  '<': '小于',
  '<=': '小于等于',
  between: '区间',
  // Date (same as number operators)
  on: '等于',
  before: '早于',
  after: '晚于',
}

// Get operators for a column type
export function getOperatorsForType(type: ColumnMeta['type']): FilterOperator[] {
  switch (type) {
    case 'text':
      return ['contains', 'not_contains', 'equals', 'not_equals', 'starts_with', 'ends_with', 'blank', 'not_blank']
    case 'number':
    case 'percent':
      return ['=', '!=', '>', '>=', '<', '<=', 'between', 'blank', 'not_blank']
    case 'date':
      return ['on', 'before', 'after', 'between', 'blank', 'not_blank']
    default:
      return ['=', '!=', 'blank', 'not_blank']
  }
}

// Check if operator requires value input
export function operatorNeedsValue(op: FilterOperator): boolean {
  return op !== 'blank' && op !== 'not_blank'
}

// Check if operator requires two values (between)
export function operatorNeedsTwoValues(op: FilterOperator): boolean {
  return op === 'between'
}

