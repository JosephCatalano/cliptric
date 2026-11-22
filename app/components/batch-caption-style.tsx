"use client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { CaptionStyleLibrary } from "./caption-style-library";
import { api, message } from "../../lib/client-api";
import { type CaptionStyle, type Project } from "../../lib/projects";

export function BatchCaptionStyle({
  project,
  selected,
  disabled,
  onBusy,
  onSaved,
}: {
  project: Project;
  selected: Set<string>;
  disabled: boolean;
  onBusy: (busy: boolean) => void;
  onSaved: (project: Project) => void;
}) {
  const [style, setStyle] = useState<CaptionStyle>({
    preset: project.settings.caption_preset,
    position: project.settings.caption_position,
    customization: project.settings.caption_customization,
  });
  const [error, setError] = useState("");
  const eligible = project.items.filter((item) => item.review !== "rejected");
  const chosen = eligible.filter((item) => selected.has(item.id));
  async function apply(all: boolean) {
    const targets = (all ? eligible : chosen).map((item) => ({
      id: item.id,
      revision: item.revision,
    }));
    if (!targets.length || disabled) return;
    onBusy(true);
    setError("");
    try {
      const value = await api<Project>(
        `/api/projects/${project.id}/caption-style`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ style, targets }),
        },
      );
      onSaved(value);
    } catch (reason) {
      setError(message(reason));
    } finally {
      onBusy(false);
    }
  }
  return (
    <details>
      <summary>Apply a caption style across clips</summary>
      <div className="flex flex-col gap-3">
        <CaptionStyleLibrary
          value={style}
          onApply={setStyle}
          disabled={disabled}
        />
        <p>
          Style to apply: {style.preset}, {style.position},{" "}
          {style.customization.font_size}px. Initially uses the project
          defaults; load a saved style to change it.
        </p>
        <p>
          Applying replaces caption styling overrides, including caption censor
          settings. Clip-specific emphasis words, transcripts, framing,
          overlays, and previous exports are preserved. Rejected clips are
          excluded.
        </p>
        <p>
          Selected targets ({chosen.length}):{" "}
          {chosen.map((item) => item.title).join(", ") || "None"}.
        </p>
        <p>
          All eligible targets ({eligible.length}):{" "}
          {eligible.map((item) => item.title).join(", ") || "None"}.
        </p>
        <Button
          type="button"
          variant="outline"
          disabled={disabled || !chosen.length}
          onClick={() => void apply(false)}
        >
          Replace style on {chosen.length} selected clips
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={disabled || !eligible.length}
          onClick={() => void apply(true)}
        >
          Replace style on all {eligible.length} eligible clips
        </Button>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
      </div>
    </details>
  );
}
