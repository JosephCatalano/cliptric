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

const stageDetails: Record<string, { label: string; colour: string }> = {
  download: { label: "Download", colour: "var(--chart-1)" },
  transcription: { label: "Transcription", colour: "var(--chart-3)" },
  ai: { label: "AI analysis", colour: "var(--chart-2)" },
  visual: { label: "Visual analysis", colour: "var(--chart-4)" },
  render: { label: "Rendering", colour: "var(--chart-5)" },
  thumbnails: { label: "Thumbnails", colour: "var(--chart-6)" },
};
const elapsed = (seconds: number) =>
  seconds < 60
    ? `${seconds.toFixed(1)}s`
    : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;

export function ProcessingTimings({ project }: { project: Project }) {
  const stages = Object.entries(project.stage_seconds ?? {}).filter(([key]) =>
    Object.hasOwn(stageDetails, key),
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
              <span
                aria-hidden="true"
                className="size-2 rounded-full bg-(--chart-7)"
              />
              Speech: {backend === "cuda" ? "NVIDIA GPU" : "CPU"}
            </Badge>
          ))}
          {project.analysis_cache_hit ? (
            <Badge variant="secondary">Reused saved highlights</Badge>
          ) : null}
          {[...new Set(renders.map((render) => render.backend))].map(
            (backend) => (
              <Badge key={backend} variant="outline">
                <span
                  aria-hidden="true"
                  className="size-2 rounded-full bg-(--chart-8)"
                />
                Video: {backend === "nvenc" ? "NVIDIA GPU" : "CPU"}
              </Badge>
            ),
          )}
        </div>
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          {stages.map(([key, seconds]) => (
            <div key={key}>
              <dt className="flex items-center gap-2 text-sm text-muted-foreground">
                <span
                  aria-hidden="true"
                  className="size-2 shrink-0 rounded-full"
                  style={{ backgroundColor: stageDetails[key].colour }}
                />
                {stageDetails[key].label}
              </dt>
              <dd>{elapsed(seconds)}</dd>
            </div>
          ))}
        </dl>
        {warnings.map((warning) => (
          <Alert key={warning} variant="warning">
            <AlertDescription>{warning}</AlertDescription>
          </Alert>
        ))}
      </CardContent>
    </Card>
  );
}
