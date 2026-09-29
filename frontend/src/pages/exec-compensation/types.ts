/**
 * TypeScript mirror of backend/app/schemas/exec_compensation.py (frozen API
 * contract, OPT-0068). Fields are only ever added server-side; keep in sync.
 */

export type DealClass =
  | "no_plugin"
  | "plugin_passthrough"
  | "close_by"
  | "so_first"
  | "so_rest"
  | "limit"
  | "stop"
  | "other_type"
  | "sl"
  | "tp"
  | "market"
  | "dealer"
  | "other_reason";
export type Side = "buy" | "sell";
export type EntryKind = "open" | "close" | "inout" | "close_by";
export type Outcome = "worse" | "same" | "better";
export type NotCountedReason =
  | "not_eligible_class"
  | "position_open_at_as_of"
  | "position_anomaly";
export type OrdersView = "counted" | "excluded_open" | "all";
export type SortBy =
  | "fill_time_srv"
  | "comp_usd"
  | "delay_ms"
  | "lots"
  | "symbol"
  | "login"
  | "deal_id";
export type SortDir = "asc" | "desc";
export type ErrorCode =
  | "SUBJECT_REQUIRED"
  | "SUBJECT_AMBIGUOUS"
  | "INVALID_LOGIN_SID"
  | "SUBJECT_NOT_FOUND"
  | "RANGE_INVALID"
  | "RANGE_BEFORE_COVERAGE"
  | "AS_OF_TOO_LATE"
  | "SORT_NOT_ALLOWED"
  | "VALIDATION_ERROR"
  | "QUERY_TOO_LARGE"
  | "BUSY"
  | "UNKNOWN_CURRENCY"
  | "UPSTREAM_TIMEOUT";

export interface Basis {
  reference: "request_price";
  order_scope: "client_market_open_close";
  netting: "net";
  exclude_open_positions: true;
  day_boundary: "mt_server";
  currency: "USD";
  calc_version: number;
}

export interface Coverage {
  earliest_srv_date: string;
  ready_through_srv_date: string;
  complete: boolean;
  reason?: string | null;
  unmatched_deals: number;
  replica_latest_fill_utc?: string | null;
}

export interface Statistics {
  from_cache: boolean;
  query_time_ms: number;
}

export interface Totals {
  deals: number;
  lots: number;
  comp_net_usd: number;
  comp_positive_usd: number;
}

export interface GroupRow extends Totals {
  key: string;
}

export interface ClassRow extends Totals {
  cls: DealClass;
}

export interface SummaryData extends Totals {
  subject: { client_id?: number | null; login_sid?: string | null; logins: number[] };
  query: {
    date_from: string;
    date_to: string;
    date_to_requested: string;
    date_to_clipped: boolean;
    as_of: string;
  };
  outcomes: { worse: number; same: number; better: number };
  delay: {
    n: number;
    median_ms?: number | null;
    p95_ms?: number | null;
    max_ms?: number | null;
    min_ms?: number | null;
  };
  max_single_comp_usd?: number | null;
  by_account: GroupRow[];
  by_symbol: GroupRow[];
  by_entry: GroupRow[];
  by_day: GroupRow[];
  excluded_open: {
    positions: number;
    deals: number;
    lots: number;
    comp_net_usd_if_counted: number;
  };
  anomaly_positions: number;
  not_counted_by_class: ClassRow[];
}

export interface SummaryResponse {
  data: SummaryData;
  basis: Basis;
  as_of: string;
  coverage: Coverage;
  statistics: Statistics;
}

export interface OrderRow {
  deal_id: number;
  order_id: number;
  position_id: number;
  login: number;
  login_sid: string;
  /** mt4_users.GROUP; explains plugin_passthrough rows (01 D20). */
  account_group?: string | null;
  ccy: "CEN" | "USD";
  symbol: string;
  side: Side;
  entry: EntryKind;
  cls: DealClass;
  eligible: boolean;
  counted: boolean;
  not_counted_reason?: NotCountedReason | null;
  lots: number;
  req_time_utc?: string | null;
  /** MT server wall clock of the request (added b89244a). */
  req_time_srv?: string | null;
  fill_time_utc: string;
  fill_time_srv: string;
  srv_date: string;
  delay_ms?: number | null;
  ref_price?: number | null;
  fill_price: number;
  worse_px?: number | null;
  outcome?: Outcome | null;
  comp_usd?: number | null;
  raw: {
    action: number;
    entry: number;
    order_type?: number | null;
    order_reason?: number | null;
    dealer?: number | null;
  };
}

export interface OrdersResponse {
  data: OrderRow[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  basis: Basis;
  as_of: string;
  coverage: Coverage;
  statistics: Statistics;
}

export interface StatusData {
  calc_version: number;
  earliest_srv_date: string;
  default_as_of: string;
  ready_through_srv_date: string;
  replica_latest_fill_utc?: string | null;
  limits: {
    statement_timeout_ms: number;
    query_budget_s: number;
    max_deals: number;
    max_concurrent: number;
    cache_ttl_s: number;
    page_size_max: number;
  };
}

export interface StatusResponse {
  data: StatusData;
  basis: Basis;
}

// ── display labels ──────────────────────────────────────────────────────────

export const CLASS_LABELS: Record<DealClass, string> = {
  market: "客户市价单",
  no_plugin: "未经插件（oneZero 网关）",
  plugin_passthrough: "插件直通（排除组）",
  close_by: "对冲平仓（Close By）",
  so_first: "强平（首笔）",
  so_rest: "强平（后续）",
  limit: "限价挂单",
  stop: "止损挂单（Stop 单）",
  other_type: "其他订单类型",
  sl: "止损触发（SL）",
  tp: "止盈触发（TP）",
  dealer: "交易员操作",
  other_reason: "其他来源",
};

export const ENTRY_LABELS: Record<EntryKind, string> = {
  open: "开仓",
  close: "平仓",
  inout: "反手",
  close_by: "对冲平仓",
};

export const SIDE_LABELS: Record<Side, string> = { buy: "买", sell: "卖" };

export const OUTCOME_LABELS: Record<Outcome, string> = {
  worse: "更差",
  same: "不变",
  better: "更好",
};

export const REASON_LABELS: Record<NotCountedReason, string> = {
  not_eligible_class: "非客户市价单",
  position_open_at_as_of: "仓位未完全平仓",
  position_anomaly: "仓位异常",
};

export const VIEW_OPTIONS: { value: OrdersView; label: string }[] = [
  { value: "counted", label: "计入" },
  { value: "excluded_open", label: "未计入（未平仓）" },
  { value: "all", label: "全部" },
];
