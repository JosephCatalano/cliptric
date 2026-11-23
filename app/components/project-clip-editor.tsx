"use client";

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { flushSync } from "react-dom";
import { ArrowLeftIcon } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, message } from "@/lib/client-api";
import { OPENING_HEADLINE_SECONDS } from "@/lib/clip-preview";
import {
  defaultQuality,
  parseEmphasis,
  type CaptionStyle,
  type FramingMode,
  type Preset,
  type Project,
  type ProjectItem,
  type QualityOptions,
  type SourceRegion,
  type SplitLayout,
} from "@/lib/projects";
import {
  CaptionCustomizationFields,
  captionCustomizationFromForm,
} from "./caption-customization-fields";
import { CaptionEmphasisField } from "./caption-emphasis-field";
import { CaptionStyleLibrary } from "./caption-style-library";
import { CaptionStylePicker } from "./caption-style-picker";
import { FramingEditor } from "./framing-editor";
import { ClipTranscript } from "./clip-transcript";
import { AnalysisRange } from "./analysis-range";
import { HookSuggestions } from "./hook-suggestions";
import { OutputQualityControls } from "./output-quality-controls";
import { TextOverlayFields, readOverlays } from "./text-overlay-fields";
import { useUnsavedEdits } from "./use-unsaved-edits";

const tabs = ["Content", "Captions", "Layout", "Audio & quality", "Export"];

