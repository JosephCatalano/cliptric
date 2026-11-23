"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { api, message } from "@/lib/client-api";
import type { CaptionSource } from "@/lib/projects";
import { clock } from "./analysis-range";

const pageSeconds = 30;

export function ClipTranscript({ source }: { source: CaptionSource }) {
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const start = source.start + offset;
  const end = Math.min(source.end, start + pageSeconds);
  const [loaded, setLoaded] = useState<{
    start: number;
    text: string;
    error?: string;
  }>();

  useEffect(() => {
    if (!open || end <= start) return;
    const controller = new AbortController();
    const params = new URLSearchParams({
      start: String(start),
      end: String(end),
      revision: String(source.revision),
    });
    api<{ words: { text: string }[] }>(
      `/api/transcriptions/${source.transcriptionId}/words?${params}`,
      { signal: controller.signal },
    )
      .then(({ words }) => {
        if (!controller.signal.aborted)
          setLoaded({
            start,
            text: words
              .map((word) => word.text)
              .filter(Boolean)
              .join(" "),
          });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setLoaded({ start, text: "", error: message(error) });
      });
    return () => controller.abort();
  }, [open, start, end, source.revision, source.transcriptionId]);

  const current = loaded?.start === start ? loaded : undefined;
  return (
    <details
      className="rounded-lg border p-3"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="cursor-pointer font-medium">
        Selected transcript
      </summary>
      {open && (
        <div className="mt-3 flex flex-col gap-2">
          <p className="text-xs tabular-nums text-muted-foreground">
            {clock(start)} to {clock(end)} in the source
          </p>
          <p role="status" className="wrap-anywhere text-sm">
            {current
              ? (current.error ??
                (current.text || "No speech in this interval."))
              : "Loading transcript…"}
          </p>
          {source.end - source.start > pageSeconds && (
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(offset - pageSeconds)}
              >
                Earlier
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={end >= source.end}
                onClick={() => setOffset(offset + pageSeconds)}
              >
                Later
              </Button>
            </div>
          )}
        </div>
      )}
    </details>
  );
}
