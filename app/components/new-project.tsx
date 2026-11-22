"use client";

import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { FieldSet, FieldLabel, Field, FieldGroup } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";

import {
  useId,
  useEffect,
  useRef,
  useState,
  useCallback,
  type FormEvent,
} from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { api, message } from "../../lib/client-api";
import {
  type Project,
  type ProjectSettings,
  type Recording,
  type YouTubeSource,
} from "../../lib/projects";
import { AnalysisRange } from "./analysis-range";
import { CaptionStylePicker } from "./caption-style-picker";
import { CaptionEmphasisField } from "./caption-emphasis-field";
import { CaptionCustomizationFields } from "./caption-customization-fields";
import { CaptionStyleLibrary } from "./caption-style-library";
import { AiInstructionsField } from "./ai-instructions-field";
import { PresentationField } from "./presentation-field";
import { parseEmphasis } from "../../lib/projects";
import { FramingEditor } from "./framing-editor";
import { WorkspaceLoading } from "./workspace-loading";
import { MAX_VIDEO_SECONDS } from "../../lib/projects";
import { LoaderCircleIcon } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { useSetupDownload } from "./use-setup-download";

const defaults: ProjectSettings = {
  presentation_mode: "auto",
  ai_instructions: "",
  quality_mode: true,
  audio_cleanup: "light",
  normalize_audio: true,
  analysis_start: 0,
  analysis_end: 60,
  duration_mode: "auto",
  min_seconds: 15,
  max_seconds: 60,
  max_clips: 5,
  caption_preset: "impact",
  caption_customization: {
    grouping: "auto",
    capitalization: "original",
    font_size: 64,
  },
  emphasis_words: [],
  caption_position: "auto",
  framing: "pad",
};

