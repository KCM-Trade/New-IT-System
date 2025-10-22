import { ColumnMeta } from '@/types/filter'

// Column metadata whitelist for CustomerPnLMonitorV2
// fresh grad note: Only columns marked filterable=true can be used in the filter builder
// Computed/aggregated columns are disabled until backend supports them

export const FILTER_COLUMNS: ColumnMeta[] = [
  // User & Account Info
  { id: 'login', label: '账户ID', type: 'text', filterable: true },
  { id: 'user_name', label: '客户名称', type: 'text', filterable: true },
  { id: 'user_group', label: 'Group', type: 'text', filterable: true },
  { id: 'country', label: '国家/地区', type: 'text', filterable: true },
  { id: 'zipcode', label: 'ZipCode', type: 'text', filterable: true },
  { id: 'user_id', label: 'ClientID', type: 'text', filterable: true },
  { id: 'symbol', label: 'Symbol', type: 'text', filterable: true },

  // Account Balance & Floating
  { id: 'user_balance', label: 'Balance', type: 'number', filterable: true },
  { id: 'positions_floating_pnl', label: '持仓浮动盈亏', type: 'number', filterable: true },
  { id: 'equity', label: 'Equity', type: 'number', filterable: true },

  // SELL Closed Stats
  { id: 'closed_sell_volume_lots', label: 'Closed Sell Volume (Lots)', type: 'number', filterable: true },
  { id: 'closed_sell_count', label: 'Closed Sell Count', type: 'number', filterable: true },
  { id: 'closed_sell_profit', label: 'Closed Sell Profit', type: 'number', filterable: true },
  { id: 'closed_sell_swap', label: 'Closed Sell Swap', type: 'number', filterable: true },
  { id: 'closed_sell_overnight_count', label: 'Closed Sell Overnight Count', type: 'number', filterable: true },
  { id: 'closed_sell_overnight_volume_lots', label: 'Closed Sell Overnight Volume', type: 'number', filterable: true },

  // BUY Closed Stats
  { id: 'closed_buy_volume_lots', label: 'Closed Buy Volume (Lots)', type: 'number', filterable: true },
  { id: 'closed_buy_count', label: 'Closed Buy Count', type: 'number', filterable: true },
  { id: 'closed_buy_profit', label: 'Closed Buy Profit', type: 'number', filterable: true },
  { id: 'closed_buy_swap', label: 'Closed Buy Swap', type: 'number', filterable: true },
  { id: 'closed_buy_overnight_count', label: 'Closed Buy Overnight Count', type: 'number', filterable: true },
  { id: 'closed_buy_overnight_volume_lots', label: 'Closed Buy Overnight Volume', type: 'number', filterable: true },

  // Commission & Deposit/Withdrawal
  { id: 'total_commission', label: 'Total Commission', type: 'number', filterable: true },
  { id: 'deposit_count', label: '入金笔数', type: 'number', filterable: true },
  { id: 'deposit_amount', label: '入金金额', type: 'number', filterable: true },
  { id: 'withdrawal_count', label: '出金笔数', type: 'number', filterable: true },
  { id: 'withdrawal_amount', label: '出金金额', type: 'number', filterable: true },
  { id: 'net_deposit', label: 'Net Deposit', type: 'number', filterable: true },

  // Date
  { id: 'last_updated', label: '更新时间', type: 'date', filterable: true },

  // Computed columns (now supported by backend database)
  { id: 'closed_total_profit', label: '平仓总盈亏', type: 'number', filterable: true, note: '从数据库字段 closed_total_profit_with_swap 映射' },
  
  // Frontend-computed columns (disabled until backend supports)
  { id: 'overnight_volume_ratio', label: '过夜成交量占比', type: 'percent', filterable: false, note: '前端计算列，暂不支持筛选' },
  // Aggregated columns from valueGetter (disabled until backend supports)
  // { id: 'overnight_volume_all', label: '过夜订单手数', type: 'number', filterable: false, note: '前端聚合列，暂不支持筛选' },
  // { id: 'total_volume_all', label: '总订单手数', type: 'number', filterable: false, note: '前端聚合列，暂不支持筛选' },
  // { id: 'overnight_order_all', label: '过夜订单数', type: 'number', filterable: false, note: '前端聚合列，暂不支持筛选' },
  // { id: 'total_order_all', label: '总订单数', type: 'number', filterable: false, note: '前端聚合列，暂不支持筛选' },
]

// Helper: get filterable columns only
export function getFilterableColumns(): ColumnMeta[] {
  return FILTER_COLUMNS.filter(col => col.filterable)
}

// Helper: get column meta by id
export function getColumnMeta(id: string): ColumnMeta | undefined {
  return FILTER_COLUMNS.find(col => col.id === id)
}

