import { lazy, Suspense } from "react"
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom"
import { AuthProvider, useAuth } from "@/providers/auth-provider"

const DashboardLayout = lazy(() => import("@/layouts/DashboardLayout"))
const LoginPage = lazy(() => import("@/pages/Login"))
const DashboardTemplatePage = lazy(() => import("@/pages/Dashboard"))
const BasisPage = lazy(() => import("@/pages/Basis"))
const GoldQuotePage = lazy(() => import("@/pages/GoldQuote"))
const DownloadsPage = lazy(() => import("@/pages/Downloads"))
const WarehousePage = lazy(() => import("@/pages/Warehouse"))
const EquityMonitorPage = lazy(() => import("@/pages/EquityMonitor"))
const PositionPage = lazy(() => import("@/pages/Position"))
const WarehouseProductsPage = lazy(() => import("@/pages/WarehouseProducts"))
const WarehouseOthersPage = lazy(() => import("@/pages/WarehouseOthers"))
const IBDataPage = lazy(() => import("@/pages/IBData"))
const LoginIPsPage = lazy(() => import("@/pages/LoginIPs"))
const ProfitPage = lazy(() => import("@/pages/Profit"))
const AgentGlobalPage = lazy(() => import("@/pages/AgentGlobal"))
const ClientTradingAnalyticsPage = lazy(() => import("@/pages/ClientTradingAnalytics"))
const IbidLotsPage = lazy(() => import("@/pages/IbidLots"))
const SwapFreeControlPage = lazy(() => import("@/pages/SwapFreeControl"))
const CustomerPnLMonitorPage = lazy(() => import("@/pages/CustomerPnLMonitor"))
const CustomerPnLMonitorV2Page = lazy(() => import("@/pages/CustomerPnLMonitorV2"))
const ClientPnLMonitorPage = lazy(() => import("@/pages/ClientPnLMonitor"))
const ClientPnLAnalysisPage = lazy(() => import("@/pages/ClientPnLAnalysis"))
const ConfigPlaceholder = lazy(() => import("@/pages/ConfigPlaceholder"))
const IBReportPage = lazy(() => import("@/pages/IBReport"))
const SettingsPage = lazy(() => import("@/pages/Settings"))
const SearchPage = lazy(() => import("@/pages/Search"))

function PrivateRoute({ children }: { children: React.ReactElement }) {
  if (import.meta.env.VITE_DISABLE_AUTH === 'true') return children
  const { isAuthenticated } = useAuth()
  return isAuthenticated ? children : <Navigate to="/login" replace />
}

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Suspense fallback={<div className="p-4">Loading...</div>}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              path="/"
              element={
                <PrivateRoute>
                  <DashboardLayout />
                </PrivateRoute>
              }
            >
              <Route index element={<Navigate to="/cfg/managers" replace />} />
              <Route path="template" element={<DashboardTemplatePage />} />
              <Route path="equity-monitor" element={<EquityMonitorPage />} />
              <Route path="gold" element={<GoldQuotePage />} />
              <Route path="basis" element={<BasisPage />} />
              <Route path="downloads" element={<DownloadsPage />} />
              <Route path="warehouse" element={<WarehousePage />} />
              <Route path="warehouse/products" element={<WarehouseProductsPage />} />
              <Route path="warehouse/ib-data" element={<IBDataPage />} />
              <Route path="warehouse/others" element={<WarehouseOthersPage />} />
              <Route path="warehouse/agent-global" element={<AgentGlobalPage />} />
              <Route path="position" element={<PositionPage />} />
              <Route path="login-ips" element={<LoginIPsPage />} />
              <Route path="profit" element={<ProfitPage />} />
              <Route path="client-trading" element={<ClientTradingAnalyticsPage />} />
              <Route path="ibid-lots" element={<IbidLotsPage />} />
              <Route path="swap-free-control" element={<SwapFreeControlPage />} />
              <Route path="customer-pnl-monitor" element={<CustomerPnLMonitorPage />} />
              <Route path="customer-pnl-monitor-v2" element={<CustomerPnLMonitorV2Page />} />
              <Route path="client-pnl-monitor" element={<ClientPnLMonitorPage />} />
              <Route path="client-pnl-analysis" element={<ClientPnLAnalysisPage />} />
              <Route path="ib-report" element={<IBReportPage />} />
              {/* test page removed */}
              <Route path="settings" element={<SettingsPage />} />
              <Route path="search" element={<SearchPage />} />
              {/* Configuration routes */}
              <Route path="cfg">
                <Route path=":" element={<ConfigPlaceholder />} />
                <Route path="managers" element={<ConfigPlaceholder />} />
                <Route path="custom-groups" element={<ConfigPlaceholder />} />
                <Route path="reports" element={<ConfigPlaceholder />} />
                <Route path="financial" element={<ConfigPlaceholder />} />
                <Route path="clients" element={<ConfigPlaceholder />} />
                <Route path="tasks" element={<ConfigPlaceholder />} />
                <Route path="marketing" element={<ConfigPlaceholder />} />
              </Route>
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </AuthProvider>
  )
}

export default App