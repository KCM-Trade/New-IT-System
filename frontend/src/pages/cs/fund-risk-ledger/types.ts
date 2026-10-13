/**
 * TypeScript mirror of the fund risk-control ledger API contract
 * (backend/app/schemas/cs_fund_risk.py). Field names and enum strings must
 * stay identical to the Pydantic models — the backend owns the contract.
 */

export const DIRECTIONS = ["deposit", "withdrawal"] as const;
export type Direction = (typeof DIRECTIONS)[number];

export const FROZEN_SCOPES = [
  "none",
  "one_card",
  "all_cards",
  "non_counter",
  "unknown",
] as const;
export type FrozenScope = (typeof FROZEN_SCOPES)[number];

export const AUTHORITIES = ["bank", "police_antifraud", "alipay", "unknown"] as const;
export type Authority = (typeof AUTHORITIES)[number];

export const CLIENT_SOURCES = ["online", "offline"] as const;
export type ClientSource = (typeof CLIENT_SOURCES)[number];

export const TRANSFER_SUCCEEDED = ["yes", "no", "unknown"] as const;
export type TransferSucceeded = (typeof TRANSFER_SUCCEEDED)[number];

export const ROW_SOURCES = ["manual", "import"] as const;
export type RowSource = (typeof ROW_SOURCES)[number];

export const ORDER_WARNINGS = [
  "psp_unmapped",
  "non_cn_psp",
  "test_account",
  "not_approved",
  "amount_not_cny",
] as const;
export type OrderWarning = (typeof ORDER_WARNINGS)[number];

/** Field length limits enforced by the backend (mirrored for maxLength). */
export const TEXT_MAX = 4000;
export const SHORT_MAX = 64;
export const FREEZE_NOTE_MAX = 500;
export const PWA_DOMAIN_MAX = 200;
export const FREEZE_DAYS_MAX = 36500;

/** Order facts derived from the CRM replica; never typed by staff. */
export interface OrderSnapshot {
  transaction_id: number;
  client_id: number | null;
  client_name: string | null; // live only, null when the replica is down
  login_sid: string | null;
  venue: string | null;
  direction: Direction;
  txn_type: string;
  psp_id: number | null;
  psp_name: string | null;
  channel: string;
  amount_cny: number | null;
  txn_status: string;
  txn_created_at: string | null; // UTC ISO8601 Z
  txn_processed_at: string | null;
  is_test_account: boolean;
}

export interface OrderPreviewResponse {
  order: OrderSnapshot;
  existing_id: number | null;
  warnings: OrderWarning[];
}

/** Staff-entered fields (IncidentFields). */
export interface IncidentFields {
  reported_date: string | null; // YYYY-MM-DD
  transfer_succeeded: TransferSucceeded;
  frozen_scope: FrozenScope;
  freeze_days: number | null; // null = unknown
  freeze_note: string | null;
  authority: Authority;
  client_source: ClientSource | null;
  client_bank_province: string | null;
  client_bank_city: string | null;
  trader_bank_province: string | null;
  trader_bank_city: string | null;
  id_province: string | null;
  id_city: string | null;
  ip_region: string | null;
  pwa_domain: string | null;
  account_type: string | null;
  venue_override: string | null;
  event_text: string | null;
  result_text: string | null;
}

export interface IncidentCreate extends IncidentFields {
  transaction_id: number;
  incident_group_id: number | null; // null = start a new group
}

export interface IncidentUpdate extends IncidentFields {
  version: number;
  apply_to_group: boolean;
}

export interface Incident extends IncidentFields {
  id: number;
  incident_group_id: number;
  group_size: number;
  transaction_id: number | null; // null only on imported legacy rows
  order: OrderSnapshot | null;
  order_is_live: boolean;
  source: RowSource;
  version: number;
  created_by: string;
  created_at: string;
  updated_by: string;
  updated_at: string;
}

export interface IncidentStatistics {
  query_time_ms: number;
  replica_live: boolean;
}

export interface IncidentListResponse {
  data: Incident[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  statistics: IncidentStatistics;
}

/** Body of a 409: duplicate transaction id or stale version. */
export interface IncidentConflict {
  detail: string;
  existing_id: number | null;
  current_version: number | null;
}

/** Server-side sort whitelist of GET /incidents. */
export const SORT_KEYS = ["reported_date", "txn_processed_at", "id"] as const;
export type SortKey = (typeof SORT_KEYS)[number];
export type SortOrder = "asc" | "desc";
