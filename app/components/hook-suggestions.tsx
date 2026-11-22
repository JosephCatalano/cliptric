"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { FieldSet, FieldLegend } from "@/components/ui/field";
import { api, message } from "../../lib/client-api";

type Hook = {
  start: number;
  opening_text: string;
  evidence: string;
  context: string;
  reason: string;
  context_start: number;
  context_end: number;
};

export function HookSuggestions({
  projectId,
  itemId,
  recordingId,
  onChoose,
}: {
  projectId: string;
  itemId: string;
  recordingId: string | null;
  onChoose(hook: Hook): void;
}) {
  const [hooks, setHooks] = useState<Hook[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function load() {
    setBusy(true);
    setError("");
    try {
      setHooks(
        await api<Hook[]>(`/api/projects/${projectId}/clips/${itemId}/hooks`),
      );
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <FieldSet>
      <FieldLegend>Source-grounded hooks</FieldLegend>
      <p>
        Review an opening cut from the saved AI-selected passage and original
        transcript. No additional AI request. Check the context and speaker
        meaning before saving.
      </p>
      <Button type="button" variant="outline" disabled={busy} onClick={load}>
        {busy ? "Loading hooks…" : "Review hook suggestion"}
      </Button>
      {error ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {hooks?.length === 0 ? (
        <p>
          No matching source-grounded hook is available for this saved clip.
        </p>
      ) : null}
      {hooks?.map((hook) => (
        <div key={hook.start} className="flex flex-col gap-3">
          <p>Suggested start: {hook.start.toFixed(2)} source seconds</p>
          <blockquote>{hook.evidence}</blockquote>
          <p>Source context: {hook.context}</p>
          {recordingId ? (
            <video
              controls
              preload="none"
              className="w-full"
              aria-label="Original source context for hook review"
              src={`/api/recordings/${recordingId}/media#t=${hook.context_start},${hook.context_end}`}
            />
          ) : null}
          <p>{hook.reason}</p>
          <Button
            type="button"
            variant="outline"
            onClick={() => onChoose(hook)}
          >
            Use this start and opening text
          </Button>
        </div>
      ))}
      <p>
        Choosing fills the editor. Save changes to accept and render; closing
        discards it. Speech is never rearranged. Overlay times stay relative to
        the new clip start and must fit the new duration.
      </p>
    </FieldSet>
  );
}
