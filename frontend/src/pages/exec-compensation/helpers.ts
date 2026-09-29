/**
 * Pure helpers for the execution-compensation page (OPT-0068).
 * Contract: backend/app/schemas/exec_compensation.py (frozen).
 * Kept free of React so vitest can cover them without jsdom.
 */

import type { ErrorCode, SortBy } from "./types";

// ── subject (client id OR MT5 account) ─────────────────────────────────────

export type Subject =
  | { kind: "client_id"; value: number }
  | { kind: "login_sid"; value: string };

export type SubjectParse = { ok: true; subject: Subject } | { ok: false; message: string };

/**
 * One input box accepts either a CRM client id (`153034`) or an MT5 account
 * loginSid (`5-8616169`). A bare number is always a client id — an MT5 login
 * must carry the `5-` prefix, otherwise the two id spaces are ambiguous.
 */
export function parseSubject(raw: string): SubjectParse {
  const s = raw.replace(/\s+/g, "").replace(/[－—–]/g, "-");
  if (!s) return { ok: false, message: "请输入客户 ID 或 MT5 账户（5-<login>）" };
  if (/^\d+$/.test(s)) {
    const n = Number(s);
    if (!Number.isSafeInteger(n) || n <= 0) {
      return { ok: false, message: "客户 ID 格式不正确" };
    }
    return { ok: true, subject: { kind: "client_id", value: n } };
  }
  const m = /^(\d+)-(\d+)$/.exec(s);
  if (m) {
    if (m[1] !== "5") {
      return {
        ok: false,
        message: `只支持 MT5 账户（5-<login>），${s} 不是 MT5 账户`,
      };
    }
    return { ok: true, subject: { kind: "login_sid", value: `5-${Number(m[2])}` } };
  }
  return {
    ok: false,
    message: "格式不正确：请输入客户 ID（纯数字）或 MT5 账户（5-<login>）",
  };
}

export function subjectLabel(subject: Subject): string {
  return subject.kind === "client_id"
    ? `客户 ${subject.value}`
    : `账户 ${subject.value}`;
}

// ── query params ────────────────────────────────────────────────────────────

export interface CommonQuery {
  subject: Subject;
  dateFrom: string; // YYYY-MM-DD, MT server day, inclusive
  dateTo: string;
  /** Omit on the first call (server defaults to yesterday); pinned afterwards
   *  to the summary's echoed as_of so orders / export match the summary. */
  asOf?: string;
}

export function buildCommonParams(q: CommonQuery): URLSearchParams {
  const p = new URLSearchParams();
  if (q.subject.kind === "client_id") p.set("client_id", String(q.subject.value));
  else p.set("login_sid", q.subject.value);
  p.set("date_from", q.dateFrom);
  p.set("date_to", q.dateTo);
  if (q.asOf) p.set("as_of", q.asOf);
  return p;
}

// ── dates (calendar dates, local-midnight Date objects) ─────────────────────

/** Local calendar date → `YYYY-MM-DD` (no timezone shift). */
export function toYmd(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** `YYYY-MM-DD` → local-midnight Date (what react-day-picker compares). */
export function fromYmd(s: string): Date {
  const [y, m, d] = s.slice(0, 10).split("-").map(Number);
  return new Date(y, (m ?? 1) - 1, d ?? 1);
}

export function addDays(d: Date, n: number): Date {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  x.setDate(x.getDate() + n);
  return x;
}

/** Default range: the last 30 days ending on `maxYmd`, clipped to `minYmd`. */
export function defaultRange(maxYmd: string, minYmd: string): { from: Date; to: Date } {
  const to = fromYmd(maxYmd);
  let from = addDays(to, -29);
  const min = fromYmd(minYmd);
  if (from < min) from = min;
  return { from, to };
}

/** Clamp a picked range into [min, max]; `null` when nothing is left. */
export function clampRange(
  from: Date,
  to: Date,
  min: Date,
  max: Date,
): { from: Date; to: Date } | null {
  const f = from < min ? min : from;
  const t = to > max ? max : to;
  return f <= t ? { from: f, to: t } : null;
}

// ── time display ────────────────────────────────────────────────────────────

const HK_FMT = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Hong_Kong",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function parseUtc(iso: string): number {
  // Accept "...Z", "...+00:00" and zone-less (treated as UTC).
  const s = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`;
  return Date.parse(s.replace(" ", "T"));
}

function msSuffix(iso: string): string {
  const m = /\.(\d{1,3})/.exec(iso);
  return m ? `.${m[1].padEnd(3, "0")}` : "";
}

/** UTC ISO → `YYYY-MM-DD HH:MM:SS(.fff)` in Asia/Hong_Kong. */
export function utcToHk(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = parseUtc(iso);
  if (Number.isNaN(t)) return "—";
  const parts = Object.fromEntries(
    HK_FMT.formatToParts(new Date(t)).map((p) => [p.type, p.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}${msSuffix(iso)}`;
}

