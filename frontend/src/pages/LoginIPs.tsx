/**
 * Login IP Monitor — main page.
 *
 * Tabs:
 *   1. 日报告      — daily correlation report (read-only)
 *   2. 监控账户    — watchlist CRUD
 *   3. 手动搜索    — on-demand search + async CSV export
 *   4. 运维        — scheduler timeline + mail recipients
 *   5. 交易 IP 盈亏 — trade-IP profit attribution (OPT-0063). The page stays a
 *      `cs` module page; THIS tab alone is gated to the `risk` module.
 *
 * The tabs are URL-controlled (`?tab=`, same pattern as RiskMonitor /
 * WindowScan): a deep link always wins over the persisted last-active tab
 * (LOGIN_IP_ACTIVE_TAB_V1), and a tab the user may not see resolves to the
 * default instead of rendering an empty panel. `?tab=search&q=<ip>` pre-fills
 * the manual search — that is where the trade-profit tab's IP drill-down
 * lands.
 *
 * Each tab is a self-contained component in ./login-ip/. This file is just
 * the shell — routing, sidebar, i18n are already wired up in App.tsx /
 * app-sidebar.tsx / site-header.tsx for the existing `/login-ips` route.
 */

import { useCallback, useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  IconReport,
  IconUsers,
  IconSearch,
  IconSettings,
  IconChartLine,
} from "@tabler/icons-react";

import { ReportTab } from "./login-ip/ReportTab";
import { WatchlistTab } from "./login-ip/WatchlistTab";
import { SearchTab } from "./login-ip/SearchTab";
import { OperationsTab } from "./login-ip/OperationsTab";
import { TradeProfitTab } from "./login-ip/TradeProfitTab";
import {
  isLoginIpTab,
  resolveActiveTab,
  resolveRestoredTab,
  LOGIN_IP_ACTIVE_TAB_KEY,
  LOGIN_IP_DEFAULT_TAB,
  LOGIN_IP_TRADE_PROFIT_TAB,
} from "./login-ip/tabs";
import { useI18n } from "@/components/i18n-provider";
import { useAuth } from "@/providers/auth-provider";
import { ALL_MODULES, hasModule, type ModuleAccess } from "@/lib/modules";
import { cn } from "@/lib/utils";

export default function LoginIPsPage() {
  const { t } = useI18n();
  const { user, authEnabled } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  // Same ModuleAccess shape the sidebar and ModuleRoute build. The
  // null-user fallback stays permissive: PrivateRoute has already resolved
  // /auth/me before this page renders, so a null user here is a mid-session
  // refresh, not an anonymous visitor.
  const access = useMemo<ModuleAccess>(
    () => ({
      authEnabled,
      isManager: !authEnabled || user?.role === "manager",
      allowedModules: user ? user.allowedModules : [ALL_MODULES],
    }),
    [authEnabled, user],
  );
  // The page is cs; the trade-profit tab alone is gated to risk (OPT-0063).
  const canSeeTradeProfit = hasModule(access, "risk");

  const tabParam = searchParams.get("tab");
  const activeTab = resolveActiveTab(tabParam, canSeeTradeProfit);

  // Drop unknown / not-granted ?tab= values so the address bar matches what
  // is rendered — a deep link to trade-profit without the risk grant falls
  // back to the default tab rather than rendering an empty panel.
  useEffect(() => {
    if (tabParam !== null && tabParam !== activeTab) {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.delete("tab");
          return next;
        },
        { replace: true },
      );
    }
  }, [tabParam, activeTab, setSearchParams]);

  // Restore the last-active tab only when the URL is silent — a deep link
  // must never be overridden by a stale local preference.
  useEffect(() => {
    if (tabParam !== null) return;
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(LOGIN_IP_ACTIVE_TAB_KEY);
    } catch {
      // private mode / storage disabled — stay on the default
    }
    const restored = resolveRestoredTab(saved, canSeeTradeProfit);
    if (!restored) return;
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("tab", restored);
        return next;
      },
      { replace: true },
    );
    // Mount only; afterwards onTabChange keeps storage in sync.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onTabChange = useCallback(
    (value: string) => {
      if (!isLoginIpTab(value)) return;
      try {
        localStorage.setItem(LOGIN_IP_ACTIVE_TAB_KEY, value);
      } catch {
        // ignore
      }
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          // Default tab → omit the param entirely for a shorter URL.
          if (value === LOGIN_IP_DEFAULT_TAB) next.delete("tab");
          else next.set("tab", value);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // `?q=` pre-fill for the manual search (the trade-profit tab's IP
  // drill-down). SearchTab consumes the param once its search has settled,
  // so a later remount restores the session cache instead of re-searching.
  const qParam = searchParams.get("q");
  const handleInitialQueryConsumed = useCallback(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("q");
        return next;
      },
      { replace: true },
    );
  }, [setSearchParams]);

  // Trade-profit detail → "investigate this IP": switch to the search tab
  // with the IP pre-filled.
  const handleSearchIp = useCallback(
    (ip: string) => {
      try {
        localStorage.setItem(LOGIN_IP_ACTIVE_TAB_KEY, "search");
      } catch {
        // ignore
      }
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("tab", "search");
          next.set("q", ip);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  return (
    <div className="flex-1 space-y-4 p-4 md:p-6">
      <Tabs value={activeTab} onValueChange={onTabChange} className="w-full">
        <TabsList
          className={cn(
            "grid w-full max-w-2xl",
            // Column count follows the VISIBLE tabs — the trade-profit
            // trigger is absent for cs-only users.
            canSeeTradeProfit ? "grid-cols-5" : "grid-cols-4",
          )}
        >
          <TabsTrigger value="report" className="gap-1.5">
            <IconReport className="h-4 w-4" />
            {t("loginIpsPage.tabs.report")}
          </TabsTrigger>
          <TabsTrigger value="watchlist" className="gap-1.5">
            <IconUsers className="h-4 w-4" />
            {t("loginIpsPage.tabs.watchlist")}
          </TabsTrigger>
          <TabsTrigger value="search" className="gap-1.5">
            <IconSearch className="h-4 w-4" />
            {t("loginIpsPage.tabs.search")}
          </TabsTrigger>
          <TabsTrigger value="ops" className="gap-1.5">
            <IconSettings className="h-4 w-4" />
            {t("loginIpsPage.tabs.ops")}
          </TabsTrigger>
          {canSeeTradeProfit && (
            <TabsTrigger value={LOGIN_IP_TRADE_PROFIT_TAB} className="gap-1.5">
              <IconChartLine className="h-4 w-4" />
              {t("loginIpsPage.tabs.tradeProfit")}
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="report" className="mt-4">
          <ReportTab />
        </TabsContent>
        <TabsContent value="watchlist" className="mt-4">
          <WatchlistTab />
        </TabsContent>
        <TabsContent value="search" className="mt-4">
          <SearchTab
            initialQuery={activeTab === "search" ? qParam : null}
            onInitialQueryConsumed={handleInitialQueryConsumed}
          />
        </TabsContent>
        <TabsContent value="ops" className="mt-4">
          <OperationsTab />
        </TabsContent>
        {canSeeTradeProfit && (
          <TabsContent value={LOGIN_IP_TRADE_PROFIT_TAB} className="mt-4">
            <TradeProfitTab onSearchIp={handleSearchIp} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
