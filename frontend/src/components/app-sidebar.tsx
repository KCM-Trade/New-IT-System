import * as React from "react"
import {
  IconCamera,
  IconChartBar,
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

const data = {
  user: {
    name: "shadcn",
    email: "m@example.com",
    avatar: "/avatars/shadcn.jpg",
  },
  navMain: [
    { title: "客户盈亏监控", url: "/customer-pnl-monitor", icon: IconReport },
    { title: "客户交易分析", url: "/client-trading", icon: IconReport },
    { title: "基差分析", url: "/basis", icon: IconDashboard },
    { title: "数据下载", url: "/downloads", icon: IconListDetails },
    {
      title: "报仓数据",
      icon: IconChartBar,
      children: [
        { title: "产品报仓", url: "/warehouse/products" },
        { title: "全仓报表", url: "/position" },
        { title: "其他", url: "/warehouse/others" },
      ],
    },
    { title: "Login IP监测", url: "/login-ips", icon: IconUsers },
    { title: "利润分析", url: "/profit", icon: IconReport },
    {
      title: "其他",
      icon: IconSettings,
      children: [
        { title: "模板", url: "/template" },
        { title: "代理统计Global", url: "/warehouse/agent-global" },
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
    { title: "Settings", url: "/settings", icon: IconSettings },
    { title: "Get Help", url: "https://ui.shadcn.com/docs/installation", icon: IconHelp }, 
    { title: "Search", url: "/search", icon: IconSearch },
  ],
  documents: [
    { name: "Managers", url: "/cfg/managers", icon: IconDatabase },
    { name: "自定义组别", url: "/cfg/custom-groups", icon: IconSettings },
    { name: "Reports", url: "/cfg/reports", icon: IconReport },
    { name: "Financial", url: "/cfg/financial", icon: IconFileWord },
    { name: "Clients", url: "/cfg/clients", icon: IconDatabase },
    { name: "Tasks", url: "/cfg/tasks", icon: IconReport },
    { name: "Marketing", url: "/cfg/marketing", icon: IconFileWord },
    
  ],
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
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
