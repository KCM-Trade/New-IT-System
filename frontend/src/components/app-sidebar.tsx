import * as React from "react"
import {
  IconCamera,
  IconChartBar,
  IconChartCandle,
  IconDashboard,
  IconDatabase,
  IconFileAi,
  IconFileDescription,
  IconFileWord,
  IconHelp,
  IconListDetails,
  IconReport,
  IconSearch,
  IconSettings,
  IconUsers,
} from "@tabler/icons-react"

import { NavDocuments } from "@/components/nav-documents"
import { NavMain } from "@/components/nav-main"
import { NavSecondary } from "@/components/nav-secondary"
import { NavUser } from "@/components/nav-user"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
} from "@/components/ui/sidebar"
import { useI18n } from "@/components/i18n-provider"

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const { t } = useI18n()
  
  // Navigation data with translations
  const data = React.useMemo(() => ({
    user: {
      name: "shadcn",
      email: "m@example.com",
      avatar: "/avatars/shadcn.jpg",
    },
    navMain: [
      // fresh grad: Equity - Monitor is placed above AccountID PnL Monitor in the trading accounts section.
      { title: t("nav.equityMonitor"), url: "/equity-monitor", icon: IconChartBar },
      { title: t("nav.customerPnLMonitorV2"), url: "/customer-pnl-monitor-v2", icon: IconChartCandle },
      { title: t("nav.clientPnLMonitor"), url: "/client-pnl-monitor", icon: IconUsers },
      { title: t("nav.swapFreeControl"), url: "/swap-free-control", icon: IconSettings },
      { title: t("nav.clientTrading"), url: "/client-trading", icon: IconReport },
      { title: t("nav.basisAnalysis"), url: "/basis", icon: IconDashboard },
      { title: t("nav.downloads"), url: "/downloads", icon: IconListDetails },
      {
        title: t("nav.warehouse"),
        icon: IconChartBar,
        children: [
          { title: t("nav.warehouseProducts"), url: "/warehouse/products" },
          { title: t("nav.position"), url: "/position" },
          { title: t("nav.warehouseOthers"), url: "/warehouse/others" },
        ],
      },
      { title: t("nav.loginIPs"), url: "/login-ips", icon: IconUsers },
      { title: t("nav.profitAnalysis"), url: "/profit", icon: IconReport },
      {
        title: t("nav.others"),
        icon: IconSettings,
        children: [
          { title: t("nav.template"), url: "/template" },
          { title: t("nav.agentGlobal"), url: "/warehouse/agent-global" },
          { title: t("nav.customerPnLMonitor"), url: "/customer-pnl-monitor" },
        ],
      },
    ],
    navClouds: [
      {
        title: "Capture",
        icon: IconCamera,
        isActive: true,
        url: "#",
        items: [
          {
            title: "Active Proposals",
            url: "#",
          },
          {
            title: "Archived",
            url: "#",
          },
        ],
      },
      {
        title: "Proposal",
        icon: IconFileDescription,
        url: "#",
        items: [
          {
            title: "Active Proposals",
            url: "#",
          },
          {
            title: "Archived",
            url: "#",
          },
        ],
      },
      {
        title: "Prompts",
        icon: IconFileAi,
        url: "#",
        items: [
          {
            title: "Active Proposals",
            url: "#",
          },
          {
            title: "Archived",
            url: "#",
          },
        ],
      },
    ],
    navSecondary: [
      { title: t("common.settings"), url: "/settings", icon: IconSettings },
      { title: t("nav.getHelp"), url: "https://ui.shadcn.com/docs/installation", icon: IconHelp }, 
      { title: t("common.search"), url: "/search", icon: IconSearch },
    ],
    documents: [
      { name: t("config.managers"), url: "/cfg/managers", icon: IconDatabase },
      { name: t("config.customGroups"), url: "/cfg/custom-groups", icon: IconSettings },
      { name: t("config.reports"), url: "/cfg/reports", icon: IconReport },
      { name: t("config.financial"), url: "/cfg/financial", icon: IconFileWord },
      { name: t("config.clients"), url: "/cfg/clients", icon: IconDatabase },
      { name: t("config.tasks"), url: "/cfg/tasks", icon: IconReport },
      { name: t("config.marketing"), url: "/cfg/marketing", icon: IconFileWord },
    ],
  }), [t])

  return (
    <Sidebar collapsible="offcanvas" {...props}>
      <SidebarHeader>
        <div className="flex items-center gap-2 px-3 py-2">
          <img src="/logo.svg" alt="Company" className="h-24 w-auto block" />
          {/* <span className="text-base font-semibold">KCM Trade</span> */}
        </div>
      </SidebarHeader>
      <SidebarContent>
        <NavMain items={data.navMain} />
        <NavDocuments items={data.documents} />
        <NavSecondary items={data.navSecondary} className="mt-auto" />
      </SidebarContent>
      <SidebarFooter>
        <NavUser user={data.user} />
      </SidebarFooter>
    </Sidebar>
  )
}
