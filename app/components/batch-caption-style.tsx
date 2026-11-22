"use client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { CaptionStylePicker } from "./caption-style-picker";
import { AdvancedOptions } from "./creation-flow";
import { CaptionStyleLibrary } from "./caption-style-library";
import { api, message } from "../../lib/client-api";
import { type CaptionStyle, type Project } from "../../lib/projects";

export function BatchCaptionStyle({
  project,
  selected,
  disabled,
  onBusy,
  onSaved,
  selectedOnly = false,
}: {
  project: Project;
  selected: Set<string>;
  disabled: boolean;
  onBusy: (busy: boolean) => void;
  onSaved: (project: Project) => void;
  selectedOnly?: boolean;
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
    <>
      <div className="flex flex-col gap-3">
        <CaptionStylePicker
          name="selected-caption-preset"
          value={style.preset}
          disabled={disabled}
          onChange={(preset) => setStyle((value) => ({ ...value, preset }))}
        />
        <AdvancedOptions label="Saved caption styles">
          <CaptionStyleLibrary
            value={style}
            onApply={setStyle}
            disabled={disabled}
          />
        </AdvancedOptions>
        <p>
          Applying replaces selected caption styling, including censor settings.
          Rejected clips are excluded.
        </p>
        <Button
          type="button"
          variant="outline"
          disabled={disabled || !chosen.length}
          onClick={() => void apply(false)}
        >
          Replace style on {chosen.length} selected clips
        </Button>
        {!selectedOnly && (
          <Button
            type="button"
            variant="outline"
            disabled={disabled || !eligible.length}
            onClick={() => void apply(true)}
          >
            Replace style on all {eligible.length} eligible clips
          </Button>
        )}
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
      </div>
    </>
  );
}
