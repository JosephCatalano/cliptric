import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { SiteShell } from "./components/site-shell";
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
      className={instrumentSans.variable}
      data-theme="dark"
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <a href="#main-content" className="skip-link">
          Skip to content
        </a>
        <SiteShell>{children}</SiteShell>
      </body>
    </html>
  );
}
