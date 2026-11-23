"use client";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";

import { Input } from "@/components/ui/input";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  Empty,
  EmptyHeader,
  EmptyTitle,
  EmptyDescription,
  EmptyContent,
} from "@/components/ui/empty";
import { LoaderCircleIcon } from "lucide-react";

import { useEffect, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { api, message } from "../../lib/client-api";
import { active, type Project } from "../../lib/projects";
import { DeleteAction } from "../components/delete-action";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [fetching, setFetching] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [offset, setOffset] = useState(0);
  const [more, setMore] = useState(false);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState("newest");
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const values = await api<Project[]>(
          `/api/projects?${new URLSearchParams({ limit: "50", offset: String(offset), search, status, sort })}`,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        setProjects(values);
        setMore(values.length === 50);
        setError("");
        if (values.some((project) => active(project.status)))
          timer = setTimeout(load, 2000);
      } catch (error) {
        if (!controller.signal.aborted) {
          setError(message(error));
          timer = setTimeout(load, 5000);
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          setFetching(false);
        }
      }
    }
    timer = setTimeout(load, search ? 250 : 0);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [offset, refresh, search, status, sort]);
  return (
    <section className="project-workspace">
      <header className="project-heading">
        <h1>Your projects</h1>
        <div className="project-actions">
          <Button asChild variant="default">
            <Link href="/workspace">New project</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/projects/batch">Batch edit supplied clips</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/studio">Clip editor</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/connections">Connected accounts</Link>
          </Button>
        </div>
      </header>
      <FieldGroup className="grid gap-4 sm:grid-cols-3">
        <Field>
          <FieldLabel htmlFor="project-search">Search projects</FieldLabel>
          <Input
            id="project-search"
            type="search"
            value={search}
            maxLength={120}
            placeholder="Project name"
            onChange={(event) => {
              setSearch(event.target.value);
              setOffset(0);
              setFetching(true);
            }}
          />
        </Field>
        <Field>
          <FieldLabel htmlFor="project-status">Status</FieldLabel>
          <NativeSelect
            id="project-status"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setOffset(0);
              setFetching(true);
            }}
          >
            <NativeSelectOption value="all">All projects</NativeSelectOption>
            <NativeSelectOption value="processing">
              Processing
            </NativeSelectOption>
            <NativeSelectOption value="ready">Ready</NativeSelectOption>
            <NativeSelectOption value="partial">
              Partly ready
            </NativeSelectOption>
            <NativeSelectOption value="failed">Failed</NativeSelectOption>
            <NativeSelectOption value="interrupted">
              Interrupted
            </NativeSelectOption>
            <NativeSelectOption value="cancelled">Cancelled</NativeSelectOption>
          </NativeSelect>
        </Field>
        <Field>
          <FieldLabel htmlFor="project-sort">Sort</FieldLabel>
          <NativeSelect
            id="project-sort"
            value={sort}
            onChange={(event) => {
              setSort(event.target.value);
              setOffset(0);
              setFetching(true);
            }}
          >
            <NativeSelectOption value="newest">Newest first</NativeSelectOption>
            <NativeSelectOption value="oldest">Oldest first</NativeSelectOption>
            <NativeSelectOption value="title">Name</NativeSelectOption>
          </NativeSelect>
        </Field>
      </FieldGroup>
      {error ? (
        <Alert role="alert" variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      <Button
        disabled={fetching}
        onClick={() => {
          setFetching(true);
          setRefresh((value) => value + 1);
        }}
        variant="outline"
      >
        {fetching ? (
          <LoaderCircleIcon data-icon="inline-start" className="animate-spin" />
        ) : null}
        {fetching ? "Refreshing…" : "Refresh"}
      </Button>
      {loading ? (
        <div role="status" className="project-grid">
          <span className="sr-only">Loading projects…</span>
          {[0, 1, 2].map((key) => (
            <Card key={key} aria-hidden="true">
              <CardContent className="flex flex-col gap-4">
                <Skeleton className="aspect-video w-full" />
                <Skeleton className="h-6 w-2/3" />
                <Skeleton className="h-4 w-1/2" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : !projects.length && !error ? (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>
              {search || status !== "all"
                ? "No matching projects"
                : offset
                  ? "No older projects"
                  : "Create your first clips"}
            </EmptyTitle>
            <EmptyDescription>
              Start with an uploaded video, YouTube link or Twitch VOD link,
              then choose your clip settings.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            {search || status !== "all" ? (
              <Button
                variant="outline"
                onClick={() => {
                  setSearch("");
                  setStatus("all");
                  setOffset(0);
                  setFetching(true);
                }}
              >
                Clear filters
              </Button>
            ) : null}
            <Button asChild>
              <Link href="/workspace">New project</Link>
            </Button>
          </EmptyContent>
        </Empty>
      ) : null}
      <div className="project-grid" aria-busy={fetching}>
        {projects.map((project) => (
          <div key={project.id} className="flex min-w-0 flex-col gap-2">
            <Link
              className="block min-w-0 rounded-xl focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring"
              href={`/projects/${project.id}`}
            >
              <Card className="h-full">
                {project.thumbnail || project.source_thumbnail_url ? (
                  <Image
                    unoptimized
                    src={
                      project.thumbnail
                        ? `/api/projects/${project.id}/thumbnail`
                        : project.source_thumbnail_url!
                    }
                    width={480}
                    height={270}
                    alt={`Source for ${project.title}`}
                  />
                ) : (
                  <div className="project-placeholder">
                    Source preview pending
                  </div>
                )}

                <CardHeader>
                  <CardTitle role="heading" aria-level={2}>
                    {project.title}
                  </CardTitle>
                  <CardDescription>
                    <p>{new Date(project.created_at).toLocaleDateString()}</p>
                    <p>
                      {project.items.length} clips ·{" "}
                      <Badge
                        variant={
                          project.status === "failed"
                            ? "destructive"
                            : "secondary"
                        }
                      >
                        {project.status.replaceAll("_", " ")}
                      </Badge>
                    </p>
                    <p>{project.stage}</p>
                  </CardDescription>
                </CardHeader>
                {active(project.status) ? (
                  <CardContent>
                    <Progress
                      value={project.progress * 100}
                      aria-label={`Clip creation progress for ${project.title}`}
                    />
                    <p className="mt-2 text-sm text-muted-foreground">
                      {Math.round(project.progress * 100)}% of workflow
                      completed
                    </p>
                  </CardContent>
                ) : null}
              </Card>
            </Link>
            <DeleteAction
              path={`/api/projects/${project.id}`}
              label="Delete project"
              name={project.title}
              disabled={active(project.status)}
              description="Removes this project and its current clip exports. Source videos, transcripts, older export versions, downloaded copies and published posts remain."
              onDeleted={() => {
                setProjects((values) =>
                  values.filter((value) => value.id !== project.id),
                );
                setRefresh((value) => value + 1);
              }}
            />
          </div>
        ))}
      </div>
      <div className="project-actions">
        <Button
          disabled={offset === 0 || fetching}
          onClick={() => {
            setFetching(true);
            setLoading(true);
            setProjects([]);
            setOffset((value) => Math.max(0, value - 50));
          }}
          variant="outline"
        >
          Previous projects
        </Button>
        <Button
          disabled={!more || fetching}
          onClick={() => {
            setFetching(true);
            setLoading(true);
            setProjects([]);
            setOffset((value) => value + 50);
          }}
          variant="outline"
        >
          Older projects
        </Button>
      </div>
    </section>
  );
}
