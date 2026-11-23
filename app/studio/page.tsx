"use client";
import {
  useImportLimits,
  ImportLimitNotice,
} from "../components/import-limits";
import { DeleteAction } from "../components/delete-action";
import { PostedCheckbox } from "../components/posted-checkbox";
import { MAX_VIDEO_SECONDS } from "../../lib/projects";
import {
  Empty,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
  EmptyDescription,
} from "@/components/ui/empty";

import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import {
  FieldLabel,
  Field,
  FieldGroup,
  FieldSet,
  FieldLegend,
} from "@/components/ui/field";
import { Checkbox } from "@/components/ui/checkbox";
import { DownloadIcon, FilmIcon, UploadIcon } from "lucide-react";

import { useId, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, ApiError, message, uploadRecording } from "../../lib/client-api";
import { Progress } from "@/components/ui/progress";
import {
  LoadingStatus,
  WorkspaceLoading,
} from "../components/workspace-loading";
import {
  CaptionPanel,
  type CaptionPosition,
  type CaptionSelection,
  type CaptionState,
} from "../components/caption-panel";
import { HighlightPanel } from "../components/highlight-panel";
import { CaptionCustomizationFields } from "../components/caption-customization-fields";
import { CaptionStyleLibrary } from "../components/caption-style-library";
import { OutputQualityControls } from "../components/output-quality-controls";
import { defaultQuality, type QualityOptions } from "../../lib/projects";
import { CaptionStylePicker } from "../components/caption-style-picker";
import { CaptionEmphasisField } from "../components/caption-emphasis-field";
import { parseEmphasis, type Preset } from "../../lib/projects";
import {
  defaultCaptionCustomization,
  type CaptionCustomization,
  type FramingMode,
  type FollowPlan,
  type AutoLayoutPlan,
  type SplitLayout,
  type SourceRegion,
} from "../../lib/projects";
import { FramingEditor } from "../components/framing-editor";
import { EditorTools } from "../components/editor-tools";
import { useWorkspaceNavigation } from "../components/workspace-navigation";

type Recording = {
  id: string;
  name: string;
  duration: number;
  width: number;
  height: number;
  has_audio: boolean;
  size_bytes: number;
  created_at: string;
};
type Clip = {
  auto_layout?: AutoLayoutPlan | null;
  follow_plan?: FollowPlan | null;
  performance?: {
    backend: "cpu" | "nvenc";
    seconds: number;
    warning: string | null;
  } | null;
  posted?: boolean;
  id: string;
  recording_id: string;
  start: number;
  end: number;
  mode: FramingMode;
  status:
    | "queued"
    | "rendering"
    | "cancelling"
    | "succeeded"
    | "failed"
    | "cancelled";
  duration: number | null;
  error: string | null;
  size_bytes: number | null;
  captions: CaptionSelection | null;
};
type Headline = {
  appearance?: "classic" | "cover" | "profile";
  text: string;
  position: "auto" | "upper" | "centre";
  seconds: number;
};
type SelectionDraft = {
  quality: QualityOptions;
  headline?: Headline | null;
  start: number;
  end: number;
  mode: FramingMode;
  split_layout?: SplitLayout | null;
  crop_region?: SourceRegion | null;
  captions_enabled: boolean;
  caption_position: CaptionPosition;
  caption_customization: CaptionCustomization;
  caption_preset: Exclude<Preset, "none">;
  emphasis_words: string[];
  revision: number;
};
const running = (clip: Clip) =>
  ["queued", "rendering", "cancelling"].includes(clip.status);

