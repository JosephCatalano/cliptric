"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  LayoutGridIcon,
  ListIcon,
  MoreHorizontalIcon,
  PlusIcon,
  SearchIcon,
  Trash2Icon,
} from "lucide-react";
import { api, message } from "@/lib/client-api";
import { reviewNeeded } from "@/lib/project-edit";
import { active, type Project } from "@/lib/projects";
import { DeleteAction } from "../components/delete-action";
import {
  ProjectMedia,
  ProjectStatus,
  projectDate,
} from "../components/project-media";

const limit = 24;
const statuses = [
  ["all", "All statuses"],
  ["processing", "Processing"],
  ["ready", "Ready"],
  ["partial", "Partly ready"],
  ["failed", "Failed"],
  ["interrupted", "Interrupted"],
  ["cancelled", "Cancelled"],
];
const sorts = [
  ["newest", "Newest first"],
  ["oldest", "Oldest first"],
  ["title", "Name"],
];
const sourceDuration = (project: Project) =>
  project.recording_ids?.length
    ? null
    : project.items.find((item) => item.source_duration)?.source_duration;

export default function ProjectsPage() {
  return (
    <section className="project-workspace">
      <header className="workspace-heading">
        <div>
          <h1>Projects</h1>
          <p>Manage your source videos and generated clips.</p>
        </div>
        <Button asChild>
          <Link href="/workspace">
            <PlusIcon data-icon="inline-start" />
            New project
          </Link>
        </Button>
      </header>
      <Suspense fallback={<ProjectSkeleton />}>
        <ProjectLibrary />
      </Suspense>
    </section>
  );
}

function ProjectSkeleton() {
  return (
    <div role="status" className="project-grid">
      <span className="sr-only">Loading projects…</span>
      {[0, 1, 2].map((key) => (
        <Skeleton key={key} className="aspect-video" />
      ))}
    </div>
  );
}

