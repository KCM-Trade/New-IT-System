/**
 * Pure helpers for the fund risk-control ledger page: form <-> payload
 * serialisation, filter -> query string, and small parsers. No React and no
 * DOM in this file so vitest covers it without a browser environment.
 */
import {
  FREEZE_DAYS_MAX,
  type Authority,
  type ClientSource,
  type FrozenScope,
  type Incident,
  type IncidentConflict,
  type IncidentCreate,
  type IncidentFields,
  type IncidentUpdate,
  type SortKey,
  type SortOrder,
  type TransferSucceeded,
} from "./types";

/** Route prefix of the ledger API (docs/features/fund-risk-ledger.md §4). */
export const API_BASE = "/api/v1/cs/fund-risk";

export function isAbortError(e: unknown): boolean {
  return (e instanceof DOMException || e instanceof Error) && e.name === "AbortError";
}

// ── dates ───────────────────────────────────────────────────────────────────

/** Local calendar date as YYYY-MM-DD (the format of `reported_date`). */
export function toYmd(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Parse YYYY-MM-DD into a local Date, or undefined when malformed. */
export function fromYmd(ymd: string | null | undefined): Date | undefined {
  if (!ymd) return undefined;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ymd);
  if (!m) return undefined;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isNaN(d.getTime()) ? undefined : d;
}

// ── list filters ────────────────────────────────────────────────────────────

export const DATE_PRESETS = ["7d", "30d", "90d", "all"] as const;
export type DatePreset = (typeof DATE_PRESETS)[number];

export const DATE_PRESET_LABELS: Record<DatePreset, string> = {
  "7d": "近 7 天",
  "30d": "近 30 天",
  "90d": "近 90 天",
  all: "全部日期",
};

const PRESET_DAYS: Record<DatePreset, number | null> = {
  "7d": 7,
  "30d": 30,
  "90d": 90,
  all: null,
};

export const ALL = "all";

/**
 * View preferences that are persisted (useFilterPersist). Must be a `type`
 * alias, not an interface: interfaces have no implicit index signature and
 * fail the `Record<string, unknown>` constraint of the persist hook.
 */
export type LedgerViewFilters = {
  datePreset: DatePreset;
  channel: string; // "all" | channel label
  direction: string; // "all" | Direction
  authority: string; // "all" | Authority
  pendingOnly: boolean;
};

export const VIEW_FILTER_DEFAULTS: LedgerViewFilters = {
  datePreset: "90d",
  channel: ALL,
  direction: ALL,
  authority: ALL,
  pendingOnly: false,
};

export type DateRange = { from: string; to: string };

/** Everything that shapes one list / export request. */
export type LedgerQuery = LedgerViewFilters & {
  /** Absolute range; wins over `datePreset`. Never persisted. */
  customRange: DateRange | null;
  /** Committed search text (transaction id or client id). Never persisted. */
  search: string;
};

/** Inclusive `reported_date` range of a preset; null = no date filter. */
export function presetRange(preset: DatePreset, today: Date): DateRange | null {
  const days = PRESET_DAYS[preset];
  if (days === null || days === undefined) return null;
  const from = new Date(today.getFullYear(), today.getMonth(), today.getDate() - (days - 1));
  return { from: toYmd(from), to: toYmd(today) };
}

/**
 * The backend matches `q` exactly against a transaction id or a client id, so
 * only digits are meaningful. Returns the digits, "" for an empty box, or
 * null when the text cannot be a valid id.
 */
export function normalizeSearch(text: string): string | null {
  const t = text.trim();
  if (t === "") return "";
  return /^\d{1,18}$/.test(t) ? t : null;
}

/** Filter params shared by GET /incidents and GET /export. */
export function buildFilterParams(query: LedgerQuery, today: Date): URLSearchParams {
  const params = new URLSearchParams();
  const range = query.customRange ?? presetRange(query.datePreset, today);
  if (range) {
    params.set("date_from", range.from);
    params.set("date_to", range.to);
  }
  if (query.channel !== ALL) params.set("channel", query.channel);
  if (query.direction !== ALL) params.set("direction", query.direction);
  if (query.authority !== ALL) params.set("authority", query.authority);
  if (query.pendingOnly) params.set("pending_result", "true");
  const q = normalizeSearch(query.search);
  if (q) params.set("q", q);
  return params;
}

export type ListPaging = {
  page: number;
  pageSize: number;
  sort: SortKey;
  order: SortOrder;
};

export const DEFAULT_SORT: SortKey = "reported_date";
export const DEFAULT_ORDER: SortOrder = "desc";
export const PAGE_SIZE = 50;

