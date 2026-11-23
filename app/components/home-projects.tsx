"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowUpRightIcon, FolderOpenIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Empty,
  EmptyHeader,
  EmptyTitle,
  EmptyDescription,
  EmptyMedia,
} from "@/components/ui/empty";
import { cn } from "@/lib/utils";
import { api, message } from "@/lib/client-api";
import { active, type Project } from "@/lib/projects";

import { ProjectMedia, ProjectStatus, projectDate } from "./project-media";

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
        const values = await api<Project[]>("/api/projects?limit=4&offset=0", {
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
        <h2 id="recent-projects-title">Recent projects</h2>
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
            <EmptyTitle>No projects yet</EmptyTitle>
            <EmptyDescription>
              Add a video above to create your first clips. Your saved projects
              will be ready to return to here.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : null}
      {projects.length ? (
        <div className="home-project-grid">
          {projects.map((project, index) => (
            <Link
              key={project.id}
              href={`/projects/${project.id}`}
              className="home-project-link"
              aria-disabled={disabled}
              onClick={(event) => {
                if (disabled) event.preventDefault();
              }}
            >
              <Card
                className={cn(
                  "home-project-card gap-0 py-0",
                  projects.length === 1 &&
                    "sm:grid sm:grid-cols-[minmax(11rem,16rem)_minmax(0,1fr)]",
                )}
              >
                <ProjectMedia project={project} eager={index === 0} />
                <div className="flex min-w-0 flex-col justify-center gap-3 py-3">
                  <CardHeader>
                    <CardTitle
                      role="heading"
                      aria-level={3}
                      className="home-project-title"
                    >
                      {project.title}
                    </CardTitle>
                    <CardDescription>
                      {project.items.length}{" "}
                      {project.items.length === 1 ? "clip" : "clips"} /{" "}
                      {projectDate(project.created_at)}
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <ProjectStatus project={project} />
                  </CardContent>
                </div>
              </Card>
            </Link>
          ))}
        </div>
      ) : null}
    </section>
  );
}