/**
 * The API gives the fill time both as UTC and as MT server wall clock, but the
 * request time only as UTC. The server offset for that row (+2h / +3h, DST) is
 * the whole-hour difference between the two fill fields; applying it to the
 * request time gives its MT wall clock without a client-side DST table.
 */
export function reqTimeSrv(
  reqUtc: string | null | undefined,
  fillUtc: string,
  fillSrv: string,
): string {
  if (!reqUtc) return "—";
  const fu = parseUtc(fillUtc);
  const fs = parseUtc(fillSrv); // wall clock parsed "as if UTC"
  const ru = parseUtc(reqUtc);
  if ([fu, fs, ru].some(Number.isNaN)) return "—";
  const offsetMs = Math.round((fs - fu) / 3_600_000) * 3_600_000;
  const d = new Date(ru + offsetMs);
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return (
    `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ` +
    `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}` +
    msSuffix(reqUtc)
  );
}

// ── number formatting ───────────────────────────────────────────────────────

export function fmtUsd(v: number | null | undefined, maxFrac = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v < 0 ? "-" : "";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: Math.max(2, maxFrac),
  })}`;
}

export function fmtInt(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-US");
}

export function fmtLots(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Prices: keep the instrument's own precision (up to 6 dp), no padding. */
export function fmtPrice(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: 6 });
}

export function fmtMs(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${Math.round(v).toLocaleString("en-US")} ms`;
}

/** page-style-conventions §10: > 0 green, < 0 red, 0 / null uncoloured. */
export function signedClass(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0 || Number.isNaN(v)) return "";
  return v > 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400";
}

// ── errors ──────────────────────────────────────────────────────────────────

const CODE_HINT: Partial<Record<ErrorCode, string>> = {
  QUERY_TOO_LARGE: "请缩小日期范围，或改查单个 MT5 账户后再试。",
  BUSY: "查询繁忙，请稍后再试。",
  UPSTREAM_TIMEOUT: "数据库查询超时，请缩小日期范围后再试。",
};

/**
 * Error bodies: the contract shape `{"error":{"code","message"}}` (possibly
 * wrapped in FastAPI's `detail`), a plain `detail` string, or a Pydantic 422
 * array. Always returns something readable.
 */
export function apiErrorMessage(status: number, body: unknown): { code: string | null; message: string } {
  const pick = (b: unknown): { code?: unknown; message?: unknown } | null => {
    if (!b || typeof b !== "object") return null;
    const o = b as Record<string, unknown>;
    if (o.error && typeof o.error === "object") return o.error as Record<string, unknown>;
    if (o.detail && typeof o.detail === "object" && !Array.isArray(o.detail)) return pick(o.detail);
    return null;
  };
  const err = pick(body);
  if (err && typeof err.message === "string") {
    const code = typeof err.code === "string" ? err.code : null;
    const hint = code ? CODE_HINT[code as ErrorCode] : undefined;
    return { code, message: hint ? `${err.message}（${hint}）` : err.message };
  }
  const detail = body && typeof body === "object" ? (body as { detail?: unknown }).detail : undefined;
  if (typeof detail === "string" && detail.trim()) return { code: null, message: detail };
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d && typeof d === "object" ? String((d as { msg?: string }).msg ?? "") : ""))
      .filter(Boolean);
    if (msgs.length) return { code: "VALIDATION_ERROR", message: msgs.join("；") };
  }
  // Body unreadable (e.g. an nginx error page): map the contract's statuses.
  if (status === 503) return { code: "BUSY", message: "查询繁忙，请稍后再试。" };
  if (status === 504) return { code: "UPSTREAM_TIMEOUT", message: "数据库查询超时，请缩小日期范围后再试。" };
  return { code: null, message: `请求失败 (HTTP ${status})` };
}

export function isAbortError(e: unknown): boolean {
  return (
    (e instanceof DOMException && e.name === "AbortError") ||
    (e instanceof Error && e.name === "AbortError")
  );
}

export function isTimeoutError(e: unknown): boolean {
  return (e instanceof DOMException || e instanceof Error) && e.name === "TimeoutError";
}

// ── export filename ─────────────────────────────────────────────────────────

export function filenameFromDisposition(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (utf8?.[1]) {
    try {
      return decodeURIComponent(utf8[1].trim());
    } catch {
      return utf8[1].trim();
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain?.[1]?.trim() || fallback;
}

// ── server-side sort ────────────────────────────────────────────────────────

/** Grid colId → `sort_by` whitelist value. Columns not listed are unsortable. */
export const COL_TO_SORT_BY: Readonly<Record<string, SortBy>> = {
  fill_time_srv: "fill_time_srv",
  comp_usd: "comp_usd",
  delay_ms: "delay_ms",
  lots: "lots",
  symbol: "symbol",
  login_sid: "login",
  deal_id: "deal_id",
};
