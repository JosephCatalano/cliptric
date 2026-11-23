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
import { BatchPublish } from "./batch-publish";
import { confirmWorkspaceNavigation } from "./use-unsaved-edits";
import dynamic from "next/dynamic";
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
  FieldGroup,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { EllipsisIcon, PlayIcon } from "lucide-react";

import { useCallback, useId, useRef, useEffect, useState } from "react";
import { flushSync } from "react-dom";
import Image from "next/image";
import Link from "next/link";
import { api, ApiError, message } from "../../lib/client-api";
import {
  active,
  ready,
  deliverable,
  type Project,
  type ProjectItem,
} from "../../lib/projects";
import { BatchCaptionStyle } from "./batch-caption-style";
import { Progress } from "@/components/ui/progress";
import { WorkspaceLoading } from "./workspace-loading";

const ProjectClipEditor = dynamic(() => import("./project-clip-editor"), {
  loading: () => <WorkspaceLoading label="Loading clip editor…" />,
});

export function ProjectResults({ id, initialClip }: { id: string; initialClip?: string }) {
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
  const [editingId, setEditingId] = useState(initialClip || "");
  const [previewId, setPreviewId] = useState<string | null>(null);
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
          if (
            !(error instanceof ApiError) ||
            error.status === 0 ||
            error.status === 408 ||
            error.status >= 500
          )
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
  const allowEditorExit = useRef(false);
  useEffect(() => {
    const projectPath = `/projects/${id}`;
    const editorUrl = new URL(location.href);
    function handleBack(event: PopStateEvent) {
      const current = new URL(location.href);
      const next = current.pathname === projectPath ? current.searchParams.get("clip") ?? "" : "";
      if (next === editingId) return;
      const authorized = allowEditorExit.current;
      allowEditorExit.current = false;
      if (editingId && !authorized && !confirmWorkspaceNavigation()) {
        event.stopImmediatePropagation();
        window.history.pushState(
          current.pathname === projectPath ? { cliptricEditorProject: id } : null,
          "",
          editorUrl,
        );
        return;
      }
      setEditingId(next);
      if (editingId && !next && current.pathname === projectPath) {
        requestAnimationFrame(() => document.getElementById(`clip-options-${editingId}`)?.focus({ preventScroll: true }));
      }
    }
    window.addEventListener("popstate", handleBack, true);
    return () => window.removeEventListener("popstate", handleBack, true);
  }, [editingId, id]);

  function changeEditor(clipId: string, check = true) {
    if (check && !confirmWorkspaceNavigation()) return;
    const url = new URL(location.href);
    if (clipId) {
      url.searchParams.set("clip", clipId);
      window.history.pushState({ cliptricEditorProject: id }, "", url);
      setPreviewId(null);
      setReviewId(null);
      window.scrollTo(0, 0);
    } else if (window.history.state?.cliptricEditorProject === id) {
      allowEditorExit.current = true;
      window.history.back();
      return;
    } else {
      url.searchParams.delete("clip");
      window.history.replaceState(null, "", url);
      requestAnimationFrame(() => document.getElementById(`clip-options-${editingId}`)?.focus({ preventScroll: true }));
    }
    setEditingId(clipId);
  }

  const running = project ? active(project.status) : false;
  const readyItems = project?.items.filter(deliverable) ?? [];
  const selectedReady = readyItems.filter((i) => selected.has(i.id));
  const allReadySelected =
    readyItems.length > 0 && selectedReady.length === readyItems.length;
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
    setPreviewId(null);
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
  const editedClip = project?.items.find((item) => item.id === editingId);
  return (
    <>
    <section className="project-workspace project-results-workspace" hidden={Boolean(editingId && project)}>
      <nav aria-label="Breadcrumb">
        <Button asChild variant="ghost" size="sm"><Link href="/projects">← Projects</Link></Button>
      </nav>
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
                  {project.items.filter(ready).length} of {project.items.length} exports ready
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
          {project.media_analysis_warning && (
            <Alert role="status">
              <AlertDescription>{project.media_analysis_warning}</AlertDescription>
            </Alert>
          )}
          <FieldGroup className="grid gap-3 rounded-xl border bg-card p-3 sm:grid-cols-3">
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
                {visibleItems.length === project.items.length ? "Review all clips" : "Review shown clips"}
              </Button>
              {readyItems.length ? (
                <Button asChild variant="outline">
                  <a href={`/api/projects/${id}/bundle`}>
                    Download all ready ({readyItems.length})
                  </a>
                </Button>
              ) : null}
              <Field orientation="horizontal" className="w-auto px-2">
                <Checkbox
                  id="select-all-ready-clips"
                  disabled={!readyItems.length}
                  checked={
                    allReadySelected
                      ? true
                      : selectedReady.length
                        ? "indeterminate"
                        : false
                  }
                  onCheckedChange={(checked) =>
                    setSelected((previous) => {
                      const next = new Set(previous);
                      for (const item of readyItems) {
                        if (checked === true) next.add(item.id);
                        else next.delete(item.id);
                      }
                      return next;
                    })
                  }
                />
                <FieldLabel htmlFor="select-all-ready-clips">
                  Select all ready clips
                </FieldLabel>
              </Field>
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
                  <BatchPublish
                    items={selectedReady}
                    disabled={
                      running || busy || selectedReady.length !== selected.size
                    }
                  />
                ) : null}
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
              onEdit={(clipId) => changeEditor(clipId, false)}
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
                previewOpen={previewId === item.id && visibleIds.has(item.id)}
                onPreviewChange={(open) => setPreviewId(open ? item.id : null)}
                onReview={() => openReview(item.id)}
                onEdit={() => changeEditor(item.id)}
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
              includeFrames={project.settings.multimodal ?? false}
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
                        did not finish.
                        {project.settings.multimodal && " This can send new sampled frames to OpenAI."}
                        {" "}Check API usage first.
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
    {editingId && project && (editedClip ? (
      <ProjectClipEditor key={editedClip.id} project={project} item={editedClip} locked={running || busy}
        onBack={() => changeEditor("")}
        onSaved={(value) => {
          setProject(value);
          setRefresh((v) => v + 1);
          changeEditor("", false);
        }}
      />
    ) : (
      <section className="project-workspace">
        <Alert variant="destructive"><AlertDescription>That clip is unavailable in this project.</AlertDescription></Alert>
        <Button className="self-start" variant="outline" onClick={() => changeEditor("")}>Back to project</Button>
      </section>
    ))}
    </>
  );
}

