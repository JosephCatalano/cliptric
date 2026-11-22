"use client";

import { useEffect, useState } from "react";
import { Progress } from "@/components/ui/progress";

export function ProjectStepProgress({
  progress,
  started,
  updated,
}: {
  progress?: number | null;
  started?: string | null;
  updated?: string | null;
}) {
  const [time, setTime] = useState<number | null>(null);
  useEffect(() => {
    const timer = window.setInterval(() => setTime(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const elapsed =
    time && started
      ? Math.max(0, Math.floor((time - Date.parse(started)) / 1000))
      : null;
  const quiet =
    time && updated
      ? Math.max(0, Math.floor((time - Date.parse(updated)) / 1000))
      : null;
  return (
    <div className="flex flex-col gap-2" aria-label="Current processing step">
      <div className="flex flex-wrap justify-between gap-2 text-sm">
        <span>
          Current step:{" "}
          {progress == null
            ? "Working (percentage unavailable)"
            : `${Math.round(progress * 100)}%`}
        </span>
        {elapsed !== null ? (
          <span>
            Elapsed: {Math.floor(elapsed / 60)}m {elapsed % 60}s
          </span>
        ) : null}
      </div>
      <Progress
        value={progress == null ? undefined : progress * 100}
        aria-label="Current step progress"
      />
      {quiet !== null ? (
        <p className="text-sm text-muted-foreground">
          Last recorded activity: {quiet}s ago.
        </p>
      ) : null}
      {quiet !== null && quiet > 90 ? (
        <p role="status" className="text-sm text-muted-foreground">
          No recent progress has been reported. This step may be slow or
          stalled; cancel and resume if it stays unchanged.
        </p>
      ) : null}
    </div>
  );
}
