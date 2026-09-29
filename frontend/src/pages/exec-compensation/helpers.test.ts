import { describe, expect, it } from "vitest";
import {
  apiErrorMessage,
  buildCommonParams,
  clampRange,
  COL_TO_SORT_BY,
  defaultRange,
  filenameFromDisposition,
  fmtUsd,
  fromYmd,
  parseSubject,
  reqTimeSrv,
  signedClass,
  toYmd,
  utcToHk,
} from "./helpers";

describe("parseSubject", () => {
  it("bare number is a CRM client id", () => {
    expect(parseSubject(" 153034 ")).toEqual({
      ok: true,
      subject: { kind: "client_id", value: 153034 },
    });
  });
  it("5-<login> is an MT5 account", () => {
    expect(parseSubject("5-8616169")).toEqual({
      ok: true,
      subject: { kind: "login_sid", value: "5-8616169" },
    });
    // full-width / en dash and spaces are tolerated
    expect(parseSubject("5 – 8616169")).toEqual({
      ok: true,
      subject: { kind: "login_sid", value: "5-8616169" },
    });
  });
  it("rejects non-MT5 servers, empty and garbage", () => {
    expect(parseSubject("1-8522845").ok).toBe(false);
    expect(parseSubject("").ok).toBe(false);
    expect(parseSubject("abc").ok).toBe(false);
    expect(parseSubject("0").ok).toBe(false);
  });
});

describe("buildCommonParams", () => {
  it("sends exactly one subject key and omits as_of when unset", () => {
    const p = buildCommonParams({
      subject: { kind: "client_id", value: 153034 },
      dateFrom: "2026-08-29",
      dateTo: "2026-09-28",
    });
    expect(p.toString()).toBe("client_id=153034&date_from=2026-08-29&date_to=2026-09-28");
  });
  it("pins as_of when given", () => {
    const p = buildCommonParams({
      subject: { kind: "login_sid", value: "5-1" },
      dateFrom: "2026-09-01",
      dateTo: "2026-09-02",
      asOf: "2026-09-28",
    });
    expect(p.get("login_sid")).toBe("5-1");
    expect(p.has("client_id")).toBe(false);
    expect(p.get("as_of")).toBe("2026-09-28");
  });
});

describe("dates", () => {
  it("round-trips YYYY-MM-DD without timezone drift", () => {
    expect(toYmd(fromYmd("2026-03-08"))).toBe("2026-03-08");
    expect(toYmd(fromYmd("2023-02-20"))).toBe("2023-02-20");
  });
  it("default range = 30 days ending at max, clipped to min", () => {
    const r = defaultRange("2026-09-28", "2023-02-20");
    expect(toYmd(r.from)).toBe("2026-08-30");
    expect(toYmd(r.to)).toBe("2026-09-28");
    const c = defaultRange("2023-03-01", "2023-02-20");
    expect(toYmd(c.from)).toBe("2023-02-20");
  });
  it("clampRange cuts to bounds and returns null when empty", () => {
    const min = fromYmd("2023-02-20");
    const max = fromYmd("2026-09-28");
    const r = clampRange(fromYmd("2023-01-01"), fromYmd("2026-09-29"), min, max);
    expect(r && [toYmd(r.from), toYmd(r.to)]).toEqual(["2023-02-20", "2026-09-28"]);
    expect(clampRange(fromYmd("2026-10-01"), fromYmd("2026-10-02"), min, max)).toBeNull();
  });
});

describe("time display", () => {
  it("converts UTC to Hong Kong time, keeping milliseconds", () => {
    expect(utcToHk("2026-09-01T02:03:04.5Z")).toBe("2026-09-01 10:03:04.500");
    expect(utcToHk("2026-09-01T20:00:00Z")).toBe("2026-09-02 04:00:00");
    expect(utcToHk(null)).toBe("—");
  });
  it("derives request MT time from the fill row's server offset", () => {
    // summer: MT = UTC+3
    expect(
      reqTimeSrv("2026-09-01T07:00:00.100Z", "2026-09-01T07:00:00.350Z", "2026-09-01 10:00:00.350"),
    ).toBe("2026-09-01 10:00:00.100");
    // winter: MT = UTC+2, request crosses midnight
    expect(
      reqTimeSrv("2026-12-01T21:59:59.900Z", "2026-12-01T22:00:00.200Z", "2026-12-02 00:00:00.200"),
    ).toBe("2026-12-01 23:59:59.900");
    expect(reqTimeSrv(null, "2026-09-01T07:00:00Z", "2026-09-01 10:00:00")).toBe("—");
  });
});