export function NewProject({
  recordingId,
  youtube = false,
}: {
  recordingId: string;
  youtube?: boolean;
}) {
  const fieldId = useId();
  const router = useRouter();
  const [source, setSource] = useState<Recording | YouTubeSource | null>(null);
  const [title, setTitle] = useState("");
  const [settings, setSettings] = useState(defaults);
  const [emphasisText, setEmphasisText] = useState("");
  const [error, setError] = useState("");
  const [previewError, setPreviewError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const player = useRef<HTMLVideoElement>(null);
  const requestToken = useRef<{ signature: string; id: string } | null>(null);
  const downloaded = useCallback((recording: Recording) => {
    const duration = recording.duration;
    setSettings((current) => ({
      ...current,
      analysis_start: Math.min(
        current.analysis_start,
        Math.max(0, duration - 0.1),
      ),
      analysis_end: Math.min(current.analysis_end, duration),
    }));
  }, []);
  const download = useSetupDownload(
    youtube && source && "url" in source ? source.url : null,
    downloaded,
  );
  const mediaSource = youtube ? download.recording : source;
  useEffect(() => {
    const controller = new AbortController();
    api<Recording | YouTubeSource>(
      youtube
        ? `/api/youtube-sources/${recordingId}`
        : `/api/recordings/${recordingId}`,
      {
        signal: controller.signal,
      },
    )
      .then((recording) => {
        if (controller.signal.aborted) return;
        setSource(recording);
        setTitle(
          (youtube
            ? recording.name
            : recording.name.replace(/\.(mp4|mov|mkv|webm|m4v)$/i, "")
          ).slice(0, 120),
        );
        setSettings((s) => ({
          ...s,
          analysis_start: 0,
          analysis_end: recording.duration,
        }));
        setError("");
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(message(error));
      });
    return () => controller.abort();
  }, [recordingId, youtube, refresh]);
  function preview(value: number) {
    const video = player.current;
    if (video && video.readyState >= 1) {
      video.pause();
      video.currentTime = value;
    }
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const payload = {
      ...(youtube
        ? { youtube_source_id: recordingId }
        : { recording_id: recordingId }),
      title,
      settings,
    };
    const signature = JSON.stringify(payload);
    if (requestToken.current?.signature !== signature)
      requestToken.current = { signature, id: crypto.randomUUID() };
    try {
      await api<Project>("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, id: requestToken.current.id }),
      });
      router.push("/projects");
    } catch (error) {
      setError(
        `${message(error)} Check the project library after a lost connection. Retrying unchanged settings reuses the creation token.`,
      );
    } finally {
      setBusy(false);
    }
  }
  const minimum = settings.duration_mode === "auto" ? 8 : settings.min_seconds;
  const validRange = settings.analysis_end - settings.analysis_start >= minimum;
  return (
    <section className="project-workspace new-project-workspace">
      <header className="project-heading">
        <Button asChild variant="outline">
          <Link href="/">Choose another video</Link>
        </Button>
        <Button asChild variant="outline">
          <Link href="/projects">Your projects</Link>
        </Button>
      </header>
      <h1>New project</h1>
      {error ? (
        <Alert role="alert" variant="destructive">
          <AlertDescription>
            {error}{" "}
            {!source ? (
              <Button
                onClick={() => setRefresh((v) => v + 1)}
                variant="outline"
              >
                Retry
              </Button>
            ) : null}
          </AlertDescription>
        </Alert>
      ) : null}
      {!source && !error ? (
        <WorkspaceLoading label="Loading your video…" />
      ) : null}
      {source ? (
        <form onSubmit={create}>
          <FieldSet disabled={busy}>
            <Field className="project-title-field">
              <FieldLabel htmlFor={`${fieldId}-1`}>Project title</FieldLabel>
              <Input
                id={`${fieldId}-1`}
                type="text"
                required
                maxLength={120}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
            </Field>
            {youtube && "url" in source && !mediaSource ? (
              <>
                <Image
                  unoptimized
                  src={`https://i.ytimg.com/vi/${new URL(source.url).searchParams.get("v")}/hqdefault.jpg`}
                  width={480}
                  height={360}
                  alt={source.name}
                  className="setup-preview object-contain"
                />
                <p>
                  Duration: {Math.floor(source.duration / 60)}m{" "}
                  {Math.floor(source.duration % 60)}s. Downloading while you
                  configure your clips.
                </p>
              </>
            ) : (
              <video
                ref={player}
                className="setup-preview"
                controls
                preload="metadata"
                poster={`/api/recordings/${mediaSource!.id}/thumbnail`}
                src={`/api/recordings/${mediaSource!.id}/media`}
                onError={() => setPreviewError(true)}
                onPlay={() => {
                  if (
                    player.current &&
                    (player.current.currentTime < settings.analysis_start ||
                      player.current.currentTime >= settings.analysis_end)
                  )
                    player.current.currentTime = settings.analysis_start;
                }}
                onTimeUpdate={() => {
                  if (
                    player.current &&
                    !player.current.paused &&
                    player.current.currentTime >= settings.analysis_end
                  )
                    player.current.pause();
                }}
              />
            )}
            {youtube ? (
              <div
                className="setup-download flex max-w-lg flex-col gap-2"
                role="status"
              >
                <p>
                  {download.recording
                    ? "Video ready for preview and cropping"
                    : download.job?.status === "processing"
                      ? "Download complete. Preparing your video…"
                      : `Downloading video${download.percent == null ? "…" : ` · ${download.percent}%`}`}
                </p>
                <Progress
                  value={download.percent ?? undefined}
                  aria-label="Source video download"
                />
                {download.error ? (
                  <Alert variant="destructive">
                    <AlertDescription>
                      {download.error}{" "}
                      <Button
                        type="button"
                        variant="outline"
                        onClick={download.retry}
                      >
                        Retry download
                      </Button>
                    </AlertDescription>
                  </Alert>
                ) : null}
              </div>
            ) : null}
            {previewError ? (
              <p>
                Browser playback is unavailable for this source. You can still
                use the thumbnails and generate clips.
              </p>
            ) : null}
            <h2>Choose the part to analyze</h2>
            <AnalysisRange
              recordingId={mediaSource?.id}
              duration={mediaSource?.duration ?? source.duration}
              start={settings.analysis_start}
              end={settings.analysis_end}
              disabled={busy}
              onChange={(analysis_start, analysis_end) =>
                setSettings((s) => ({ ...s, analysis_start, analysis_end }))
              }
              onPreview={preview}
            />
            <h2>Clip settings</h2>
            <FieldGroup className="project-fields grid gap-4 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor={`${fieldId}-2`}>Clip duration</FieldLabel>
                <NativeSelect
                  id={`${fieldId}-2`}
                  value={settings.duration_mode}
                  onChange={(event) =>
                    setSettings((s) => ({
                      ...s,
                      duration_mode: event.target
                        .value as ProjectSettings["duration_mode"],
                    }))
                  }
                  className="w-full"
                >
                  <NativeSelectOption value="auto">
                    Auto: AI chooses each clip’s length
                  </NativeSelectOption>
                  <NativeSelectOption value="manual">
                    Manual duration bounds
                  </NativeSelectOption>
                </NativeSelect>
              </Field>
              <Field>
                <FieldLabel htmlFor={`${fieldId}-3`}>Maximum clips</FieldLabel>
                <Input
                  id={`${fieldId}-3`}
                  required
                  type="number"
                  min={1}
                  max={20}
                  value={
                    Number.isNaN(settings.max_clips) ? "" : settings.max_clips
                  }
                  onChange={(event) =>
                    setSettings((s) => ({
                      ...s,
                      max_clips: event.target.valueAsNumber,
                    }))
                  }
                />
              </Field>
              {settings.duration_mode === "manual" ? (
                <>
                  <Field>
                    <FieldLabel htmlFor={`${fieldId}-4`}>
                      Minimum seconds
                    </FieldLabel>
                    <Input
                      id={`${fieldId}-4`}
                      required
                      type="number"
                      min={8}
                      max={MAX_VIDEO_SECONDS}
                      value={
                        Number.isNaN(settings.min_seconds)
                          ? ""
                          : settings.min_seconds
                      }
                      onChange={(event) =>
                        setSettings((s) => ({
                          ...s,
                          min_seconds: event.target.valueAsNumber,
                        }))
                      }
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor={`${fieldId}-5`}>
                      Maximum seconds
                    </FieldLabel>
                    <Input
                      id={`${fieldId}-5`}
                      required
                      type="number"
                      min={settings.min_seconds}
                      max={MAX_VIDEO_SECONDS}
                      value={
                        Number.isNaN(settings.max_seconds)
                          ? ""
                          : settings.max_seconds
                      }
                      onChange={(event) =>
                        setSettings((s) => ({
                          ...s,
                          max_seconds: event.target.valueAsNumber,
                        }))
                      }
                    />
                  </Field>
                </>
              ) : null}
              <Field>
                <FieldLabel htmlFor={`${fieldId}-6`}>
                  Caption position
                </FieldLabel>
                <NativeSelect
                  id={`${fieldId}-6`}
                  value={settings.caption_position}
                  onChange={(event) =>
                    setSettings((s) => ({
                      ...s,
                      caption_position: event.target
                        .value as ProjectSettings["caption_position"],
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
            </FieldGroup>
            <FramingEditor
              mode={settings.framing}
              layout={settings.split_layout ?? null}
              sourceUrl={
                mediaSource
                  ? `/api/recordings/${mediaSource.id}/media`
                  : undefined
              }
              downloadProgress={download.percent}
              timestamp={settings.analysis_start}
              disabled={busy}
              onChange={(framing, split_layout) =>
                setSettings((current) => ({
                  ...current,
                  framing,
                  split_layout,
                }))
              }
            />
            <CaptionStylePicker
              key="caption-preset"
              disabled={busy}
              name="caption_preset"
              value={settings.caption_preset}
              onChange={(caption_preset) =>
                setSettings((s) => ({ ...s, caption_preset }))
              }
            />
            <CaptionStyleLibrary
              disabled={busy}
              value={{
                preset: settings.caption_preset,
                position: settings.caption_position,
                customization: settings.caption_customization,
              }}
              onApply={(style) =>
                setSettings((current) => ({
                  ...current,
                  caption_preset: style.preset,
                  caption_position: style.position,
                  caption_customization: style.customization,
                }))
              }
            />
            <CaptionCustomizationFields
              preset={settings.caption_preset}
              value={settings.caption_customization}
              disabled={busy}
              onChange={(caption_customization) =>
                setSettings((s) => ({ ...s, caption_customization }))
              }
            />
            <CaptionEmphasisField
              name="emphasis_words"
              value={emphasisText}
              disabled={busy || settings.caption_preset === "none"}
              onChange={(value) => {
                setEmphasisText(value);
                setSettings((s) => ({
                  ...s,
                  emphasis_words: parseEmphasis(value),
                }));
              }}
            />
            <Field orientation="horizontal">
              <Checkbox
                id={fieldId + "-quality"}
                checked={settings.quality_mode ?? false}
                disabled={busy}
                onCheckedChange={(checked) =>
                  setSettings((current) => ({
                    ...current,
                    quality_mode: checked === true,
                  }))
                }
              />
              <FieldLabel htmlFor={fieldId + "-quality"}>
                Prioritize hook, context, payoff, and standalone clarity (may
                return fewer clips)
              </FieldLabel>
            </Field>
            <PresentationField
              value={settings.presentation_mode ?? "off"}
              disabled={busy}
              onChange={(value) =>
                setSettings((current) => ({
                  ...current,
                  presentation_mode: value,
                }))
              }
            />
            <Field>
              <FieldLabel htmlFor={fieldId + "-cleanup"}>
                Default speech cleanup
              </FieldLabel>
              <NativeSelect
                id={fieldId + "-cleanup"}
                value={settings.audio_cleanup ?? "off"}
                disabled={busy}
                onChange={(event) =>
                  setSettings((current) => ({
                    ...current,
                    audio_cleanup: event.target
                      .value as ProjectSettings["audio_cleanup"],
                  }))
                }
              >
                <NativeSelectOption value="off">Off</NativeSelectOption>
                <NativeSelectOption value="light">
                  Light noise reduction
                </NativeSelectOption>
                <NativeSelectOption value="standard">
                  Standard noise reduction
                </NativeSelectOption>
              </NativeSelect>
            </Field>
            <Field orientation="horizontal">
              <Checkbox
                id={fieldId + "-normalize"}
                checked={settings.normalize_audio ?? false}
                disabled={busy}
                onCheckedChange={(checked) =>
                  setSettings((current) => ({
                    ...current,
                    normalize_audio: checked === true,
                  }))
                }
              />
              <FieldLabel htmlFor={fieldId + "-normalize"}>
                Normalize speaking volume in new clips
              </FieldLabel>
            </Field>
            <AiInstructionsField
              value={settings.ai_instructions ?? ""}
              onChange={(ai_instructions) =>
                setSettings((current) => ({ ...current, ai_instructions }))
              }
              mode="highlights"
              disabled={busy}
            />
            {!validRange ? (
              <Alert role="alert" variant="destructive">
                <AlertDescription>
                  Choose at least {minimum} seconds to analyze.
                </AlertDescription>
              </Alert>
            ) : null}
            {!source.has_audio ? (
              <Alert role="alert" variant="destructive">
                <AlertDescription>
                  This video has no audio.{" "}
                  <Link href="/studio">Use the manual studio</Link> to clip it.
                </AlertDescription>
              </Alert>
            ) : null}
            <p>
              AI can return fewer clips or none. Creating sends saved transcript
              text and timestamps to OpenAI with billed API usage. Your video
              stays local.
            </p>
            <Button
              type="submit"
              disabled={
                !source.has_audio ||
                !validRange ||
                busy ||
                (settings.framing === "split" && !mediaSource)
              }
              variant="default"
            >
              {busy ? (
                <LoaderCircleIcon
                  data-icon="inline-start"
                  className="animate-spin"
                />
              ) : null}
              {busy ? "Saving project…" : "Create clips"}
            </Button>
          </FieldSet>
        </form>
      ) : null}
    </section>
  );
}
