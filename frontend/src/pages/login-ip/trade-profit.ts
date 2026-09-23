/**
 * Trade-IP profit attribution (OPT-0063 Phase 3) — API types + pure helpers.
 *
 * Field names mirror backend/app/schemas/login_ip_trade_profit.py 1:1 — do
 * not rename here without changing the backend contract. Kept free of React
 * so vitest covers the window / query-param logic without a DOM environment
 * (same pattern as lib/crm-tag-filter.ts).
 */

// ── API types (mirror of schemas/login_ip_trade_profit.py) ───────────────

export interface TradeProfitDailyPoint {
  date: string; // YYYY-MM-DD (MT close day)
  profit_usd: number;
}

/** One account group in the list view (one row per union-find component). */
export interface TradeProfitGroupRow {
  group_id: string;
  profit_usd: number;
  trades: number;
  lots: number;
  accounts: number;
  clients: number;
  ibs: number;
  /** true = 一人多户 (1 client, >= 2 accounts). */
  same_client: boolean;
  shared_ips: number;
  active_days: number;
  profitable_days: number;
  dominant_symbol: string;
  dominant_symbol_share: number;
  avg_hold_min: number;
  daily: TradeProfitDailyPoint[];
}

export interface TradeProfitStatistics {
  from_cache: boolean;
  query_time_ms: number;
  window_with_ip_trades: number;
  groups_trades: number;
}