/** Query string (no leading "?") of one GET /incidents request. */
export function buildListQuery(query: LedgerQuery, paging: ListPaging, today: Date): string {
  const params = buildFilterParams(query, today);
  params.set("page", String(paging.page));
  params.set("page_size", String(paging.pageSize));
  params.set("sort", paging.sort);
  params.set("order", paging.order);
  return params.toString();
}

/** Grid colId -> server sort key. Columns not listed are not sortable. */
export const COL_TO_SORT: Readonly<Record<string, SortKey>> = {
  reported_date: "reported_date",
  txn_processed_at: "txn_processed_at",
  id: "id",
};

// ── transaction id input ────────────────────────────────────────────────────

/** Positive integer transaction id, or null when the text is not one. */
export function parseTransactionId(text: string): number | null {
  const t = text.trim();
  if (!/^\d{1,15}$/.test(t)) return null;
  const n = Number(t);
  return n > 0 ? n : null;
}

// ── form state ──────────────────────────────────────────────────────────────

/**
 * Editable form state. Text inputs hold strings ("" = empty); the payload
 * builder turns empty strings into null. Must stay a flat object of
 * primitives — dirty detection compares it field by field.
 */
export type LedgerForm = {
  reportedDate: string; // YYYY-MM-DD or ""
  transferSucceeded: TransferSucceeded;
  frozenScope: FrozenScope;
  freezeDaysText: string;
  freezeDaysUnknown: boolean;
  freezeNote: string;
  authority: Authority;
  clientSource: ClientSource | "";
  clientBankProvince: string;
  clientBankCity: string;
  traderBankProvince: string;
  traderBankCity: string;
  idProvince: string;
  idCity: string;
  ipRegion: string;
  pwaDomain: string;
  accountType: string;
  venueOverride: string;
  eventText: string;
  resultText: string;
};

/**
 * Default for 是否成功转账: an approved order went through, anything else
 * (fresh / declined / ...) did not. Staff can still override it.
 */
export function defaultTransferSucceeded(
  txnStatus: string | null | undefined,
): TransferSucceeded {
  return (txnStatus ?? "").trim().toLowerCase() === "approved" ? "yes" : "no";
}

/** Blank form for a new record. `txnStatus` seeds 是否成功转账. */
export function emptyForm(today: Date, txnStatus?: string | null): LedgerForm {
  return {
    reportedDate: toYmd(today),
    transferSucceeded:
      txnStatus === undefined ? "unknown" : defaultTransferSucceeded(txnStatus),
    frozenScope: "unknown",
    freezeDaysText: "",
    freezeDaysUnknown: true,
    freezeNote: "",
    authority: "unknown",
    clientSource: "",
    clientBankProvince: "",
    clientBankCity: "",
    traderBankProvince: "",
    traderBankCity: "",
    idProvince: "",
    idCity: "",
    ipRegion: "",
    pwaDomain: "",
    accountType: "",
    venueOverride: "",
    eventText: "",
    resultText: "",
  };
}

/** Load a stored record into the form. */
export function incidentToForm(row: IncidentFields): LedgerForm {
  return {
    reportedDate: row.reported_date ?? "",
    transferSucceeded: row.transfer_succeeded,
    frozenScope: row.frozen_scope,
    freezeDaysText: row.freeze_days === null ? "" : String(row.freeze_days),
    freezeDaysUnknown: row.freeze_days === null,
    freezeNote: row.freeze_note ?? "",
    authority: row.authority,
    clientSource: row.client_source ?? "",
    clientBankProvince: row.client_bank_province ?? "",
    clientBankCity: row.client_bank_city ?? "",
    traderBankProvince: row.trader_bank_province ?? "",
    traderBankCity: row.trader_bank_city ?? "",
    idProvince: row.id_province ?? "",
    idCity: row.id_city ?? "",
    ipRegion: row.ip_region ?? "",
    pwaDomain: row.pwa_domain ?? "",
    accountType: row.account_type ?? "",
    venueOverride: row.venue_override ?? "",
    eventText: row.event_text ?? "",
    resultText: row.result_text ?? "",
  };
}

/**
 * Form for the next order of the same incident: the event description, the
 * findings, the authority and the reported date describe the incident and are
 * carried over; everything that belongs to one order is reset.
 */
export function carryOverForm(
  prev: LedgerForm,
  today: Date,
  txnStatus?: string | null,
): LedgerForm {
  return {
    ...emptyForm(today, txnStatus),
    reportedDate: prev.reportedDate || toYmd(today),
    authority: prev.authority,
    eventText: prev.eventText,
    resultText: prev.resultText,
  };
}

/** True when any field differs between the two form states. */
export function isFormDirty(current: LedgerForm, baseline: LedgerForm): boolean {
  return (Object.keys(baseline) as (keyof LedgerForm)[]).some(
    (k) => current[k] !== baseline[k],
  );
}

export type FreezeDaysResult =
  | { ok: true; value: number | null }
  | { ok: false; error: string };

