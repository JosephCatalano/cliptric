"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { api, message } from "@/lib/client-api";
import type { Recording } from "@/lib/projects";
import { NewBatch } from "./new-batch";
import { WorkspaceLoading } from "./workspace-loading";

export function BatchFromRecording({
  id,
  mode,
}: {
  id: string;
  mode: "keep" | "highlights";
}) {
  const [recording, setRecording] = useState<Recording>();
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const request = new AbortController();
    api<Recording>(`/api/recordings/${encodeURIComponent(id)}`, {
      signal: request.signal,
    })
      .then((value) => {
        if (!request.signal.aborted) setRecording(value);
      })
      .catch((cause) => {
        if (!request.signal.aborted) setError(message(cause));
      });
    return () => request.abort();
  }, [id, retry]);

  if (recording?.id === id)
    return (
      <NewBatch key={id} initialRecordings={[recording]} initialMode={mode} />
    );
  return (
    <section className="project-workspace">
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setError("");
                setRetry((value) => value + 1);
              }}
            >
              Retry
            </Button>
            <Button variant="outline" asChild>
              <Link href="/workspace">Choose another source</Link>
            </Button>
          </div>
        </Alert>
      ) : (
        <WorkspaceLoading label="Loading your recording…" />
      )}
    </section>
  );
}