const seconds = (value: number) => `${value.toFixed(1)} seconds`;
const clock = (value: number) => {
  const total = Math.floor(value);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}`
    : `${minutes}:${seconds}`;
};

export default function Workspace() {
  const limits = useImportLimits();
  const fieldId = useId();
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [clips, setClips] = useState<Clip[]>([]);
  const [start, setStart] = useState("0");
  const [end, setEnd] = useState("10");
  const [mode, setMode] = useState<FramingMode>("pad");
  const [cropRegion, setCropRegion] = useState<SourceRegion | null>(null);
  const [splitLayout, setSplitLayout] = useState<SplitLayout | null>(null);
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const importRequest = useRef<AbortController | null>(null);
  useEffect(() => () => importRequest.current?.abort(), []);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [previewError, setPreviewError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const [activeClip, setActiveClip] = useState<Clip | null>(null);
  const [historyError, setHistoryError] = useState("");
  const [moreRecordings, setMoreRecordings] = useState(false);
  const [libraryBusy, setLibraryBusy] = useState(false);
  const [clipOffset, setClipOffset] = useState(0);
  const [playerReady, setPlayerReady] = useState(false);
  const [captionState, setCaptionState] = useState<CaptionState | null>(null);
  const [previewSlot, setPreviewSlot] = useState<HTMLDivElement | null>(null);
  const [captionsEnabled, setCaptionsEnabled] = useState(false);
  const [captionPosition, setCaptionPosition] =
    useState<CaptionPosition>("auto");
  const [headline, setHeadline] = useState<Headline | null>(null);
  const [captionCustomization, setCaptionCustomization] =
    useState<CaptionCustomization>(defaultCaptionCustomization);
  const [captionPreset, setCaptionPreset] =
    useState<Exclude<Preset, "none">>("impact");
  const [emphasisText, setEmphasisText] = useState("");
  const [quality, setQuality] = useState<QualityOptions>(defaultQuality);
  const headlineValid = !headline || headline.text.trim().length > 0;
  const [savedDraft, setSavedDraft] = useState<{
    recordingId: string;
    value: SelectionDraft;
  } | null>(null);
  const [draftLoading, setDraftLoading] = useState(false);
  const [draftSaving, setDraftSaving] = useState(false);
  const [draftError, setDraftError] = useState("");
  const [draftRefresh, setDraftRefresh] = useState(0);
  const draftAction = useRef<AbortController | null>(null);
  const historyVersion = useRef(0);
  const selectionRef = useRef("");
  const fileInput = useRef<HTMLInputElement>(null);
  const player = useRef<HTMLVideoElement>(null);
  const selected = recordings.find((recording) => recording.id === selectedId);
  const activeExport = activeClip !== null && running(activeClip);
  const rangeStart = Number(start),
    rangeEnd = Number(end);
  const validRange =
    !!selected &&
    start !== "" &&
    end !== "" &&
    Number.isFinite(rangeStart) &&
    Number.isFinite(rangeEnd) &&
    rangeStart >= 0 &&
    rangeEnd <= selected.duration &&
    rangeEnd - rangeStart >= 0.1 &&
    rangeEnd - rangeStart <= MAX_VIDEO_SECONDS;
  const draft =
    savedDraft?.recordingId === selectedId ? savedDraft.value : null;
  const draftDirty =
    !!draft &&
    (start === "" ||
      end === "" ||
      rangeStart !== draft.start ||
      rangeEnd !== draft.end ||
      mode !== draft.mode ||
      JSON.stringify(cropRegion) !==
        JSON.stringify(draft.crop_region ?? null) ||
      JSON.stringify(splitLayout) !==
        JSON.stringify(draft.split_layout ?? null) ||
      captionsEnabled !== draft.captions_enabled ||
      captionPosition !== draft.caption_position ||
      JSON.stringify(quality) !==
        JSON.stringify(draft.quality ?? defaultQuality) ||
      captionPreset !== (draft.caption_preset ?? "classic") ||
      JSON.stringify(parseEmphasis(emphasisText)) !==
        JSON.stringify(draft.emphasis_words ?? []) ||
      JSON.stringify(captionCustomization) !==
        JSON.stringify(
          draft.caption_customization ?? defaultCaptionCustomization,
        ) ||
      JSON.stringify(headline) !== JSON.stringify(draft.headline ?? null));
  const currentCaption =
    captionState?.recordingId === selectedId &&
    captionState.start === rangeStart &&
    captionState.end === rangeEnd
      ? captionState
      : null;
  const correctionsLocked =
    !!currentCaption && (currentCaption.dirty || currentCaption.busy);
  const settingsLocked = draftLoading || draftSaving || !draft;
  const navigationLocked = draftDirty || correctionsLocked || draftSaving;
  const { setLocked } = useWorkspaceNavigation();
  useEffect(() => {
    setLocked(navigationLocked);
    return () => setLocked(false);
  }, [navigationLocked, setLocked]);
  const captionsReady =
    !!currentCaption?.ready && !!currentCaption.transcriptionId;

  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    api<SelectionDraft>(`/api/recordings/${selectedId}/draft`, {
      signal: controller.signal,
    })
      .then((result) => {
        if (controller.signal.aborted) return;
        setSavedDraft({ recordingId: selectedId, value: result });
        setStart(String(result.start));
        setEnd(String(result.end));
        setMode(result.mode);
        setSplitLayout(result.split_layout ?? null);
        setCropRegion(result.crop_region ?? null);
        setCaptionsEnabled(result.captions_enabled);
        setCaptionPosition(result.caption_position);
        setQuality(result.quality ?? defaultQuality);
        setCaptionPreset(result.caption_preset ?? "classic");
        setEmphasisText((result.emphasis_words ?? []).join(", "));
        setCaptionCustomization(
          result.caption_customization ?? defaultCaptionCustomization,
        );
        setHeadline(result.headline ?? null);
        setDraftError("");
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setDraftError(`Cannot load the saved selection. ${message(reason)}`);
      })
      .finally(() => {
        if (!controller.signal.aborted) setDraftLoading(false);
      });
    return () => controller.abort();
  }, [selectedId, draftRefresh]);

  useEffect(() => () => draftAction.current?.abort(), []);

  useEffect(() => {
    if (!navigationLocked) return;
    function preventLoss(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", preventLoss);
    return () => window.removeEventListener("beforeunload", preventLoss);
  }, [navigationLocked]);

  useEffect(() => {
    const controller = new AbortController();
    api<Recording[]>("/api/recordings", { signal: controller.signal })
      .then((items) => {
        if (controller.signal.aborted) return;
        setRecordings(items);
        setMoreRecordings(items.length === 100);
        setSelectedId(items[0]?.id ?? "");
        selectionRef.current = items[0]?.id ?? "";
        setStart("0");
        setEnd(String(Math.min(items[0]?.duration ?? 10, 30)));
        setClips([]);
        setPreviewError(false);
        setPlayerReady(false);
        setDraftLoading(items.length > 0);
        setSavedDraft(null);
        setDraftRefresh((value) => value + 1);
        setCaptionState(null);
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setError(
            `Cannot load the local workspace. Start the API and retry. ${message(reason)}`,
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [refresh]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const version = ++historyVersion.current;
    let failures = 0;
    async function loadClips() {
      try {
        const [items, active] = await Promise.all([
          selectedId
            ? api<Clip[]>(
                `/api/recordings/${selectedId}/clips?limit=20&offset=${clipOffset}`,
                { signal: controller.signal },
              )
            : Promise.resolve([]),
          api<Clip | null>("/api/exports/active", {
            signal: controller.signal,
          }),
        ]);
        if (controller.signal.aborted || version !== historyVersion.current)
          return;
        setClips(items);
        setActiveClip(active);
        setHistoryError("");
        failures = 0;
        timer = setTimeout(
          loadClips,
          active || items.some(running) ? 1000 : 5000,
        );
      } catch (reason) {
        if (!controller.signal.aborted && version === historyVersion.current) {
          setHistoryError(
            `Export status unavailable. Retrying. ${message(reason)}`,
          );
          timer = setTimeout(
            loadClips,
            Math.min(10_000, 1000 * 2 ** Math.min(++failures, 4)),
          );
        }
      }
    }
    void loadClips();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [selectedId, historyRefresh, clipOffset]);

  function refreshHistory() {
    historyVersion.current++;
    setHistoryRefresh((value) => value + 1);
  }

  function selectRecording(recording: Recording) {
    if (navigationLocked) return;
    selectionRef.current = recording.id;
    historyVersion.current++;
    setSelectedId(recording.id);
    setStart("0");
    setEnd(String(Math.min(recording.duration, 30)));
    setClips([]);
    setPreviewError(false);
    setPlayerReady(false);
    setClipOffset(0);
    setError("");
    setCaptionState(null);
    setCaptionsEnabled(false);
    setCaptionPosition("auto");
    setQuality(defaultQuality);
    setCaptionCustomization(defaultCaptionCustomization);
    setCaptionPreset("impact");
    setEmphasisText("");
    setSavedDraft(null);
    setDraftLoading(true);
    setDraftError("");
  }

  async function importFile(file: File) {
    if (importing || submitting || loading || libraryBusy || navigationLocked)
      return;
    setError("");
    if (limits.maxBytes === null) {
      setError(
        "Wait for the upload limit to load, or retry the API connection.",
      );
      return;
    }
    if (file.size === 0 || file.size > (limits.maxBytes ?? 0)) {
      setError(`Choose a non-empty video up to ${limits.label}.`);
      return;
    }
    setImporting(true);
    setUploadProgress(0);
    const controller = new AbortController();
    importRequest.current = controller;
    try {
      const recording = await uploadRecording<Recording>(
        file,
        controller.signal,
        setUploadProgress,
      );
      if (controller.signal.aborted) return;
      setRecordings((items) => [recording, ...items]);
      selectRecording(recording);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setImporting(false);
    }
  }

  async function exportClip(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !selected ||
      !validRange ||
      submitting ||
      importing ||
      activeExport ||
      !!historyError ||
      correctionsLocked ||
      settingsLocked ||
      (captionsEnabled && !captionsReady)
    )
      return;
    setError("");
    setSubmitting(true);
    historyVersion.current++;
    try {
      if (!(await saveDraft())) return;
      const clip = await api<Clip>(`/api/recordings/${selected.id}/clips`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start: rangeStart,
          end: rangeEnd,
          mode,
          split_layout: splitLayout,
          crop_region: cropRegion,
          headline,
          quality,
          captions:
            captionsEnabled && currentCaption?.transcriptionId
              ? {
                  transcription_id: currentCaption.transcriptionId,
                  revision: currentCaption.revision,
                  position: captionPosition,
                  customization: captionCustomization,
                  preset: captionPreset,
                  emphasis_words: parseEmphasis(emphasisText),
                }
              : null,
        }),
      });
      if (selectionRef.current === clip.recording_id)
        setClips((items) => [
          clip,
          ...items.filter((item) => item.id !== clip.id),
        ]);
      setActiveClip(clip);
      setClipOffset(0);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setSubmitting(false);
      refreshHistory();
    }
  }

  async function saveDraft(): Promise<boolean> {
    if (
      !selected ||
      !draft ||
      !validRange ||
      !headlineValid ||
      draftAction.current
    )
      return false;
    if (!draftDirty) return true;
    const controller = new AbortController();
    draftAction.current = controller;
    setDraftSaving(true);
    setDraftError("");
    try {
      const result = await api<SelectionDraft>(
        `/api/recordings/${selected.id}/draft`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            start: rangeStart,
            end: rangeEnd,
            mode,
            split_layout: splitLayout,
            crop_region: cropRegion,
            headline,
            quality,
            captions_enabled: captionsEnabled,
            caption_position: captionPosition,
            caption_customization: captionCustomization,
            caption_preset: captionPreset,
            emphasis_words: parseEmphasis(emphasisText),
            revision: draft.revision,
          }),
          signal: controller.signal,
        },
      );
      if (controller.signal.aborted) return false;
      setSavedDraft({ recordingId: selected.id, value: result });
      return true;
    } catch (reason) {
      if (!controller.signal.aborted)
        setDraftError(
          reason instanceof ApiError && reason.status === 409
            ? "The selection was changed in another window. Your current values are preserved. Reload the saved selection before making further changes."
            : `Selection could not be saved. ${message(reason)}`,
        );
      return false;
    } finally {
      if (!controller.signal.aborted) setDraftSaving(false);
      draftAction.current = null;
    }
  }

  function reloadDraft() {
    setDraftLoading(true);
    setDraftRefresh((value) => value + 1);
  }

  async function cancelClip(clip: Clip) {
    historyVersion.current++;
    try {
      const updated = await api<Clip>(`/api/clips/${clip.id}/cancel`, {
        method: "POST",
      });
      if (selectionRef.current === updated.recording_id)
        setClips((items) =>
          items.map((item) => (item.id === clip.id ? updated : item)),
        );
      setActiveClip(updated);
    } catch (reason) {
      setError(message(reason));
    } finally {
      refreshHistory();
    }
  }

  async function loadMoreRecordings() {
    setLibraryBusy(true);
    try {
      const items = await api<Recording[]>(
        `/api/recordings?offset=${recordings.length}`,
      );
      setRecordings((current) => [
        ...current,
        ...items.filter(
          (item) => !current.some((existing) => existing.id === item.id),
        ),
      ]);
      setMoreRecordings(items.length === 100);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setLibraryBusy(false);
    }
  }

  function setBoundaryFromPlayhead(boundary: "start" | "end") {
    const time = player.current?.currentTime;
    if (time === undefined || !Number.isFinite(time)) return;
    (boundary === "start" ? setStart : setEnd)(time.toFixed(3));
  }

  return (
    <div className="app-shell">
      <aside className="recording-library" aria-label="Recording library">
        <section className="library" aria-labelledby="library-title">
          <div className="library-heading">
            <h2 id="library-title">Recordings</h2>
            <span>
              {recordings.length}
              {moreRecordings ? "+" : ""}
            </span>
          </div>
          {loading ? (
            <LoadingStatus label="Loading library…" />
          ) : recordings.length === 0 ? (
            <p className="library-empty">
              Your library starts with a recording. Import one to get going.
            </p>
          ) : (
            <ul className="recording-list">
              {recordings.map((recording) => (
                <li key={recording.id}>
                  <Button
                    type="button"
                    className="library-recording h-auto w-full justify-start gap-2 p-2 text-left whitespace-normal"
                    aria-current={
                      selectedId === recording.id ? "true" : undefined
                    }
                    disabled={submitting || importing || navigationLocked}
                    onClick={() => {
                      if (selectedId !== recording.id)
                        selectRecording(recording);
                    }}
                    variant="ghost"
                  >
                    <span className="recording-symbol">
                      <FilmIcon data-icon="inline-start" />
                    </span>
                    <span className="recording-description">
                      <strong>{recording.name}</strong>
                      <span>
                        {clock(recording.duration)}
                        <span className="recording-type">Video</span>
                      </span>
                    </span>
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {moreRecordings && (
            <Button
              type="button"
              disabled={libraryBusy || importing}
              onClick={() => void loadMoreRecordings()}
              variant="outline"
            >
              {libraryBusy ? "Loading recordings..." : "Load older recordings"}
            </Button>
          )}
        </section>
      </aside>
      <div className="workspace-shell">
        <section id="workspace" tabIndex={-1}>
          <div className="workspace-heading">
            <div className="intro">
              <h1>Clip editor</h1>
              <p>Find your moment. Make the cut.</p>
            </div>
            <section className="import-area" aria-label="Import recording">
              {selected && !navigationLocked && !importing && !submitting ? (
                <Button asChild variant="outline">
                  <Link href={`/projects/new/${selected.id}`}>
                    Create project from this video
                  </Link>
                </Button>
              ) : null}
              <Input
                ref={fileInput}
                type="file"
                accept=".mp4,.m4v,.mov,.mkv,.webm"
                className="visually-hidden"
                aria-label="Choose recording"
                disabled={limits.maxBytes === null || importing}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) void importFile(file);
                }}
              />
              <Button
                type="button"
                className="import-button"
                disabled={
                  limits.maxBytes === null ||
                  importing ||
                  loading ||
                  submitting ||
                  libraryBusy ||
                  navigationLocked
                }
                onClick={() => fileInput.current?.click()}
                variant="default"
              >
                <UploadIcon data-icon="inline-start" />
                {importing ? "Importing recording..." : "Import recording"}
              </Button>
              <ImportLimitNotice limits={limits} />
              <p role="status">
                {importing
                  ? uploadProgress < 100
                    ? `${Math.floor(uploadProgress)}% transferred. Keep this page open.`
                    : "Saving and checking your video…"
                  : `MP4, MOV, MKV, M4V, WebM. Up to ${limits.label}.`}
              </p>
              {importing ? (
                <Progress
                  value={uploadProgress}
                  aria-label="Recording transfer progress"
                />
              ) : null}
            </section>
          </div>
          {error && (
            <Alert role="alert" variant="destructive">
              <AlertDescription>
                {error}
                <Button
                  type="button"
                  disabled={
                    importing || submitting || libraryBusy || navigationLocked
                  }
                  onClick={() => {
                    setError("");
                    setLoading(true);
                    setRefresh((value) => value + 1);
                    refreshHistory();
                  }}
                  variant="outline"
                >
                  Reload workspace
                </Button>
              </AlertDescription>
            </Alert>
          )}
          {historyError && (
            <Alert role="status" variant="default">
              <AlertDescription>{historyError}</AlertDescription>
            </Alert>
          )}
          {loading ? (
            <WorkspaceLoading label="Loading your workspace…" />
          ) : (
            <div className="studio-content">
              <div className="editor-grid">
                <div ref={setPreviewSlot} className="clip-preview-column" />
                <div className="editor-column">
                  <Card className="source-panel" aria-labelledby="source-title">
                    <CardHeader className="source-heading flex flex-wrap items-start justify-between gap-4">
                      <div>
                        <CardTitle
                          role="heading"
                          aria-level={2}
                          id="source-title"
                        >
                          Source recording
                        </CardTitle>
                        <CardDescription className="filename">
                          {selected?.name ?? "No recording imported"}
                        </CardDescription>
                      </div>
                      {recordings.length > 1 && (
                        <Field className="recording-select">
                          <FieldLabel htmlFor={`${fieldId}-1`}>
                            Recording
                          </FieldLabel>
                          <NativeSelect
                            id={`${fieldId}-1`}
                            aria-label="Recording"
                            value={selectedId}
                            disabled={
                              submitting || importing || navigationLocked
                            }
                            onChange={(event) => {
                              const found = recordings.find(
                                (item) => item.id === event.target.value,
                              );
                              if (found) selectRecording(found);
                            }}
                            className="w-full"
                          >
                            {recordings.map((recording) => (
                              <NativeSelectOption
                                key={recording.id}
                                value={recording.id}
                              >
                                {recording.name}
                              </NativeSelectOption>
                            ))}
                          </NativeSelect>
                        </Field>
                      )}
                    </CardHeader>
                    <CardContent className="flex flex-col gap-4">
                      {moreRecordings && (
                        <div className="compact-library">
                          <Button
                            type="button"
                            disabled={libraryBusy || importing}
                            onClick={() => void loadMoreRecordings()}
                            variant="outline"
                          >
                            {libraryBusy
                              ? "Loading recordings..."
                              : "Load older recordings"}
                          </Button>
                        </div>
                      )}
                      {selected ? (
                        <>
                          <video
                            ref={player}
                            key={selected.id}
                            className="source-video"
                            controls
                            playsInline
                            preload="metadata"
                            src={`/api/recordings/${selected.id}/media`}
                            onError={() => setPreviewError(true)}
                            onLoadedMetadata={() => {
                              setPlayerReady(true);
                              setPreviewError(false);
                            }}
                            aria-label="Source recording preview"
                          />
                          {previewError && (
                            <Alert role="status" variant="default">
                              <AlertDescription>
                                The source preview could not play. Check that
                                Cliptric is connected and reopen the recording.
                                If this format still will not play, import an
                                MP4 copy to preview it.
                              </AlertDescription>
                            </Alert>
                          )}
                          <p className="media-info">
                            {selected.width} × {selected.height} ·{" "}
                            {seconds(selected.duration)} ·{" "}
                            {selected.has_audio
                              ? "Audio included"
                              : "No audio track"}
                          </p>
                        </>
                      ) : (
                        <Empty className="empty-preview">
                          <EmptyHeader>
                            <EmptyMedia variant="icon">
                              <FilmIcon aria-hidden="true" />
                            </EmptyMedia>
                            <EmptyTitle>
                              A long recording. A good moment.
                            </EmptyTitle>
                            <EmptyDescription>
                              Import your video, find the part worth keeping,
                              and make it a clip.
                            </EmptyDescription>
                          </EmptyHeader>
                        </Empty>
                      )}
                      <div className="selection-panel">
                        <div className="section-heading">
                          <h2>Choose a moment</h2>
                          <Badge variant="secondary">
                            {validRange
                              ? `${(rangeEnd - rangeStart).toFixed(1)}s`
                              : "0.1 seconds to the source duration"}
                          </Badge>
                        </div>
                        {selected && (
                          <div
                            className="selection-overview"
                            aria-hidden="true"
                          >
                            <div className="source-ruler">
                              {validRange && (
                                <span
                                  style={{
                                    left: `${(100 * rangeStart) / selected.duration}%`,
                                    width: `${(100 * (rangeEnd - rangeStart)) / selected.duration}%`,
                                  }}
                                />
                              )}
                            </div>
                            <div className="ruler-labels">
                              <span>0:00</span>
                              <span>{clock(selected.duration)}</span>
                            </div>
                          </div>
                        )}
                        <FieldGroup className="boundary-fields grid gap-4 sm:grid-cols-2">
                          <Field className="time-field grid grid-cols-1 items-end gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
                            <FieldLabel htmlFor="clip-start">
                              Start (seconds)
                            </FieldLabel>
                            <Input
                              id="clip-start"
                              type="number"
                              min="0"
                              max={selected?.duration}
                              step="0.001"
                              value={start}
                              disabled={
                                !selected ||
                                submitting ||
                                correctionsLocked ||
                                settingsLocked
                              }
                              onChange={(event) => setStart(event.target.value)}
                            />
                            <Button
                              type="button"
                              disabled={
                                !selected ||
                                previewError ||
                                !playerReady ||
                                submitting ||
                                correctionsLocked ||
                                settingsLocked
                              }
                              onClick={() => setBoundaryFromPlayhead("start")}
                              variant="outline"
                            >
                              Use playhead
                            </Button>
                          </Field>
                          <Field className="time-field grid grid-cols-1 items-end gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
                            <FieldLabel htmlFor="clip-end">
                              End (seconds)
                            </FieldLabel>
                            <Input
                              id="clip-end"
                              type="number"
                              min="0.1"
                              max={selected?.duration}
                              step="0.001"
                              value={end}
                              disabled={
                                !selected ||
                                submitting ||
                                correctionsLocked ||
                                settingsLocked
                              }
                              onChange={(event) => setEnd(event.target.value)}
                            />
                            <Button
                              type="button"
                              disabled={
                                !selected ||
                                previewError ||
                                !playerReady ||
                                submitting ||
                                correctionsLocked ||
                                settingsLocked
                              }
                              onClick={() => setBoundaryFromPlayhead("end")}
                              variant="outline"
                            >
                              Use playhead
                            </Button>
                          </Field>
                        </FieldGroup>
                        <p
                          className={cn(
                            "selection-help",
                            selected && !validRange && "invalid",
                          )}
                        >
                          {!selected
                            ? "Choose a recording to set clip boundaries."
                            : validRange
                              ? `${seconds(rangeEnd - rangeStart)} selected`
                              : "Choose an interval within the recording, from 0.1 seconds up to six hours long."}
                        </p>
                      </div>
                    </CardContent>
                  </Card>
                  {selected && draft && !draftLoading && (
                    <EditorTools
                      key={`tools:${selected.id}`}
                      transcript={
                        <CaptionPanel
                          key={selected.id}
                          recordingId={selected.id}
                          hasAudio={selected.has_audio}
                          recordingDuration={selected.duration}
                          start={rangeStart}
                          end={rangeEnd}
                          validRange={validRange}
                          disabled={submitting || importing || draftSaving}
                          onState={setCaptionState}
                        />
                      }
                      highlights={
                        <HighlightPanel
                          key={`highlights:${selected.id}`}
                          recordingId={selected.id}
                          disabled={
                            submitting ||
                            importing ||
                            settingsLocked ||
                            navigationLocked
                          }
                          onSelect={(candidate) => {
                            setStart(String(candidate.start));
                            setEnd(String(candidate.end));
                            setCaptionState(null);
                            if (playerReady && player.current) {
                              player.current.currentTime = candidate.start;
                            }
                          }}
                        />
                      }
                    />
                  )}
                <Card
                  className="export-panel"
                  aria-label="Export settings"
                  role="complementary"
                >
                  <CardHeader>
                    <CardTitle role="heading" aria-level={2}>
                      Make it a clip
                    </CardTitle>
                    <CardDescription>
                      Review framing and captions before exporting.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="flex flex-col gap-4">
                    <form onSubmit={exportClip}>
                      <FieldSet
                        disabled={
                          !selected || submitting || importing || settingsLocked
                        }
                      >
                        <FieldLegend className="sr-only">
                          Export settings
                        </FieldLegend>
                        <FieldSet>
                          <FieldLegend>Opening headline</FieldLegend>
                          <Field orientation="horizontal">
                            <Checkbox
                              id="studio-headline-enabled"
                              checked={headline !== null}
                              onCheckedChange={(checked) =>
                                setHeadline(
                                  checked === true
                                    ? {
                                        text:
                                          selected?.name
                                            .replace(/\.[^.]+$/, "")
                                            .slice(0, 120) || "Your moment",
                                        position: "auto",
                                        seconds: 8,
                                      }
                                    : null,
                                )
                              }
                            />
                            <FieldLabel htmlFor="studio-headline-enabled">
                              Show a headline for the first eight seconds
                            </FieldLabel>
                          </Field>
                          {headline ? (
                            <>
                              <Field data-invalid={!headlineValid}>
                                <FieldLabel htmlFor="studio-headline-text">
                                  Headline text
                                </FieldLabel>
                                <Input
                                  id="studio-headline-text"
                                  maxLength={120}
                                  required
                                  aria-invalid={!headlineValid}
                                  value={headline.text}
                                  onChange={(event) =>
                                    setHeadline({
                                      ...headline,
                                      text: event.target.value,
                                    })
                                  }
                                />
                              </Field>
                              <Field>
                                <FieldLabel htmlFor="studio-headline-position">
                                  Headline placement
                                </FieldLabel>
                                <NativeSelect
                                  id="studio-headline-position"
                                  value={headline.position}
                                  onChange={(event) => {
                                    const position = event.target.value;
                                    if (
                                      position === "auto" ||
                                      position === "upper" ||
                                      position === "centre"
                                    )
                                      setHeadline({ ...headline, position });
                                  }}
                                  className="w-full"
                                >
                                  <NativeSelectOption value="auto">
                                    Auto: avoid the caption area
                                  </NativeSelectOption>
                                  <NativeSelectOption value="upper">
                                    Top
                                  </NativeSelectOption>
                                  <NativeSelectOption value="centre">
                                    Middle
                                  </NativeSelectOption>
                                </NativeSelect>
                              </Field>
                            </>
                          ) : null}
                          <p>
                            Black text on a white card. Auto avoids spoken
                            captions; use Top or Middle if it covers the
                            subject.
                          </p>
                        </FieldSet>
                        <div className="output-format">
                          <span className="format-frame">9:16</span>
                          <div>
                            <strong>Vertical video</strong>
                            <span>1080 × 1920 / MP4</span>
                          </div>
                        </div>
                        <FramingEditor
                          mode={mode}
                          layout={splitLayout}
                          cropRegion={cropRegion}
                          previewSlot={previewSlot}
                          previewOptions={{
                            captions: {
                              preset: captionsEnabled ? captionPreset : "none",
                              position: captionPosition,
                              customization: captionCustomization,
                            },
                            headline,
                            transcriptionId: currentCaption?.transcriptionId,
                            transcriptRevision: currentCaption?.revision,
                            emphasis: parseEmphasis(emphasisText),
                            savedExportId: clips.find(
                              (clip) => clip.status === "succeeded" &&
                                clip.recording_id === selectedId &&
                                clip.start === rangeStart && clip.end === rangeEnd,
                            )?.id,
                          }}
                          quality={quality}
                          autoLayout={
                            clips.find(
                              (clip) =>
                                clip.mode === "auto" &&
                                clip.recording_id === selectedId &&
                                clip.start === rangeStart &&
                                clip.end === rangeEnd,
                            )?.auto_layout
                          }
                          followPlan={
                            clips.find(
                              (clip) =>
                                (clip.mode === "follow" ||
                                  clip.mode === "auto") &&
                                clip.recording_id === selectedId &&
                                clip.start === rangeStart &&
                                clip.end === rangeEnd,
                            )?.follow_plan
                          }
                          end={validRange ? rangeEnd : undefined}
                          sourceUrl={
                            selected
                              ? `/api/recordings/${selected.id}/media`
                              : undefined
                          }
                          timestamp={rangeStart}
                          disabled={
                            !selected ||
                            submitting ||
                            importing ||
                            settingsLocked
                          }
                          onChange={(nextMode, layout, crop) => {
                            setMode(nextMode);
                            setSplitLayout(layout);
                            setCropRegion(crop);
                            setQuality((value) => ({
                              ...value,
                              smart_framing: null,
                            }));
                          }}
                        />
                        <div className="caption-settings">
                          <Field
                            className="radio-option"
                            orientation="horizontal"
                          >
                            <Checkbox
                              id={`${fieldId}-2`}
                              checked={captionsEnabled}
                              disabled={!captionsEnabled && !captionsReady}
                              onCheckedChange={(checked) =>
                                setCaptionsEnabled(checked === true)
                              }
                            />
                            <FieldLabel htmlFor={`${fieldId}-2`}>
                              Include captions
                            </FieldLabel>
                          </Field>
                          <Field className="caption-position">
                            <FieldLabel htmlFor={`${fieldId}-3`}>
                              Caption position
                            </FieldLabel>
                            <NativeSelect
                              id={`${fieldId}-3`}
                              value={captionPosition}
                              disabled={!captionsEnabled}
                              onChange={(event) =>
                                setCaptionPosition(
                                  event.target.value as CaptionPosition,
                                )
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
                          <CaptionCustomizationFields
                            key="caption-customization"
                            preset={captionsEnabled ? captionPreset : "none"}
                            source={
                              currentCaption?.ready &&
                              currentCaption.transcriptionId
                                ? {
                                    transcriptionId:
                                      currentCaption.transcriptionId,
                                    revision: currentCaption.revision,
                                    start: rangeStart,
                                    end: rangeEnd,
                                  }
                                : undefined
                            }
                            value={captionCustomization}
                            disabled={
                              !captionsEnabled ||
                              submitting ||
                              activeExport ||
                              draftSaving
                            }
                            onChange={setCaptionCustomization}
                          />
                          <OutputQualityControls
                            value={quality}
                            onChange={setQuality}
                            recordingId={selectedId}
                            start={rangeStart}
                            end={rangeEnd}
                            allowSmart={
                              mode !== "split" &&
                              mode !== "follow" &&
                              mode !== "auto" &&
                              cropRegion === null
                            }
                            disabled={
                              !selected ||
                              submitting ||
                              activeExport ||
                              draftSaving ||
                              correctionsLocked
                            }
                            allowedEnd={selected?.duration ?? 0}
                            onBounds={(start, end) => {
                              setStart(String(start));
                              setEnd(String(end));
                            }}
                            source={
                              currentCaption?.ready &&
                              currentCaption.transcriptionId
                                ? {
                                    transcriptionId:
                                      currentCaption.transcriptionId,
                                    revision: currentCaption.revision,
                                    start: rangeStart,
                                    end: rangeEnd,
                                  }
                                : undefined
                            }
                          />
                          <CaptionStylePicker
                            name="studio_preset"
                            value={captionsEnabled ? captionPreset : "none"}
                            disabled={submitting || activeExport || draftSaving}
                            onChange={(preset) => {
                              setCaptionsEnabled(preset !== "none");
                              if (preset !== "none") setCaptionPreset(preset);
                            }}
                          />
                          <CaptionStyleLibrary
                            value={{
                              preset: captionsEnabled ? captionPreset : "none",
                              position: captionPosition,
                              customization: captionCustomization,
                            }}
                            disabled={submitting || activeExport || draftSaving}
                            onApply={(style) => {
                              setCaptionsEnabled(style.preset !== "none");
                              if (style.preset !== "none")
                                setCaptionPreset(style.preset);
                              setCaptionPosition(style.position);
                              setCaptionCustomization(style.customization);
                            }}
                          />
                          <CaptionEmphasisField
                            name="studio_emphasis"
                            value={emphasisText}
                            onChange={setEmphasisText}
                            disabled={
                              !captionsEnabled ||
                              submitting ||
                              activeExport ||
                              draftSaving
                            }
                            source={
                              currentCaption?.ready &&
                              currentCaption.transcriptionId
                                ? {
                                    transcriptionId:
                                      currentCaption.transcriptionId,
                                    revision: currentCaption.revision,
                                    start: rangeStart,
                                    end: rangeEnd,
                                  }
                                : undefined
                            }
                          />
                          <p className="caption-help">
                            White text with a dark outline. Captions are burned
                            into the exported video using the saved transcript.
                          </p>
                          {captionsEnabled && !captionsReady && (
                            <Alert variant="default" role="status">
                              <AlertDescription>
                                Review and save the selected words before
                                exporting captions, or turn captions off.
                              </AlertDescription>
                            </Alert>
                          )}
                        </div>
                        <Button
                          className="export-button"
                          type="submit"
                          disabled={
                            !validRange ||
                            submitting ||
                            activeExport ||
                            importing ||
                            !!historyError ||
                            correctionsLocked ||
                            (captionsEnabled && !captionsReady)
                          }
                          variant="default"
                        >
                          {submitting
                            ? "Starting export..."
                            : activeExport
                              ? "Export processing..."
                              : "Export clip"}
                        </Button>
                      </FieldSet>
                    </form>
                    {selected && (
                      <div className="selection-draft">
                        {draftLoading ? (
                          <LoadingStatus label="Loading saved selection…" />
                        ) : (
                          <p role="status">
                            {draftSaving
                              ? "Saving selection..."
                              : draftDirty
                                ? "Selection has unsaved changes. Export also saves these settings."
                                : draft
                                  ? "Selection saved."
                                  : "Selection unavailable."}
                          </p>
                        )}
                        {draftError && (
                          <Alert role="alert" variant="destructive">
                            <AlertDescription>{draftError}</AlertDescription>
                          </Alert>
                        )}
                        <div className="caption-actions">
                          <Button
                            type="button"
                            disabled={
                              !draftDirty ||
                              !validRange ||
                              draftLoading ||
                              draftSaving ||
                              submitting ||
                              correctionsLocked
                            }
                            onClick={() => void saveDraft()}
                            variant="outline"
                          >
                            Save selection
                          </Button>
                          {(draftDirty || draftError) && (
                            <Button
                              type="button"
                              disabled={
                                draftLoading ||
                                draftSaving ||
                                submitting ||
                                correctionsLocked
                              }
                              onClick={reloadDraft}
                              variant="outline"
                            >
                              {draftDirty
                                ? "Discard changes and reload selection"
                                : "Reload saved selection"}
                            </Button>
                          )}
                        </div>
                        {draftDirty && (
                          <p className="caption-help">
                            Save or discard the selection before switching
                            recordings.
                          </p>
                        )}
                      </div>
                    )}
                    {activeClip &&
                      activeClip.recording_id !== selectedId &&
                      running(activeClip) && (
                        <div className="active-export" role="status">
                          <p>
                            An export for another recording is{" "}
                            {activeClip.status === "cancelling"
                              ? "cancelling"
                              : "processing"}
                            .
                          </p>
                          <Button
                            type="button"
                            disabled={activeClip.status === "cancelling"}
                            onClick={() => void cancelClip(activeClip)}
                            variant="outline"
                          >
                            Cancel active export
                          </Button>
                        </div>
                      )}
                  </CardContent>
                </Card>
                </div>
              </div>
              <section
                className="exports"
                id="exports"
                aria-labelledby="exports-title"
              >
                <div className="section-heading">
                  <div>
                    <h2 id="exports-title">Your exports</h2>
                    <p>Finished clips, ready for their next screen.</p>
                  </div>
                  <DownloadIcon data-icon="inline-start" />
                </div>
                {clips.length === 0 ? (
                  <Empty>
                    <EmptyHeader>
                      <EmptyTitle>No exports yet</EmptyTitle>
                      <EmptyDescription>
                        No exports yet. Choose a moment above, then export your
                        first clip.
                      </EmptyDescription>
                    </EmptyHeader>
                  </Empty>
                ) : (
                  <ul className="export-list">
                    {clips.map((clip) => (
                      <li
                        key={clip.id}
                        className="export-item"
                        data-status={clip.status}
                      >
                        <Card className="h-full">
                          <CardHeader>
                            <CardTitle role="heading" aria-level={3}>
                              Clip {clip.id.slice(0, 8)}
                            </CardTitle>
                          </CardHeader>
                          <CardContent className="flex items-start gap-4">
                            {clip.status === "succeeded" && (
                              <video
                                controls
                                playsInline
                                preload="metadata"
                                src={`/api/clips/${clip.id}/media`}
                                aria-label={`Clip ${clip.id.slice(0, 8)} preview`}
                              />
                            )}

                            <div className="export-detail">
                              <p>
                                {seconds(
                                  clip.duration ?? clip.end - clip.start,
                                )}
                              </p>
                              {clip.captions && (
                                <p>
                                  Captions included · revision{" "}
                                  {clip.captions.revision}
                                </p>
                              )}
                              <p
                                className="export-state"
                                role={running(clip) ? "status" : undefined}
                              >
                                {clip.status === "succeeded"
                                  ? "Ready"
                                  : clip.status === "rendering"
                                    ? "Rendering and verifying..."
                                    : clip.status === "queued"
                                      ? "Starting..."
                                      : clip.status === "cancelling"
                                        ? "Cancelling..."
                                        : clip.status === "cancelled"
                                          ? "Cancelled"
                                          : "Export failed"}
                              </p>
                              {clip.performance ? (
                                <div className="flex flex-col gap-2">
                                  <Badge variant="outline">
                                    {clip.performance.backend === "nvenc"
                                      ? "NVIDIA GPU"
                                      : "CPU"}{" "}
                                    · {clip.performance.seconds.toFixed(1)}s
                                  </Badge>
                                  {clip.performance.warning ? (
                                    <Alert>
                                      <AlertDescription>
                                        {clip.performance.warning}
                                      </AlertDescription>
                                    </Alert>
                                  ) : null}
                                </div>
                              ) : null}
                              {clip.error && (
                                <p className="invalid">{clip.error}</p>
                              )}
                              {clip.status === "succeeded" && (
                                <Button asChild variant="outline">
                                  <a
                                    href={`/api/clips/${clip.id}/media?download=true`}
                                  >
                                    <DownloadIcon data-icon="inline-start" />{" "}
                                    Download
                                  </a>
                                </Button>
                              )}
                              {running(clip) && (
                                <Button
                                  type="button"
                                  disabled={clip.status === "cancelling"}
                                  onClick={() => void cancelClip(clip)}
                                  variant="outline"
                                >
                                  Cancel export
                                </Button>
                              )}
                              <DeleteAction
                                path={`/api/clips/${clip.id}`}
                                label="Delete clip"
                                name={`Clip ${clip.start.toFixed(1)}–${clip.end.toFixed(1)} seconds`}
                                disabled={running(clip)}
                                description="Permanently removes this local exported video and any linked project clip entry. Source videos, transcripts, other versions, downloaded copies and published posts remain."
                                onDeleted={() => {
                                  historyVersion.current++;
                                  setActiveClip((value) =>
                                    value?.id === clip.id ? null : value,
                                  );
                                  setClips((values) =>
                                    values.filter(
                                      (value) => value.id !== clip.id,
                                    ),
                                  );
                                  setHistoryRefresh((value) => value + 1);
                                }}
                              />
                              <PostedCheckbox
                                path={`/api/clips/${clip.id}/posted`}
                                checked={clip.posted}
                                disabled={running(clip)}
                                title={`Clip ${clip.start.toFixed(1)}–${clip.end.toFixed(1)} seconds`}
                                onSaved={(posted) => {
                                  historyVersion.current++;
                                  setClips((values) =>
                                    values.map((value) =>
                                      value.id === clip.id
                                        ? { ...value, posted }
                                        : value,
                                    ),
                                  );
                                  setHistoryRefresh((value) => value + 1);
                                }}
                              />
                            </div>
                          </CardContent>
                        </Card>
                      </li>
                    ))}
                  </ul>
                )}
                {(clipOffset > 0 || clips.length === 20) && (
                  <div
                    className="history-navigation"
                    aria-label="Export history pages"
                  >
                    <Button
                      type="button"
                      disabled={clipOffset === 0}
                      onClick={() =>
                        setClipOffset((value) => Math.max(0, value - 20))
                      }
                      variant="outline"
                    >
                      Newer exports
                    </Button>
                    <Button
                      type="button"
                      disabled={clips.length < 20}
                      onClick={() => setClipOffset((value) => value + 20)}
                      variant="outline"
                    >
                      Older exports
                    </Button>
                  </div>
                )}
              </section>{" "}
            </div>
          )}
          <footer className="workspace-footer">
            <span>Cliptric. Keep the good part.</span>
            <span>Original recordings are preserved.</span>
          </footer>
        </section>
      </div>
    </div>
  );
}
