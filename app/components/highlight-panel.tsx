import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  FieldSet,
  FieldLegend,
  FieldLabel,
  Field,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { useId, useEffect, useRef, useState } from "react";
import { api, message } from "../../lib/client-api";
import { MAX_VIDEO_SECONDS } from "../../lib/projects";

type Review = "unreviewed" | "accepted" | "rejected";
export type HighlightCandidate = {
  id: string;
  job_id: string;
  start: number;
  end: number;
  score: number;
  quality?: {
    hook: number;
    context: number;
    payoff: number;
    standalone: number;
  } | null;
  title: string;
  reason: string;
  evidence: string;
  category: string;
  review: Review;
  revision: number;
};
type HighlightJob = {
  id: string;
  status:
    | "queued"
    | "analysing"
    | "cancelling"
    | "succeeded"
    | "failed"
    | "cancelled";
  total_windows: number;
  completed_windows: number;
  editorial_status?: "pending" | "reviewing" | "completed" | null;
  requests_made: number;
  input_tokens: number;
  output_tokens: number;
  rejected_candidates: number;
  error: string | null;
  stale: boolean;
  candidates: HighlightCandidate[];
};
type HighlightState = {
  latest: HighlightJob | null;
  result: HighlightJob | null;
  transcription: { id: string; status: string; revision: number } | null;
};
type Configuration = {
  configured: boolean;
  model: string;
  max_requests: number;
  max_output_tokens_per_request: number;
};
const processing = (job: HighlightJob | null) =>
  !!job && ["queued", "analysing", "cancelling"].includes(job.status);
const time = (seconds: number) => `${seconds.toFixed(2)}s`;

