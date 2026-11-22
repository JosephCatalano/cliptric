"use client";

import { useEffect, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { api, message } from "@/lib/client-api";

export function useImportLimits() {
  const [maxBytes, setMaxBytes] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api<{ max_upload_bytes: number }>("/api/health", {
      signal: controller.signal,
    })
      .then((value) => {
        if (controller.signal.aborted) return;
        if (
          !Number.isSafeInteger(value.max_upload_bytes) ||
          value.max_upload_bytes <= 0 ||
          value.max_upload_bytes > 100 * 1024 ** 3
        )
          throw new Error("The API returned an invalid upload limit.");
        setMaxBytes(value.max_upload_bytes);
        setError("");
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setError(`Could not load the upload limit. ${message(error)}`);
      });
    return () => controller.abort();
  }, [reload]);
  const label =
    maxBytes === null
      ? "Checking upload limit..."
      : maxBytes >= 1024 ** 3
        ? `${(maxBytes / 1024 ** 3).toLocaleString()} GiB`
        : `${maxBytes.toLocaleString()} bytes`;
  return {
    maxBytes,
    label,
    error,
    retry: () => {
      setError("");
      setMaxBytes(null);
      setReload((value) => value + 1);
    },
  };
}

export function ImportLimitNotice({
  limits,
}: {
  limits: ReturnType<typeof useImportLimits>;
}) {
  return limits.error ? (
    <Alert variant="destructive">
      <AlertDescription>
        {limits.error}
        <Button type="button" variant="outline" onClick={limits.retry}>
          Retry connection
        </Button>
      </AlertDescription>
    </Alert>
  ) : null;
}
