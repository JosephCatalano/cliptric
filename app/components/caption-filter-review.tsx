"use client";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { api, message } from "../../lib/client-api";
import {
  type CaptionCustomization,
  type CaptionSource,
} from "../../lib/projects";

type Match = {
  id: number;
  start: number;
  end: number;
  original: string;
  display: string;
};
export function CaptionFilterReview({
  source,
  customization,
  disabled,
}: {
  source: CaptionSource;
  customization: CaptionCustomization;
  disabled?: boolean;
}) {
  const [result, setResult] = useState<{
    key: string;
    matches: Match[];
    truncated: boolean;
    total_matches: number;
  } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const action = useRef<AbortController | null>(null);
  const key = JSON.stringify({ source, customization });
  useEffect(() => () => action.current?.abort(), []);
  async function review() {
    if (action.current) return;
    const controller = new AbortController();
    action.current = controller;
    setBusy(true);
    setError("");
    try {
      const response = await api<{
        matches: Match[];
        truncated: boolean;
        total_matches: number;
      }>(`/api/transcriptions/${source.transcriptionId}/caption-review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          revision: source.revision,
          start: source.start,
          end: source.end,
          customization,
        }),
      });
      if (!controller.signal.aborted)
        setResult({
          key,
          matches: response.matches,
          truncated: response.truncated,
          total_matches: response.total_matches,
        });
    } catch (reason) {
      if (!controller.signal.aborted) setError(message(reason));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      action.current = null;
    }
  }
  return (
    <div className="flex flex-col gap-2">
      <Button
        type="button"
        variant="outline"
        disabled={
          disabled ||
          busy ||
          !Number.isFinite(source.start) ||
          source.end <= source.start
        }
        onClick={() => void review()}
      >
        {busy ? "Checking caption matches..." : "Review caption censor matches"}
      </Button>
      {result &&
        result.key === key &&
        (result.matches.length ? (
          <ul>
            {result.matches.map((match) => (
              <li key={match.id}>
                {match.start.toFixed(2)}–{match.end.toFixed(2)}s:{" "}
                {match.original} → {match.display}
              </li>
            ))}
          </ul>
        ) : (
          <p>No exact-word matches in this saved interval.</p>
        ))}
      {result?.key === key && result.truncated && (
        <p>
          Showing the first 200 of {result.total_matches} matches. Review a
          smaller interval to inspect the rest.
        </p>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
    </div>
  );
}