function ProjectLibrary() {
  const params = useSearchParams();
  const search = params.get("q")?.slice(0, 120) ?? "";
  const status = statuses.some(([value]) => value === params.get("status"))
    ? params.get("status")!
    : "all";
  const sort = sorts.some(([value]) => value === params.get("sort"))
    ? params.get("sort")!
    : "newest";
  const view = params.get("view") === "list" ? "list" : "grid";
  const page = Math.min(
    400,
    Math.max(1, Number.parseInt(params.get("page") ?? "1", 10) || 1),
  );
  const key = JSON.stringify([search, status, sort, page]);
  const [result, setResult] = useState<{ key: string; projects: Project[] }>();
  const [error, setError] = useState<{ key: string; text: string }>();
  const [refresh, setRefresh] = useState(0);
  const projects = result?.key === key ? result.projects : [];
  const failure = error?.key === key ? error.text : "";
  const loading = result?.key !== key && !failure;

  function filters(values: Record<string, string>) {
    const url = new URL(window.location.href);
    for (const [name, value] of Object.entries(values)) {
      if (value) url.searchParams.set(name, value);
      else url.searchParams.delete(name);
    }
    window.history.replaceState(null, "", url);
  }

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const values = await api<Project[]>(
          `/api/projects?${new URLSearchParams({ limit: String(limit), offset: String((page - 1) * limit), search, status, sort })}`,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        setResult({ key, projects: values });
        setError(undefined);
        if (values.some((project) => active(project.status)))
          timer = setTimeout(load, 2500);
      } catch (cause) {
        if (!controller.signal.aborted) setError({ key, text: message(cause) });
      }
    }
    timer = setTimeout(load, search ? 250 : 0);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [key, page, refresh, search, status, sort]);

  function remove(id: string) {
    setResult(
      (previous) =>
        previous && {
          ...previous,
          projects: previous.projects.filter((project) => project.id !== id),
        },
    );
    setRefresh((previous) => previous + 1);
  }

  return (
    <>
      <div
        className="library-toolbar"
        role="group"
        aria-label="Project filters"
      >
        <div className="relative min-w-0 flex-1 basis-52">
          <SearchIcon
            aria-hidden="true"
            className="pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            className="ps-9"
            aria-label="Search projects"
            type="search"
            value={search}
            placeholder="Search projects"
            maxLength={120}
            onChange={(event) => filters({ q: event.target.value, page: "" })}
          />
        </div>
        <NativeSelect
          aria-label="Status"
          value={status}
          onChange={(event) =>
            filters({ status: event.target.value, page: "" })
          }
        >
          {statuses.map(([value, label]) => (
            <NativeSelectOption key={value} value={value}>
              {label}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label="Sort"
          value={sort}
          onChange={(event) => filters({ sort: event.target.value, page: "" })}
        >
          {sorts.map(([value, label]) => (
            <NativeSelectOption key={value} value={value}>
              {label}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        <div
          role="group"
          aria-label="Project view"
          className="flex gap-1 rounded-lg border p-0.5"
        >
          {[
            { value: "grid", label: "Grid view", icon: LayoutGridIcon },
            { value: "list", label: "List view", icon: ListIcon },
          ].map(({ value, label, icon: Icon }) => (
            <Tooltip key={value}>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  size="icon-sm"
                  variant={view === value ? "secondary" : "ghost"}
                  aria-label={label}
                  aria-pressed={view === value}
                  onClick={() => filters({ view: value })}
                >
                  <Icon />
                </Button>
              </TooltipTrigger>
              <TooltipContent>{label}</TooltipContent>
            </Tooltip>
          ))}
        </div>
      </div>
      {failure && (
        <Alert variant="destructive" role="alert">
          <AlertDescription className="flex flex-wrap items-center gap-3">
            {failure}
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setError(undefined);
                setRefresh((n) => n + 1);
              }}
            >
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {loading ? (
        <ProjectSkeleton />
      ) : !failure && !projects.length ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyTitle>
              {search || status !== "all"
                ? "No matching projects"
                : page > 1
                  ? "No projects on this page"
                  : "No projects yet"}
            </EmptyTitle>
            <EmptyDescription>
              {search || status !== "all"
                ? "Try another search or clear your filters."
                : "Import a video to generate your first clips."}
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            {search || status !== "all" ? (
              <Button
                variant="outline"
                onClick={() => filters({ q: "", status: "", page: "" })}
              >
                Clear filters
              </Button>
            ) : (
              <Button asChild>
                <Link href="/workspace">Create project</Link>
              </Button>
            )}
          </EmptyContent>
        </Empty>
      ) : null}
      {projects.length > 0 &&
        (view === "grid" ? (
          <div className="project-grid">
            {projects.map((project, index) => (
              <article key={project.id} className="project-library-card">
                <Link
                  href={`/projects/${project.id}`}
                  aria-label={`Open ${project.title}`}
                  className="block"
                >
                  <ProjectMedia project={project} eager={index === 0} />
                </Link>
                <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-2 p-4">
                  <div className="min-w-0 space-y-2">
                    <h2 className="truncate text-base">
                      <Link href={`/projects/${project.id}`}>
                        {project.title}
                      </Link>
                    </h2>
                    <p className="text-sm text-muted-foreground">
                      {project.items.length}{" "}
                      {project.items.length === 1 ? "clip" : "clips"} /{" "}
                      {projectDate(project.created_at)}
                    </p>
                    <ProjectStatus project={project} />
                  </div>
                  <ProjectMenu
                    project={project}
                    onDeleted={() => remove(project.id)}
                  />
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div
            className="overflow-x-auto rounded-xl border bg-card"
            tabIndex={0}
            aria-label="Projects list"
          >
            <table className="w-full min-w-[36rem] text-left text-sm">
              <thead className="border-b bg-muted/35 text-muted-foreground">
                <tr>
                  {[
                    "Project",
                    "Clips",
                    "Needs review",
                    "Status",
                    "Created",
                    "Actions",
                  ].map((label) => (
                    <th
                      key={label}
                      scope="col"
                      className="px-4 py-3 font-medium"
                    >
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {projects.map((project) => (
                  <tr key={project.id} className="border-b last:border-0">
                    <th scope="row" className="px-4 py-3 font-medium">
                      <Link
                        href={`/projects/${project.id}`}
                        className="flex items-center gap-3"
                      >
                        <div className="w-16 shrink-0 overflow-hidden rounded">
                          <ProjectMedia
                            project={project}
                            duration={sourceDuration(project)}
                          />
                        </div>
                        <span className="line-clamp-2">{project.title}</span>
                      </Link>
                    </th>
                    <td className="px-4 py-3">{project.items.length}</td>
                    <td className="px-4 py-3">
                      {project.items.filter(reviewNeeded).length}
                    </td>
                    <td className="px-4 py-3">
                      <ProjectStatus project={project} />
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-muted-foreground">
                      {projectDate(project.created_at)}
                    </td>
                    <td className="px-4 py-3">
                      <ProjectMenu
                        project={project}
                        onDeleted={() => remove(project.id)}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      {(page > 1 || projects.length === limit) && (
        <nav
          className="flex items-center justify-end gap-3"
          aria-label="Project pages"
        >
          <Button
            variant="outline"
            size="sm"
            disabled={page === 1 || loading}
            onClick={() => filters({ page: String(page - 1) })}
          >
            Previous
          </Button>
          <span className="text-sm text-muted-foreground">Page {page}</span>
          <Button
            variant="outline"
            size="sm"
            disabled={projects.length < limit || page === 400 || loading}
            onClick={() => filters({ page: String(page + 1) })}
          >
            Next
          </Button>
        </nav>
      )}
    </>
  );
}

function ProjectMenu({
  project,
  onDeleted,
}: {
  project: Project;
  onDeleted(): void;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            ref={trigger}
            variant="ghost"
            size="icon"
            aria-label={`More options for ${project.title}`}
          >
            <MoreHorizontalIcon />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem asChild>
            <Link href={`/projects/${project.id}`}>Open project</Link>
          </DropdownMenuItem>
          <DropdownMenuItem
            variant="destructive"
            disabled={active(project.status)}
            onSelect={() => setOpen(true)}
          >
            <Trash2Icon /> Delete project
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <DeleteAction
        hideTrigger
        open={open}
        onOpenChange={setOpen}
        returnFocus={trigger}
        path={`/api/projects/${project.id}`}
        label="Delete project"
        name={project.title}
        disabled={active(project.status)}
        onDeleted={onDeleted}
        description="Removes this project and its current clip exports. Source videos, transcripts, older exports, downloaded copies and published posts remain."
      />
    </>
  );
}
