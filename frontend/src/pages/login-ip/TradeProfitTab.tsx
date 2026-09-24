/**
 * Tab 5 — Trade-IP Profit Attribution (交易 IP 盈亏), OPT-0063 Phase 3.
 *
 * Ranks ACCOUNT GROUPS. The fixed rule (no toolbar knobs): an IP used by
 * >= 5 distinct CRM clients connects every account that used it; one client
 * with several accounts does not count. risk-only inside this cs page — the
 * parent (LoginIPs.tsx) renders this tab only for `hasModule(access, "risk")`,
 * and the backend gates `/login-ip/trade-profit/*` to the risk module anyway.
 *
 * Data is static within a day (the 08:30 HKT reconcile produces yesterday's
 * close-day rows), so there is NO polling here — filters refetch, and a
 * manual Refresh button covers "the reconcile just ran".
 *
 * Drill-down: clicking an IP in the detail card hops to the Search tab with
 * that IP pre-filled (`?tab=search&q=<ip>`, handled by the parent).
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { apiFetch } from "@/lib/fetch";
import { AgGridReact } from "ag-grid-react";
import type {
  ColDef,
  GridApi,
  ICellRendererParams,
  RowClassParams,
} from "ag-grid-community";
import { useI18n } from "@/components/i18n-provider";
import { useTheme } from "@/components/theme-provider";
import { useIsMobile } from "@/hooks/use-mobile";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Calendar } from "@/components/ui/calendar";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  IconInfoCircle,
  IconRefresh,
} from "@tabler/icons-react";
import { Calendar as CalendarIcon } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { DateRange } from "react-day-picker";
import { format } from "date-fns";
import { cn } from "@/lib/utils";
import { crmAccountUrl, crmUserUrl } from "@/lib/crm-links";
import { ColumnVisibilityMenu } from "@/components/ColumnVisibilityMenu";
import { InfoHeader } from "@/components/ui/info-header";
import {
  GRID_STORAGE_KEYS,
  useGridColumnPersist,
} from "@/hooks/useGridColumnPersist";
import { useFilterPersist, readFilterState } from "@/hooks/useFilterPersist";
import {
  buildTradeProfitLookupParams,
  buildTradeProfitParams,
  collectSharedOpenIps,
  computeTradeProfitWindow,
  detectTradeProfitQueryKind,
  fmtHoldMin,
  fmtUsd,
  profitColorClass,
  shouldShowEmptyState,
  sortOpenIpsSharedFirst,
  LOGIN_IP_TRADE_PROFIT_FILTERS_KEY,
  TRADE_PROFIT_FILTER_DEFAULTS,
  TRADE_PROFIT_IP_MIN_CLIENTS,
  type TradeProfitCoverageResponse,
  type TradeProfitFilters,
  type TradeProfitGroupDetailResponse,
  type TradeProfitGroupRow,
  type TradeProfitGroupsResponse,
  type TradeProfitLookupAccount,
  type TradeProfitLookupResponse,
  type TradeProfitRangePreset,
  type TradeProfitStatistics,
} from "./trade-profit";

/** Backend caps page_size at 200; groups are sorted by P&L desc, so a
 *  truncated list still shows the groups that matter (a note says so). */
const MAX_GROUPS = 200;

// Toolbar control widths come from ONE pair of constants so the grid tracks
// stay aligned at any wrap count (page-style-conventions §4.1).
const FILTER_CONTROL_CLASS = "h-9 w-full min-w-0";
const ACTION_BUTTON_CLASS = "h-9 w-full sm:w-[140px]";

const linkCls = "text-blue-600 hover:underline dark:text-blue-400";

/**
 * shadcn-Table header label with an ⓘ tooltip — the plain-Table counterpart
 * of AG-Grid's InfoHeader (which only works inside a grid). The app shell's
 * SidebarProvider already provides a TooltipProvider, so no local wrapper.
 */
function ThHint({ label, tip }: { label: string; tip: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      {label}
      <Tooltip>
        <TooltipTrigger asChild>
          <IconInfoCircle className="size-3.5 cursor-help opacity-60 hover:opacity-100" />
        </TooltipTrigger>
        <TooltipContent className="max-w-xs text-left text-xs leading-relaxed">
          {tip}
        </TooltipContent>
      </Tooltip>
    </span>
  );
}

/** Amber chip for open IPs shared by ≥2 clients in this lookup result. */
const SHARED_IP_CHIP_CLS =
  "rounded px-1 py-0.5 bg-amber-100 text-amber-900 dark:bg-amber-900/50 dark:text-amber-100";