export default function ProjectClipEditor({
  project: savedProject,
  item,
  locked,
  onSaved,
  onBack,
}: {
  project: Project;
  item: ProjectItem;
  locked: boolean;
  onSaved(value: Project): void;
  onBack(): void;
}) {
  const project: Project = item.recording_id
    ? {
        ...savedProject,
        recording_id: item.recording_id,
        transcription_id: item.transcription_id ?? null,
        transcript_revision: item.transcript_revision ?? null,
        settings: {
          ...savedProject.settings,
          analysis_start: 0,
          analysis_end: item.source_duration ?? item.end,
        },
      }
    : savedProject;
  const fieldId = useId();
  const [editing] = useState(item);
  const [captionStyle, setCaptionStyle] = useState<CaptionStyle>({
    preset: item.caption_preset,
    position: item.caption_position,
    customization: item.caption_customization,
  });
  const [draftStart, setDraftStart] = useState(String(item.start));
  const [quality, setQuality] = useState<QualityOptions>(
    item.quality ?? defaultQuality,
  );
  const [draftEnd, setDraftEnd] = useState(String(item.end));
  const [previewSlot, setPreviewSlot] = useState<HTMLDivElement | null>(null);
  const [draftTitle, setDraftTitle] = useState(item.title);
  const [draftHeadline, setDraftHeadline] = useState(item.headline_enabled);
  const [draftHeadlinePosition, setDraftHeadlinePosition] = useState(
    item.headline_position,
  );
  const [openingText, setOpeningText] = useState(item.opening_text ?? "");
  const [draftOverlays, setDraftOverlays] = useState(item.overlays ?? []);
  const [draftEmphasis, setDraftEmphasis] = useState(
    (item.emphasis_words ?? []).join(", "),
  );
  const [reassess, setReassess] = useState(false);
  const [framing, setFraming] = useState<FramingMode>(
    item.framing ?? project.settings.framing,
  );
  const [splitLayout, setSplitLayout] = useState<SplitLayout | null>(
    item.framing
      ? (item.split_layout ?? null)
      : (project.settings.split_layout ?? null),
  );
  const [cropRegion, setCropRegion] = useState<SourceRegion | null>(
    item.framing
      ? (item.crop_region ?? null)
      : (project.settings.crop_region ?? null),
  );
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  useUnsavedEdits(dirty);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const value = await api<Project>(
        `/api/projects/${project.id}/clips/${item.id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            revision: editing.revision,
            review: form.get("review"),
            title: form.get("title"),
            headline_enabled: form.get("headline_enabled") === "on",
            headline_position: form.get("headline_position"),
            opening_text: String(form.get("opening_text") ?? "").trim() || null,
            overlays: readOverlays(form),
            framing,
            split_layout: splitLayout,
            crop_region: cropRegion,
            quality:
              form.get("auto_presentation") === "on"
                ? { ...quality, smart_framing: null, cover_at: null }
                : quality,
            auto_presentation: form.get("auto_presentation") === "on",
            start: Number(form.get("start")),
            end: Number(form.get("end")),
            caption_preset: form.get("preset") as Preset,
            caption_customization: captionCustomizationFromForm(form),
            caption_position: form.get("position"),
            emphasis_words: parseEmphasis(
              String(form.get("emphasis_words") ?? ""),
            ),
          }),
        },
      );
      setDirty(false);
      onSaved(value);
    } catch (error) {
      setError(message(error));
    } finally {
      setSaving(false);
    }
  }

  const [tab, setTab] = useState("Content");
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus({ preventScroll: true }), []);
  const sourcePlayer = useRef<HTMLVideoElement>(null);
  const attachSourcePlayer = useCallback((player: HTMLVideoElement | null) => {
    sourcePlayer.current = player;
  }, []);
  const start = Number(draftStart);
  const end = Number(draftEnd);
  const minStart = project.settings.analysis_start;
  const maxEnd = project.settings.analysis_end;
  const duration = item.source_duration ?? maxEnd;
  const validTrim =
    draftStart.trim() &&
    draftEnd.trim() &&
    start >= minStart &&
    end <= maxEnd &&
    end - start >= 0.1 - 1e-8;
  function setTrim(nextStart: number, nextEnd: number) {
    setDraftStart(String(nextStart));
    setDraftEnd(String(nextEnd));
    setDirty(true);
  }
  return (
    <section className="project-workspace clip-editor-workspace">
      <header className="workspace-heading">
        <div className="min-w-0">
          <Button
            className="-ms-2 mb-2"
            type="button"
            variant="ghost"
            size="sm"
            disabled={saving}
            onClick={onBack}
          >
            <ArrowLeftIcon data-icon="inline-start" /> Back to project
          </Button>
          <h1
            ref={heading}
            tabIndex={-1}
            className="wrap-anywhere outline-none"
          >
            {item.title}
          </h1>
          <p>Edit clip / {savedProject.title}</p>
        </div>
      </header>
      <form
        className="clip-editor-form"
        onSubmit={save}
        onInvalidCapture={(event) => {
          const target = event.target;
          if (!(target instanceof HTMLElement)) return;
          const disclosure = target.closest("details");
          if (disclosure) disclosure.open = true;
          const panel =
            target.closest<HTMLElement>("[data-inspector]")?.dataset.inspector;
          if (panel && panel !== tab) {
            event.preventDefault();
            flushSync(() => setTab(panel));
            requestAnimationFrame(() => target.focus());
          }
        }}
        onChangeCapture={(event) => {
          if (!(
            event.target instanceof Element &&
            event.target.closest(".live-clip-preview")
          ))
            setDirty(true);
        }}
      >
        <div className="clip-editor-preview">
          <div ref={setPreviewSlot} />
          <div className="rounded-lg border bg-card p-3 text-sm">
            {validTrim ? (
              <AnalysisRange
                purpose="trim"
                recordingId={
                  item.source_duration
                    ? (project.recording_id ?? undefined)
                    : undefined
                }
                duration={duration}
                minimumStart={minStart}
                maximumEnd={maxEnd}
                start={start}
                end={end}
                disabled={locked || saving}
                onChange={setTrim}
                onPreview={(at) => {
                  const player = sourcePlayer.current;
                  if (player && player.readyState >= 1)
                    player.currentTime = Math.min(at, player.duration - 0.01);
                }}
              />
            ) : (
              <p className="text-muted-foreground">
                Enter valid clip times to use the trim timeline.
              </p>
            )}
          </div>
        </div>
        <Tabs
          value={tab}
          onValueChange={setTab}
          className="clip-editor-inspector"
        >
          <TabsList
            aria-label="Editing controls"
            className="h-auto w-full flex-wrap justify-start"
          >
            {tabs.map((name) => (
              <TabsTrigger
                key={name}
                value={name}
                className="flex-none px-3 py-1.5"
              >
                {name}
              </TabsTrigger>
            ))}
          </TabsList>
          <FieldSet disabled={locked || saving} className="min-w-0 p-4">
            <TabsContent
              value="Content"
              data-inspector="Content"
              forceMount
              hidden={tab !== "Content"}
              className="flex min-w-0 flex-col gap-4"
            >
              <Field>
                <FieldLabel htmlFor={`${fieldId}-3-${editing.revision}`}>
                  Clip title
                </FieldLabel>
                <Input
                  id={`${fieldId}-3-${editing.revision}`}
                  type="text"
                  name="title"
                  value={draftTitle}
                  onChange={(event) => setDraftTitle(event.target.value)}
                  required
                  maxLength={120}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor={`${fieldId}-4-${editing.revision}`}>
                  Review
                </FieldLabel>
                <NativeSelect
                  id={`${fieldId}-4-${editing.revision}`}
                  name="review"
                  defaultValue={editing.review}
                  className="w-full"
                >
                  <NativeSelectOption value="unreviewed">
                    Unreviewed
                  </NativeSelectOption>
                  <NativeSelectOption value="accepted">
                    Accepted
                  </NativeSelectOption>
                  <NativeSelectOption value="rejected">
                    Rejected, exclude from batch
                  </NativeSelectOption>
                </NativeSelect>
              </Field>
              <FieldGroup className="project-fields grid gap-4 sm:grid-cols-2">
                <Field>
                  <FieldLabel htmlFor={`${fieldId}-5-${editing.revision}`}>
                    Clip start
                  </FieldLabel>
                  <Input
                    id={`${fieldId}-5-${editing.revision}`}
                    name="start"
                    type="number"
                    min={project.settings.analysis_start}
                    max={project.settings.analysis_end}
                    step="any"
                    required
                    value={draftStart}
                    onChange={(event) => setDraftStart(event.target.value)}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor={`${fieldId}-6-${editing.revision}`}>
                    Clip end
                  </FieldLabel>
                  <Input
                    id={`${fieldId}-6-${editing.revision}`}
                    name="end"
                    type="number"
                    min={project.settings.analysis_start}
                    max={project.settings.analysis_end}
                    step="any"
                    required
                    value={draftEnd}
                    onChange={(event) => setDraftEnd(event.target.value)}
                  />
                </Field>
              </FieldGroup>
              {validTrim &&
                project.transcription_id &&
                project.transcript_revision && (
                  <ClipTranscript
                    key={`${project.transcription_id}:${project.transcript_revision}:${start}:${end}`}
                    source={{
                      transcriptionId: project.transcription_id,
                      revision: project.transcript_revision,
                      start,
                      end,
                    }}
                  />
                )}
              <HookSuggestions
                key={item.id}
                projectId={project.id}
                itemId={item.id}
                recordingId={project.recording_id}
                onChoose={(hook) => {
                  setDraftStart(String(hook.start));
                  setOpeningText(hook.opening_text);
                  setDirty(true);
                }}
              />
              <FieldSet>
                <FieldLegend>Opening headline</FieldLegend>
                <Field orientation="horizontal">
                  <Checkbox
                    id={fieldId + "-headline-enabled"}
                    name="headline_enabled"
                    checked={draftHeadline}
                    onCheckedChange={(value) => {
                      setDraftHeadline(value === true);
                      setDirty(true);
                    }}
                  />
                  <FieldLabel htmlFor={fieldId + "-headline-enabled"}>
                    Show headline for the first {OPENING_HEADLINE_SECONDS}{" "}
                    seconds
                  </FieldLabel>
                </Field>
                <Field>
                  <FieldLabel htmlFor={fieldId + "-headline-position"}>
                    Headline placement
                  </FieldLabel>
                  <NativeSelect
                    id={fieldId + "-headline-position"}
                    name="headline_position"
                    value={draftHeadlinePosition}
                    onChange={(event) =>
                      setDraftHeadlinePosition(
                        event.target.value as typeof draftHeadlinePosition,
                      )
                    }
                    className="w-full"
                  >
                    <NativeSelectOption value="auto">
                      Auto: avoid the caption area
                    </NativeSelectOption>
                    <NativeSelectOption value="upper">Top</NativeSelectOption>
                    <NativeSelectOption value="centre">
                      Middle
                    </NativeSelectOption>
                  </NativeSelect>
                </Field>
                <Field>
                  <FieldLabel htmlFor={fieldId + "-opening-text"}>
                    Opening text (blank uses clip title)
                  </FieldLabel>
                  <Input
                    id={fieldId + "-opening-text"}
                    name="opening_text"
                    maxLength={120}
                    value={openingText}
                    onChange={(event) => setOpeningText(event.target.value)}
                  />
                </Field>
              </FieldSet>
              <TextOverlayFields
                initial={editing.overlays ?? []}
                onChange={(overlays) => {
                  setDraftOverlays(overlays);
                  setDirty(true);
                }}
                duration={Math.max(0, Number(draftEnd) - Number(draftStart))}
              />
              {item.editorial_quality && (
                <p>
                  AI editorial assessment (review required): hook{" "}
                  {item.editorial_quality.hook}/5; context{" "}
                  {item.editorial_quality.context}/5; payoff{" "}
                  {item.editorial_quality.payoff}/5; standalone{" "}
                  {item.editorial_quality.standalone}/5.
                </p>
              )}
            </TabsContent>
            <TabsContent
              value="Captions"
              data-inspector="Captions"
              forceMount
              hidden={tab !== "Captions"}
              className="flex min-w-0 flex-col gap-4"
            >
              <CaptionStylePicker
                disabled={locked || saving}
                name="preset"
                value={captionStyle.preset}
                onChange={(preset) =>
                  setCaptionStyle((current) => ({ ...current, preset }))
                }
              />
              <CaptionStyleLibrary
                value={captionStyle}
                onApply={(style) => {
                  setCaptionStyle(style);
                  setDirty(true);
                }}
                disabled={locked || saving}
              />
              <details className="rounded-lg border p-3">
                <summary className="cursor-pointer font-medium">
                  Customize captions
                </summary>
                <div className="mt-3">
                  <CaptionCustomizationFields
                    compact
                    preset={captionStyle.preset}
                    value={captionStyle.customization}
                    onChange={(customization) =>
                      setCaptionStyle((current) => ({
                        ...current,
                        customization,
                      }))
                    }
                    disabled={locked || saving}
                    source={
                      project.transcription_id && project.transcript_revision
                        ? {
                            transcriptionId: project.transcription_id,
                            revision: project.transcript_revision,
                            start: Number(draftStart),
                            end: Number(draftEnd),
                          }
                        : undefined
                    }
                  />
                </div>
              </details>
              <CaptionEmphasisField
                name="emphasis_words"
                value={draftEmphasis}
                onChange={(value) => {
                  setDraftEmphasis(value);
                  setDirty(true);
                }}
                disabled={locked || saving}
                source={
                  project.transcription_id && project.transcript_revision
                    ? {
                        transcriptionId: project.transcription_id,
                        revision: project.transcript_revision,
                        start: Number(draftStart),
                        end: Number(draftEnd),
                      }
                    : undefined
                }
              />
              <Field>
                <FieldLabel htmlFor={`${fieldId}-7-${editing.revision}`}>
                  Position
                </FieldLabel>
                <NativeSelect
                  id={`${fieldId}-7-${editing.revision}`}
                  name="position"
                  value={captionStyle.position}
                  onChange={(event) =>
                    setCaptionStyle((current) => ({
                      ...current,
                      position: event.target.value as CaptionStyle["position"],
                    }))
                  }
                  className="w-full"
                >
                  <NativeSelectOption value="auto">
                    Auto · avoid text and webcam
                  </NativeSelectOption>
                  <NativeSelectOption value="lower">Lower</NativeSelectOption>
                  <NativeSelectOption value="centre">Centre</NativeSelectOption>
                  <NativeSelectOption value="upper">Upper</NativeSelectOption>
                </NativeSelect>
              </Field>
            </TabsContent>
            <TabsContent
              value="Layout"
              data-inspector="Layout"
              forceMount
              hidden={tab !== "Layout"}
              className="flex min-w-0 flex-col gap-4"
            >
              <Field orientation="horizontal">
                <Checkbox
                  id={`${fieldId}-presentation`}
                  name="auto_presentation"
                  checked={reassess}
                  onCheckedChange={(value) => {
                    setReassess(value === true);
                    setDirty(true);
                  }}
                />
                <FieldLabel htmlFor={`${fieldId}-presentation`}>
                  Re-analyze automatic presentation on save (local, no AI
                  charge)
                </FieldLabel>
              </Field>
              <FramingEditor
                mode={framing}
                layout={splitLayout}
                cropRegion={cropRegion}
                followPlan={reassess ? null : item.export?.follow_plan}
                autoLayout={reassess ? null : item.export?.auto_layout}
                onPlayer={attachSourcePlayer}
                previewSlot={previewSlot}
                previewOptions={{
                  savedExportId:
                    item.export?.status === "succeeded" ? item.export.id : null,
                  transcriptionId: project.transcription_id,
                  transcriptRevision: project.transcript_revision,
                  captions: captionStyle,
                  headline: draftHeadline
                    ? {
                        text: openingText.trim() || draftTitle,
                        position: draftHeadlinePosition,
                        seconds: OPENING_HEADLINE_SECONDS,
                      }
                    : null,
                  emphasis: parseEmphasis(draftEmphasis),
                  overlays: draftOverlays,
                }}
                quality={
                  reassess ? { ...quality, smart_framing: null } : quality
                }
                end={
                  draftStart.trim() && draftEnd.trim()
                    ? Number(draftEnd)
                    : undefined
                }
                sourceUrl={
                  project.recording_id
                    ? `/api/recordings/${project.recording_id}/media`
                    : undefined
                }
                timestamp={Number(draftStart)}
                disabled={locked || saving}
                onChange={(mode, layout, crop) => {
                  setDirty(true);
                  setFraming(mode);
                  setSplitLayout(layout);
                  setCropRegion(crop);
                  setQuality((value) => ({
                    ...value,
                    smart_framing: null,
                  }));
                }}
              />
            </TabsContent>
            <TabsContent
              value="Audio & quality"
              data-inspector="Audio & quality"
              forceMount
              hidden={tab !== "Audio & quality"}
              className="flex min-w-0 flex-col gap-4"
            >
              <OutputQualityControls
                value={quality}
                onChange={(value) => {
                  setQuality(value);
                  setDirty(true);
                }}
                recordingId={project.recording_id!}
                start={Number(draftStart)}
                end={Number(draftEnd)}
                disabled={locked || saving}
                allowSmart={
                  (framing ?? project.settings.framing) !== "split" &&
                  framing !== "follow" &&
                  framing !== "auto" &&
                  cropRegion === null
                }
                allowedStart={project.settings.analysis_start}
                allowedEnd={project.settings.analysis_end}
                onBounds={setTrim}
                source={
                  project.transcription_id && project.transcript_revision
                    ? {
                        transcriptionId: project.transcription_id,
                        revision: project.transcript_revision,
                        start: Number(draftStart),
                        end: Number(draftEnd),
                      }
                    : undefined
                }
              />
            </TabsContent>
            <TabsContent
              value="Export"
              data-inspector="Export"
              forceMount
              hidden={tab !== "Export"}
              className="flex min-w-0 flex-col gap-4"
            >
              <h2>Saved export</h2>
              <p className="text-sm text-muted-foreground">
                Saving updates this clip. Earlier exports remain in project
                version history.
              </p>
              {item.export?.status === "succeeded" ? (
                <Button asChild variant="outline" className="self-start">
                  <a href={`/api/clips/${item.export.id}/media?download=true`}>
                    Download current MP4
                  </a>
                </Button>
              ) : (
                <p className="text-sm text-muted-foreground">
                  A finished export is required to download this clip.
                </p>
              )}
            </TabsContent>
          </FieldSet>
          {error ? (
            <Alert role="alert" variant="destructive" className="mx-4">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
        </Tabs>
        <footer className="clip-editor-footer">
          <p role="status" className="text-sm text-muted-foreground">
            {saving
              ? "Saving…"
              : dirty
                ? "Unsaved changes"
                : locked
                  ? "Project processing"
                  : "Saved"}
          </p>
          <Button type="submit" disabled={locked || saving}>
            {saving ? "Saving…" : "Save changes"}
          </Button>
        </footer>
      </form>
    </section>
  );
}
