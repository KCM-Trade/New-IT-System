/**
 * Logic tests for the Login IPs tab model (tabs.ts): the risk-gating matrix,
 * `?tab=` deep-link resolution, and localStorage restore. No DOM — pure
 * functions only (project test convention: component behavior is covered via
 * extracted logic, same pattern as lib/crm-tag-filter.ts).
 */
import { describe, expect, it } from "vitest";
import {
  isLoginIpTab,
  LOGIN_IP_ACTIVE_TAB_KEY,
  LOGIN_IP_BASE_TABS,
  LOGIN_IP_DEFAULT_TAB,
  LOGIN_IP_TRADE_PROFIT_TAB,
  resolveActiveTab,
  resolveRestoredTab,
  visibleLoginIpTabs,
} from "./tabs";

describe("visibleLoginIpTabs (gating matrix)", () => {
  it("risk user sees all five tabs, trade-profit last", () => {
    expect(visibleLoginIpTabs(true)).toEqual([
      "report",
      "watchlist",
      "search",
      "ops",
      "trade-profit",
    ]);
  });

  it("cs-only user sees the four base tabs", () => {
    expect(visibleLoginIpTabs(false)).toEqual(LOGIN_IP_BASE_TABS);
    expect(visibleLoginIpTabs(false)).not.toContain(LOGIN_IP_TRADE_PROFIT_TAB);
  });
});

describe("isLoginIpTab", () => {
  it("accepts known tabs, rejects junk and null", () => {
    expect(isLoginIpTab("report")).toBe(true);
    expect(isLoginIpTab("trade-profit")).toBe(true);
    expect(isLoginIpTab("Trade-Profit")).toBe(false);
    expect(isLoginIpTab("")).toBe(false);
    expect(isLoginIpTab(null)).toBe(false);
  });
});

describe("resolveActiveTab (?tab= deep link)", () => {
  it("passes through a granted tab", () => {
    expect(resolveActiveTab("trade-profit", true)).toBe("trade-profit");
    expect(resolveActiveTab("search", false)).toBe("search");
  });

  it("falls back to the default when the tab is not granted", () => {
    // cs-only user opening ?tab=trade-profit must not render a blank panel.
    expect(resolveActiveTab("trade-profit", false)).toBe(LOGIN_IP_DEFAULT_TAB);
  });

  it("falls back to the default for unknown or missing values", () => {
    expect(resolveActiveTab("nope", true)).toBe("report");
    expect(resolveActiveTab(null, true)).toBe("report");
  });
});

describe("resolveRestoredTab (localStorage restore)", () => {
  it("restores a saved non-default tab the user may see", () => {
    expect(resolveRestoredTab("ops", false)).toBe("ops");
    expect(resolveRestoredTab("trade-profit", true)).toBe("trade-profit");
  });

  it("drops a saved trade-profit tab for a user without the risk grant", () => {
    // Restoring it would 403 every request the tab makes.
    expect(resolveRestoredTab("trade-profit", false)).toBeNull();
  });

  it("ignores junk and the default (nothing to restore)", () => {
    expect(resolveRestoredTab("nope", true)).toBeNull();
    expect(resolveRestoredTab("report", true)).toBeNull();
    expect(resolveRestoredTab(null, true)).toBeNull();
  });
});

describe("LOGIN_IP_ACTIVE_TAB_KEY", () => {
  it("matches the backend view-profile key pattern (snapshots 422 otherwise)", () => {
    expect(LOGIN_IP_ACTIVE_TAB_KEY).toMatch(/^[A-Z0-9_]+_ACTIVE_TAB_V\d+$/);
  });
});
