// Filter module data models for CustomerPnLMonitorV2
// fresh grad note: This defines the structure for advanced filtering

// Column metadata for filter builder
export interface ColumnMeta {
  id: string // field name in backend API
  label: string // display label in UI
  type: 'text' | 'number' | 'date' | 'percent' // data type
  filterable: boolean // whether this column can be filtered
  note?: string // optional tooltip or help text
  // fresh grad note: optional enum options for value selection (e.g. server id -> name)
  options?: Array<{ label: string; value: string | number }>
  // fresh grad note: optional per-column operator whitelist to keep UI and execution logic aligned
  operators?: FilterOperator[]
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
  // NOTE: Per product request, operator labels are ALWAYS English (do not localize).
  contains: 'contains',
  not_contains: 'not contains',
  equals: 'equals',
  not_equals: 'not equals',
  starts_with: 'starts with',
  ends_with: 'ends with',
  blank: 'is empty',
  not_blank: 'is not empty',
  // Number
  '=': 'equals',
  '!=': 'not equals',
  '>': 'greater than',
  '>=': 'greater or equal',
  '<': 'less than',
  '<=': 'less or equal',
  between: 'between',
  // Date (same as number operators)
  on: 'on',
  before: 'before',
  after: 'after',
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

