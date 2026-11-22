"use client";

import {
  useCreationFlow,
  CreationFlow,
  CreationPanel,
  CreationFooter,
  AdvancedOptions,
} from "./creation-flow";
import { ProjectPresets } from "./project-presets";
import {
  useSetupDraft,
  SetupDraftNotice,
  type SetupDraft,
} from "./setup-draft";
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
import { api, message, creationFingerprint } from "../../lib/client-api";
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
    font_size: 80,
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
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const player = useRef<HTMLVideoElement>(null);
  const requestToken = useRef<{ signature: string; id: string } | null>(null);
  const [savedToken, setSavedToken] = useState<{
    signature: string;
    id: string;
  } | null>(null);
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
        ? `/api/video-sources/${recordingId}`
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
  const draft = useSetupDraft(
    `cliptric:setup:${youtube ? "link" : "file"}:${recordingId}`,
    {
      title,
      settings,
      recording_ids: [],
      creation_token: savedToken?.id,
      creation_signature: savedToken?.signature,
    },
    source !== null && !busy,
  );
  function restoreSetup(value: SetupDraft) {
    const duration = source!.duration;
    setTitle(value.title);
    setSettings({
      ...value.settings,
      duration_mode:
        value.settings.duration_mode === "full"
          ? "auto"
          : value.settings.duration_mode,
      analysis_start: Math.min(
        value.settings.analysis_start,
        Math.max(0, duration - 8),
      ),
      analysis_end: Math.min(value.settings.analysis_end, duration),
    });
    setEmphasisText(value.settings.emphasis_words.join(", "));
    requestToken.current =
      value.creation_token && value.creation_signature
        ? { id: value.creation_token, signature: value.creation_signature }
        : null;
    setSavedToken(requestToken.current);
  }
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
    draft.persistCurrent();
    let signature: string;
    try {
      const prepared = await api<SetupDraft>("/api/setup/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title,
          settings: payload.settings,
          recording_ids: [],
        }),
      });
      payload.settings = prepared.settings;
      signature = await creationFingerprint(payload);
    } catch (error) {
      setError(message(error));
      setBusy(false);
      return;
    }
    if (requestToken.current?.signature !== signature)
      requestToken.current = { signature, id: crypto.randomUUID() };
    setSavedToken(requestToken.current);
    draft.persistToken(requestToken.current);
    try {
      await api<Project>("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, id: requestToken.current.id }),
      });
      draft.clear();
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
  const flow = useCreationFlow(
    Boolean(source?.has_audio && title.trim() && validRange),
    validRange && (settings.framing !== "split" || Boolean(mediaSource)),
    busy,
  );
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
        <form
          id={flow.formId}
          onSubmit={(event) => flow.submit(event, create)}
          onInvalidCapture={flow.onInvalid}
          className="flex flex-col gap-5"
        >
          <SetupDraftNotice draft={draft} onRestore={restoreSetup} />
          <CreationFlow
            flow={flow}
            preview={
              <FieldSet disabled={busy}>
                {youtube && "url" in source && !mediaSource ? (
                  <>
                    {source.preview_image_url ||
                    new URL(source.url).searchParams.has("v") ? (
                      <Image
                        unoptimized
                        src={
                          source.preview_image_url ||
                          `https://i.ytimg.com/vi/${new URL(source.url).searchParams.get("v")}/hqdefault.jpg`
                        }
                        width={480}
                        height={360}
                        alt={source.name}
                        className="setup-preview object-contain"
                      />
                    ) : (
                      <p>Video preview will be available after download.</p>
                    )}
                    <p>
                      Duration: {Math.floor(source.duration / 60)}m{" "}
                      {Math.floor(source.duration % 60)}s. Downloading while you
                      configure your clips.
                    </p>
                  </>
                ) : (
                  <FramingEditor
                    mode={settings.framing}
                    layout={settings.split_layout ?? null}
                    cropRegion={settings.crop_region ?? null}
                    onPlayer={(node) => {
                      player.current = node;
                    }}
                    end={settings.analysis_end}
                    sourceUrl={
                      mediaSource
                        ? `/api/recordings/${mediaSource.id}/media`
                        : undefined
                    }
                    downloadProgress={download.percent}
                    timestamp={settings.analysis_start}
                    disabled={busy}
                    onChange={(framing, split_layout, crop_region) =>
                      setSettings((current) => ({
                        ...current,
                        framing,
                        split_layout,
                        crop_region,
                      }))
                    }
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
              </FieldSet>
            }
          >
            <FieldSet disabled={busy}>
              <CreationPanel value={0} active={flow.step}>
                <h2>Choose your source</h2>
                <Field className="project-title-field">
                  <FieldLabel htmlFor={`${fieldId}-1`}>
                    Project title
                  </FieldLabel>
                  <Input
                    id={`${fieldId}-1`}
                    type="text"
                    required
                    maxLength={120}
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                  />
                </Field>
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
                      <Link href="/studio">Use the manual studio</Link> to clip
                      it.
                    </AlertDescription>
                  </Alert>
                ) : null}
              </CreationPanel>
              <CreationPanel value={1} active={flow.step}>
                <h2>Make it your style</h2>
                <FieldGroup className="grid gap-4 sm:grid-cols-2">
                  <Field>
                    <FieldLabel htmlFor={`${fieldId}-2`}>
                      Clip duration
                    </FieldLabel>
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
                    <FieldLabel htmlFor={`${fieldId}-3`}>
                      Maximum clips
                    </FieldLabel>
                    <Input
                      id={`${fieldId}-3`}
                      required
                      type="number"
                      min={1}
                      max={20}
                      value={
                        Number.isNaN(settings.max_clips)
                          ? ""
                          : settings.max_clips
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
                </FieldGroup>
                <CaptionStylePicker
                  key="caption-preset"
                  disabled={busy}
                  name="caption_preset"
                  value={settings.caption_preset}
                  onChange={(caption_preset) =>
                    setSettings((s) => ({ ...s, caption_preset }))
                  }
                />
                <AiInstructionsField
                  value={settings.ai_instructions ?? ""}
                  onChange={(ai_instructions) =>
                    setSettings((current) => ({ ...current, ai_instructions }))
                  }
                  mode="highlights"
                  disabled={busy}
                />
                <AdvancedOptions>
                  <Field orientation="horizontal">
                    <Checkbox
                      id={`${fieldId}-headline`}
                      checked={settings.headline_enabled ?? true}
                      onCheckedChange={(value) =>
                        setSettings((current) => ({
                          ...current,
                          headline_enabled: value === true,
                        }))
                      }
                    />
                    <FieldLabel htmlFor={`${fieldId}-headline`}>
                      Opening headline
                    </FieldLabel>
                  </Field>
                  <Field>
                    <FieldLabel htmlFor={`${fieldId}-headline-position`}>
                      Headline position
                    </FieldLabel>
                    <NativeSelect
                      id={`${fieldId}-headline-position`}
                      value={settings.headline_position ?? "auto"}
                      onChange={(event) =>
                        setSettings((current) => ({
                          ...current,
                          headline_position: event.target
                            .value as ProjectSettings["headline_position"],
                        }))
                      }
                    >
                      <NativeSelectOption value="auto">Auto</NativeSelectOption>
                      <NativeSelectOption value="upper">
                        Upper
                      </NativeSelectOption>
                      <NativeSelectOption value="centre">
                        Centre
                      </NativeSelectOption>
                    </NativeSelect>
                  </Field>
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
                      <NativeSelectOption value="lower">
                        Lower
                      </NativeSelectOption>
                      <NativeSelectOption value="centre">
                        Centre
                      </NativeSelectOption>
                      <NativeSelectOption value="upper">
                        Upper
                      </NativeSelectOption>
                    </NativeSelect>
                  </Field>

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
                      Prioritize hook, context, payoff, and standalone clarity
                      (may return fewer clips)
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
                </AdvancedOptions>
                <AdvancedOptions label="Project presets">
                  <ProjectPresets
                    workflow="long"
                    settings={settings}
                    disabled={busy}
                    onApply={(value) => {
                      setSettings(value);
                      setEmphasisText(value.emphasis_words.join(", "));
                    }}
                  />
                </AdvancedOptions>
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
                      <Link href="/studio">Use the manual studio</Link> to clip
                      it.
                    </AlertDescription>
                  </Alert>
                ) : null}
              </CreationPanel>
              <CreationPanel value={2} active={flow.step}>
                <h2>Ready to create</h2>
                <dl className="grid grid-cols-2 gap-4">
                  <div>
                    <dt className="text-muted-foreground">Project</dt>
                    <dd>{title}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Maximum clips</dt>
                    <dd>{settings.max_clips}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Source range</dt>
                    <dd>
                      {Math.round(settings.analysis_start)}-
                      {Math.round(settings.analysis_end)} seconds
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Captions</dt>
                    <dd>{settings.caption_preset}</dd>
                  </div>
                </dl>
                <p>
                  Creating shares saved transcript text and timestamps with
                  OpenAI. API charges apply. Your video stays local; AI may
                  return fewer clips.
                </p>
              </CreationPanel>
              <CreationFooter
                flow={flow}
                busy={busy}
                action={
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
                }
              />
            </FieldSet>
          </CreationFlow>
        </form>
      ) : null}
    </section>
  );
}
