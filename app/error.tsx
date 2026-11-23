"use client";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export default function PageError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <section className="mx-auto flex w-full max-w-xl flex-col items-start gap-4 p-6">
      <Alert variant="destructive">
        <AlertTitle>This page could not open</AlertTitle>
        <AlertDescription>
          <p>
            Cliptric hit an unexpected error while loading this view. Try
            opening it again. If it keeps failing, share the page address and
            any reference shown below.
          </p>
          {error.digest ? <p>Error reference {error.digest}</p> : null}
        </AlertDescription>
      </Alert>
      <Button type="button" onClick={retry}>
        Reopen page
      </Button>
    </section>
  );
}
