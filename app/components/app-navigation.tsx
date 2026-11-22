"use client";
import { ProcessingNotices } from "./processing-notices";

import Image from "next/image";
import Link from "next/link";
import type { MouseEvent } from "react";
import { useWorkspaceNavigation } from "./workspace-navigation";
import { usePathname } from "next/navigation";
import {
  CalendarDaysIcon,
  FolderOpenIcon,
  LinkIcon,
  MonitorCheckIcon,
  HouseIcon,
  ScissorsIcon,
} from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

const destinations = [
  { href: "/", label: "Home", icon: HouseIcon },
  { href: "/projects", label: "Projects", icon: FolderOpenIcon },
  { href: "/studio", label: "Clip editor", icon: ScissorsIcon },
  { href: "/calendar", label: "Schedule", icon: CalendarDaysIcon },
  { href: "/connections", label: "Accounts", icon: LinkIcon },
];

function currentDestination(pathname: string) {
  if (pathname.startsWith("/projects/new/")) return "/";
  if (pathname.startsWith("/projects/")) return "/projects";
  if (pathname.startsWith("/publish/")) return "/connections";
  return pathname;
}

export function AppSidebar() {
  const current = currentDestination(usePathname());
  const { setOpenMobile } = useSidebar();
  const { locked } = useWorkspaceNavigation();
  function navigate(event: MouseEvent<HTMLAnchorElement>) {
    if (locked) {
      event.preventDefault();
      return;
    }
    setOpenMobile(false);
  }
  return (
    <Sidebar
      collapsible="icon"
      mobileBehaviour="rail"
      aria-label="Cliptric navigation"
    >
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild size="lg" tooltip="Cliptric">
              <Link
                href="/"
                aria-label="Cliptric home"
                aria-disabled={locked}
                onClick={navigate}
              >
                <Image
                  src="/icon.svg"
                  alt=""
                  width={32}
                  height={32}
                  unoptimized
                  className="size-8 shrink-0"
                />
                <span className="truncate text-base font-semibold">
                  Cliptric
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Workspace</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {destinations.map((destination) => {
                const Icon = destination.icon;
                return (
                  <SidebarMenuItem key={destination.href}>
                    <SidebarMenuButton
                      asChild
                      isActive={current === destination.href}
                      tooltip={destination.label}
                    >
                      <Link
                        href={destination.href}
                        aria-label={destination.label}
                        aria-current={
                          current === destination.href ? "page" : undefined
                        }
                        aria-disabled={locked}
                        onClick={navigate}
                      >
                        <Icon aria-hidden="true" />
                        <span>{destination.label}</span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground group-data-[collapsible=icon]:justify-center">
          <MonitorCheckIcon className="size-4" aria-hidden="true" />
          <span className="group-data-[collapsible=icon]:hidden">
            Source video stays local
          </span>
        </div>
      </SidebarFooter>
      <SidebarRail className="max-md:hidden" />
    </Sidebar>
  );
}

export function AppHeader() {
  const pathname = usePathname();
  const { locked } = useWorkspaceNavigation();
  const current = currentDestination(pathname);
  const title = pathname.startsWith("/publish/x/")
    ? "Review X post"
    : pathname.startsWith("/publish/facebook/")
      ? "Review Facebook Reel"
      : pathname.startsWith("/publish/instagram/")
        ? "Review Instagram Reel"
        : pathname.startsWith("/publish/youtube/")
          ? "Review YouTube upload"
          : pathname.startsWith("/publish/tiktok/")
            ? "Review TikTok post"
            : pathname.startsWith("/projects/new/")
              ? "Set up your project"
              : (destinations.find(
                  (destination) => destination.href === current,
                )?.label ?? "Workspace");
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b px-4 md:px-6">
      <SidebarTrigger className="hidden md:inline-flex" />
      <Separator orientation="vertical" className="hidden h-4 md:block" />
      <span className="min-w-0 flex-1 truncate text-sm font-medium">
        {title}
      </span>
      {locked ? (
        <Badge variant="outline">Save or discard studio edits</Badge>
      ) : null}
      <ProcessingNotices />
    </header>
  );
}