describe("formatting", () => {
  it("fmtUsd signs and pads", () => {
    expect(fmtUsd(208.2398)).toBe("$208.24");
    expect(fmtUsd(-1234.5)).toBe("-$1,234.50");
    expect(fmtUsd(0.01234, 4)).toBe("$0.0123");
    expect(fmtUsd(null)).toBe("—");
  });
  it("signedClass: green positive, red negative, none for 0/null", () => {
    expect(signedClass(1)).toContain("green");
    expect(signedClass(-1)).toContain("red");
    expect(signedClass(0)).toBe("");
    expect(signedClass(null)).toBe("");
  });
});

describe("apiErrorMessage", () => {
  it("shows the QUERY_TOO_LARGE backend message as-is", () => {
    const msg =
      "本次查询涉及 180,000 笔成交，超过单次上限 150,000 笔。请缩小日期范围；如确需整段数据，请联系 IT 手动处理。";
    const r = apiErrorMessage(422, { error: { code: "QUERY_TOO_LARGE", message: msg } });
    expect(r.code).toBe("QUERY_TOO_LARGE");
    expect(r.message).toBe(msg);
  });
  it("replaces the backend message for UPSTREAM_UNAVAILABLE and QUERY_BUDGET_EXCEEDED", () => {
    const a = apiErrorMessage(503, {
      error: { code: "UPSTREAM_UNAVAILABLE", message: "the MT5 replica is unavailable right now" },
    });
    expect(a).toEqual({ code: "UPSTREAM_UNAVAILABLE", message: "数据库暂时不可用，请稍后再试" });
    const b = apiErrorMessage(503, {
      error: { code: "QUERY_BUDGET_EXCEEDED", message: "query exceeded its 60s time budget" },
    });
    expect(b).toEqual({
      code: "QUERY_BUDGET_EXCEEDED",
      message: "查询超时（系统繁忙），请稍后再试或缩小日期范围",
    });
  });
  it("accepts the envelope nested under FastAPI detail", () => {
    const r = apiErrorMessage(503, { detail: { error: { code: "BUSY", message: "busy" } } });
    expect(r.code).toBe("BUSY");
    expect(r.message).toContain("稍后再试");
  });
  it("falls back to detail string, pydantic array, then status", () => {
    expect(apiErrorMessage(400, { detail: "bad" }).message).toBe("bad");
    expect(apiErrorMessage(422, { detail: [{ msg: "a" }, { msg: "b" }] }).message).toBe("a；b");
    expect(apiErrorMessage(500, null).message).toContain("500");
    expect(apiErrorMessage(503, "<html>").code).toBe("BUSY");
    expect(apiErrorMessage(504, null).message).toContain("缩小日期范围");
  });
});

describe("filenameFromDisposition", () => {
  it("prefers RFC 5987 filename*", () => {
    expect(
      filenameFromDisposition(
        "attachment; filename=\"x.xlsx\"; filename*=UTF-8''%E8%A1%A5%E5%81%BF.xlsx",
        "f.xlsx",
      ),
    ).toBe("补偿.xlsx");
  });
  it("falls back to plain filename then default", () => {
    expect(filenameFromDisposition('attachment; filename="a b.xlsx"', "f")).toBe("a b.xlsx");
    expect(filenameFromDisposition(null, "f.xlsx")).toBe("f.xlsx");
  });
});

describe("COL_TO_SORT_BY", () => {
  it("only maps to the backend sort whitelist", () => {
    const allowed = new Set([
      "fill_time_srv",
      "comp_usd",
      "delay_ms",
      "lots",
      "symbol",
      "login",
      "deal_id",
    ]);
    for (const v of Object.values(COL_TO_SORT_BY)) expect(allowed.has(v)).toBe(true);
    expect(COL_TO_SORT_BY.login_sid).toBe("login");
  });
});
