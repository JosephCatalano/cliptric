"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppHeader, AppSidebar } from "./app-navigation";
import { WorkspaceNavigationProvider } from "./workspace-navigation";

const publicPages = new Set(["/about", "/privacy", "/terms", "/data-deletion"]);

const legalFooter = (
  <footer className="mt-auto flex flex-wrap items-center justify-center gap-x-6 gap-y-2 border-t px-4 py-4 text-sm text-muted-foreground">
    <span>Cliptric</span>
    <nav aria-label="Legal" className="flex flex-wrap gap-x-6 gap-y-2">
      <Link href="/privacy" className="underline underline-offset-4">Privacy Policy</Link>
      <Link href="/terms" className="underline underline-offset-4">Terms of Service</Link>
      <Link href="/data-deletion" className="underline underline-offset-4">Data deletion</Link>
    </nav>
  </footer>
);

export function SiteShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <TooltipProvider>
      <WorkspaceNavigationProvider>
        <SidebarProvider>
          {publicPages.has(pathname) ? (
            <div className="flex min-h-svh w-full flex-col">
              <main id="main-content" tabIndex={-1} className="flex-1">{children}</main>
              {legalFooter}
            </div>
          ) : (
            <>
              <AppSidebar />
              <SidebarInset id="main-content" tabIndex={-1} className="min-w-0">
                <AppHeader />
                {children}
                {legalFooter}
              </SidebarInset>
            </>
          )}
        </SidebarProvider>
      </WorkspaceNavigationProvider>
    </TooltipProvider>
  );
}