/**
 * 封卡天数: the 「尚不知」 checkbox or an empty box both mean unknown (null);
 * otherwise a whole number of days within the backend's range.
 */
export function parseFreezeDays(text: string, unknown: boolean): FreezeDaysResult {
  if (unknown) return { ok: true, value: null };
  const t = text.trim();
  if (t === "") return { ok: true, value: null };
  if (!/^\d+$/.test(t)) {
    return { ok: false, error: "封卡天数必须是不小于 0 的整数" };
  }
  const n = Number(t);
  if (n > FREEZE_DAYS_MAX) {
    return { ok: false, error: `封卡天数不能超过 ${FREEZE_DAYS_MAX}` };
  }
  return { ok: true, value: n };
}

function textOrNull(s: string): string | null {
  const t = s.trim();
  return t === "" ? null : t;
}

/** Form -> IncidentFields. Throws an Error with a user-facing message. */
export function buildFields(form: LedgerForm): IncidentFields {
  const days = parseFreezeDays(form.freezeDaysText, form.freezeDaysUnknown);
  if (!days.ok) throw new Error(days.error);
  if (form.reportedDate !== "" && !fromYmd(form.reportedDate)) {
    throw new Error("登记日期格式不正确");
  }
  return {
    reported_date: form.reportedDate === "" ? null : form.reportedDate,
    transfer_succeeded: form.transferSucceeded,
    frozen_scope: form.frozenScope,
    freeze_days: days.value,
    freeze_note: textOrNull(form.freezeNote),
    authority: form.authority,
    client_source: form.clientSource === "" ? null : form.clientSource,
    client_bank_province: textOrNull(form.clientBankProvince),
    client_bank_city: textOrNull(form.clientBankCity),
    trader_bank_province: textOrNull(form.traderBankProvince),
    trader_bank_city: textOrNull(form.traderBankCity),
    id_province: textOrNull(form.idProvince),
    id_city: textOrNull(form.idCity),
    ip_region: textOrNull(form.ipRegion),
    pwa_domain: textOrNull(form.pwaDomain),
    account_type: textOrNull(form.accountType),
    // Venues are upper-cased by the backend when read from the CRM; keep the
    // manual correction in the same form so the two compare equal.
    venue_override: textOrNull(form.venueOverride)?.toUpperCase() ?? null,
    event_text: textOrNull(form.eventText),
    result_text: textOrNull(form.resultText),
  };
}

export function buildCreatePayload(
  form: LedgerForm,
  transactionId: number,
  incidentGroupId: number | null,
): IncidentCreate {
  return {
    ...buildFields(form),
    transaction_id: transactionId,
    incident_group_id: incidentGroupId,
  };
}

export function buildUpdatePayload(
  form: LedgerForm,
  version: number,
  applyToGroup: boolean,
): IncidentUpdate {
  return {
    ...buildFields(form),
    version,
    apply_to_group: applyToGroup,
  };
}

// ── responses ───────────────────────────────────────────────────────────────

/**
 * Read a 409 body. The contract puts `existing_id` / `current_version` at the
 * top level; a FastAPI HTTPException would nest the same object under
 * `detail`, so both shapes are accepted.
 */
export function parseConflict(body: unknown): IncidentConflict {
  const empty: IncidentConflict = {
    detail: "",
    existing_id: null,
    current_version: null,
  };
  if (typeof body !== "object" || body === null) return empty;
  const top = body as Record<string, unknown>;
  const nested =
    typeof top.detail === "object" && top.detail !== null
      ? (top.detail as Record<string, unknown>)
      : null;
  const src = nested ?? top;
  const num = (v: unknown): number | null =>
    typeof v === "number" && Number.isFinite(v) ? v : null;
  return {
    detail: typeof src.detail === "string" ? src.detail : "",
    existing_id: num(src.existing_id),
    current_version: num(src.current_version),
  };
}

// ── display ─────────────────────────────────────────────────────────────────

/** 场地 shown to staff: the manual correction wins over the CRM tag. */
export function effectiveVenue(
  row: Pick<Incident, "venue_override" | "order">,
): string | null {
  return row.venue_override ?? row.order?.venue ?? null;
}

export function fmtAmount(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** 省 + 市 in one cell. */
export function fmtRegion(
  province: string | null | undefined,
  city: string | null | undefined,
): string {
  const parts = [province, city].filter((s): s is string => Boolean(s && s.trim()));
  return parts.length ? parts.join(" ") : "—";
}

/** 封卡天数 cell: days, else the free-text note, else unknown. */
export function fmtFreeze(
  days: number | null | undefined,
  note: string | null | undefined,
): string {
  if (days !== null && days !== undefined) {
    return note ? `${days} 天（${note}）` : `${days} 天`;
  }
  return note ? note : "尚不知";
}