export interface TradeProfitGroupsResponse {
  data: TradeProfitGroupRow[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  statistics: TradeProfitStatistics;
}

export interface TradeProfitGroupAccount {
  account_key: string; // '{server}-{login}', e.g. 'MT5-67043240'
  server: string;
  account_id: number;
  user_id: number | null;
  ib_id: number | null;
  trades: number;
  profit_usd: number;
  lots: number;
  active_days: number;
  avg_hold_min: number;
  dominant_symbol: string;
}

export interface TradeProfitGroupIp {
  ip: string;
  accounts: number; // member accounts seen on this IP in the window
  bridge: boolean; // true = this IP created at least one edge inside the group
  country: string | null; // cache-only geo
  window_clients: number | null;
  window_active_days: number | null;
}

/** The group row as the detail endpoint returns it (adds accounts_detail). */
export type TradeProfitGroupDetail = TradeProfitGroupRow & {
  accounts_detail?: TradeProfitGroupAccount[];
};

export interface TradeProfitGroupDetailResponse {
  group: TradeProfitGroupDetail;
  member_ips: TradeProfitGroupIp[];
  params: Record<string, unknown>;
  statistics: Record<string, unknown>;
}

export interface TradeProfitNoIpCause {
  cause: string;
  trades: number;
  profit_usd: number;
}

export interface TradeProfitCoverageResponse {
  date_from: string;
  date_to: string;
  /** First day with order_ip data (Phase 1 go-live backfill). */
  data_from: string;
  total_trades: number;
  total_profit_usd: number;
  with_ip_trades: number;
  with_ip_profit_usd: number;
  no_ip_trades: number;
  no_ip_profit_usd: number;
  no_ip_by_cause: TradeProfitNoIpCause[];
  /** (date, server) whose journal was never parsed. */
  incomplete_logs: { date: string; server: string }[];
  /** Window days with zero reconciled rows (reconcile never ran for them). */
  unreconciled_dates: string[];
}

// ── Toolbar filters ──────────────────────────────────────────────────────
//
// Persisted via useFilterPersist (viewing preferences). The custom absolute
// range is NOT persisted — it is investigation context (see
// grid-column-persist.md §13): reopening the page on a stale absolute window
// reads as "no new data", which is the footgun the rule exists to prevent.

export type TradeProfitRangePreset = "7d" | "30d" | "90d" | "custom";

export interface TradeProfitFilters extends Record<string, unknown> {
  rangePreset: TradeProfitRangePreset;
}

export const LOGIN_IP_TRADE_PROFIT_FILTERS_KEY =
  "LOGIN_IP_TRADE_PROFIT_FILTERS_V1";

export const TRADE_PROFIT_FILTER_DEFAULTS: TradeProfitFilters = {
  rangePreset: "7d",
};

/**
 * Fixed grouping rule for this tab (the three threshold controls were
 * removed). An IP used by this many distinct CRM clients forms a group;
 * one person with several accounts does not count toward the number.
 */
export const TRADE_PROFIT_IP_MIN_CLIENTS = 5;

/**
 * Backend cap. Sent so a busy IP is not dropped as a "shared exit" before
 * the 5-client rule can see it. No window IP has anywhere near 1000 clients.
 */
export const TRADE_PROFIT_PUBLIC_IP_CLIENTS = 1000;

// ── Window ───────────────────────────────────────────────────────────────

export interface TradeProfitWindow {
  from: string; // YYYY-MM-DD (MT close day)
  to: string;
}

const DAY_MS = 24 * 3600 * 1000;
const HKT_OFFSET_MS = 8 * 3600 * 1000;

/**
 * A Hong Kong calendar date, shifted by `shiftDays` from `now`, formatted
 * YYYY-MM-DD. HKT is a fixed UTC+8 (no DST), so plain arithmetic is exact.
 */
export function hkDayShift(now: Date, shiftDays: number): string {
  const d = new Date(now.getTime() + HKT_OFFSET_MS + shiftDays * DAY_MS);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export const RANGE_PRESET_DAYS: Record<
  Exclude<TradeProfitRangePreset, "custom">,
  number
> = { "7d": 7, "30d": 30, "90d": 90 };

/**
 * Resolve the query window [from, to] over MT close days.
 *
 * `to` is YESTERDAY (HKT), never today: the nightly 08:30 HKT reconcile
 * produces yesterday's close-day rows, so the current day is always empty
 * and including it would only add an unreconciled entry to coverage. A
 * custom range with only `from` picked queries that single day; custom with
 * no range at all falls back to the 7d window (the Select can transiently
 * be "custom" before the picker returns a range).
 */
export function computeTradeProfitWindow(
  preset: TradeProfitRangePreset,
  custom: { from?: string; to?: string } | undefined,
  now: Date,
): TradeProfitWindow {
  if (preset === "custom" && custom?.from) {
    return { from: custom.from, to: custom.to ?? custom.from };
  }
  const days =
    preset === "custom" ? RANGE_PRESET_DAYS["7d"] : RANGE_PRESET_DAYS[preset];
  return { from: hkDayShift(now, -days), to: hkDayShift(now, -1) };
}

// ── Query strings ────────────────────────────────────────────────────────

/**
 * The threshold params shared by the list and detail calls. group_id is a
 * hash of (window, thresholds, account list), so the detail request MUST
 * echo the exact set the list was produced under — one builder for both
 * keeps that echo structural instead of remembered.
 *
 * The three toolbar knobs are gone. Every call sends the fixed rule:
 * ip_min_clients = 5 (that IP connects its accounts), min_clients = 5,
 * include_same_client = false, and public_ip_clients at the cap so those
 * IPs are not excluded first.
 */
export function buildTradeProfitParams(
  window: TradeProfitWindow,
): URLSearchParams {
  return new URLSearchParams({
    from: window.from,
    to: window.to,
    min_clients: String(TRADE_PROFIT_IP_MIN_CLIENTS),
    public_ip_clients: String(TRADE_PROFIT_PUBLIC_IP_CLIENTS),
    include_same_client: "false",
    ip_min_clients: String(TRADE_PROFIT_IP_MIN_CLIENTS),
  });
}

// ── Display helpers ──────────────────────────────────────────────────────

/**
 * The empty state replaces the grid only for a settled, successful, empty
 * result — loading and error each have their own surface, and showing "no
 * clusters" over a failed request would be a lie.
 */
export function shouldShowEmptyState(
  loading: boolean,
  error: string | null,
  rowCount: number,
): boolean {
  return !loading && error === null && rowCount === 0;
}

/** + green / − red / 0|null uncoloured (page-style-conventions §10). */
export function profitColorClass(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "";
  return v > 0
    ? "text-green-600 dark:text-green-400"
    : "text-red-600 dark:text-red-400";
}

export function fmtUsd(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const sign = v < 0 ? "-" : "";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/** Average hold in minutes → "45m" / "5.2h" / "2.3d". */
export function fmtHoldMin(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v < 60) return `${Math.round(v)}m`;
  const hours = v / 60;
  if (hours < 48) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
}
