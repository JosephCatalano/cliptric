"use client";
import { PostCopyEditor } from "./post-copy-editor";
import { ProjectStepProgress } from "./project-step-progress";
import { AdvancedOptions } from "./creation-flow";
import { PublishChooser } from "./publish-chooser";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import {
  Empty,
  EmptyHeader,
  EmptyTitle,
  EmptyDescription,
  EmptyContent,
} from "@/components/ui/empty";
import { ClipReview } from "./clip-review";
import { ClipVersions } from "./clip-versions";
import { ClipBulkActions } from "./clip-bulk-actions";
import { useUnsavedEdits } from "./use-unsaved-edits";
import { reviewNeeded } from "@/lib/project-edit";
import { useRouter } from "next/navigation";
import { DeleteAction } from "./delete-action";
import { PostedCheckbox } from "./posted-checkbox";
import { MoreClips } from "./more-clips";
import { ProcessingTimings } from "./processing-timings";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";

import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from "@/components/ui/accordion";

import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Checkbox } from "@/components/ui/checkbox";
import {
  FieldLabel,
  Field,
  FieldSet,
  FieldGroup,
  FieldLegend,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { PlayIcon } from "lucide-react";

import { useId, useRef, useEffect, useState, type FormEvent } from "react";
import Image from "next/image";
import Link from "next/link";
import { api, ApiError, message } from "../../lib/client-api";
import {
  CaptionCustomizationFields,
  captionCustomizationFromForm,
} from "./caption-customization-fields";
import {
  active,
  ready,
  deliverable,
  type Project,
  type ProjectItem,
  type Preset,
} from "../../lib/projects";
import { CaptionStylePicker } from "./caption-style-picker";
import { CaptionStyleLibrary } from "./caption-style-library";
import { BatchCaptionStyle } from "./batch-caption-style";
import { OutputQualityControls } from "./output-quality-controls";
import { defaultQuality, type QualityOptions } from "../../lib/projects";
import { type CaptionStyle } from "../../lib/projects";
import { CaptionEmphasisField } from "./caption-emphasis-field";
import { parseEmphasis } from "../../lib/projects";
import { Progress } from "@/components/ui/progress";
import { HookSuggestions } from "./hook-suggestions";
import { FramingEditor } from "./framing-editor";
import {
  type FramingMode,
  type SourceRegion,
  type SplitLayout,
} from "../../lib/projects";
import { TextOverlayFields, readOverlays } from "./text-overlay-fields";
import { WorkspaceLoading } from "./workspace-loading";

export function ProjectResults({ id }: { id: string }) {
  const router = useRouter();
  const fieldId = useId();
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState("");
  const [retryAnalysis, setRetryAnalysis] = useState(false);
  const [retryTitles, setRetryTitles] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [clipSearch, setClipSearch] = useState("");
  const [clipFilter, setClipFilter] = useState("all");
  const [clipSort, setClipSort] = useState("ai");
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [reviewIds, setReviewIds] = useState<string[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const value = await api<Project>(`/api/projects/${id}`, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setProject(value);
        setError("");
        if (active(value.status)) timer = setTimeout(load, 1500);
      } catch (error) {
        if (!controller.signal.aborted) {
          setError(message(error));
          if (!(error instanceof ApiError) || error.status >= 500)
            timer = setTimeout(load, 5000);
        }
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [id, refresh]);
  async function action(name: "cancel" | "resume", allowAiRetry = false) {
    setBusy(true);
    setError("");
    try {
      const value = await api<Project>(`/api/projects/${id}/${name}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          name === "resume"
            ? {
                retry_analysis: allowAiRetry && retryAnalysis,
                retry_titles: allowAiRetry && retryTitles,
              }
            : {},
        ),
      });
      setProject(value);
      setRetryTitles(false);
      setRetryAnalysis(false);
      setRefresh((v) => v + 1);
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  const running = project ? active(project.status) : false;
  const readyItems = project?.items.filter(deliverable) ?? [];
  const selectedReady = readyItems.filter((i) => selected.has(i.id));
  const orderedItems = [...(project?.items ?? [])].sort((a, b) => {
    const duration = (item: ProjectItem) =>
      item.export?.duration ?? item.end - item.start;
    if (clipSort === "shortest") return duration(a) - duration(b);
    if (clipSort === "longest") return duration(b) - duration(a);
    if (clipSort === "newest")
      return (b.export?.created_at ?? "").localeCompare(
        a.export?.created_at ?? "",
      );
    return 0;
  });
  const visibleItems = orderedItems.filter(
    (item) =>
      (!clipSearch ||
        `${item.title} ${item.source_name ?? ""}`
          .toLowerCase()
          .includes(clipSearch.toLowerCase())) &&
      (clipFilter === "all" ||
        (clipFilter === "posted" && item.posted) ||
        (clipFilter === "unposted" && !item.posted) ||
        (clipFilter === "ready" && deliverable(item)) ||
        (clipFilter === "failed" &&
          Boolean(item.error || item.export?.status === "failed")) ||
        (clipFilter === "review" && reviewNeeded(item))),
  );
  const visibleIds = new Set(visibleItems.map((item) => item.id));
  function openReview(id: string) {
    document
      .querySelectorAll<HTMLVideoElement>(".clip-gallery video")
      .forEach((video) => video.pause());
    setReviewIds(visibleItems.map((item) => item.id));
    setReviewId(id);
  }
  function showDetails() {
    setDetailsOpen("project");
    requestAnimationFrame(() =>
      document
        .getElementById("project-details")
        ?.scrollIntoView({ block: "start" }),
    );
  }
  return (
    <section className="project-workspace project-results-workspace">
      <header className="project-heading">
        <Button asChild variant="outline">
          <Link href="/projects">All projects</Link>
        </Button>
        <Button asChild variant="outline">
          <Link href="/studio">Clip editor</Link>
        </Button>
      </header>
      {error ? (
        <Alert role="alert" variant="destructive">
          <AlertDescription>
            {error}{" "}
            <Button onClick={() => setRefresh((v) => v + 1)} variant="outline">
              Refresh
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}
      {!project && !error ? (
        <WorkspaceLoading label="Loading project…" />
      ) : !project ? null : (
        <>
          <div className="project-heading">
            <div className="flex min-w-0 flex-col gap-2">
              <h1>{project.title}</h1>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary">
                  {running
                    ? "Processing"
                    : project.status === "ready"
                      ? "Ready"
                      : project.status === "partial" ||
                          project.status === "failed" ||
                          project.status === "interrupted"
                        ? "Needs attention"
                        : "Cancelled"}
                </Badge>
                <span className="text-sm text-muted-foreground">
                  {readyItems.length} of {project.items.length} clips ready
                </span>
                {project.transcription_warning ? (
                  <Button variant="link" size="sm" onClick={showDetails}>
                    Transcription note
                  </Button>
                ) : null}
              </div>
            </div>
            <Button
              variant="outline"
              onClick={() => {
                setDetailsOpen("project");
                requestAnimationFrame(() =>
                  document
                    .getElementById("project-details")
                    ?.scrollIntoView({ block: "start", behavior: "instant" }),
                );
              }}
            >
              Project details
            </Button>
          </div>
          {running ? (
            <div className="flex flex-col gap-2">
              <p className="text-sm">
                Overall progress: {Math.round(project.progress * 100)}%
              </p>
              <Progress
                value={project.progress * 100}
                aria-label="Clip creation progress"
              />
              <ProjectStepProgress
                progress={project.step_progress}
                started={project.step_started_at}
                updated={project.step_updated_at}
              />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p role="status">{project.stage}</p>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy || project.status === "cancelling"}
                  onClick={() => void action("cancel")}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : null}
          {project.error ? (
            <Alert role="alert" variant="destructive">
              <AlertDescription>
                {project.error}
                <Button
                  type="button"
                  variant="link"
                  onClick={() => {
                    setDetailsOpen("project");
                    requestAnimationFrame(() =>
                      document
                        .getElementById("project-details")
                        ?.scrollIntoView({ block: "start" }),
                    );
                  }}
                >
                  Recovery options
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}
          <FieldGroup className="grid gap-4 sm:grid-cols-3">
            <Field>
              <FieldLabel htmlFor={`${fieldId}-clip-search`}>
                Search clips
              </FieldLabel>
              <Input
                id={`${fieldId}-clip-search`}
                type="search"
                value={clipSearch}
                onChange={(event) => setClipSearch(event.target.value)}
                placeholder="Title or source"
              />
            </Field>
            <Field>
              <FieldLabel htmlFor={`${fieldId}-clip-filter`}>Show</FieldLabel>
              <NativeSelect
                id={`${fieldId}-clip-filter`}
                value={clipFilter}
                onChange={(event) => setClipFilter(event.target.value)}
              >
                <NativeSelectOption value="all">All clips</NativeSelectOption>
                <NativeSelectOption value="unposted">
                  Unposted
                </NativeSelectOption>
                <NativeSelectOption value="posted">Posted</NativeSelectOption>
                <NativeSelectOption value="ready">Ready</NativeSelectOption>
                <NativeSelectOption value="failed">Failed</NativeSelectOption>
                <NativeSelectOption value="review">
                  Needs review
                </NativeSelectOption>
              </NativeSelect>
            </Field>
            <Field>
              <FieldLabel htmlFor={`${fieldId}-clip-sort`}>
                Sort clips
              </FieldLabel>
              <NativeSelect
                id={`${fieldId}-clip-sort`}
                value={clipSort}
                onChange={(event) => setClipSort(event.target.value)}
              >
                <NativeSelectOption value="ai">
                  Original AI / source order
                </NativeSelectOption>
                <NativeSelectOption value="newest">
                  Newest export
                </NativeSelectOption>
                <NativeSelectOption value="shortest">
                  Shortest first
                </NativeSelectOption>
                <NativeSelectOption value="longest">
                  Longest first
                </NativeSelectOption>
              </NativeSelect>
            </Field>
          </FieldGroup>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p role="status" className="text-sm text-muted-foreground">
              {visibleItems.length} of {project.items.length} clips shown
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="default"
                disabled={!visibleItems.length}
                onClick={() => openReview(visibleItems[0].id)}
              >
                Review clips
              </Button>
              {readyItems.length ? (
                <Button asChild variant="outline">
                  <a href={`/api/projects/${id}/bundle`}>
                    Download all ready ({readyItems.length})
                  </a>
                </Button>
              ) : null}
              <Button
                disabled={!project.items.length}
                onClick={() =>
                  setSelected(
                    (previous) =>
                      new Set([...previous, ...visibleItems.map((i) => i.id)]),
                  )
                }
                variant="outline"
              >
                Select visible clips
              </Button>
            </div>
          </div>
          <section
            aria-label="Selected clip actions"
            className="selection-toolbar"
            style={!selected.size ? { display: "none" } : undefined}
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p>{selected.size} selected across all filters</p>
              <div className="flex flex-wrap gap-2">
                {selectedReady.length ? (
                  <Button asChild variant="outline">
                    <a
                      href={`/api/projects/${id}/bundle?ids=${[...selected].join(",")}`}
                    >
                      Download selected
                    </a>
                  </Button>
                ) : null}
                <Button
                  disabled={!selected.size}
                  onClick={() => setSelected(new Set())}
                  variant="outline"
                >
                  Clear selection
                </Button>
              </div>
            </div>
            <ClipBulkActions
              project={project}
              selected={selected}
              disabled={running || busy}
              onBusy={setBusy}
              onSaved={(value) => {
                setProject(value);
                setRefresh((v) => v + 1);
              }}
            />
            <AdvancedOptions label="Caption style for selected clips">
              <BatchCaptionStyle
                selectedOnly
                key={project.id}
                project={project}
                selected={selected}
                disabled={running || busy}
                onBusy={setBusy}
                onSaved={(value) => {
                  setProject(value);
                  setRefresh((v) => v + 1);
                }}
              />
            </AdvancedOptions>
          </section>
          {!visibleItems.length && project.items.length ? (
            <Empty>
              <EmptyHeader>
                <EmptyTitle>No matching clips</EmptyTitle>
                <EmptyDescription>
                  Change the search or filter to see more clips. Your selections
                  are kept.
                </EmptyDescription>
              </EmptyHeader>
              <EmptyContent>
                <Button
                  variant="outline"
                  onClick={() => {
                    setClipSearch("");
                    setClipFilter("all");
                  }}
                >
                  Clear filters
                </Button>
              </EmptyContent>
            </Empty>
          ) : null}
          {!running &&
          project.items.length === 0 &&
          project.selection_complete ? (
            <p>
              No clips remain in this project. Create a new project or use
              manual selection to make more clips.
            </p>
          ) : null}
          {reviewId ? (
            <ClipReview
              project={project}
              items={reviewIds.flatMap((id) => {
                const item = project.items.find((item) => item.id === id);
                return item ? [item] : [];
              })}
              currentId={reviewId}
              onCurrent={setReviewId}
              onClose={() => setReviewId(null)}
              locked={running || busy}
              onSaved={(value) => {
                setProject(value);
                setRefresh((v) => v + 1);
              }}
            />
          ) : null}
          <h2 className="sr-only">Clips</h2>
          <div className="clip-gallery">
            {orderedItems.map((item) => (
              <ClipCard
                key={item.id}
                hidden={!visibleIds.has(item.id)}
                onReview={() => openReview(item.id)}
                project={project}
                item={item}
                locked={running || busy}
                onPosted={(posted) => {
                  setProject((previous) =>
                    previous
                      ? {
                          ...previous,
                          items: previous.items.map((value) =>
                            value.id === item.id ? { ...value, posted } : value,
                          ),
                        }
                      : previous,
                  );
                  setRefresh((value) => value + 1);
                }}
                onDeleted={() => {
                  setProject({
                    ...project,
                    items: project.items.filter(
                      (value) => value.id !== item.id,
                    ),
                  });
                  setSelected((values) => {
                    const next = new Set(values);
                    next.delete(item.id);
                    return next;
                  });
                  setRefresh((value) => value + 1);
                }}
                selected={selected.has(item.id)}
                onSelect={(value) =>
                  setSelected((previous) => {
                    const next = new Set(previous);
                    if (value) next.add(item.id);
                    else next.delete(item.id);
                    return next;
                  })
                }
                onSaved={(value) => {
                  setProject(value);
                  setRefresh((v) => v + 1);
                }}
              />
            ))}
          </div>
          {project.recording_id &&
          !project.recording_ids?.length &&
          project.selection_complete ? (
            <MoreClips
              key={project.id}
              projectId={project.id}
              disabled={running || busy}
            />
          ) : null}
          <Accordion
            id="project-details"
            type="single"
            collapsible
            value={detailsOpen}
            onValueChange={setDetailsOpen}
          >
            <AccordionItem value="project">
              <AccordionTrigger>Project details and recovery</AccordionTrigger>
              <AccordionContent forceMount hidden={detailsOpen !== "project"}>
                <div className="flex flex-col gap-5">
                  <DeleteAction
                    path={`/api/projects/${project.id}`}
                    label="Delete project"
                    name={project.title}
                    disabled={running || busy}
                    description="Removes this project and its current clip exports. Source videos, transcripts, older export versions, downloaded copies and published posts remain."
                    onDeleted={() => router.replace("/projects")}
                  />
                  <p>{new Date(project.created_at).toLocaleString()}</p>
                  <p role="status">
                    <strong>{project.status}</strong>: {project.stage}
                  </p>
                  {project.thumbnail_error ? (
                    <p>{project.thumbnail_error}</p>
                  ) : null}
                  {project.transcription_warning ? (
                    <Alert role="alert" variant="destructive">
                      <AlertDescription>
                        Caption review needed: {project.transcription_warning}{" "}
                        <Link href="/studio">
                          Review the transcript in the manual studio.
                        </Link>
                      </AlertDescription>
                    </Alert>
                  ) : null}
                  <ProcessingTimings project={project} />
                  <Accordion type="single" collapsible>
                    <AccordionItem value="details">
                      <AccordionTrigger>
                        Saved creation settings
                      </AccordionTrigger>
                      <AccordionContent>
                        {project.recording_ids?.length ? (
                          <p>
                            {project.recording_ids.length} supplied videos, each
                            processed separately.{" "}
                            {project.settings.batch_mode === "highlights"
                              ? `AI selects up to ${project.settings.clips_per_source ?? 3} distinct highlights per video, or keeps one complete video.`
                              : project.settings.batch_mode === "auto"
                                ? "AI reviews whether to keep the whole video or choose a tighter cut."
                                : "Full original lengths are preserved."}{" "}
                            Captions: {project.settings.caption_preset},{" "}
                            {project.settings.caption_position}. Framing:{" "}
                            {project.settings.framing}. Titles:{" "}
                            {project.generate_titles ||
                            project.settings.batch_mode === "auto" ||
                            project.settings.batch_mode === "highlights"
                              ? "AI with transcript-sharing consent"
                              : "Filenames"}
                            .
                          </p>
                        ) : (
                          <p>
                            Source seconds {project.settings.analysis_start}–
                            {project.settings.analysis_end}. Duration:{" "}
                            {project.settings.duration_mode === "auto"
                              ? "Auto, natural moments within the analysis range"
                              : `${project.settings.min_seconds}–${project.settings.max_seconds} seconds`}
                            . Up to {project.settings.max_clips} clips.
                            Captions: {project.settings.caption_preset},{" "}
                            {project.settings.caption_position}. Framing:{" "}
                            {project.settings.framing}.
                          </p>
                        )}
                        <div className="flex flex-col gap-2">
                          <h3>Extra AI instructions</h3>
                          <p className="whitespace-pre-wrap break-words">
                            {project.settings.ai_instructions ||
                              "None added. This project uses the default AI instructions."}
                          </p>
                        </div>
                        <p>
                          Automatic presentation:{" "}
                          {project.settings.presentation_mode ?? "off"}. Your
                          chosen caption style is retained.
                        </p>
                        <p>
                          These settings stay fixed for this project. Create
                          another project to analyze different settings; saved
                          clips and exports remain available.
                        </p>
                      </AccordionContent>
                    </AccordionItem>
                  </Accordion>
                  <div className="project-actions">
                    {running ? (
                      <Button
                        disabled={busy || project.status === "cancelling"}
                        onClick={() => void action("cancel")}
                        variant="outline"
                      >
                        Cancel processing
                      </Button>
                    ) : (
                      <Button
                        disabled={busy}
                        onClick={() => void action("resume")}
                        variant="outline"
                      >
                        Retry / update exports
                      </Button>
                    )}
                    {!running && (retryAnalysis || retryTitles) ? (
                      <Button
                        disabled={busy}
                        onClick={() => void action("resume", true)}
                      >
                        Run approved AI retry
                      </Button>
                    ) : null}
                    <Button
                      onClick={() => setRefresh((v) => v + 1)}
                      variant="outline"
                    >
                      Refresh
                    </Button>
                  </div>
                  {!running ? (
                    <p className="text-sm text-muted-foreground">
                      Export retries reuse saved analysis. Select the approval
                      below to retry unfinished AI work; another AI request may
                      incur API charges.
                    </p>
                  ) : null}
                  {!running &&
                  project.generate_titles &&
                  project.items.some(
                    (item) => item.title_attempted && !item.title_complete,
                  ) ? (
                    <Field orientation="horizontal">
                      <Checkbox
                        id={`${fieldId}-retry-titles`}
                        checked={retryTitles}
                        onCheckedChange={(value) =>
                          setRetryTitles(value === true)
                        }
                        disabled={busy}
                      />
                      <FieldLabel htmlFor={`${fieldId}-retry-titles`}>
                        Allow another billed AI attempt for unfinished titles
                        when resuming. I have checked API usage. Successful
                        titles and exports are reused.
                      </FieldLabel>
                    </Field>
                  ) : null}
                  {!running &&
                  ((project.analysis_attempted &&
                    !project.selection_complete) ||
                    ((project.settings.batch_mode === "auto" ||
                      project.settings.batch_mode === "highlights") &&
                      project.items.some(
                        (item) =>
                          item.analysis_attempted && !item.analysis_complete,
                      ))) ? (
                    <Field className="project-check" orientation="horizontal">
                      <Checkbox
                        id={`${fieldId}-1`}
                        checked={retryAnalysis}
                        onCheckedChange={(checked) =>
                          setRetryAnalysis(checked === true)
                        }
                      />
                      <FieldLabel htmlFor={`${fieldId}-1`}>
                        Allow another billed AI attempt if the previous attempt
                        did not finish. Check API usage first.
                      </FieldLabel>
                    </Field>
                  ) : null}
                  {running ? (
                    <p>
                      Processing continues when you leave this page while the
                      backend is running. Completed clips can be downloaded
                      below.
                    </p>
                  ) : null}
                </div>
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </>
      )}
    </section>
  );
}

function ClipCard({
  project: savedProject,
  hidden,
  onReview,
  item,
  locked,
  selected,
  onSelect,
  onSaved,
  onDeleted,
  onPosted,
}: {
  project: Project;
  hidden: boolean;
  onReview(): void;
  item: ProjectItem;
  locked: boolean;
  selected: boolean;
  onSelect(value: boolean): void;
  onSaved(value: Project): void;
  onDeleted(): void;
  onPosted(posted: boolean): void;
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
  const [editing, setEditing] = useState<ProjectItem | null>(null);
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
  const [openingText, setOpeningText] = useState(item.opening_text ?? "");
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
  const [playing, setPlaying] = useState(false);
  const clipPlayer = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (hidden) clipPlayer.current?.pause();
  }, [hidden]);
  const [failedThumbnail, setFailedThumbnail] = useState<string | null>(null);
  const thumbnailUrl = `/api/projects/${project.id}/thumbnail?item_id=${item.id}&revision=${item.revision}&export=${encodeURIComponent(item.thumbnail_export_id ?? item.export_id ?? "")}`;
  const available = ready(item);
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
            revision: editing!.revision,
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
      setEditing(null);
      setDirty(false);
      setPlaying(false);
      onSaved(value);
    } catch (error) {
      setError(message(error));
    } finally {
      setSaving(false);
    }
  }
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const moreTrigger = useRef<HTMLButtonElement>(null);
  const toggleEditor = () => {
    if (editing && dirty && !window.confirm("Discard unsaved clip edits?"))
      return;
    setDirty(false);
    setEditing((v) => (v ? null : item));
    setQuality(item.quality ?? defaultQuality);
    setCaptionStyle({
      preset: item.caption_preset,
      position: item.caption_position,
      customization: item.caption_customization,
    });
    setDraftStart(String(item.start));
    setDraftEnd(String(item.end));
    setOpeningText(item.opening_text ?? "");
    setFraming(item.framing ?? project.settings.framing);
    setCropRegion(
      item.framing
        ? (item.crop_region ?? null)
        : (project.settings.crop_region ?? null),
    );
    setSplitLayout(
      item.framing
        ? (item.split_layout ?? null)
        : (project.settings.split_layout ?? null),
    );
    setError("");
  };
  return (
    <Card
      className="project-card"
      style={hidden ? { display: "none" } : undefined}
    >
      {playing && available ? (
        <video
          ref={clipPlayer}
          className="project-preview"
          controls
          autoPlay
          src={`/api/clips/${item.export!.id}/media`}
          poster={
            item.thumbnail && failedThumbnail !== thumbnailUrl
              ? thumbnailUrl
              : undefined
          }
        />
      ) : (
        <div className="project-thumbnail relative">
          {item.thumbnail && failedThumbnail !== thumbnailUrl ? (
            <Image
              className="project-preview block"
              unoptimized
              src={thumbnailUrl}
              onError={() => setFailedThumbnail(thumbnailUrl)}
              width={360}
              height={640}
              alt={`Preview for ${item.title}`}
            />
          ) : (
            <div className="project-placeholder">
              {failedThumbnail === thumbnailUrl ? (
                <>
                  <p>Thumbnail could not load.</p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setFailedThumbnail(null)}
                  >
                    Retry preview
                  </Button>
                </>
              ) : (
                (item.thumbnail_error ?? "Preview pending")
              )}
            </div>
          )}
          {available ? (
            <Button
              type="button"
              variant="secondary"
              size="icon-lg"
              className="project-thumbnail-play absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2"
              aria-label={`Play ${item.title}`}
              title="Play clip"
              onClick={() => setPlaying(true)}
            >
              <PlayIcon aria-hidden="true" />
            </Button>
          ) : null}
        </div>
      )}

      <CardHeader>
        <CardTitle
          className="line-clamp-2"
          title={item.title}
          role="heading"
          aria-level={3}
        >
          {item.title}
        </CardTitle>
        <CardDescription>
          {(item.export?.duration ?? item.end - item.start).toFixed(1)}s
        </CardDescription>
      </CardHeader>
      <CardContent className="project-card-body">
        <PostCopyEditor
          projectId={project.id}
          item={item}
          disabled={locked || saving || dirty}
          onSaved={onSaved}
        />
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant={
              item.error || item.export?.status === "failed"
                ? "destructive"
                : "secondary"
            }
          >
            {item.error || item.export?.status === "failed"
              ? "Failed"
              : available
                ? "Ready"
                : item.export?.status === "rendering"
                  ? "Rendering"
                  : item.export?.status === "queued"
                    ? "Queued"
                    : "Waiting"}
          </Badge>
          <Badge variant="outline">{item.review}</Badge>
          {reviewNeeded(item) &&
          (item.transcription_warning ||
            item.title_error ||
            item.presentation_warnings?.length) ? (
            <Button
              type="button"
              variant="link"
              size="sm"
              onClick={() => setInfoOpen(true)}
            >
              Needs review
            </Button>
          ) : null}
        </div>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <Field className="project-check" orientation="horizontal">
            <Checkbox
              id={`${fieldId}-2`}
              checked={selected}
              onCheckedChange={(checked) => onSelect(checked === true)}
            />
            <FieldLabel htmlFor={`${fieldId}-2`}>Select clip</FieldLabel>
          </Field>
          <PostedCheckbox
            path={`/api/projects/${project.id}/clips/${item.id}/posted`}
            checked={item.posted}
            disabled={locked || saving}
            title={item.title}
            onSaved={onPosted}
          />
        </div>
        {item.error || item.export?.error ? (
          <Alert variant="destructive">
            <AlertDescription>
              {item.error ?? item.export?.error}
            </AlertDescription>
          </Alert>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button type="button" disabled={dirty || saving} onClick={onReview}>
            Review
          </Button>
          {available ? (
            <Button asChild variant="outline">
              <a href={`/api/clips/${item.export!.id}/media?download=true`}>
                Download
              </a>
            </Button>
          ) : (
            <Button variant="outline" disabled>
              Download
            </Button>
          )}
          <PublishChooser
            exportId={item.export?.id ?? ""}
            title={item.title}
            disabled={!available || dirty}
          />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                ref={moreTrigger}
                variant="outline"
                disabled={saving}
                aria-label={`More options for ${item.title}`}
              >
                More
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuGroup>
                <DropdownMenuItem onSelect={() => setInfoOpen(true)}>
                  Clip details
                </DropdownMenuItem>
                <DropdownMenuItem
                  disabled={locked || saving}
                  onSelect={() => toggleEditor()}
                >
                  Edit details
                </DropdownMenuItem>
                <DropdownMenuItem
                  disabled={locked || dirty}
                  onSelect={() => setVersionsOpen(true)}
                >
                  Versions / undo
                </DropdownMenuItem>
                {playing ? (
                  <DropdownMenuItem onSelect={() => setPlaying(false)}>
                    Hide preview
                  </DropdownMenuItem>
                ) : null}
                {item.thumbnail ? (
                  <DropdownMenuItem asChild>
                    <a href={`${thumbnailUrl}&download=true`}>
                      Download thumbnail
                    </a>
                  </DropdownMenuItem>
                ) : null}
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                <DropdownMenuItem
                  variant="destructive"
                  disabled={locked || saving || dirty}
                  onSelect={() => setDeleteOpen(true)}
                >
                  Delete clip
                </DropdownMenuItem>
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        <ClipVersions
          project={savedProject}
          item={item}
          locked={locked || dirty}
          onSaved={onSaved}
          hideTrigger
          open={versionsOpen}
          onOpenChange={setVersionsOpen}
          returnFocus={moreTrigger}
        />
        <DeleteAction
          hideTrigger
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          returnFocus={moreTrigger}
          path={`/api/projects/${project.id}/clips/${item.id}?revision=${item.revision}`}
          label="Delete clip"
          name={item.title}
          disabled={locked || saving}
          description="Removes this clip entry and its current local exported video. Source videos, transcripts, older export versions, downloaded copies and published posts remain."
          onDeleted={onDeleted}
        />
        <Dialog open={infoOpen} onOpenChange={setInfoOpen}>
          <DialogContent
            className="max-h-[85dvh] overflow-y-auto sm:max-w-xl"
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              moreTrigger.current?.focus();
            }}
          >
            <DialogHeader>
              <DialogTitle>{item.title}</DialogTitle>
              <DialogDescription>
                Source, analysis and review notes.
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-3">
              {item.presentation_summary && (
                <Accordion type="single" collapsible>
                  <AccordionItem value="presentation">
                    <AccordionTrigger>Presentation choices</AccordionTrigger>
                    <AccordionContent>
                      <p>{item.presentation_summary}</p>
                      {item.presentation_warnings?.map((warning) => (
                        <p key={warning}>{warning}</p>
                      ))}
                    </AccordionContent>
                  </AccordionItem>
                </Accordion>
              )}
              <p>
                {(item.export?.duration ?? item.end - item.start).toFixed(1)}{" "}
                seconds · source {item.start.toFixed(1)}–{item.end.toFixed(1)}
              </p>
              <p>
                {item.export?.status ?? (item.error ? "failed" : "waiting")}
              </p>
              {item.source_name && (
                <p className="text-sm text-muted-foreground">
                  Source: {item.source_name}
                </p>
              )}
              {item.analysis_reason && (
                <Alert role="status">
                  <AlertDescription>
                    <strong>
                      {item.analysis_choice === "keep_full"
                        ? "AI source recommendation: keep whole"
                        : item.analysis_choice === "trim"
                          ? "AI source recommendation: shorter cut"
                          : "Manual interval"}
                    </strong>
                    : {item.analysis_reason}{" "}
                    {item.analysis_start != null &&
                      item.analysis_end != null &&
                      `Original suggestion: source seconds ${item.analysis_start.toFixed(1)}–${item.analysis_end.toFixed(1)}. `}
                    Review the source and result before publishing.
                  </AlertDescription>
                </Alert>
              )}
              {Boolean(
                item.analysis_input_tokens || item.analysis_output_tokens,
              ) && (
                <p className="text-sm text-muted-foreground">
                  AI review usage: {item.analysis_input_tokens} input tokens,{" "}
                  {item.analysis_output_tokens} output tokens.
                </p>
              )}
              {item.transcription_warning && (
                <Alert role="status">
                  <AlertDescription>
                    {item.transcription_warning} Review the transcript in the
                    clip editor.
                  </AlertDescription>
                </Alert>
              )}
              {item.title_error && (
                <Alert role="status">
                  <AlertDescription>
                    Title needs review: {item.title_error} The current title is
                    retained. Edit it manually or explicitly allow another AI
                    title attempt before resuming.
                  </AlertDescription>
                </Alert>
              )}
              {item.title_complete &&
                Boolean(
                  item.title_input_tokens || item.title_output_tokens,
                ) && (
                  <p className="text-sm text-muted-foreground">
                    AI title usage: {item.title_input_tokens} input tokens,{" "}
                    {item.title_output_tokens} output tokens. Review the title
                    against the original clip before publishing.
                  </p>
                )}
              {item.error || item.export?.error ? (
                <Alert role="alert" variant="destructive">
                  <AlertDescription>
                    {item.error ?? item.export?.error}
                  </AlertDescription>
                </Alert>
              ) : null}
            </div>
          </DialogContent>
        </Dialog>
        <Dialog
          open={Boolean(editing)}
          onOpenChange={(value) => {
            if (!value && !saving) toggleEditor();
          }}
        >
          <DialogContent
            className="max-h-[90dvh] overflow-y-auto sm:max-w-4xl"
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              moreTrigger.current?.focus();
            }}
          >
            <DialogHeader>
              <DialogTitle>Edit clip details</DialogTitle>
              <DialogDescription>
                {item.title}. Save to update this clip; earlier exports remain
                available.
              </DialogDescription>
            </DialogHeader>
            {editing ? (
              <form
                key={editing.revision}
                onSubmit={save}
                onChangeCapture={() => setDirty(true)}
              >
                <p role="status" className="text-sm text-muted-foreground">
                  {saving ? "Saving..." : dirty ? "Unsaved changes" : "Saved"}
                </p>
                <FieldSet disabled={locked || saving}>
                  <Field orientation="horizontal">
                    <Checkbox
                      id={`${fieldId}-presentation`}
                      name="auto_presentation"
                    />
                    <FieldLabel htmlFor={`${fieldId}-presentation`}>
                      Re-analyze automatic presentation on save (local, no AI
                      charge)
                    </FieldLabel>
                  </Field>
                  <Field>
                    <FieldLabel htmlFor={`${fieldId}-3-${editing.revision}`}>
                      Clip title
                    </FieldLabel>
                    <Input
                      id={`${fieldId}-3-${editing.revision}`}
                      type="text"
                      name="title"
                      defaultValue={editing.title}
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
                        Start (source seconds)
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
                        End (source seconds)
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
                  <HookSuggestions
                    key={item.id}
                    projectId={project.id}
                    itemId={item.id}
                    recordingId={project.recording_id}
                    onChoose={(hook) => {
                      setDraftStart(String(hook.start));
                      setOpeningText(hook.opening_text);
                    }}
                  />
                  <FramingEditor
                    mode={framing}
                    layout={splitLayout}
                    cropRegion={cropRegion}
                    followPlan={item.export?.follow_plan}
                    autoLayout={item.export?.auto_layout}
                    end={Number(draftEnd)}
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
                  <FieldSet>
                    <FieldLegend>Opening headline</FieldLegend>
                    <Field orientation="horizontal">
                      <Checkbox
                        id={fieldId + "-headline-enabled"}
                        name="headline_enabled"
                        defaultChecked={editing.headline_enabled}
                      />
                      <FieldLabel htmlFor={fieldId + "-headline-enabled"}>
                        Show headline for the first eight seconds
                      </FieldLabel>
                    </Field>
                    <Field>
                      <FieldLabel htmlFor={fieldId + "-headline-position"}>
                        Headline placement
                      </FieldLabel>
                      <NativeSelect
                        id={fieldId + "-headline-position"}
                        name="headline_position"
                        defaultValue={editing.headline_position ?? "auto"}
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
                    <p>
                      Bold black text on a white card. Saving changes creates a
                      new export and preserves the previous video.
                    </p>
                  </FieldSet>
                  <TextOverlayFields
                    initial={editing.overlays ?? []}
                    duration={Math.max(
                      0,
                      Number(draftEnd) - Number(draftStart),
                    )}
                  />
                  <OutputQualityControls
                    value={quality}
                    onChange={setQuality}
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
                    onBounds={(start, end) => {
                      setDraftStart(String(start));
                      setDraftEnd(String(end));
                    }}
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
                  {item.editorial_quality && (
                    <p>
                      AI editorial assessment (review required): hook{" "}
                      {item.editorial_quality.hook}/5; context{" "}
                      {item.editorial_quality.context}/5; payoff{" "}
                      {item.editorial_quality.payoff}/5; standalone{" "}
                      {item.editorial_quality.standalone}/5.
                    </p>
                  )}
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
                    onApply={setCaptionStyle}
                    disabled={locked || saving}
                  />
                  <CaptionCustomizationFields
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
                  <CaptionEmphasisField
                    name="emphasis_words"
                    defaultValue={(editing.emphasis_words ?? []).join(", ")}
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
                          position: event.target
                            .value as CaptionStyle["position"],
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
                  <p>
                    Timing, caption, and headline changes rerender this clip and
                    preserve other outputs. Changing the title also rerenders
                    when the headline is enabled. Trim stays within this source,
                    up to six hours.
                  </p>
                  <Button type="submit" variant="default">
                    {saving ? "Saving…" : "Save changes"}
                  </Button>
                </FieldSet>
                {error ? (
                  <Alert role="alert" variant="destructive">
                    <AlertDescription>{error}</AlertDescription>
                  </Alert>
                ) : null}
              </form>
            ) : null}
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  );
}
