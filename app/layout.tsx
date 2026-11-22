import type { Metadata } from "next";
import localFont from "next/font/local";
import Link from "next/link";
import "./globals.css";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppHeader, AppSidebar } from "./components/app-navigation";
import { WorkspaceNavigationProvider } from "./components/workspace-navigation";
import { themeScript } from "./lib/theme";

const instrumentSans = localFont({
  src: "./fonts/InstrumentSans.ttf",
  variable: "--font-instrument",
  weight: "400 700",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "Cliptric | Video workspace", template: "%s | Cliptric" },
  applicationName: "Cliptric",
  description:
    "Import a recording, choose a moment, and export a vertical video locally.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`dark ${instrumentSans.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <a href="#main-content" className="skip-link">
          Skip to content
        </a>
        <TooltipProvider>
          <WorkspaceNavigationProvider>
            <SidebarProvider>
              <AppSidebar />
              <SidebarInset id="main-content" tabIndex={-1} className="min-w-0">
                <AppHeader />
                {children}
                <footer className="mt-auto flex flex-wrap items-center justify-center gap-x-6 gap-y-2 border-t px-4 py-4 text-sm text-muted-foreground">
                  <span>Cliptric</span>
                  <nav
                    aria-label="Legal"
                    className="flex flex-wrap gap-x-6 gap-y-2"
                  >
                    <Link
                      href="/terms"
                      className="underline underline-offset-4"
                    >
                      Terms of Service
                    </Link>
                    <Link
                      href="/privacy"
                      className="underline underline-offset-4"
                    >
                      Privacy Policy
                    </Link>
                  </nav>
                </footer>
              </SidebarInset>
            </SidebarProvider>
          </WorkspaceNavigationProvider>
        </TooltipProvider>
      </body>
    </html>
  );
}
