"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { ArrowUpRightIcon, FilmIcon, FolderOpenIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Empty,
  EmptyHeader,
  EmptyTitle,
  EmptyDescription,
  EmptyMedia,
} from "@/components/ui/empty";
import { api, message } from "@/lib/client-api";
import { active, type Project } from "@/lib/projects";

const statusLabels: Record<string, string> = {
  queued: "Queued",
  downloading: "Downloading",
  transcribing: "Transcribing",
  analysing: "Finding moments",
  rendering: "Rendering",
  cancelling: "Stopping",
  ready: "Ready",
  partial: "Some clips need attention",
  failed: "Needs attention",
  cancelled: "Stopped",
};

export function HomeProjects({ disabled }: { disabled: boolean }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const values = await api<Project[]>("/api/projects?limit=3&offset=0", {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setProjects(values);
        setError("");
        if (values.some((project) => active(project.status)))
          timer = setTimeout(load, 3000);
      } catch (error) {
        if (!controller.signal.aborted) setError(message(error));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [refresh]);
  return (
    <section
      aria-labelledby="recent-projects-title"
      className="flex min-w-0 flex-col gap-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="recent-projects-title">Pick up where you left off</h2>
        <Button asChild variant="ghost" disabled={disabled}>
          <Link
            href="/projects"
            aria-disabled={disabled}
            onClick={(event) => {
              if (disabled) event.preventDefault();
            }}
          >
            View all projects <ArrowUpRightIcon data-icon="inline-end" />
          </Link>
        </Button>
      </div>
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>
            <span>Recent projects could not be loaded. {error}</span>
            <Button
              variant="outline"
              size="sm"
              disabled={loading}
              onClick={() => {
                setLoading(true);
                setRefresh((value) => value + 1);
              }}
            >
              Retry projects
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}
      {loading && !projects.length ? (
        <div role="status" className="home-project-grid">
          <span className="sr-only">Loading recent projects…</span>
          {[0, 1, 2].map((key) => (
            <Card key={key} aria-hidden="true">
              <CardContent className="flex flex-col gap-4">
                <Skeleton className="aspect-video w-full" />
                <Skeleton className="h-5 w-3/4" />
                <Skeleton className="h-4 w-1/2" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : null}
      {!loading && !error && !projects.length ? (
        <Empty className="border border-dashed">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <FolderOpenIcon />
            </EmptyMedia>
            <EmptyTitle>Your next project belongs here</EmptyTitle>
            <EmptyDescription>
              Add a video above to create your first clips. Your saved projects
              will be ready to return to here.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : null}
      {projects.length ? (
        <div className="home-project-grid">
          {projects.map((project) => (
            <Link
              key={project.id}
              href={`/projects/${project.id}`}
              className="home-project-link"
              aria-disabled={disabled}
              onClick={(event) => {
                if (disabled) event.preventDefault();
              }}
            >
              <Card className="home-project-card">
                <div className="home-project-cover">
                  {project.thumbnail || project.source_thumbnail_url ? (
                    <Image
                      unoptimized
                      src={
                        project.thumbnail
                          ? `/api/projects/${project.id}/thumbnail`
                          : project.source_thumbnail_url!
                      }
                      width={640}
                      height={360}
                      alt=""
                      className="aspect-video w-full object-cover"
                    />
                  ) : (
                    <div className="home-project-fallback">
                      <FilmIcon aria-hidden="true" className="size-10" />
                      <span>Source preview pending</span>
                    </div>
                  )}
                  <div className="absolute bottom-3 left-3">
                    <Badge
                      variant={
                        project.status === "partial"
                          ? "warning"
                          : project.error || project.status === "failed"
                            ? "destructive"
                            : project.status === "ready"
                              ? "success"
                              : "info"
                      }
                    >
                      {statusLabels[project.status] ??
                        project.status.replaceAll("_", " ")}
                    </Badge>
                  </div>
                </div>
                <CardHeader>
                  <CardTitle
                    role="heading"
                    aria-level={3}
                    className="home-project-title"
                  >
                    {project.title}
                  </CardTitle>
                  <CardDescription>
                    {new Date(project.created_at).toLocaleDateString(
                      undefined,
                      { month: "short", day: "numeric", year: "numeric" },
                    )}{" "}
                    · {project.items.length}{" "}
                    {project.items.length === 1 ? "clip" : "clips"}
                  </CardDescription>
                </CardHeader>
                {active(project.status) ? (
                  <CardContent className="flex flex-col gap-2">
                    <Progress
                      value={project.progress * 100}
                      aria-label={`Workflow progress for ${project.title}`}
                    />
                    <p className="text-xs">
                      {project.stage} · {Math.round(project.progress * 100)}%
                    </p>
                  </CardContent>
                ) : null}
              </Card>
            </Link>
          ))}
        </div>
      ) : null}
    </section>
  );
}
