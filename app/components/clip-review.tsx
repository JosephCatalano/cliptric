"use client";
import { FramingEditor } from "./framing-editor";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, message } from "@/lib/client-api";
import { OPENING_HEADLINE_SECONDS } from "@/lib/clip-preview";
import { itemEdit } from "@/lib/project-edit";
import { useUnsavedEdits } from "./use-unsaved-edits";
import type { Project, ProjectItem } from "@/lib/projects";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";

export function ClipReview({
  project,
  items,
  currentId,
  onCurrent,
  onClose,
  onEdit,
  onSaved,
  locked,
}: {
  project: Project;
  items: ProjectItem[];
  currentId: string;
  locked: boolean;
  onCurrent: (id: string) => void;
  onClose: () => void;
  onEdit: (id: string) => void;
  onSaved: (value: Project) => void;
}) {
  const index = Math.max(
    0,
    items.findIndex((item) => item.id === currentId),
  );
  const item = items[index];
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  function leave(action: () => void) {
    if (busy || (dirty && !window.confirm("Discard unsaved clip edits?")))
      return;
    setDirty(false);
    action();
  }
  return (
    <Dialog
      open
      onOpenChange={(value) => {
        if (!value) leave(onClose);
      }}
    >
      <DialogContent className="max-h-[94dvh] overflow-y-auto sm:max-w-6xl">
        <DialogHeader>
          <DialogTitle>
            Review clip {index + 1} of {items.length}
          </DialogTitle>
          <DialogDescription>
            Use the arrow keys for previous/next, Space to play, A to accept, R
            to reject, and P to toggle Posted. Shortcuts pause while typing or
            editing.
          </DialogDescription>
        </DialogHeader>
        {locked ? (
          <Alert>
            <AlertDescription>
              Processing is still running. Playback is available; editing and
              review actions resume when processing finishes.
            </AlertDescription>
          </Alert>
        ) : null}
        {item ? (
          <ReviewItem
            key={`${item.id}-${item.revision}`}
            project={project}
            item={item}
            locked={locked}
            onSaved={onSaved}
            onDirty={setDirty}
            onBusy={setBusy}
            onNext={() =>
              leave(() =>
                onCurrent(items[Math.min(items.length - 1, index + 1)].id),
              )
            }
            onPrevious={() =>
              leave(() => onCurrent(items[Math.max(0, index - 1)].id))
            }
          />
        ) : (
          <p>This clip is no longer available.</p>
        )}
        <div className="flex flex-wrap justify-between gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={!index || busy}
            onClick={() => leave(() => onCurrent(items[index - 1].id))}
          >
            Previous
          </Button>
          {item && <Button type="button" variant="outline" disabled={locked || busy} onClick={() => leave(() => onEdit(item.id))}>Open editor</Button>}
          <Button
            type="button"
            variant="outline"
            disabled={index >= items.length - 1 || busy}
            onClick={() => leave(() => onCurrent(items[index + 1].id))}
          >
            Next clip
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ReviewItem({
  project,
  item,
  locked,
  onSaved,
  onNext,
  onPrevious,
  onDirty,
  onBusy,
}: {
  project: Project;
  item: ProjectItem;
  locked: boolean;
  onSaved: (value: Project) => void;
  onNext: () => void;
  onPrevious: () => void;
  onDirty: (value: boolean) => void;
  onBusy: (value: boolean) => void;
}) {
  const player = useRef<HTMLVideoElement>(null);
  const [previewSlot, setPreviewSlot] = useState<HTMLDivElement | null>(null);
  const setPlayer = useCallback((node: HTMLVideoElement | null) => {
    player.current = node;
  }, []);
  const [title, setTitle] = useState(item.title);
  const [text, setText] = useState(item.opening_text ?? "");
  const [headline, setHeadline] = useState(item.headline_enabled);
  const [position, setPosition] = useState(item.headline_position);
  const [captions, setCaptions] = useState(item.caption_position);
  const originalFraming = {
    mode: item.framing ?? project.settings.framing,
    layout: item.framing
      ? (item.split_layout ?? null)
      : (project.settings.split_layout ?? null),
    crop: item.framing
      ? (item.crop_region ?? null)
      : (project.settings.crop_region ?? null),
  };
  const [framing, setFraming] = useState(originalFraming);
  const framingChanged =
    JSON.stringify(framing) !== JSON.stringify(originalFraming);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dirty =
    title !== item.title ||
    text !== (item.opening_text ?? "") ||
    headline !== item.headline_enabled ||
    position !== item.headline_position ||
    captions !== item.caption_position ||
    framingChanged;
  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);
  useUnsavedEdits(dirty);
  async function update(
    operation: "accepted" | "rejected" | "posted" | "save",
  ) {
    if (busy || locked || (operation !== "save" && dirty)) return;
    setBusy(true);
    onBusy(true);
    setError("");
    try {
      const path = `/api/projects/${project.id}`;
      const value = await api<Project>(
        operation === "save" ? `${path}/clips/${item.id}` : `${path}/bulk`,
        {
          method: operation === "save" ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            operation === "save"
              ? {
                  ...itemEdit(item),
                  title,
                  opening_text: text.trim() || null,
                  headline_enabled: headline,
                  headline_position: position,
                  caption_position: captions,
                  ...(framingChanged
                    ? {
                        framing: framing.mode,
                        split_layout: framing.layout,
                        crop_region: framing.crop,
                        quality: { ...item.quality, smart_framing: null },
                      }
                    : {}),
                }
              : {
                  targets: [{ id: item.id, revision: item.revision }],
                  ...(operation === "posted"
                    ? { action: "posted", posted: !item.posted }
                    : { action: "review", review: operation }),
                },
          ),
        },
      );
      onDirty(false);
      onSaved(value);
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  function keyboard(event: React.KeyboardEvent) {
    if (
      event.defaultPrevented ||
      event.repeat ||
      event.ctrlKey ||
      event.metaKey ||
      event.altKey ||
      (event.target instanceof HTMLElement &&
        event.target.closest(
          "input,textarea,select,button,[contenteditable=true],video",
        ))
    )
      return;
    const key = event.key.toLowerCase();
    if (!["arrowright", "arrowleft", " ", "a", "r", "p"].includes(key)) return;
    event.preventDefault();
    if (key === "arrowright") onNext();
    else if (key === "arrowleft") onPrevious();
    else if (key === " " && player.current) {
      if (player.current.paused)
        void player.current.play().catch((error) => setError(message(error)));
      else player.current.pause();
    } else if (key === "a") void update("accepted");
    else if (key === "r") void update("rejected");
    else if (key === "p") void update("posted");
  }
  return (
    <div
      onKeyDown={keyboard}
      className="grid items-start gap-6 lg:grid-cols-[minmax(16rem,0.85fr)_minmax(0,1.15fr)]"
      tabIndex={0}
      aria-label="Clip review workspace"
    >
      <div ref={setPreviewSlot} className="clip-preview-column" />
      <div className="flex min-w-0 flex-col gap-4">
        <p>
          {item.start.toFixed(1)}–{item.end.toFixed(1)}s in the source
        </p>
        <FramingEditor
          mode={framing.mode}
          layout={framing.layout}
          cropRegion={framing.crop}
          followPlan={item.export?.follow_plan}
          autoLayout={item.export?.auto_layout}
          previewSlot={previewSlot}
          previewOptions={{
            savedExportId:
              item.export?.status === "succeeded" ? item.export.id : null,
            onPlaybackTarget: setPlayer,
            transcriptionId: item.transcription_id ?? project.transcription_id,
            transcriptRevision:
              item.transcript_revision ?? project.transcript_revision,
            captions: {
              preset: item.caption_preset,
              position: captions,
              customization: item.caption_customization,
            },
            headline: headline
              ? {
                  text: text.trim() || title,
                  position,
                  seconds: OPENING_HEADLINE_SECONDS,
                }
              : null,
            emphasis: item.emphasis_words,
            overlays: item.overlays,
          }}
          quality={
            framingChanged
              ? { ...item.quality, smart_framing: null }
              : item.quality
          }
          sourceUrl={
            project.recording_id || item.recording_id
              ? `/api/recordings/${item.recording_id ?? project.recording_id}/media`
              : undefined
          }
          timestamp={item.start}
          end={item.end}
          disabled={locked || busy}
          onChange={(mode, layout, crop) => setFraming({ mode, layout, crop })}
        />
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">{item.review}</Badge>
          <Badge variant="outline">{item.posted ? "Posted" : "Unposted"}</Badge>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            disabled={locked || busy || dirty}
            onClick={() => void update("accepted")}
          >
            Accept
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={locked || busy || dirty}
            onClick={() => void update("rejected")}
          >
            Reject
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={locked || busy || dirty}
            onClick={() => void update("posted")}
          >
            {item.posted ? "Mark unposted" : "Mark posted"}
          </Button>
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void update("save");
          }}
        >
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor={`review-title-${item.id}`}>
                Clip title
              </FieldLabel>
              <Input
                id={`review-title-${item.id}`}
                value={title}
                required
                maxLength={120}
                disabled={busy || locked}
                onChange={(event) => setTitle(event.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor={`review-headline-${item.id}`}>
                Opening headline (blank uses title)
              </FieldLabel>
              <Input
                id={`review-headline-${item.id}`}
                value={text}
                maxLength={120}
                disabled={busy || locked}
                onChange={(event) => setText(event.target.value)}
              />
            </Field>
            <Field orientation="horizontal">
              <Checkbox
                id={`review-enabled-${item.id}`}
                checked={headline}
                disabled={busy || locked}
                onCheckedChange={(value) => setHeadline(value === true)}
              />
              <FieldLabel htmlFor={`review-enabled-${item.id}`}>
                Show headline
              </FieldLabel>
            </Field>
            <Field>
              <FieldLabel htmlFor={`review-position-${item.id}`}>
                Headline position
              </FieldLabel>
              <NativeSelect
                id={`review-position-${item.id}`}
                value={position}
                disabled={busy || locked}
                onChange={(event) =>
                  setPosition(event.target.value as typeof position)
                }
              >
                <NativeSelectOption value="auto">Auto</NativeSelectOption>
                <NativeSelectOption value="upper">Upper</NativeSelectOption>
                <NativeSelectOption value="centre">Centre</NativeSelectOption>
              </NativeSelect>
            </Field>
            <Field>
              <FieldLabel htmlFor={`review-captions-${item.id}`}>
                Caption position
              </FieldLabel>
              <NativeSelect
                id={`review-captions-${item.id}`}
                value={captions}
                disabled={busy || locked}
                onChange={(event) =>
                  setCaptions(event.target.value as typeof captions)
                }
              >
                <NativeSelectOption value="auto">Auto</NativeSelectOption>
                <NativeSelectOption value="lower">Lower</NativeSelectOption>
                <NativeSelectOption value="centre">Centre</NativeSelectOption>
                <NativeSelectOption value="upper">Upper</NativeSelectOption>
              </NativeSelect>
            </Field>
            <p role="status" className="text-sm text-muted-foreground">
              {busy
                ? "Saving…"
                : dirty
                  ? "Unsaved changes. Save before using review actions."
                  : "Saved"}
            </p>
            <Button type="submit" disabled={!dirty || busy || locked}>
              Save changes
            </Button>
            {error ? (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}
          </FieldGroup>
        </form>
      </div>
    </div>
  );
}
