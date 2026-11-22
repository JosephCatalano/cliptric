"use client";

import { useEffect, useState } from "react";
import { api, message } from "../../lib/client-api";
import type { Recording } from "../../lib/projects";

type Download = {
  id: string;
  status: string;
  downloaded_bytes: number;
  total_bytes: number | null;
  recording_id: string | null;
  error: string | null;
};

export function useSetupDownload(
  url: string | null,
  onReady: (recording: Recording) => void,
) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{
    url: string;
    job: Download | null;
    recording: Recording | null;
    error: string;
  } | null>(null);
  useEffect(() => {
    if (!url) return;
    const sourceUrl = url;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let job: Download | null = null;
    async function inspect() {
      try {
        job = job
          ? await api<Download>(`/api/video-imports/${job.id}`, {
              signal: controller.signal,
            })
          : await api<Download>("/api/video-imports", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ url: sourceUrl }),
              signal: controller.signal,
            });
        if (controller.signal.aborted) return;
        if (["failed", "cancelled", "interrupted"].includes(job.status))
          throw new Error(
            job.error || "Download stopped. Retry to prepare the video.",
          );
        if (job.status === "succeeded" && !job.recording_id)
          throw new Error(
            "The download has no playable recording. Retry the download.",
          );
        const recording =
          job.status === "succeeded" && job.recording_id
            ? await api<Recording>(`/api/recordings/${job.recording_id}`, {
                signal: controller.signal,
              })
            : null;
        if (controller.signal.aborted) return;
        setState({ url: sourceUrl, job, recording, error: "" });
        if (recording) onReady(recording);
        if (!recording) timer = setTimeout(inspect, 1000);
      } catch (error) {
        if (!controller.signal.aborted)
          setState({
            url: sourceUrl,
            job,
            recording: null,
            error: message(error),
          });
      }
    }
    void inspect();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [url, attempt, onReady]);
  const current = state?.url === url ? state : null;
  const percent = current?.recording
    ? 100
    : current?.job?.total_bytes
      ? Math.min(
          100,
          Math.floor(
            (current.job.downloaded_bytes / current.job.total_bytes) * 100,
          ),
        )
      : null;
  return { ...current, percent, retry: () => setAttempt((value) => value + 1) };
}
