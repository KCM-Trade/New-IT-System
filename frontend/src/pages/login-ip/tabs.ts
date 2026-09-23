/**
 * Login IP Monitor — tab identity + deep-link resolution (OPT-0063 Phase 3).
 *
 * The page's Tabs are URL-controlled (`?tab=`, same pattern as RiskMonitor /
 * WindowScan): a deep link always wins over the persisted last-active tab,
 * and a tab the user may not see (trade-profit is risk-only inside a cs
 * page) resolves to the default tab instead of rendering an empty panel.
 *
 * Kept free of React so vitest covers the gating matrix without a DOM
 * environment (same pattern as lib/crm-tag-filter.ts).
 */

export const LOGIN_IP_BASE_TABS = ["report", "watchlist", "search", "ops"] as const;
export type LoginIpBaseTab = (typeof LOGIN_IP_BASE_TABS)[number];

/** OPT-0063: trade-IP profit attribution. risk-only inside the cs page. */
export const LOGIN_IP_TRADE_PROFIT_TAB = "trade-profit" as const;

export type LoginIpTab = LoginIpBaseTab | typeof LOGIN_IP_TRADE_PROFIT_TAB;

export const LOGIN_IP_DEFAULT_TAB: LoginIpTab = "report";

/**
 * localStorage key for the last-active tab. URL `?tab=` still wins — this
 * only fires when the page is opened without a tab param, so deep links
 * (chat, bookmarks) keep working. Hand-listed in view-profiles/manifest.ts
 * UI_STATE_KEYS; the name must keep matching the backend's profile-key
 * pattern `^[A-Z0-9_]+_ACTIVE_TAB_V\d+$` or snapshots 422.
 */
export const LOGIN_IP_ACTIVE_TAB_KEY = "LOGIN_IP_ACTIVE_TAB_V1";

export function isLoginIpTab(v: string | null): v is LoginIpTab {
  return (
    v !== null &&
    ((LOGIN_IP_BASE_TABS as readonly string[]).includes(v) ||
      v === LOGIN_IP_TRADE_PROFIT_TAB)
  );
}

/** The tabs this user may see, in display order. */
export function visibleLoginIpTabs(
  canSeeTradeProfit: boolean,
): readonly LoginIpTab[] {
  return canSeeTradeProfit
    ? [...LOGIN_IP_BASE_TABS, LOGIN_IP_TRADE_PROFIT_TAB]
    : LOGIN_IP_BASE_TABS;
}

function isVisible(tab: LoginIpTab, canSeeTradeProfit: boolean): boolean {
  return tab !== LOGIN_IP_TRADE_PROFIT_TAB || canSeeTradeProfit;
}

/**
 * Which tab to render for the current `?tab=` value. An unknown value AND a
 * known-but-not-granted value both land on the default: from the user's
 * point of view there is no difference between "no such tab" and "no such
 * tab for you", and neither should render a blank panel.
 */
export function resolveActiveTab(
  tabParam: string | null,
  canSeeTradeProfit: boolean,
): LoginIpTab {
  if (isLoginIpTab(tabParam) && isVisible(tabParam, canSeeTradeProfit)) {
    return tabParam;
  }
  return LOGIN_IP_DEFAULT_TAB;
}

/**
 * Which tab to restore from localStorage when the URL is silent, or null to
 * stay on the default. A saved trade-profit tab is dropped for a user
 * without the risk grant — restoring it would 403 every request it makes.
 */
export function resolveRestoredTab(
  saved: string | null,
  canSeeTradeProfit: boolean,
): LoginIpTab | null {
  if (!isLoginIpTab(saved)) return null;
  if (!isVisible(saved, canSeeTradeProfit)) return null;
  if (saved === LOGIN_IP_DEFAULT_TAB) return null;
  return saved;
}
