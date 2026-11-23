import Image from "next/image";
import { FilmIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { active, type Project } from "@/lib/projects";

const labels: Record<string, string> = {
  queued: "Queued",
  downloading: "Downloading",
  transcribing: "Transcribing",
  analysing: "Finding moments",
  rendering: "Rendering",
  cancelling: "Stopping",
  ready: "Ready",
  partial: "Partly ready",
  failed: "Failed",
  interrupted: "Interrupted",
  cancelled: "Stopped",
};

export function projectDate(value: string) {
  return new Date(value).toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function ProjectMedia({
  project,
  duration,
  eager = false,
}: {
  project: Project;
  duration?: number | null;
  eager?: boolean;
}) {
  const src = project.thumbnail
    ? `/api/projects/${project.id}/thumbnail`
    : project.source_thumbnail_url;
  const seconds = duration ? Math.round(duration) : null;
  return (
    <div className="relative flex aspect-video w-full items-center justify-center overflow-hidden bg-muted text-muted-foreground">
      {src ? (
        <Image
          unoptimized
          src={src}
          fill
          alt=""
          loading={eager ? "eager" : "lazy"}
          className="object-cover"
        />
      ) : (
        <FilmIcon aria-hidden="true" className="size-5" />
      )}
      {seconds && (
        <span className="absolute bottom-1.5 right-1.5 rounded bg-overlay px-1.5 py-0.5 text-xs tabular-nums text-white">
          {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}
        </span>
      )}
    </div>
  );
}

export function ProjectStatus({ project }: { project: Project }) {
  return (
    <div className="flex flex-col items-start gap-1.5">
      <Badge
        variant={
          project.status === "ready"
            ? "success"
            : project.status === "failed"
              ? "destructive"
              : project.status === "partial" || project.status === "interrupted"
                ? "warning"
                : active(project.status)
                  ? "info"
                  : "secondary"
        }
      >
        {labels[project.status] ?? project.status.replaceAll("_", " ")}
      </Badge>
      {active(project.status) && (
        <Progress
          className="w-full min-w-16"
          value={project.progress * 100}
          aria-label={`Workflow progress for ${project.title}`}
        />
      )}
    </div>
  );
}