export function HighlightPanel({
  recordingId,
  disabled,
  onSelect,
}: {
  recordingId: string;
  disabled: boolean;
  onSelect: (candidate: HighlightCandidate) => void;
}) {
  const fieldId = useId();
  const [state, setState] = useState<HighlightState | null>(null);
  const [config, setConfig] = useState<Configuration | null>(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [consent, setConsent] = useState(false);
  const [minimum, setMinimum] = useState("15");
  const [maximum, setMaximum] = useState("60");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const version = useRef(0);
  const action = useRef<AbortController | null>(null);
  const latest = state?.latest ?? null;
  const result = state?.result ?? null;
  const active = processing(latest);
  const ready = state?.transcription?.status === "succeeded";
  const boundsValid =
    Number.isInteger(Number(minimum)) &&
    Number.isInteger(Number(maximum)) &&
    Number(minimum) >= 8 &&
    Number(maximum) <= MAX_VIDEO_SECONDS &&
    Number(maximum) >= Number(minimum);
  const canGenerate =
    !disabled &&
    !busy &&
    !active &&
    !!config?.configured &&
    ready &&
    consent &&
    boundsValid &&
    !error;

  useEffect(() => {
    const controller = new AbortController();
    api<Configuration>("/api/highlights/config", { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setConfig(value);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setActionError(message(reason));
      });
    return () => controller.abort();
  }, [refresh]);

  useEffect(() => {
    const controller = new AbortController();
    const current = ++version.current;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    async function poll() {
      try {
        const value = await api<HighlightState>(
          `/api/recordings/${recordingId}/highlights`,
          { signal: controller.signal },
        );
        if (controller.signal.aborted || current !== version.current) return;
        setState(value);
        setError("");
        failures = 0;
        timer = setTimeout(poll, processing(value.latest) ? 1000 : 5000);
      } catch (reason) {
        if (controller.signal.aborted || current !== version.current) return;
        setError(`Highlight status unavailable. Retrying. ${message(reason)}`);
        timer = setTimeout(
          poll,
          Math.min(10000, 1000 * 2 ** Math.min(++failures, 4)),
        );
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [recordingId, refresh]);

  useEffect(() => () => action.current?.abort(), []);

  async function update(operation: "generate" | "regenerate" | "cancel") {
    if (
      action.current ||
      (operation !== "cancel" && !canGenerate) ||
      (operation === "cancel" && !latest)
    )
      return;
    const controller = new AbortController();
    action.current = controller;
    version.current++;
    setBusy(true);
    setActionError("");
    setNotice("");
    try {
      await api<HighlightJob>(
        operation === "cancel"
          ? `/api/highlights/${latest!.id}/cancel`
          : `/api/recordings/${recordingId}/highlights`,
        {
          method: "POST",
          signal: controller.signal,
          ...(operation !== "cancel"
            ? {
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  transcription_id: state!.transcription!.id,
                  revision: state!.transcription!.revision,
                  remote_consent: true,
                  quality_mode: true,
                  min_seconds: Number(minimum),
                  max_seconds: Number(maximum),
                  regenerate: operation === "regenerate",
                }),
              }
            : {}),
        },
      );
      if (!controller.signal.aborted)
        setNotice(
          operation === "cancel"
            ? "Cancellation requested. In-flight API usage may still be billed."
            : "Analysis requested. Matching saved results are reused when available.",
        );
    } catch (reason) {
      if (!controller.signal.aborted) setActionError(message(reason));
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        setRefresh((value) => value + 1);
      }
      action.current = null;
    }
  }

  async function review(
    candidate: HighlightCandidate,
    value: Review,
    use: boolean,
  ) {
    if (disabled || action.current || !result || result.stale) return;
    const controller = new AbortController();
    action.current = controller;
    version.current++;
    setBusy(true);
    setActionError("");
    try {
      const saved = await api<HighlightCandidate>(
        `/api/highlight-candidates/${candidate.id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ review: value, revision: candidate.revision }),
          signal: controller.signal,
        },
      );
      if (controller.signal.aborted) return;
      setState((current) =>
        current?.result
          ? {
              ...current,
              result: {
                ...current.result,
                candidates: current.result.candidates.map((item) =>
                  item.id === saved.id ? saved : item,
                ),
              },
            }
          : current,
      );
      if (use) {
        onSelect(saved);
        setNotice(
          "Moment loaded into the editor. Adjust its boundaries and captions, then save the selection or export.",
        );
      } else setNotice("Review saved.");
    } catch (reason) {
      if (!controller.signal.aborted) setActionError(message(reason));
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        setRefresh((value) => value + 1);
      }
      action.current = null;
    }
  }

  return (
    <section
      className="caption-panel highlight-panel"
      aria-labelledby="highlight-title"
    >
      <div className="section-heading">
        <h2 id="highlight-title">Suggested moments</h2>
        <Badge variant="secondary">AI assisted</Badge>
      </div>
      <p className="caption-help">
        Find spoken highlights in your saved transcript, then pick a moment to
        make your own. Review each suggestion before exporting.
      </p>
      <Accordion type="single" collapsible>
        <AccordionItem value="details">
          <AccordionTrigger>
            How suggestions work and what is shared
          </AccordionTrigger>
          <AccordionContent>
            <p>
              Suggestions can miss visual action or silent reactions. Scores
              rank editorial potential; they do not predict views.
            </p>
            {config && (
              <p>
                Model: {config.model}. Each analysis is capped at{" "}
                {config.max_requests} requests and{" "}
                {config.max_output_tokens_per_request} output tokens per
                request. API usage is billed separately.
              </p>
            )}
          </AccordionContent>
        </AccordionItem>
      </Accordion>
      {config && !config.configured && (
        <Alert role="status" variant="default">
          <AlertDescription>
            Highlight detection needs a backend OpenAI API key. Configure it and
            restart the API; manual clipping remains available.
          </AlertDescription>
        </Alert>
      )}
      {!state && !error && <p role="status">Loading highlight status...</p>}
      {state && !ready && (
        <p>Transcribe this recording before finding highlights.</p>
      )}
      <FieldSet
        className="highlight-options grid gap-4 sm:grid-cols-2"
        disabled={disabled || busy || active}
      >
        <FieldLegend>Highlight duration</FieldLegend>
        <Field>
          <FieldLabel htmlFor={`${fieldId}-1`}>Minimum seconds </FieldLabel>
          <Input
            id={`${fieldId}-1`}
            type="number"
            min={8}
            max={MAX_VIDEO_SECONDS}
            value={minimum}
            onChange={(event) => setMinimum(event.target.value)}
          />
        </Field>
        <Field>
          <FieldLabel htmlFor={`${fieldId}-2`}>Maximum seconds </FieldLabel>
          <Input
            id={`${fieldId}-2`}
            type="number"
            min={8}
            max={MAX_VIDEO_SECONDS}
            value={maximum}
            onChange={(event) => setMaximum(event.target.value)}
          />
        </Field>
        {!boundsValid && (
          <p className="invalid">
            Choose whole seconds from 8 up to the source duration (at most six
            hours), with the minimum no greater than the maximum.
          </p>
        )}
        <Field className="radio-option consent-option" orientation="horizontal">
          <Checkbox
            id={`${fieldId}-3`}
            checked={consent}
            onCheckedChange={(checked) => setConsent(checked === true)}
          />
          <FieldLabel htmlFor={`${fieldId}-3`}>
            Send this recording&apos;s saved transcript text and timestamps to
            OpenAI for analysis. Video and audio stay local.
          </FieldLabel>
        </Field>
      </FieldSet>
      <div className="caption-actions">
        <Button
          type="button"
          disabled={!canGenerate}
          onClick={() => void update("generate")}
          variant="default"
        >
          {latest?.status === "failed" || latest?.status === "cancelled"
            ? "Retry highlight analysis"
            : "Find highlights / reuse results"}
        </Button>
        {result && (
          <Button
            type="button"
            disabled={!canGenerate}
            onClick={() => void update("regenerate")}
            variant="outline"
          >
            Regenerate (new API usage)
          </Button>
        )}
        {active && (
          <Button
            type="button"
            disabled={busy || latest?.status === "cancelling"}
            onClick={() => void update("cancel")}
            variant="outline"
          >
            Cancel analysis
          </Button>
        )}
      </div>
      {active && latest && (
        <div role="status">
          <p>
            {latest.status === "cancelling"
              ? "Cancelling analysis..."
              : latest.editorial_status === "reviewing"
                ? "Comparing highlights in editorial review..."
                : `Discovering highlights: ${latest.completed_windows}/${latest.total_windows} windows`}
          </p>
          <Progress
            aria-label="Highlight analysis progress"
            value={
              (90 * latest.completed_windows) /
              Math.max(1, latest.total_windows)
            }
          />
        </div>
      )}
      {latest?.error && (
        <Alert role="status" variant="default">
          <AlertDescription>{latest.error}</AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert role="status" variant="default">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {actionError && (
        <Alert role="alert" variant="destructive">
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      )}
      {notice && <p role="status">{notice}</p>}
      {result && (
        <>
          {latest?.id !== result.id && (
            <p className="caption-help">
              Showing the previous successful analysis while the latest attempt
              completes or awaits retry.
            </p>
          )}
          {result.stale && (
            <Alert role="status" variant="default">
              <AlertDescription>
                The saved transcript has changed. Generate current suggestions
                before using these moments.
              </AlertDescription>
            </Alert>
          )}
          {result.editorial_status !== "completed" && (
            <p className="caption-help">
              This saved analysis predates global editorial review.
            </p>
          )}
          <p className="caption-help">
            Saved analysis: {result.requests_made} requests,{" "}
            {result.input_tokens} input tokens, {result.output_tokens} output
            tokens.{" "}
            {result.rejected_candidates > 0 &&
              `${result.rejected_candidates} unsupported suggestions were discarded.`}
          </p>
          {result.candidates.length === 0 && (
            <p>
              No suitable spoken moments were found. Adjust the duration limits
              or select a moment manually.
            </p>
          )}
          <ol className="highlight-list" aria-label="Suggested highlights">
            {result.candidates.map((candidate) => (
              <li key={candidate.id} data-review={candidate.review}>
                <div className="highlight-heading">
                  <h3>{candidate.title}</h3>
                  <Badge
                    aria-label={`Editorial rank ${candidate.score} out of 100`}
                    variant="secondary"
                  >
                    {candidate.score}
                    <small>/100</small>
                  </Badge>
                </div>
                <div className="highlight-meta">
                  <span>
                    {time(candidate.start)} to {time(candidate.end)}
                  </span>
                  <span>
                    {candidate.review === "unreviewed"
                      ? "To review"
                      : candidate.review === "accepted"
                        ? "Accepted"
                        : "Rejected"}
                  </span>
                </div>
                <p>{candidate.reason}</p>
                {candidate.quality && (
                  <p>
                    AI assessment, not a quality guarantee: hook{" "}
                    {candidate.quality.hook}/5, context{" "}
                    {candidate.quality.context}/5, payoff{" "}
                    {candidate.quality.payoff}/5, standalone{" "}
                    {candidate.quality.standalone}/5.
                  </p>
                )}
                <blockquote>{candidate.evidence}</blockquote>
                <div className="caption-actions">
                  <Button
                    type="button"
                    disabled={disabled || busy || result.stale}
                    onClick={() => void review(candidate, "accepted", true)}
                    variant="default"
                  >
                    Use this moment
                  </Button>
                  <Button
                    type="button"
                    disabled={disabled || busy || result.stale}
                    onClick={() =>
                      void review(
                        candidate,
                        candidate.review === "rejected"
                          ? "unreviewed"
                          : "rejected",
                        false,
                      )
                    }
                    variant="outline"
                  >
                    {candidate.review === "rejected"
                      ? "Restore for review"
                      : "Reject"}
                  </Button>
                </div>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
