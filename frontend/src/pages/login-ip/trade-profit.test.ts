/**
 * Logic tests for the trade-IP profit tab helpers (trade-profit.ts): the
 * HKT window math, the list/detail query-param echo (group_id is a hash of
 * the params, so both calls MUST be built by one builder), and the display
 * helpers. No DOM — pure functions only (project convention, same pattern
 * as lib/crm-tag-filter.ts).
 */
import { describe, expect, it } from "vitest";
import {
  buildTradeProfitParams,
  computeTradeProfitWindow,
  fmtHoldMin,
  fmtUsd,
  hkDayShift,
  LOGIN_IP_TRADE_PROFIT_FILTERS_KEY,
  profitColorClass,
  shouldShowEmptyState,
  TRADE_PROFIT_IP_MIN_CLIENTS,
  TRADE_PROFIT_PUBLIC_IP_CLIENTS,
} from "./trade-profit";

// 2026-09-23 10:00 HKT = 2026-09-23 02:00 UTC.
const NOW = new Date("2026-09-23T02:00:00Z");

describe("hkDayShift (HKT calendar math)", () => {
  it("yesterday in HKT, plain case", () => {
    expect(hkDayShift(NOW, -1)).toBe("2026-09-22");
  });

  it("uses the HKT date, not the UTC date, near midnight", () => {
    // 2026-09-22 17:00 UTC = 2026-09-23 01:00 HKT → yesterday is the 22nd.
    // Naive UTC arithmetic would answer the 21st here.
    expect(hkDayShift(new Date("2026-09-22T17:00:00Z"), -1)).toBe("2026-09-22");
  });

  it("crosses month and year boundaries", () => {
    // 2026-01-01 00:30 HKT = 2025-12-31 16:30 UTC → yesterday is 2025-12-31.
    expect(hkDayShift(new Date("2025-12-31T16:30:00Z"), -1)).toBe("2025-12-31");
    // 2026-03-01 00:30 HKT → yesterday is 2026-02-28 (not a leap year).
    expect(hkDayShift(new Date("2026-02-28T16:30:00Z"), -1)).toBe("2026-02-28");
  });
});

describe("computeTradeProfitWindow", () => {
  it("7d preset ends yesterday (HKT) and spans 7 close days", () => {
    expect(computeTradeProfitWindow("7d", undefined, NOW)).toEqual({
      from: "2026-09-16",
      to: "2026-09-22",
    });
  });

  it("30d / 90d presets", () => {
    expect(computeTradeProfitWindow("30d", undefined, NOW)).toEqual({
      from: "2026-08-24",
      to: "2026-09-22",
    });
    expect(computeTradeProfitWindow("90d", undefined, NOW)).toEqual({
      from: "2026-06-25",
      to: "2026-09-22",
    });
  });

  it("custom range echoes the picked dates", () => {
    expect(
      computeTradeProfitWindow(
        "custom",
        { from: "2026-09-01", to: "2026-09-10" },
        NOW,
      ),
    ).toEqual({ from: "2026-09-01", to: "2026-09-10" });
  });

  it("custom with only `from` picked queries that single day", () => {
    expect(
      computeTradeProfitWindow("custom", { from: "2026-09-10" }, NOW),
    ).toEqual({ from: "2026-09-10", to: "2026-09-10" });
  });

  it("custom with no range yet falls back to the 7d window", () => {
    // The Select can transiently be "custom" before the picker returns.
    expect(computeTradeProfitWindow("custom", undefined, NOW)).toEqual(
      computeTradeProfitWindow("7d", undefined, NOW),
    );
  });
});

describe("buildTradeProfitParams (list/detail echo)", () => {
  const window = { from: "2026-09-16", to: "2026-09-22" };

  it("sends the fixed 5-client rule, not the removed toolbar knobs", () => {
    const p = buildTradeProfitParams(window);
    expect(p.get("from")).toBe("2026-09-16");
    expect(p.get("to")).toBe("2026-09-22");
    expect(p.get("min_clients")).toBe(String(TRADE_PROFIT_IP_MIN_CLIENTS));
    expect(p.get("ip_min_clients")).toBe(String(TRADE_PROFIT_IP_MIN_CLIENTS));
    expect(p.get("public_ip_clients")).toBe(String(TRADE_PROFIT_PUBLIC_IP_CLIENTS));
    expect(p.get("include_same_client")).toBe("false");
  });

  it("produces byte-identical strings for identical inputs (the echo contract)", () => {
    expect(buildTradeProfitParams(window).toString()).toBe(
      buildTradeProfitParams(window).toString(),
    );
  });
});

describe("shouldShowEmptyState", () => {
  it("only for a settled, successful, empty result", () => {
    expect(shouldShowEmptyState(false, null, 0)).toBe(true);
    expect(shouldShowEmptyState(true, null, 0)).toBe(false); // still loading
    expect(shouldShowEmptyState(false, "boom", 0)).toBe(false); // error surface
    expect(shouldShowEmptyState(false, null, 3)).toBe(false); // has rows
  });
});

describe("display helpers", () => {
  it("fmtUsd: sign, thousands, dash for null", () => {
    expect(fmtUsd(3385.3)).toBe("$3,385.30");
    expect(fmtUsd(-12.5)).toBe("-$12.50");
    expect(fmtUsd(0)).toBe("$0.00");
    expect(fmtUsd(null)).toBe("—");
  });

  it("fmtHoldMin: m → h → d", () => {
    expect(fmtHoldMin(45)).toBe("45m");
    expect(fmtHoldMin(312)).toBe("5.2h");
    expect(fmtHoldMin(3600)).toBe("2.5d");
    expect(fmtHoldMin(null)).toBe("—");
  });

  it("profitColorClass: + green / − red / 0 & null uncoloured", () => {
    expect(profitColorClass(1)).toContain("green");
    expect(profitColorClass(-1)).toContain("red");
    expect(profitColorClass(0)).toBe("");
    expect(profitColorClass(null)).toBe("");
  });
});

describe("LOGIN_IP_TRADE_PROFIT_FILTERS_KEY", () => {
  it("matches the backend view-profile key pattern (snapshots 422 otherwise)", () => {
    expect(LOGIN_IP_TRADE_PROFIT_FILTERS_KEY).toMatch(/^[A-Z0-9_]+_FILTERS_V\d+$/);
  });
});
