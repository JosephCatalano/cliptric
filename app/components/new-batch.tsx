"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  useCreationFlow,
  CreationFlow,
  CreationPanel,
  CreationFooter,
  AdvancedOptions,
} from "./creation-flow";
import { BatchSourcePreview } from "./batch-source-preview";
import { ProjectPresets } from "./project-presets";
import {
  useSetupDraft,
  SetupDraftNotice,
  type SetupDraft,
} from "./setup-draft";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldDescription,
  FieldSet,
  FieldLegend,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Progress } from "@/components/ui/progress";
import {
  api,
  message,
  creationFingerprint,
  uploadRecording,
} from "@/lib/client-api";
import {
  defaultCaptionCustomization,
  MAX_VIDEO_SECONDS,
  type CaptionStyle,
  type Project,
  type ProjectSettings,
  type Recording,
  type SourceRegion,
} from "@/lib/projects";
import { CaptionStylePicker } from "./caption-style-picker";
import { CaptionCustomizationFields } from "./caption-customization-fields";
import { CaptionStyleLibrary } from "./caption-style-library";
import { AiInstructionsField } from "./ai-instructions-field";
import { useImportLimits, ImportLimitNotice } from "./import-limits";
import { PresentationField } from "./presentation-field";

type Upload = {
  id: string;
  name: string;
  file: File | null;
  recording: Recording | null;
  error: string;
};

