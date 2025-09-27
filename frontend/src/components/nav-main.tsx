import { useState } from "react"
import { IconChevronDown, type Icon } from "@tabler/icons-react"
import { Link, useLocation } from "react-router-dom"
import { cn } from "@/lib/utils"

import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubItem,
  SidebarMenuSubButton,
} from "@/components/ui/sidebar"

export function NavMain({
  items,
}: {
  items: {
    title: string
    url?: string
    icon?: Icon
    children?: { title: string; url: string }[]
  }[]
}) {
  // Local open/close state keyed by item title
  const [openKeys, setOpenKeys] = useState<Record<string, boolean>>({})
  const { pathname } = useLocation()

  function toggle(key: string) {
    setOpenKeys((prev: Record<string, boolean>) => ({ ...prev, [key]: !prev[key] }))
  }

  return (
    <SidebarGroup>
      <SidebarGroupContent className="flex flex-col gap-2">
        
        <SidebarMenu>
          {items.map((item) => {
            const hasChildren = !!item.children && item.children.length > 0
            const isOpen = !!openKeys[item.title]

            if (!hasChildren) {
              const isActive = !!item.url && (pathname === item.url || pathname.startsWith(item.url + "/"))
              return (
                <SidebarMenuItem key={item.title}>
                  <SidebarMenuButton
                    asChild
                    tooltip={item.title}
                    className={cn(isActive && "bg-muted shadow-sm")}
                  >
                    <Link to={item.url ?? "#"}>
                      {item.icon && <item.icon />}
                      <span>{item.title}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )
            }

            const groupActive = !!item.children?.some((c) => pathname === c.url || pathname.startsWith(c.url + "/"))
            return (
              <SidebarMenuItem key={item.title}>
                <SidebarMenuButton
                  tooltip={item.title}
                  onClick={() => toggle(item.title)}
                  aria-expanded={isOpen}
                  data-state={isOpen ? "open" : "closed"}
                  className={cn(groupActive && "bg-muted shadow-sm")}
                >
                  {item.icon && <item.icon />}
                  <span>{item.title}</span>
                  <IconChevronDown className={`ml-auto transition-transform ${isOpen ? "rotate-180" : "rotate-0"}`} />
                </SidebarMenuButton>
                <div
                  className={`overflow-hidden transition-[max-height,opacity] duration-300 ${isOpen ? "max-h-96 opacity-100" : "max-h-0 opacity-0"}`}
                >
                  <SidebarMenuSub>
                    {item.children!.map((sub) => {
                      const isSubActive = pathname === sub.url || pathname.startsWith(sub.url + "/")
                      return (
                        <SidebarMenuSubItem key={sub.title}>
                          <SidebarMenuSubButton asChild className={cn(isSubActive && "bg-muted shadow-sm")}>
                            <Link to={sub.url}>
                              <span>{sub.title}</span>
                            </Link>
                          </SidebarMenuSubButton>
                        </SidebarMenuSubItem>
                      )
                    })}
                  </SidebarMenuSub>
                </div>
              </SidebarMenuItem>
            )
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}
