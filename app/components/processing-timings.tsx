import type { Project } from "@/lib/projects";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";

const labels: Record<string, string> = {
  download: "Download",
  transcription: "Transcription",
  ai: "AI analysis",
  visual: "Visual analysis",
  render: "Rendering",
  thumbnails: "Thumbnails",
};
const elapsed = (seconds: number) =>
  seconds < 60
    ? `${seconds.toFixed(1)}s`
    : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;

export function ProcessingTimings({ project }: { project: Project }) {
  const stages = Object.entries(project.stage_seconds ?? {}).filter(
    ([key]) => key in labels,
  );
  const renders = project.items.flatMap((item) =>
    item.export?.performance ? [item.export.performance] : [],
  );
  const speech = [
    ...new Set(
      [
        project.transcription_backend,
        ...project.items.map((item) => item.transcription_backend),
      ].filter(Boolean),
    ),
  ];
  const warnings = [
    ...new Set(
      renders.flatMap((render) => (render.warning ? [render.warning] : [])),
    ),
  ];
  if (!stages.length && !renders.length) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Processing time</CardTitle>
        <CardDescription>
          Saved elapsed time for completed stages in the latest run, including
          time waiting for shared workers. Running stages appear when they
          finish.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap gap-2">
          {speech.map((backend) => (
            <Badge key={backend} variant="secondary">
              Speech: {backend === "cuda" ? "NVIDIA GPU" : "CPU"}
            </Badge>
          ))}
          {project.analysis_cache_hit ? (
            <Badge variant="secondary">Reused saved highlights</Badge>
          ) : null}
          {[...new Set(renders.map((render) => render.backend))].map(
            (backend) => (
              <Badge key={backend} variant="outline">
                Video: {backend === "nvenc" ? "NVIDIA GPU" : "CPU"}
              </Badge>
            ),
          )}
        </div>
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          {stages.map(([key, seconds]) => (
            <div key={key}>
              <dt className="text-sm text-muted-foreground">{labels[key]}</dt>
              <dd>{elapsed(seconds)}</dd>
            </div>
          ))}
        </dl>
        {warnings.map((warning) => (
          <Alert key={warning}>
            <AlertDescription>{warning}</AlertDescription>
          </Alert>
        ))}
      </CardContent>
    </Card>
  );
}