export function NewBatch() {
  const router = useRouter();
  const limits = useImportLimits();
  const maxFileBytes = limits.maxBytes ?? 0;
  const [clips, setClips] = useState<Upload[]>([]);
  const [title, setTitle] = useState("Supplied clips");
  const [style, setStyle] = useState<CaptionStyle>({
    preset: "impact",
    position: "auto",
    customization: defaultCaptionCustomization,
  });
  const [headline, setHeadline] = useState(true);
  const [headlinePosition, setHeadlinePosition] = useState<
    "auto" | "upper" | "centre"
  >("auto");
  const [aiTitles, setAiTitles] = useState(false);
  const [batchMode, setBatchMode] = useState<"auto" | "keep" | "highlights">(
    "highlights",
  );
  const [clipsPerSource, setClipsPerSource] = useState(3);
  const [presentation, setPresentation] =
    useState<NonNullable<ProjectSettings["presentation_mode"]>>("auto");
  const [reviewConsent, setReviewConsent] = useState(false);
  const [aiInstructions, setAiInstructions] = useState("");
  const [cleanup, setCleanup] = useState<"off" | "light" | "standard">("off");
  const [normalize, setNormalize] = useState(false);
  const [cropRegion, setCropRegion] = useState<SourceRegion | null>(null);
  const [framing, setFraming] = useState<"pad" | "crop">("pad");
  const [busy, setBusy] = useState<"importing" | "saving" | null>(null);
  const [error, setError] = useState("");
  const [current, setCurrent] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [sources, setSources] = useState<Recording[]>([]);
  const [loadingSources, setLoadingSources] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const token = useRef<{ signature: string; id: string } | null>(null);
  const [savedToken, setSavedToken] = useState<{
    signature: string;
    id: string;
  } | null>(null);
  const sourceRequest = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      controller.current?.abort();
      sourceRequest.current?.abort();
    },
    [],
  );
  const ready =
    clips.length > 0 && clips.every((clip) => clip.recording && !clip.error);

  const flow = useCreationFlow(
    ready && Boolean(title.trim()),
    true,
    busy !== null,
  );
  function addFiles(files: FileList | null) {
    if (limits.maxBytes === null) {
      setError(
        "Wait for the upload limit to load, or retry the API connection.",
      );
      return;
    }
    if (!files) return;
    const added = Array.from(files);
    if (clips.length + added.length > 15) {
      setError("Choose at most 15 clips per batch.");
      return;
    }
    if (
      added.some(
        (file) => !/\.(mp4|m4v|mov|mkv|webm)$/i.test(file.name) || !file.size,
      )
    ) {
      setError("Choose non-empty MP4, MOV, MKV, M4V or WebM videos.");
      return;
    }
    const size =
      clips.reduce(
        (total, clip) =>
          total + (clip.file?.size ?? clip.recording?.size_bytes ?? 0),
        0,
      ) + added.reduce((total, file) => total + file.size, 0);
    if (
      added.some((file) => file.size > maxFileBytes) ||
      size > 15 * maxFileBytes
    ) {
      setError(`Choose at most 15 videos, up to ${limits.label} each.`);
      return;
    }
    setError("");
    setClips((previous) => [
      ...previous,
      ...added.map((file) => ({
        id: crypto.randomUUID(),
        name: file.name,
        file,
        recording: null,
        error: "",
      })),
    ]);
  }

  async function loadSources() {
    setLoadingSources(true);
    setError("");
    const request = new AbortController();
    sourceRequest.current = request;
    try {
      const values = await api<Recording[]>("/api/recordings?limit=50", {
        signal: request.signal,
      });
      if (!request.signal.aborted)
        setSources(
          values.filter(
            (source) =>
              source.duration >= 0.1 && source.duration <= MAX_VIDEO_SECONDS,
          ),
        );
    } catch (error) {
      if (!request.signal.aborted) setError(message(error));
    } finally {
      if (!request.signal.aborted) setLoadingSources(false);
    }
  }

  async function importFiles() {
    setBusy("importing");
    setError("");
    const request = new AbortController();
    controller.current = request;
    try {
      for (const clip of clips) {
        if (request.signal.aborted) break;
        if (clip.recording || !clip.file) continue;
        setCurrent(clip.id);
        setProgress(0);
        setClips((previous) =>
          previous.map((value) =>
            value.id === clip.id ? { ...value, error: "" } : value,
          ),
        );
        try {
          const recording = await uploadRecording<Recording>(
            clip.file,
            request.signal,
            setProgress,
          );
          const invalid =
            recording.duration < 0.1 || recording.duration > MAX_VIDEO_SECONDS;
          setClips((previous) =>
            previous.map((value) =>
              value.id === clip.id
                ? {
                    ...value,
                    recording,
                    file: null,
                    error: invalid
                      ? "This video is outside the supported source duration (0.1 seconds to six hours). The imported source is preserved."
                      : "",
                  }
                : value,
            ),
          );
        } catch (error) {
          if (request.signal.aborted) break;
          setClips((previous) =>
            previous.map((value) =>
              value.id === clip.id
                ? { ...value, error: message(error) }
                : value,
            ),
          );
          // An uncertain import must be checked before explicitly retrying it.
          setError(
            "Import stopped. Completed imports are preserved. Check recent imports after a lost connection before retrying.",
          );
          break;
        }
      }
    } finally {
      if (request.signal.aborted)
        setError(
          "Import stopped. Completed imports are preserved. Check recent imports before retrying the interrupted file.",
        );
      setBusy(null);
      setCurrent(null);
    }
  }

  const savedSettings: ProjectSettings = {
    batch_mode: batchMode,
    clips_per_source: clipsPerSource,
    presentation_mode: presentation,
    quality_mode: batchMode !== "keep",
    ai_instructions: aiInstructions,
    analysis_start: 0,
    analysis_end: Math.max(
      0.1,
      ...clips.flatMap((clip) =>
        clip.recording ? [clip.recording.duration] : [],
      ),
    ),
    duration_mode: "full",
    min_seconds: 15,
    max_seconds: 120,
    max_clips: Math.max(1, clips.length),
    caption_preset: style.preset,
    caption_position: style.position,
    caption_customization: style.customization,
    emphasis_words: [],
    framing,
    crop_region: cropRegion,
    headline_enabled: headline,
    headline_position: headlinePosition,
    audio_cleanup: cleanup,
    normalize_audio: normalize,
  };

  const draft = useSetupDraft(
    "cliptric:setup:batch",
    {
      title,
      settings: savedSettings,
      recording_ids: clips.flatMap((clip) =>
        clip.recording ? [clip.recording.id] : [],
      ),
      creation_token: savedToken?.id,
      creation_signature: savedToken?.signature,
    },
    busy !== "saving",
  );
  function applySettings(value: ProjectSettings) {
    setStyle({
      preset: value.caption_preset,
      position: value.caption_position,
      customization: value.caption_customization,
    });
    setHeadline(value.headline_enabled ?? true);
    setHeadlinePosition(value.headline_position ?? "auto");
    setBatchMode(value.batch_mode ?? "keep");
    setClipsPerSource(value.clips_per_source ?? 3);
    setPresentation(value.presentation_mode ?? "off");
    setAiInstructions(value.ai_instructions ?? "");
    setCleanup(value.audio_cleanup ?? "off");
    setNormalize(value.normalize_audio ?? false);
    setFraming(value.framing === "crop" ? "crop" : "pad");
    setCropRegion(value.crop_region ?? null);
    setReviewConsent(false);
    setAiTitles(false);
  }
  async function restoreSetup(value: SetupDraft) {
    const recordings = await Promise.all(
      value.recording_ids.map((id) => api<Recording>(`/api/recordings/${id}`)),
    );
    setClips(
      recordings.map((recording) => ({
        id: crypto.randomUUID(),
        name: recording.name,
        file: null,
        recording,
        error: "",
      })),
    );
    setTitle(value.title);
    applySettings(value.settings);
    token.current =
      value.creation_token && value.creation_signature
        ? { id: value.creation_token, signature: value.creation_signature }
        : null;
    setSavedToken(token.current);
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ready || busy || (batchMode !== "keep" && !reviewConsent)) return;
    setBusy("saving");
    setError("");
    const settings = savedSettings;
    const body = {
      title,
      recording_ids: clips.map((clip) => clip.recording!.id),
      settings,
      generate_titles: batchMode === "keep" && aiTitles,
      remote_consent: batchMode !== "keep" ? reviewConsent : aiTitles,
    };
    draft.persistCurrent();
    let signature: string;
    try {
      const prepared = await api<SetupDraft>("/api/setup/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title,
          settings: body.settings,
          recording_ids: body.recording_ids,
        }),
      });
      body.settings = prepared.settings;
      signature = await creationFingerprint(body);
    } catch (error) {
      setError(message(error));
      setBusy(null);
      return;
    }
    if (token.current?.signature !== signature)
      token.current = { signature, id: crypto.randomUUID() };
    setSavedToken(token.current);
    draft.persistToken(token.current);
    try {
      const project = await api<Project>("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, id: token.current.id }),
      });
      draft.clear();
      router.push(`/projects/${project.id}`);
    } catch (error) {
      setError(
        `${message(error)} Check Projects after a lost connection. Retrying unchanged settings reuses the creation token.`,
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="project-workspace new-project-workspace">
      <header className="project-heading">
        <Button asChild variant="outline">
          <Link href="/">Home</Link>
        </Button>
        <Button asChild variant="outline">
          <Link href="/projects">Your projects</Link>
        </Button>
      </header>
      <h1>Batch edit supplied clips</h1>
      <p>Turn up to 15 videos into styled clips.</p>
      {error && (
        <Alert role="alert" variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <SetupDraftNotice draft={draft} onRestore={restoreSetup} />
      <form
        id={flow.formId}
        onSubmit={(event) => flow.submit(event, create)}
        onInvalidCapture={flow.onInvalid}
        className="flex flex-col gap-5"
      >
        <CreationFlow
          flow={flow}
          preview={
            <BatchSourcePreview
              mode={framing}
              cropRegion={cropRegion}
              disabled={busy !== null}
              onChange={(mode, crop) => {
                setFraming(mode);
                setCropRegion(crop);
              }}
              recordings={clips.flatMap((clip) =>
                clip.recording ? [clip.recording] : [],
              )}
            />
          }
        >
          <FieldSet disabled={busy !== null}>
            <CreationPanel value={0} active={flow.step}>
              <h2>Add your videos</h2>
              <Field>
                <FieldLabel htmlFor="batch-title">Batch name</FieldLabel>
                <Input
                  id="batch-title"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  required
                  maxLength={120}
                />
              </Field>
              <Card>
                <CardHeader>
                  <CardTitle>Videos</CardTitle>
                  <CardDescription>
                    MP4, MOV, MKV, M4V or WebM. Videos can be minutes long, up
                    to six hours each. Up to {limits.label} per file. Import
                    videos you have permission to edit.
                  </CardDescription>
                </CardHeader>
                <CardContent className="flex flex-col gap-4">
                  <ImportLimitNotice limits={limits} />
                  <Field>
                    <FieldLabel htmlFor="batch-files">Select videos</FieldLabel>
                    <Input
                      id="batch-files"
                      type="file"
                      multiple
                      accept=".mp4,.m4v,.mov,.mkv,.webm"
                      disabled={
                        busy !== null ||
                        limits.maxBytes === null ||
                        clips.length >= 15
                      }
                      onChange={(event) => {
                        addFiles(event.target.files);
                        event.target.value = "";
                      }}
                    />
                  </Field>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={loadingSources || busy !== null}
                    onClick={() => void loadSources()}
                  >
                    {loadingSources
                      ? "Loading imports…"
                      : "Choose from recent imports"}
                  </Button>
                  {sources.length > 0 && (
                    <FieldSet>
                      <FieldLegend>Recent clips</FieldLegend>
                      <FieldDescription>
                        Reuse imported videos.
                      </FieldDescription>
                      <FieldGroup>
                        {sources.map((source) => (
                          <Field key={source.id} orientation="horizontal">
                            <Checkbox
                              id={`batch-source-${source.id}`}
                              checked={clips.some(
                                (clip) => clip.recording?.id === source.id,
                              )}
                              disabled={
                                busy !== null ||
                                (clips.length >= 15 &&
                                  !clips.some(
                                    (clip) => clip.recording?.id === source.id,
                                  ))
                              }
                              onCheckedChange={(checked) => {
                                if (checked === true)
                                  setClips((previous) =>
                                    previous.some(
                                      (clip) =>
                                        clip.recording?.id === source.id,
                                    ) || previous.length >= 15
                                      ? previous
                                      : [
                                          ...previous,
                                          {
                                            id: crypto.randomUUID(),
                                            name: source.name,
                                            file: null,
                                            recording: source,
                                            error: "",
                                          },
                                        ],
                                  );
                                else
                                  setClips((previous) =>
                                    previous.filter(
                                      (clip) =>
                                        clip.recording?.id !== source.id,
                                    ),
                                  );
                              }}
                            />
                            <FieldLabel htmlFor={`batch-source-${source.id}`}>
                              {source.name} ({source.duration.toFixed(1)}s
                              {source.has_audio ? "" : ", no audio"})
                            </FieldLabel>
                          </Field>
                        ))}
                      </FieldGroup>
                    </FieldSet>
                  )}
                  {clips.length > 0 && (
                    <ul
                      className="flex flex-col gap-3"
                      aria-label="Supplied clips"
                    >
                      {clips.map((clip, index) => (
                        <li key={clip.id} className="flex flex-col gap-2">
                          <div className="flex flex-wrap items-center gap-3">
                            <span className="min-w-0 break-words">
                              {index + 1}. {clip.name}
                            </span>
                            <Badge variant="secondary">
                              {current === clip.id
                                ? "Importing"
                                : clip.error
                                  ? "Needs attention"
                                  : clip.recording
                                    ? `${clip.recording.duration.toFixed(1)}s imported`
                                    : "Waiting to import"}
                            </Badge>
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              disabled={busy !== null}
                              onClick={() =>
                                setClips((previous) =>
                                  previous.filter(
                                    (value) => value.id !== clip.id,
                                  ),
                                )
                              }
                              aria-label={`Remove ${clip.name}`}
                            >
                              Remove
                            </Button>
                          </div>
                          {clip.error && (
                            <Alert variant="destructive">
                              <AlertDescription>{clip.error}</AlertDescription>
                            </Alert>
                          )}
                          {current === clip.id && (
                            <>
                              <Progress
                                value={progress}
                                aria-label={`Import progress for ${clip.name}`}
                              />
                              <p role="status">
                                {progress < 100
                                  ? `${Math.floor(progress)}% transferred`
                                  : "Validating video…"}
                              </p>
                            </>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  <Button
                    type="button"
                    variant="outline"
                    disabled={
                      busy !== null ||
                      !clips.some((clip) => clip.file && !clip.recording)
                    }
                    onClick={() => void importFiles()}
                  >
                    Import selected clips
                  </Button>
                  <p className="text-sm text-muted-foreground">
                    Keep this page open while importing. Once processing starts,
                    you can leave and reopen the batch in Projects. Removing a
                    clip from this list preserves its imported source.
                  </p>
                </CardContent>
              </Card>
            </CreationPanel>
            <CreationPanel value={1} active={flow.step}>
              <h2>Choose a shared style</h2>
              <CaptionStylePicker
                name="batch-preset"
                value={style.preset}
                onChange={(preset) =>
                  setStyle((previous) => ({ ...previous, preset }))
                }
                disabled={busy !== null}
              />
              <Field>
                <FieldLabel htmlFor="batch-mode">
                  How should each video be processed?
                </FieldLabel>
                <NativeSelect
                  id="batch-mode"
                  value={batchMode}
                  onChange={(event) => {
                    setBatchMode(
                      event.target.value as "auto" | "keep" | "highlights",
                    );
                    setReviewConsent(false);
                  }}
                >
                  <NativeSelectOption value="highlights">
                    Find multiple highlights per video
                  </NativeSelectOption>
                  <NativeSelectOption value="auto">
                    AI review: keep whole or select a tighter cut
                  </NativeSelectOption>
                  <NativeSelectOption value="keep">
                    Keep full length
                  </NativeSelectOption>
                </NativeSelect>
                <FieldDescription>
                  AI reviews up to 6,000 words per video. Review results before
                  publishing; longer sources may need manual editing.
                </FieldDescription>
              </Field>
              {batchMode === "highlights" && (
                <Field>
                  <FieldLabel htmlFor="batch-clip-count">
                    Maximum clips per video
                  </FieldLabel>
                  <NativeSelect
                    id="batch-clip-count"
                    value={clipsPerSource}
                    onChange={(event) =>
                      setClipsPerSource(Number(event.target.value))
                    }
                  >
                    {[1, 2, 3, 4, 5].map((count) => (
                      <NativeSelectOption key={count} value={count}>
                        {count}
                      </NativeSelectOption>
                    ))}
                  </NativeSelect>
                  <FieldDescription>
                    AI may return fewer clips or keep one complete video.
                  </FieldDescription>
                </Field>
              )}
              <AiInstructionsField
                value={aiInstructions}
                onChange={setAiInstructions}
                mode={batchMode !== "keep" ? "review" : "titles"}
                disabled={busy !== null}
              />
              <AdvancedOptions>
                <PresentationField
                  value={presentation}
                  onChange={setPresentation}
                  disabled={busy !== null}
                />
                <CaptionStyleLibrary
                  value={style}
                  onApply={setStyle}
                  disabled={busy !== null}
                />
                <CaptionCustomizationFields
                  value={style.customization}
                  preset={style.preset}
                  onChange={(customization) =>
                    setStyle((previous) => ({ ...previous, customization }))
                  }
                  disabled={busy !== null}
                />
                <FieldGroup className="grid gap-4 sm:grid-cols-2">
                  <Field>
                    <FieldLabel htmlFor="batch-position">
                      Caption position
                    </FieldLabel>
                    <NativeSelect
                      id="batch-position"
                      value={style.position}
                      onChange={(event) =>
                        setStyle((previous) => ({
                          ...previous,
                          position: event.target
                            .value as CaptionStyle["position"],
                        }))
                      }
                    >
                      <NativeSelectOption value="auto">Auto</NativeSelectOption>
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

                  <Field>
                    <FieldLabel htmlFor="batch-cleanup">
                      Audio cleanup
                    </FieldLabel>
                    <NativeSelect
                      id="batch-cleanup"
                      value={cleanup}
                      onChange={(event) =>
                        setCleanup(event.target.value as typeof cleanup)
                      }
                    >
                      <NativeSelectOption value="off">
                        Off, preserve original audio
                      </NativeSelectOption>
                      <NativeSelectOption value="light">
                        Light
                      </NativeSelectOption>
                      <NativeSelectOption value="standard">
                        Standard
                      </NativeSelectOption>
                    </NativeSelect>
                  </Field>
                  <Field orientation="horizontal">
                    <Checkbox
                      id="batch-normalize"
                      checked={normalize}
                      onCheckedChange={(value) => setNormalize(value === true)}
                    />
                    <FieldLabel htmlFor="batch-normalize">
                      Normalize speaking volume
                    </FieldLabel>
                  </Field>
                </FieldGroup>
                <Field orientation="horizontal">
                  <Checkbox
                    id="batch-headline"
                    checked={headline}
                    onCheckedChange={(value) => setHeadline(value === true)}
                  />
                  <FieldLabel htmlFor="batch-headline">
                    Add an opening title card to each video
                  </FieldLabel>
                </Field>
                <Field>
                  <FieldLabel htmlFor="batch-headline-position">
                    Headline position
                  </FieldLabel>
                  <NativeSelect
                    id="batch-headline-position"
                    value={headlinePosition}
                    onChange={(event) =>
                      setHeadlinePosition(
                        event.target.value as typeof headlinePosition,
                      )
                    }
                  >
                    <NativeSelectOption value="auto">Auto</NativeSelectOption>
                    <NativeSelectOption value="upper">Upper</NativeSelectOption>
                    <NativeSelectOption value="centre">
                      Centre
                    </NativeSelectOption>
                  </NativeSelect>
                </Field>
              </AdvancedOptions>
              <AdvancedOptions label="Project presets">
                <ProjectPresets
                  workflow="batch"
                  settings={savedSettings}
                  disabled={busy !== null}
                  onApply={applySettings}
                />
              </AdvancedOptions>
            </CreationPanel>
            <CreationPanel value={2} active={flow.step}>
              <h2>Ready to create</h2>
              <dl className="grid grid-cols-2 gap-4">
                <div>
                  <dt className="text-muted-foreground">Batch</dt>
                  <dd>{title}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Videos</dt>
                  <dd>{clips.length}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Processing</dt>
                  <dd>
                    {batchMode === "keep"
                      ? "Keep full length"
                      : batchMode === "highlights"
                        ? "Find highlights"
                        : "Review and trim"}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Captions</dt>
                  <dd>{style.preset}</dd>
                </div>
              </dl>
              {batchMode !== "keep" && (
                <Field orientation="horizontal">
                  <Checkbox
                    id="batch-review-consent"
                    checked={reviewConsent}
                    onCheckedChange={(value) =>
                      setReviewConsent(value === true)
                    }
                  />
                  <FieldLabel htmlFor="batch-review-consent">
                    I agree to share saved transcripts with OpenAI for analysis.
                    API charges apply.
                  </FieldLabel>
                </Field>
              )}
              {batchMode === "keep" && (
                <Field orientation="horizontal">
                  <Checkbox
                    id="batch-ai"
                    checked={aiTitles}
                    onCheckedChange={(value) => setAiTitles(value === true)}
                  />
                  <FieldLabel htmlFor="batch-ai">
                    Generate titles with OpenAI from saved transcripts. API
                    charges apply.
                  </FieldLabel>
                </Field>
              )}
              <p>
                Transcription and rendering stay local. AI may return fewer
                highlights; review every clip before publishing.
              </p>
            </CreationPanel>
            <CreationFooter
              flow={flow}
              busy={busy !== null}
              action={
                <Button
                  type="submit"
                  disabled={
                    !ready ||
                    busy !== null ||
                    !title.trim() ||
                    (batchMode !== "keep" && !reviewConsent)
                  }
                >
                  {busy === "saving"
                    ? "Saving batch…"
                    : `Process ${clips.length || "your"} clips`}
                </Button>
              }
            />
          </FieldSet>
        </CreationFlow>
      </form>
      {busy === "importing" && (
        <Button variant="outline" onClick={() => controller.current?.abort()}>
          Stop importing
        </Button>
      )}
    </section>
  );
}