function ClipCard({
  project: savedProject,
  hidden,
  previewOpen,
  onPreviewChange,
  onReview,
  onEdit,
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
  previewOpen: boolean;
  onPreviewChange(open: boolean): void;
  onReview(): void;
  onEdit(): void;
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
  const [playbackError, setPlaybackError] = useState("");
  const clipPlayer = useRef<HTMLVideoElement>(null);
  const attachClipPlayer = useCallback((player: HTMLVideoElement | null) => {
    if (!player) clipPlayer.current?.pause();
    clipPlayer.current = player;
  }, []);
  const [failedThumbnail, setFailedThumbnail] = useState<string | null>(null);
  const thumbnailUrl = `/api/projects/${project.id}/thumbnail?item_id=${item.id}&revision=${item.revision}&export=${encodeURIComponent(item.thumbnail_export_id ?? item.export_id ?? "")}`;
  const available = ready(item);
  function playClip() {
    // Start playback inside the click so browsers can associate it with the user gesture.
    if (!previewOpen) flushSync(() => onPreviewChange(true));
    const player = clipPlayer.current;
    if (!player) return;
    setPlaybackError("");
    if (player.error) player.load();
    void player.play().catch((error: unknown) => {
      if (clipPlayer.current !== player || (error instanceof DOMException && error.name === "AbortError")) return;
      setPlaybackError(
        error instanceof DOMException && error.name === "NotAllowedError"
          ? "Your browser blocked playback. Press Play in the video controls."
          : "This export could not play. Retry playback or download the MP4 to check it locally.",
      );
    });
  }
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [copyOpen, setCopyOpen] = useState(false);
  const moreTrigger = useRef<HTMLButtonElement>(null);
  return (
    <Card
      className="project-card"
      style={hidden ? { display: "none" } : undefined}
    >
      {previewOpen && available ? (
        <video
          key={item.export!.id}
          ref={attachClipPlayer}
          className="project-preview"
          aria-label={`Player for ${item.title}`}
          controls
          playsInline
          onPlaying={() => setPlaybackError("")}
          onError={() => setPlaybackError("The export could not load. Retry playback or download the MP4 to check it locally.")}
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
              onClick={playClip}
            >
              <PlayIcon aria-hidden="true" />
            </Button>
          ) : null}
        </div>
      )}
      {previewOpen && available && playbackError ? (
        <Alert variant="destructive">
          <AlertDescription>
            <p>{playbackError}</p>
            <Button type="button" size="sm" variant="outline" onClick={playClip}>
              Retry playback
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}

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
          <Badge variant={item.review === "accepted" ? "success" : "outline"}>{item.review === "accepted" ? "Approved" : item.review === "rejected" ? "Rejected" : "Needs review"}</Badge>
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
        <div className="flex flex-wrap items-center justify-between gap-2">
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
            disabled={locked}
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
          <Button type="button" onClick={onReview}>
            Review clip
          </Button>
          {available ? (
            <Button asChild variant="outline">
              <a href={`/api/clips/${item.export!.id}/media?download=true`}>
                Download
              </a>
            </Button>
          ) : null}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                ref={moreTrigger}
                id={`clip-options-${item.id}`}
                variant="ghost"
                size="icon"
                aria-label={`More options for ${item.title}`}
              >
                <EllipsisIcon />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuGroup>
                <DropdownMenuItem onSelect={() => setInfoOpen(true)}>
                  Clip details
                </DropdownMenuItem>
                <DropdownMenuItem
                  disabled={locked}
                  onSelect={onEdit}
                >
                  Edit clip
                </DropdownMenuItem>
                <DropdownMenuItem disabled={!available} onSelect={() => setPublishOpen(true)}>
                  Publish or schedule
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => setCopyOpen(true)}>Caption &amp; hashtags</DropdownMenuItem>
                <DropdownMenuItem
                  disabled={locked}
                  onSelect={() => setVersionsOpen(true)}
                >
                  Versions / undo
                </DropdownMenuItem>
                {previewOpen ? (
                  <DropdownMenuItem onSelect={() => onPreviewChange(false)}>
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
                  disabled={locked}
                  onSelect={() => setDeleteOpen(true)}
                >
                  Delete clip
                </DropdownMenuItem>
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        <PublishChooser exportId={item.export?.id ?? ""} title={item.title} disabled={!available}
          open={publishOpen} onOpenChange={setPublishOpen} returnFocus={moreTrigger} />
        <PostCopyEditor projectId={project.id} item={item} disabled={locked} onSaved={onSaved}
          open={copyOpen} onOpenChange={setCopyOpen} returnFocus={moreTrigger} />
        <ClipVersions
          project={savedProject}
          item={item}
          locked={locked}
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
          disabled={locked}
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
              {item.selection_reason && (
                <FieldGroup className="gap-2">
                  <p>{item.selection_reason}</p>
                  {item.selection_score != null && (
                    <p className="text-sm text-muted-foreground">
                      Editorial score {item.selection_score}/100
                    </p>
                  )}
                  {item.selection_visual ? (
                    <p className="text-sm text-muted-foreground">
                      Sampled frames at {item.selection_visual.frame_times.map((time) => time.toFixed(1)).join(" and ")} seconds.
                      {" "}{item.selection_visual.description}
                    </p>
                  ) : item.selection_evidence && (
                    <blockquote className="text-sm text-muted-foreground">{item.selection_evidence}</blockquote>
                  )}
                </FieldGroup>
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
      </CardContent>
    </Card>
  );
}
