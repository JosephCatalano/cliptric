"use client";

import "./globals.css";
import PageError from "./error";

export default function GlobalError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <html lang="en" data-theme="dark">
      <body>
        <main>
          <PageError error={error} retry={retry} />
        </main>
      </body>
    </html>
  );
}