/** Below-threshold lookup: peers on the same open IP(s) in the window. */
function TradeProfitLookupBody({
  result,
  onSearchIp,
}: {
  result: TradeProfitLookupResponse;
  onSearchIp: (ip: string) => void;
}) {
  const { t } = useI18n();
  // Shared = same open IP used by ≥2 distinct client IDs in the peer table.
  const sharedByIp = useMemo(
    () => collectSharedOpenIps(result.peer_accounts),
    [result.peer_accounts],
  );
  const sharedIpSet = useMemo(
    () => new Set(sharedByIp.keys()),
    [sharedByIp],
  );
  const sharedIpRows = useMemo(
    () =>
      [...sharedByIp.entries()].sort((a, b) =>
        a[0].localeCompare(b[0], undefined, { numeric: true }),
      ),
    [sharedByIp],
  );

  return (
    <div className="space-y-4 px-4 pb-6">
      {result.below_cluster_threshold && (
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          {t("loginIpsPage.tradeProfit.lookupBelowThreshold", {
            count: TRADE_PROFIT_IP_MIN_CLIENTS,
          })}
        </p>
      )}
      {sharedIpRows.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm font-medium">
            {t("loginIpsPage.tradeProfit.lookupSharedIpsTitle")}
          </p>
          <p className="text-xs text-muted-foreground">
            {t("loginIpsPage.tradeProfit.lookupSharedIpsHint")}
          </p>
          <div className="overflow-x-auto rounded-xl border bg-card">
            <Table>
              <TableHeader className="bg-black [&_th]:font-semibold [&_th]:text-white [&_th:first-child]:rounded-tl-xl [&_th:last-child]:rounded-tr-xl">
                <TableRow>
                  <TableHead>
                    {t("loginIpsPage.tradeProfit.colOpenIps")}
                  </TableHead>
                  <TableHead>
                    {t("loginIpsPage.tradeProfit.colClient")}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sharedIpRows.map(([ip, clientIds]) => (
                  <TableRow key={ip}>
                    <TableCell className="font-mono text-xs">
                      <button
                        type="button"
                        className={cn(linkCls, SHARED_IP_CHIP_CLS)}
                        onClick={() => onSearchIp(ip)}
                      >
                        {ip}
                      </button>
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {clientIds.map((id, i) => (
                        <span key={id}>
                          {i > 0 && ", "}
                          <a
                            href={crmUserUrl(id) ?? "#"}
                            target="_blank"
                            rel="noopener noreferrer"
                            className={linkCls}
                          >
                            {id}
                          </a>
                        </span>
                      ))}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
      <div className="overflow-x-auto rounded-xl border bg-card">
        <Table>
          <TableHeader className="bg-black [&_th]:font-semibold [&_th]:text-white [&_th:first-child]:rounded-tl-xl [&_th:last-child]:rounded-tr-xl">
            <TableRow>
              <TableHead>{t("loginIpsPage.tradeProfit.colAccount")}</TableHead>
              <TableHead>{t("loginIpsPage.tradeProfit.colClient")}</TableHead>
              <TableHead>{t("loginIpsPage.tradeProfit.colIb")}</TableHead>
              <TableHead className="text-right">
                {t("loginIpsPage.tradeProfit.colTrades")}
              </TableHead>
              <TableHead className="text-right">
                {t("loginIpsPage.tradeProfit.colProfit")}
              </TableHead>
              <TableHead>{t("loginIpsPage.tradeProfit.colOpenIps")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {result.peer_accounts.map((a: TradeProfitLookupAccount) => {
              const accountHref = crmAccountUrl(a.server, a.account_id);
              const orderedIps = sortOpenIpsSharedFirst(a.open_ips, sharedIpSet);
              return (
                <TableRow key={a.account_key}>
                  <TableCell className="font-mono text-sm">
                    <span className="inline-flex items-center gap-1">
                      {accountHref ? (
                        <a
                          href={accountHref}
                          target="_blank"
                          rel="noopener noreferrer"
                          className={linkCls}
                        >
                          {a.account_key}
                        </a>
                      ) : (
                        a.account_key
                      )}
                      {a.is_seed && (
                        <Badge variant="secondary" className="px-1 py-0 text-[10px]">
                          {t("loginIpsPage.tradeProfit.lookupSeedBadge")}
                        </Badge>
                      )}
                    </span>
                  </TableCell>
                  <TableCell>
                    {a.user_id ? (
                      <a
                        href={crmUserUrl(a.user_id) ?? "#"}
                        target="_blank"
                        rel="noopener noreferrer"
                        className={linkCls}
                      >
                        {a.user_id}
                      </a>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>
                    {a.ib_id ? (
                      <a
                        href={crmUserUrl(a.ib_id) ?? "#"}
                        target="_blank"
                        rel="noopener noreferrer"
                        className={linkCls}
                      >
                        {a.ib_id}
                      </a>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="text-right">{a.trades}</TableCell>
                  <TableCell
                    className={cn(
                      "text-right font-mono",
                      profitColorClass(a.profit_usd),
                    )}
                  >
                    {fmtUsd(a.profit_usd)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    <span className="inline-flex flex-wrap gap-1">
                      {orderedIps.map((ip) => {
                        const shared = sharedIpSet.has(ip);
                        return (
                          <button
                            key={ip}
                            type="button"
                            className={cn(
                              linkCls,
                              shared && SHARED_IP_CHIP_CLS,
                            )}
                            onClick={() => onSearchIp(ip)}
                            title={
                              shared
                                ? t("loginIpsPage.tradeProfit.lookupSharedIpTip")
                                : undefined
                            }
                          >
                            {ip}
                          </button>
                        );
                      })}
                    </span>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

/** Body of the right-hand detail sheet (accounts + member IPs). */
function TradeProfitDetailBody({
  detail,
  detailLoading,
  detailError,
  onSearchIp,
}: {
  detail: TradeProfitGroupDetailResponse | null;
  detailLoading: boolean;
  detailError: string | null;
  onSearchIp: (ip: string) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="space-y-4 px-4 pb-6">
      {detailLoading && (
        <p className="text-sm text-muted-foreground">
          {t("loginIpsPage.common.loading")}
        </p>
      )}
      {detailError && (
        <p className="text-sm text-red-600 dark:text-red-400">
          {t("loginIpsPage.tradeProfit.detailLoadFailed", {
            message: detailError,
          })}
        </p>
      )}
      {detail && !detailLoading && (
        <div className="flex flex-col gap-4">
            <div className="space-y-2">
              <h3 className="text-sm font-semibold">
                {t("loginIpsPage.tradeProfit.accountsTitle", {
                  count: detail.group.accounts_detail?.length ?? 0,
                })}
              </h3>
              <div className="overflow-x-auto rounded-xl border bg-card">
                <Table>
                  <TableHeader className="bg-black [&_th]:font-semibold [&_th]:text-white [&_th:first-child]:rounded-tl-xl [&_th:last-child]:rounded-tr-xl">
                    <TableRow>
                      <TableHead>
                        {t("loginIpsPage.tradeProfit.colAccount")}
                      </TableHead>
                      <TableHead>
                        {t("loginIpsPage.tradeProfit.colClient")}
                      </TableHead>
                      <TableHead>
                        {t("loginIpsPage.tradeProfit.colIb")}
                      </TableHead>
                      <TableHead className="text-right">
                        {t("loginIpsPage.tradeProfit.colTrades")}
                      </TableHead>
                      <TableHead className="text-right">
                        {t("loginIpsPage.tradeProfit.colLots")}
                      </TableHead>
                      <TableHead className="text-right">
                        {t("loginIpsPage.tradeProfit.colProfit")}
                      </TableHead>
                      <TableHead>
                        {t("loginIpsPage.tradeProfit.colSymbol")}
                      </TableHead>
                      <TableHead className="text-right">
                        {t("loginIpsPage.tradeProfit.colAvgHold")}
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(detail.group.accounts_detail ?? []).map((a) => {
                      const accountHref = crmAccountUrl(a.server, a.account_id);
                      return (
                        <TableRow key={a.account_key}>
                          <TableCell className="font-mono text-sm">
                            {accountHref ? (
                              <a
                                href={accountHref}
                                target="_blank"
                                rel="noopener noreferrer"
                                className={linkCls}
                              >
                                {a.account_key}
                              </a>
                            ) : (
                              a.account_key
                            )}
                          </TableCell>
                          <TableCell>
                            {a.user_id ? (
                              <a
                                href={crmUserUrl(a.user_id) ?? "#"}
                                target="_blank"
                                rel="noopener noreferrer"
                                className={linkCls}
                              >
                                {a.user_id}
                              </a>
                            ) : (
                              "—"
                            )}
                          </TableCell>
                          <TableCell>
                            {a.ib_id ? (
                              <a
                                href={crmUserUrl(a.ib_id) ?? "#"}
                                target="_blank"
                                rel="noopener noreferrer"
                                className={linkCls}
                              >
                                {a.ib_id}
                              </a>
                            ) : (
                              "—"
                            )}
                          </TableCell>
                          <TableCell className="text-right">{a.trades}</TableCell>
                          <TableCell className="text-right">
                            {a.lots.toFixed(2)}
                          </TableCell>
                          <TableCell
                            className={cn(
                              "text-right font-mono",
                              profitColorClass(a.profit_usd),
                            )}
                          >
                            {fmtUsd(a.profit_usd)}
                          </TableCell>
                          <TableCell>{a.dominant_symbol || "—"}</TableCell>
                          <TableCell className="text-right">
                            {fmtHoldMin(a.avg_hold_min)}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            </div>
            <div className="space-y-2">
              <h3 className="text-sm font-semibold">
                {t("loginIpsPage.tradeProfit.ipsTitle", {
                  count: detail.member_ips.length,
                })}
              </h3>
              <p className="text-xs text-muted-foreground">
                {t("loginIpsPage.tradeProfit.clickIpHint")}
              </p>
              <div className="overflow-x-auto rounded-xl border bg-card">
                <Table>
                  <TableHeader className="bg-black [&_th]:font-semibold [&_th]:text-white [&_th:first-child]:rounded-tl-xl [&_th:last-child]:rounded-tr-xl">
                    <TableRow>
                      <TableHead>IP</TableHead>
                      <TableHead>
                        {t("loginIpsPage.tradeProfit.colCountry")}
                      </TableHead>
                      <TableHead className="text-right">
                        <ThHint
                          label={t("loginIpsPage.tradeProfit.colWindowClients")}
                          tip={t("loginIpsPage.tradeProfit.colWindowClientsTip")}
                        />
                      </TableHead>
                      <TableHead className="text-right">
                        <ThHint
                          label={t("loginIpsPage.tradeProfit.colActiveDays")}
                          tip={t("loginIpsPage.tradeProfit.colIpActiveDaysTip")}
                        />
                      </TableHead>
                      <TableHead>
                        <ThHint
                          label={t("loginIpsPage.tradeProfit.colBridge")}
                          tip={t("loginIpsPage.tradeProfit.colBridgeTip")}
                        />
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {detail.member_ips.map((ip) => (
                      <TableRow key={ip.ip}>
                        <TableCell>
                          <button
                            type="button"
                            className={cn("font-mono text-sm", linkCls)}
                            onClick={() => onSearchIp(ip.ip)}
                          >
                            {ip.ip}
                          </button>
                        </TableCell>
                        <TableCell>{ip.country ?? "—"}</TableCell>
                        <TableCell className="text-right">
                          {ip.window_clients ?? "—"}
                        </TableCell>
                        <TableCell className="text-right">
                          {ip.window_active_days ?? "—"}
                        </TableCell>
                        <TableCell>
                          {ip.bridge ? (
                            <Badge variant="outline">
                              {t("loginIpsPage.tradeProfit.bridgeYes")}
                            </Badge>
                          ) : (
                            "—"
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>
          </div>
      )}
    </div>
  );
}

export interface TradeProfitTabProps {
  /** Jump to the Search tab with this IP pre-filled (parent owns the URL). */
  onSearchIp: (ip: string) => void;
}

export function TradeProfitTab({ onSearchIp }: TradeProfitTabProps) {
  const { t } = useI18n();
  const { theme } = useTheme();
  const isDark = theme === "dark";
  const isMobile = useIsMobile();

  // ── Toolbar filters (persisted viewing preferences) ──────────────────
  const persisted = useMemo(
    () =>
      readFilterState<TradeProfitFilters>(
        LOGIN_IP_TRADE_PROFIT_FILTERS_KEY,
        TRADE_PROFIT_FILTER_DEFAULTS,
      ),
    [],
  );
  const [rangePreset, setRangePreset] = useState<TradeProfitRangePreset>(
    persisted.rangePreset,
  );
  // Investigation context — NOT persisted (grid-column-persist.md §13).
  const [customRange, setCustomRange] = useState<DateRange | undefined>();
  const [datePickerOpen, setDatePickerOpen] = useState(false);

  useFilterPersist(
    LOGIN_IP_TRADE_PROFIT_FILTERS_KEY,
    TRADE_PROFIT_FILTER_DEFAULTS,
    { rangePreset },
    // While "custom" is selected the persisted preset keeps its last real
    // value, so a reload restores e.g. 7d instead of an empty custom mode.
    { skipFields: rangePreset === "custom" ? ["rangePreset"] : [] },
  );

  // ── Window ───────────────────────────────────────────────────────────
  const [refreshToken, setRefreshToken] = useState(0);
  // Named `dateWindow`, not `window` — shadowing the global in a component
  // that also runs DOM-adjacent effects is a bug farm.
  const dateWindow = useMemo(() => {
    // refreshToken IS a real input: it re-runs this memo so `new Date()`
    // below is re-evaluated on manual refresh (eslint can't see that).
    void refreshToken;
    const custom =
      rangePreset === "custom"
        ? {
            from: customRange?.from
              ? format(customRange.from, "yyyy-MM-dd")
              : undefined,
            to: customRange?.to
              ? format(customRange.to, "yyyy-MM-dd")
              : undefined,
          }
        : undefined;
    // `new Date()` is re-evaluated on refresh, so a page left open across
    // the HKT midnight picks up the freshly reconciled day.
    return computeTradeProfitWindow(rangePreset, custom, new Date());
  }, [rangePreset, customRange, refreshToken]);

  // ── List + coverage fetch ────────────────────────────────────────────
  const [groups, setGroups] = useState<TradeProfitGroupRow[]>([]);
  const [total, setTotal] = useState(0);
  const [statistics, setStatistics] = useState<TradeProfitStatistics | null>(
    null,
  );
  const [coverage, setCoverage] = useState<TradeProfitCoverageResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [expandedGroupId, setExpandedGroupId] = useState<string | null>(null);
  const [lookupInput, setLookupInput] = useState("");
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookupPeerResult, setLookupPeerResult] =
    useState<TradeProfitLookupResponse | null>(null);
  const [lookupSheetOpen, setLookupSheetOpen] = useState(false);

  // Collapse the open detail when the query parameters change: group_id
  // hashes (window, thresholds, account list), so after a filter change the
  // open id would 404 by design. A manual refresh keeps the same signature
  // and leaves the panel open.
  const paramsSignature = [dateWindow.from, dateWindow.to].join("|");
  const prevSignatureRef = useRef(paramsSignature);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setError(null);
      if (prevSignatureRef.current !== paramsSignature) {
        prevSignatureRef.current = paramsSignature;
        setExpandedGroupId(null);
      }
      const params = buildTradeProfitParams(dateWindow);
      const listParams = new URLSearchParams(params);
      listParams.set("page", "1");
      listParams.set("page_size", String(MAX_GROUPS));
      const coverageParams = new URLSearchParams({
        from: dateWindow.from,
        to: dateWindow.to,
      });
      try {
        const [groupsRes, coverageRes] = await Promise.all([
          apiFetch(`/api/v1/login-ip/trade-profit/groups?${listParams}`, {
            signal: controller.signal,
          }),
          apiFetch(`/api/v1/login-ip/trade-profit/coverage?${coverageParams}`, {
            signal: controller.signal,
          }),
        ]);
        if (!groupsRes.ok) {
          const body: { detail?: unknown } = await groupsRes
            .json()
            .catch(() => ({}));
          throw new Error(
            typeof body?.detail === "string"
              ? body.detail
              : `HTTP ${groupsRes.status}`,
          );
        }
        const groupsData: TradeProfitGroupsResponse = await groupsRes.json();
        setGroups(groupsData.data);
        setTotal(groupsData.total);
        setStatistics(groupsData.statistics);
        // Coverage is context for the reader — never worth failing the tab.
        setCoverage(coverageRes.ok ? await coverageRes.json() : null);
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setError(e instanceof Error ? e.message : String(e));
        setGroups([]);
        setTotal(0);
        setStatistics(null);
        setCoverage(null);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [
    dateWindow,
    paramsSignature,
  ]);

  // ── Group detail fetch ───────────────────────────────────────────────
  const [detail, setDetail] = useState<TradeProfitGroupDetailResponse | null>(
    null,
  );
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    if (!expandedGroupId) {
      setDetail(null);
      setDetailError(null);
      return;
    }
    const controller = new AbortController();
    async function load() {
      setDetailLoading(true);
      setDetailError(null);
      // Same builder as the list call — group_id hashes the window and
      // thresholds, so the detail request must echo them verbatim or the id
      // resolves to nothing (404 by design, not a bug).
      const params = buildTradeProfitParams(dateWindow);
      try {
        const res = await apiFetch(
          `/api/v1/login-ip/trade-profit/groups/${expandedGroupId}?${params}`,
          { signal: controller.signal },
        );
        if (!res.ok) {
          const body: { detail?: unknown } = await res
            .json()
            .catch(() => ({}));
          throw new Error(
            res.status === 404
              ? t("loginIpsPage.tradeProfit.detailNotFound")
              : typeof body?.detail === "string"
                ? body.detail
                : `HTTP ${res.status}`,
          );
        }
        setDetail(await res.json());
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setDetail(null);
        setDetailError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!controller.signal.aborted) setDetailLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [
    expandedGroupId,
    dateWindow,
    t,
  ]);

  // ── Grid ─────────────────────────────────────────────────────────────
  const persist = useGridColumnPersist(GRID_STORAGE_KEYS.LOGIN_IP_TRADE_PROFIT);
  const gridApiRef = useRef<GridApi<TradeProfitGroupRow> | null>(null);

  // Highlight the opened row. AG-Grid caches row styles, so a selection
  // change must force a refresh or the tint lags one click behind. The
  // stored api is guarded: a destroyed grid returns undefined, it does not
  // throw (ui-pitfalls §2.6).
  useEffect(() => {
    const api = gridApiRef.current;
    if (!api || api.isDestroyed()) return;
    api.redrawRows();
  }, [expandedGroupId]);

  const columnDefs = useMemo<ColDef<TradeProfitGroupRow>[]>(
    () => [
      {
        headerName: t("loginIpsPage.tradeProfit.colProfit"),
        field: "profit_usd",
        width: 120,
        sort: "desc", // backend already sorts P&L desc; this shows the arrow
        headerComponent: InfoHeader,
        headerComponentParams: {
          tooltip: t("loginIpsPage.tradeProfit.colProfitTip"),
        },
        valueFormatter: (p) => fmtUsd(p.value),
        cellClass: (p) =>
          cn("font-mono font-semibold", profitColorClass(p.value)),
      },
      {
        headerName: t("loginIpsPage.tradeProfit.colClients"),
        field: "clients",
        width: 95,
        cellRenderer: (p: ICellRendererParams<TradeProfitGroupRow>) => (
          <span className="inline-flex items-center gap-1">
            {p.value}
            {p.data?.same_client && (
              <Badge variant="secondary" className="px-1 py-0 text-[10px]">
                {t("loginIpsPage.tradeProfit.sameClientBadge")}
              </Badge>
            )}
          </span>
        ),
      },
      {
        headerName: t("loginIpsPage.tradeProfit.colIbs"),
        field: "ibs",
        width: 85,
        headerComponent: InfoHeader,
        headerComponentParams: {
          tooltip: t("loginIpsPage.tradeProfit.colIbsTip"),
        },
      },
      {
        headerName: t("loginIpsPage.tradeProfit.colAccounts"),
        field: "accounts",
        width: 95,
      },
      {
        headerName: t("loginIpsPage.tradeProfit.colTrades"),
        field: "trades",
        width: 90,
      },
      {
        headerName: t("loginIpsPage.tradeProfit.colLots"),
        field: "lots",
        width: 90,
        valueFormatter: (p) =>
          p.value === null || p.value === undefined ? "—" : Number(p.value).toFixed(2),
      },
      {
        headerName: t("loginIpsPage.tradeProfit.colSharedIps"),
        field: "shared_ips",
        width: 95,
        headerComponent: InfoHeader,
        headerComponentParams: {
          tooltip: t("loginIpsPage.tradeProfit.colSharedIpsTip", {
            count: TRADE_PROFIT_IP_MIN_CLIENTS,
          }),
        },
      },
      {
        headerName: t("loginIpsPage.tradeProfit.colProfitDays"),
        // valueGetter-only computed column → explicit stable colId, or the
        // persisted column state would drift on any column insert/reorder.
        colId: "profitable_ratio",
        width: 120,
        headerComponent: InfoHeader,
        headerComponentParams: {
          tooltip: t("loginIpsPage.tradeProfit.colProfitDaysTip"),
        },
        valueGetter: (p) =>
          p.data ? `${p.data.profitable_days}/${p.data.active_days}` : "",
        // Client-side grid (all rows are local), so a comparator is a real
        // sort, not the fake server-sort kind (grid-column-persist.md §5.5).
        comparator: (_a, _b, nodeA, nodeB) => {
          const ratio = (d?: TradeProfitGroupRow) =>
            d && d.active_days > 0 ? d.profitable_days / d.active_days : -1;
          return ratio(nodeA.data) - ratio(nodeB.data);
        },
      },
      {
        headerName: t("loginIpsPage.tradeProfit.colSymbol"),
        field: "dominant_symbol",
        width: 140,
        cellRenderer: (p: ICellRendererParams<TradeProfitGroupRow>) => {
          if (!p.data?.dominant_symbol) return <span>—</span>;
          const share = Math.round(p.data.dominant_symbol_share * 100);
          const other = 100 - share;
          return (
            <span>
              {p.data.dominant_symbol}
              <span className="ml-1 text-xs text-muted-foreground">
                {share}%
                {/* The API only exposes the dominant share, so the remainder
                    is shown as one "other" bucket, not a per-symbol split. */}
                {other > 0 &&
                  ` · ${t("loginIpsPage.tradeProfit.symbolOther", { pct: other })}`}
              </span>
            </span>
          );
        },
      },
      {
        headerName: t("loginIpsPage.tradeProfit.colAvgHold"),
        field: "avg_hold_min",
        width: 105,
        headerComponent: InfoHeader,
        headerComponentParams: {
          tooltip: t("loginIpsPage.tradeProfit.colAvgHoldTip"),
        },
        valueFormatter: (p) => fmtHoldMin(p.value),
      },
    ],
    [t],
  );

  const defaultColDef = useMemo<ColDef>(
    () => ({
      sortable: true,
      resizable: true,
      filter: true,
      minWidth: 80,
      wrapHeaderText: true,
      autoHeaderHeight: true,
      // Cells wrap instead of silently clipping when narrowed (ag-grid-style
      // §4b) — a right-aligned number clipped from the left reads as a
      // different plausible number, which is the worst failure mode here.
      wrapText: true,
      autoHeight: true,
      cellStyle: {
        whiteSpace: "normal",
        lineHeight: "1.35",
        overflowWrap: "anywhere",
      },
    }),
    [],
  );

  // Match the sibling tabs (Report/Watchlist/Search): black header, white
  // text, not theme-dependent. All semi-transparent colours are rgba — the
  // theme's CSS variables are oklch and hsl(var(...)) would be invalid CSS.
  const gridThemeStyle = useMemo(
    () =>
      ({
        ["--ag-header-background-color" as string]: "#000000",
        ["--ag-header-foreground-color" as string]: "#ffffff",
        ["--ag-header-column-separator-color" as string]:
          "rgba(255, 255, 255, 0.12)",
        ["--ag-header-column-separator-width" as string]: "1px",
        ["--ag-cell-horizontal-padding" as string]: "4px",
        ["--ag-header-cell-hover-background-color" as string]: "#171717",
        ["--ag-icon-color" as string]: "#ffffff",
        ["--ag-background-color" as string]: "hsl(var(--card))",
        ["--ag-foreground-color" as string]: "hsl(var(--foreground))",
        ["--ag-row-border-color" as string]: "hsl(var(--border))",
        ["--ag-odd-row-background-color" as string]: isDark
          ? "rgba(255,255,255,0.04)"
          : "rgba(0,0,0,0.03)",
      }) satisfies CSSProperties,
    [isDark],
  );

  const onRowClicked = useCallback((e: { data?: TradeProfitGroupRow }) => {
    const id = e.data?.group_id;
    if (!id) return;
    setLookupSheetOpen(false);
    setLookupPeerResult(null);
    setExpandedGroupId(id);
  }, []);

  const runLookup = useCallback(async () => {
    const trimmed = lookupInput.trim();
    if (!detectTradeProfitQueryKind(trimmed)) {
      toast.error(t("loginIpsPage.tradeProfit.lookupInvalid"));
      return;
    }
    setLookupLoading(true);
    try {
      const params = buildTradeProfitLookupParams(dateWindow, trimmed);
      const res = await apiFetch(
        `/api/v1/login-ip/trade-profit/lookup?${params}`,
      );
      if (!res.ok) {
        const body: { detail?: unknown } = await res.json().catch(() => ({}));
        throw new Error(
          typeof body?.detail === "string"
            ? body.detail
            : `HTTP ${res.status}`,
        );
      }
      const data: TradeProfitLookupResponse = await res.json();
      const groupId = data.group_ids[0];
      if (groupId) {
        setLookupSheetOpen(false);
        setLookupPeerResult(null);
        setExpandedGroupId(groupId);
        return;
      }
      if (data.peer_accounts.length > 0) {
        setExpandedGroupId(null);
        setLookupPeerResult(data);
        setLookupSheetOpen(true);
        return;
      }
      toast.message(t("loginIpsPage.tradeProfit.lookupNotFound"));
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      toast.error(
        t("loginIpsPage.tradeProfit.lookupFailed", {
          message: e instanceof Error ? e.message : String(e),
        }),
      );
    } finally {
      setLookupLoading(false);
    }
  }, [lookupInput, dateWindow, t]);

  // Blue tint on the row whose sheet is open. rgba, not hsl(var(...)):
  // theme variables are oklch (ag-grid-style §3). Undefined keeps zebra.
  const getRowStyle = useCallback(
    (p: RowClassParams<TradeProfitGroupRow>) =>
      p.data?.group_id === expandedGroupId
        ? {
            background: isDark
              ? "rgba(59,130,246,0.16)"
              : "rgba(59,130,246,0.10)",
          }
        : undefined,
    [expandedGroupId, isDark],
  );

  // ── Render ───────────────────────────────────────────────────────────
  const showEmpty = shouldShowEmptyState(loading, error, groups.length);
  // The coverage banner is warn-only: the routine "N trades, M% without IP"
  // sentence was noise on every healthy day, so it renders solely when the
  // window has incomplete journals or unreconciled days (the only states the
  // reader must act on).
  const coverageWarnings = coverage
    ? coverage.incomplete_logs.length > 0 || coverage.unreconciled_dates.length > 0
    : false;

  return (
    <div className="space-y-4">
      {/* Toolbar (page-style-conventions §4.1: grid, not flex-wrap) */}
      <div className="rounded-xl border bg-card px-4 py-4 md:px-6">
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3">
            <Select
              value={rangePreset}
              onValueChange={(v) => {
                setRangePreset(v as TradeProfitRangePreset);
                if (v === "custom" && !customRange?.from) {
                  setDatePickerOpen(true);
                }
              }}
            >
              <SelectTrigger
                className={FILTER_CONTROL_CLASS}
                aria-label={t("loginIpsPage.tradeProfit.window")}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="7d">
                  {t("loginIpsPage.tradeProfit.preset7d")}
                </SelectItem>
                <SelectItem value="30d">
                  {t("loginIpsPage.tradeProfit.preset30d")}
                </SelectItem>
                <SelectItem value="90d">
                  {t("loginIpsPage.tradeProfit.preset90d")}
                </SelectItem>
                <SelectItem value="custom">
                  {t("loginIpsPage.tradeProfit.presetCustom")}
                </SelectItem>
              </SelectContent>
            </Select>

            {rangePreset === "custom" && (
              <Popover open={datePickerOpen} onOpenChange={setDatePickerOpen}>
                <PopoverTrigger asChild>
                  <Button
                    variant="outline"
                    className={cn(
                      FILTER_CONTROL_CLASS,
                      "justify-start text-left font-normal",
                      !customRange?.from && "text-muted-foreground",
                    )}
                  >
                    <CalendarIcon className="mr-2 h-4 w-4 shrink-0" />
                    <span className="truncate">
                      {customRange?.from ? (
                        customRange.to ? (
                          <>
                            {format(customRange.from, "yyyy-MM-dd")} ~{" "}
                            {format(customRange.to, "yyyy-MM-dd")}
                          </>
                        ) : (
                          format(customRange.from, "yyyy-MM-dd")
                        )
                      ) : (
                        t("loginIpsPage.tradeProfit.pickRange")
                      )}
                    </span>
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-0" align="start">
                  <Calendar
                    initialFocus
                    mode="range"
                    defaultMonth={customRange?.from}
                    selected={customRange}
                    onSelect={setCustomRange}
                    numberOfMonths={2}
                    // Tomorrow and beyond can never have reconciled rows.
                    disabled={{ after: new Date() }}
                  />
                </PopoverContent>
              </Popover>
            )}

            <div className="flex min-w-0 flex-col gap-2 sm:col-span-2 sm:flex-row">
              <Input
                className={cn(FILTER_CONTROL_CLASS, "font-mono")}
                placeholder={t("loginIpsPage.tradeProfit.lookupPlaceholder")}
                value={lookupInput}
                onChange={(e) => setLookupInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void runLookup();
                }}
                disabled={lookupLoading || loading}
                aria-label={t("loginIpsPage.tradeProfit.lookupPlaceholder")}
              />
              <Button
                className={ACTION_BUTTON_CLASS}
                onClick={() => void runLookup()}
                disabled={lookupLoading || loading || !lookupInput.trim()}
              >
                {lookupLoading
                  ? t("loginIpsPage.tradeProfit.lookupSearching")
                  : t("loginIpsPage.tradeProfit.lookupSearch")}
              </Button>
            </div>
          </div>
          <p className="text-sm text-muted-foreground">
            {t("loginIpsPage.tradeProfit.rule", {
              count: TRADE_PROFIT_IP_MIN_CLIENTS,
            })}
          </p>

          <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
            <ColumnVisibilityMenu
              persist={persist}
              columnDefs={columnDefs as ColDef<unknown>[]}
              size="sm"
              buttonClassName={ACTION_BUTTON_CLASS}
            />
            <Button
              variant="outline"
              className={cn(ACTION_BUTTON_CLASS, "gap-2")}
              onClick={() => setRefreshToken((n) => n + 1)}
              disabled={loading}
            >
              <IconRefresh
                className={cn("h-4 w-4", loading && "animate-spin")}
              />
              {t("loginIpsPage.common.refresh")}
            </Button>
          </div>
        </div>
      </div>

      {/* Coverage warnings — only when the window's data is incomplete. */}
      {coverage && coverageWarnings && (
        <div
          role="note"
          className="flex items-start gap-2 rounded-lg border bg-muted/50 px-3 py-2 text-sm text-muted-foreground"
        >
          <IconInfoCircle className="mt-0.5 size-4 shrink-0 opacity-90" />
          <div className="leading-snug">
            {coverage.incomplete_logs.length > 0 && (
              <p className="text-amber-600 dark:text-amber-400">
                {t("loginIpsPage.tradeProfit.coverageIncomplete", {
                  list: coverage.incomplete_logs
                    .map((l) => `${l.date} ${l.server}`)
                    .join(", "),
                })}
              </p>
            )}
            {coverage.unreconciled_dates.length > 0 && (
              <p>
                {t("loginIpsPage.tradeProfit.coverageUnreconciled", {
                  list: coverage.unreconciled_dates.join(", "),
                })}
              </p>
            )}
          </div>
        </div>
      )}

      {/* Main grid — one row per account group */}
      <Card className="gap-3">
        <CardHeader>
          <CardTitle className="text-base">
            {t("loginIpsPage.tradeProfit.groupsTitle", { count: total })}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {error && (
            <p className="text-sm text-red-600 dark:text-red-400">
              {t("loginIpsPage.tradeProfit.loadFailed", { message: error })}
            </p>
          )}
          {showEmpty ? (
            <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-16 text-center">
              <p className="text-sm font-medium">
                {t("loginIpsPage.tradeProfit.empty")}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {t("loginIpsPage.tradeProfit.emptyHint", {
                  count: TRADE_PROFIT_IP_MIN_CLIENTS,
                  from: dateWindow.from,
                  to: dateWindow.to,
                })}
              </p>
            </div>
          ) : (
            <div className="overflow-hidden rounded-xl border bg-card">
              <style>
                {`
                  .login-ip-trade-profit-grid .ag-header,
                  .login-ip-trade-profit-grid .ag-header-viewport,
                  .login-ip-trade-profit-grid .ag-header-row {
                    font-weight: 600;
                  }
                  .login-ip-trade-profit-grid .ag-header {
                    border-top-left-radius: 0.75rem;
                    border-top-right-radius: 0.75rem;
                  }
                `}
              </style>
              <div
                className={cn(
                  "login-ip-trade-profit-grid h-[480px] w-full",
                  isDark ? "ag-theme-quartz-dark" : "ag-theme-quartz",
                )}
                style={gridThemeStyle}
              >
                <AgGridReact<TradeProfitGroupRow>
                  rowData={groups}
                  columnDefs={columnDefs}
                  defaultColDef={defaultColDef}
                  gridOptions={{ theme: "legacy" }}
                  loading={loading}
                  animateRows
                  pagination
                  paginationPageSize={50}
                  paginationPageSizeSelector={[20, 50, 100, 200]}
                  suppressCellFocus
                  enableCellTextSelection
                  rowClass="cursor-pointer"
                  getRowId={(p) => p.data.group_id}
                  getRowStyle={getRowStyle}
                  onRowClicked={onRowClicked}
                  // Compose with the persistence handlers — never spread
                  // gridEventProps (it would replace this grid's own
                  // onGridReady / onSortChanged).
                  onGridReady={(e) => {
                    gridApiRef.current = e.api;
                    persist.gridEventProps.onGridReady(e);
                  }}
                  onSortChanged={persist.gridEventProps.onSortChanged}
                  onColumnMoved={persist.gridEventProps.onColumnMoved}
                  onColumnVisible={persist.gridEventProps.onColumnVisible}
                  onColumnPinned={persist.gridEventProps.onColumnPinned}
                  onColumnResized={persist.gridEventProps.onColumnResized}
                />
              </div>
            </div>
          )}
          {total > groups.length && (
            <p className="text-xs text-muted-foreground">
              {t("loginIpsPage.tradeProfit.truncated", {
                total,
                shown: groups.length,
              })}
            </p>
          )}
          {statistics && !showEmpty && (
            <p className="text-xs text-muted-foreground">
              {t("loginIpsPage.tradeProfit.groupsFootnote", {
                grouped: statistics.groups_trades.toLocaleString(),
                withIp: statistics.window_with_ip_trades.toLocaleString(),
              })}
            </p>
          )}
          {/* The three known blind spots, per the OPT-0063 spec — the reader
              must know what this view CANNOT see. */}
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("loginIpsPage.tradeProfit.blindSpots")}
          </p>
        </CardContent>
      </Card>

      {/* Same interaction as Risk Monitor → Gap Trade: the row stays put and
          the detail slides in from the right (from the bottom on a phone). */}
      <Sheet
        open={lookupSheetOpen}
        onOpenChange={(open) => {
          setLookupSheetOpen(open);
          if (!open) setLookupPeerResult(null);
        }}
      >
        <SheetContent
          side={isMobile ? "bottom" : "right"}
          className={cn(
            "gap-0 overflow-y-auto sm:max-w-none",
            isMobile ? "h-[85vh] rounded-t-lg" : "w-[min(640px,92vw)]",
          )}
        >
          <SheetHeader className="pr-10">
            <SheetTitle>{t("loginIpsPage.tradeProfit.lookupTitle")}</SheetTitle>
            <SheetDescription>
              {lookupPeerResult?.query ?? ""}
            </SheetDescription>
          </SheetHeader>
          {lookupPeerResult && (
            <TradeProfitLookupBody
              result={lookupPeerResult}
              onSearchIp={onSearchIp}
            />
          )}
        </SheetContent>
      </Sheet>

      <Sheet
        open={expandedGroupId !== null}
        onOpenChange={(open) => {
          if (!open) setExpandedGroupId(null);
        }}
      >
        <SheetContent
          side={isMobile ? "bottom" : "right"}
          className={cn(
            "gap-0 overflow-y-auto sm:max-w-none",
            isMobile ? "h-[85vh] rounded-t-lg" : "w-[min(640px,92vw)]",
          )}
        >
          <SheetHeader className="pr-10">
            <SheetTitle className="flex items-center gap-2">
              {t("loginIpsPage.tradeProfit.detailTitle")}
              {detail?.group.same_client && (
                <Badge variant="secondary">
                  {t("loginIpsPage.tradeProfit.sameClientBadge")}
                </Badge>
              )}
            </SheetTitle>
            <SheetDescription>
              {detail && !detailLoading
                ? t("loginIpsPage.tradeProfit.detailSummary", {
                    profit: fmtUsd(detail.group.profit_usd),
                    accounts: detail.group.accounts,
                    clients: detail.group.clients,
                    ips: detail.group.shared_ips,
                  })
                : expandedGroupId}
            </SheetDescription>
          </SheetHeader>
          <TradeProfitDetailBody
            detail={detail}
            detailLoading={detailLoading}
            detailError={detailError}
            onSearchIp={onSearchIp}
          />
        </SheetContent>
      </Sheet>
    </div>
  );
}
