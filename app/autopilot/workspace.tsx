"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import { api, message } from "@/lib/client-api";
import type { Project, ProjectItem } from "@/lib/projects";
import {
  platformNames,
  platformRoot,
  platforms,
  type Platform,
  type SocialConnection,
} from "@/lib/social";
import { BatchPublish } from "../components/batch-publish";

type Configuration = {
  enabled: boolean;
  revision: number;
  mode: "discover" | "prepare";
  ai_review: boolean;
  minimum_seconds: number;
  require_captions: boolean;
  scan_ids: string[];
};

type Snapshot = {
  configuration: Configuration;
  configured: boolean;
  worker_running: boolean;
  scans: {
    id: string;
    name: string;
    enabled: boolean;
    next_run: number | null;
    changed: boolean;
    last_status: string | null;
    last_detail: string | null;
    last_success: number | null;
  }[];
  jobs: {
    clip_id: string;
    scan_id: string | null;
    project_id: string | null;
    title: string;
    status: string;
    detail: string | null;
    clips: {
      id: string;
      title: string;
      export_id: string | null;
      revision: number;
      checks: boolean;
      ready: boolean;
      reason: string | null;
      review: string;
      warning: string | null;
      quality: string | null;
    }[];
  }[];
  post_handoffs: Record<string, number>;
  usage: { request_budget: number; source_bytes: number; results_24h: number };
};

function needsAttention(job: Snapshot["jobs"][number]) {
  return Boolean(
    job.detail ||
    ["failed", "partial", "interrupted"].includes(job.status) ||
    job.clips.some((clip) => clip.warning || clip.quality),
  );
}

const endpoint = "/api/autopilot";

function localTime(value: number) {
  return new Date(value * 1000).toLocaleString();
}

export function AutopilotWorkspace({
  view = "all",
  visible = true,
  suggestedScan = null,
}: {
  view?: "all" | "automation" | "review";
  visible?: boolean;
  suggestedScan?: string | null;
} = {}) {
  const appliedSuggestion = useRef<string | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [draft, setDraft] = useState<Configuration>();
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState("");
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const [posting, setPosting] = useState<ProjectItem[]>([]);
  const [filter, setFilter] = useState("all");
  const [accounts, setAccounts] = useState<
    Partial<Record<Platform, number | null>>
  >({});

  useEffect(() => {
    if (!visible || view === "review") return;
    const controller = new AbortController();
    for (const platform of platforms) {
      void api<SocialConnection>(platformRoot(platform), {
        signal: controller.signal,
      })
        .then((connection) => {
          if (!controller.signal.aborted)
            setAccounts((prior) => ({
              ...prior,
              [platform]: connection.accounts.length,
            }));
        })
        .catch(() => {
          if (!controller.signal.aborted)
            setAccounts((prior) => ({ ...prior, [platform]: null }));
        });
    }
    return () => controller.abort();
  }, [view, visible]);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      const state = await api<Snapshot>(endpoint, { signal });
      if (signal?.aborted) return;
      setSnapshot(state);
      const suggestion =
        suggestedScan &&
        suggestedScan !== appliedSuggestion.current &&
        state.scans.some((scan) => scan.id === suggestedScan)
          ? suggestedScan
          : null;
      appliedSuggestion.current = suggestedScan;
      setDraft((current) => {
        const settings = current ?? state.configuration;
        return suggestion && !settings.scan_ids.includes(suggestion)
          ? { ...settings, scan_ids: [...settings.scan_ids, suggestion] }
          : settings;
      });
      if (suggestion) setConsent(false);
      setOffline(false);
    },
    [suggestedScan],
  );

  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        await load(controller.signal);
      } catch {
        if (!controller.signal.aborted) setOffline(true);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 5000);
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [load, visible]);

  async function action(callback: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await callback();
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  async function save(enabled: boolean) {
    if (!draft || !snapshot) return;
    // Pausing applies to saved enrollment even if other edits are still staged.
    const settings = enabled ? draft : snapshot.configuration;
    const saved = await api<Configuration>(endpoint, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...settings, enabled, ai_consent: consent }),
    });
    setDraft(saved);
    setConsent(false);
    await load();
  }

  const ready =
    snapshot?.jobs.flatMap((job) =>
      job.project_id
        ? job.clips
            .filter((clip) => clip.ready)
            .map((clip) => ({ ...clip, projectId: job.project_id! }))
        : [],
    ) ?? [];
  const selected = ready.filter((clip) => chosen.includes(clip.id));

  async function loadBatch() {
    const targets = selected;
    const projectIds = [...new Set(targets.map((clip) => clip.projectId))];
    const projects = await Promise.all(
      projectIds.map((id) => api<Project>(`/api/projects/${id}`)),
    );
    const items = targets.map((target) => {
      const project = projects.find(
        (candidate) => candidate.id === target.projectId,
      );
      const item = project?.items.find(
        (candidate) => candidate.id === target.id,
      );
      if (
        !item ||
        item.revision !== target.revision ||
        item.export_id !== target.export_id ||
        item.export?.status !== "succeeded" ||
        item.review === "rejected" ||
        item.post_copy_stale
      ) {
        throw new Error(
          "A selected draft changed. Refresh its review before approving.",
        );
      }
      return item;
    });
    setPosting(items);
  }

  async function dismiss(projectId: string, id: string, revision: number) {
    await api(`/api/projects/${projectId}/bulk`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        targets: [{ id, revision }],
        action: "review",
        review: "rejected",
      }),
    });
    setChosen((current) => current.filter((value) => value !== id));
    setPosting([]);
    await load();
  }

  if (!snapshot || !draft)
    return offline ? (
      <Alert variant="destructive">
        <AlertDescription>
          Waiting for the local Cliptric backend. This page retries
          automatically.
        </AlertDescription>
      </Alert>
    ) : (
      <Skeleton className="h-52 w-full" />
    );

  const settings = snapshot.configuration;
  const attention = snapshot.jobs.filter(needsAttention).length;
  const visibleJobs = snapshot.jobs.filter((job) =>
    filter === "attention"
      ? needsAttention(job)
      : filter === "review"
        ? job.clips.some((clip) => clip.ready)
        : true,
  );
  const enabledScans = snapshot.scans.filter(
    (scan) => settings.scan_ids.includes(scan.id) && scan.enabled,
  );
  const activeScans = enabledScans.filter((scan) => !scan.changed);
  const runStatus = offline
    ? "Waiting for backend"
    : !settings.enabled
      ? "Off"
      : !snapshot.configured || !snapshot.worker_running
        ? "Waiting for Twitch setup"
        : !activeScans.length
          ? "Waiting for an enabled scan"
          : activeScans.some((scan) => scan.last_status === "running")
            ? "Scanning"
            : attention
              ? "Running with issues"
              : "Ready";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void action(() => save(true));
  }

  const runSettings = view !== "review" && (
      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(16rem,2fr)]">
        <Card>
          <CardHeader>
            <CardTitle role="heading" aria-level={2}>
              Run settings
            </CardTitle>
            <CardDescription>
              Enable Autopilot to start the selected scans at their saved
              intervals. Keep the local API running and the computer awake.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={submit}>
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="autopilot-mode">Mode</FieldLabel>
                  <NativeSelect
                    id="autopilot-mode"
                    value={draft.mode}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        mode:
                          event.target.value === "discover"
                            ? "discover"
                            : "prepare",
                      })
                    }
                  >
                    <NativeSelectOption value="discover">
                      Discovery only
                    </NativeSelectOption>
                    <NativeSelectOption value="prepare">
                      Prepare drafts for approval
                    </NativeSelectOption>
                  </NativeSelect>
                  <FieldDescription>
                    All publishing destinations require approval of the
                    finished export.
                  </FieldDescription>
                </Field>
                <Field>
                  <FieldLabel>Saved Twitch scans</FieldLabel>
                  {snapshot.scans.map((scan) => (
                    <Field orientation="horizontal" key={scan.id}>
                      <Checkbox
                        id={`autopilot-${scan.id}`}
                        checked={draft.scan_ids.includes(scan.id)}
                        onCheckedChange={(checked) => {
                          setDraft({
                            ...draft,
                            scan_ids:
                              checked === true
                                ? [...draft.scan_ids, scan.id]
                                : draft.scan_ids.filter(
                                    (id) => id !== scan.id,
                                  ),
                          });
                          setConsent(false);
                        }}
                      />
                      <FieldLabel htmlFor={`autopilot-${scan.id}`}>
                        {scan.name}
                      </FieldLabel>
                      <Badge variant="outline">
                        {scan.changed
                          ? "Settings changed"
                          : scan.enabled
                            ? "Recurring"
                            : "Manual only"}
                      </Badge>
                    </Field>
                  ))}
                  <Button variant="link" className="w-fit px-0" asChild>
                    <Link href="/twitch">
                      Manage Twitch scans and editor access
                    </Link>
                  </Button>
                </Field>
                {draft.mode === "prepare" && (
                  <>
                    <Field>
                      <FieldLabel htmlFor="autopilot-duration">
                        Minimum finished length (seconds)
                      </FieldLabel>
                      <Input
                        id="autopilot-duration"
                        type="number"
                        min={0}
                        max={60}
                        required
                        value={draft.minimum_seconds}
                        onChange={(event) =>
                          setDraft({
                            ...draft,
                            minimum_seconds: event.target.valueAsNumber,
                          })
                        }
                      />
                      <FieldDescription>
                        Use 0 for no minimum.
                      </FieldDescription>
                    </Field>
                    <Field orientation="horizontal">
                      <Checkbox
                        id="autopilot-captions"
                        checked={draft.require_captions}
                        onCheckedChange={(checked) =>
                          setDraft({
                            ...draft,
                            require_captions: checked === true,
                          })
                        }
                      />
                      <FieldLabel htmlFor="autopilot-captions">
                        Require captions before offering publishing review
                      </FieldLabel>
                    </Field>
                    <Field orientation="horizontal">
                      <Checkbox
                        id="autopilot-editorial"
                        checked={draft.ai_review}
                        onCheckedChange={(checked) => {
                          setDraft({ ...draft, ai_review: checked === true });
                          setConsent(false);
                        }}
                      />
                      <FieldLabel htmlFor="autopilot-editorial">
                        Review speech with AI to keep or trim each clip
                      </FieldLabel>
                    </Field>
                    {draft.ai_review && (
                      <Field orientation="horizontal">
                        <Checkbox
                          id="autopilot-consent"
                          checked={consent}
                          onCheckedChange={(checked) =>
                            setConsent(checked === true)
                          }
                        />
                        <FieldLabel htmlFor="autopilot-consent">
                          Allow Cliptric to share transcripts for this
                          enrolled configuration and use the saved AI budget.
                        </FieldLabel>
                      </Field>
                    )}
                  </>
                )}
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    disabled={busy || offline || !draft.scan_ids.length}
                  >
                    {settings.enabled ? "Apply settings" : "Enable Autopilot"}
                  </Button>
                  {settings.enabled && (
                    <Button
                      type="button"
                      variant="outline"
                      disabled={busy || offline}
                      onClick={() => void action(() => save(false))}
                    >
                      Pause Autopilot
                    </Button>
                  )}
                  <span
                    role="status"
                    className="text-sm text-muted-foreground"
                  >
                    {busy ? "Updating…" : runStatus}
                  </span>
                </div>
              </FieldGroup>
            </form>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle role="heading" aria-level={2}>
              Activity
            </CardTitle>
            <CardDescription>
              Paused runs keep their queued work. Downloads or processing
              already started may finish. Approved posts remain on the
              Publishing calendar.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline">
                {ready.length} available for review
              </Badge>
              <Badge variant="outline">{attention} need attention</Badge>
              <Badge variant="outline">
                {snapshot.post_handoffs.scheduled ?? 0} scheduled
              </Badge>
              <Badge variant="outline">
                {snapshot.post_handoffs.submitted ?? 0} sent to uploader
              </Badge>
            </div>
            <p className="text-sm text-muted-foreground">
              Last 24 hours: {snapshot.usage.results_24h} scan results,{" "}
              {snapshot.usage.request_budget} reserved AI requests,{" "}
              {(snapshot.usage.source_bytes / 1024 ** 2).toFixed(1)} MiB of
              imported media.
            </p>
            {enabledScans.map((scan) => (
              <div key={scan.id} className="flex flex-col gap-1">
                <span className="font-medium">{scan.name}</span>
                <span className="text-sm text-muted-foreground">
                  {scan.changed
                    ? "Apply settings to resume this scan."
                    : settings.enabled && scan.next_run
                      ? `Next scan ${localTime(scan.next_run)}`
                      : "Paused"}
                </span>
                {scan.last_success && (
                  <span className="text-sm text-muted-foreground">
                    Last successful scan {localTime(scan.last_success)}
                  </span>
                )}
                {scan.last_detail && (
                  <span className="text-sm">{scan.last_detail}</span>
                )}
              </div>
            ))}
            <div className="flex flex-col gap-2">
              <h3 className="font-medium">Destinations</h3>
              {platforms.map((platform) => (
                <div
                  key={platform}
                  className="flex flex-wrap items-center justify-between gap-2 text-sm"
                >
                  <span>{platformNames[platform]}</span>
                  <Badge variant="outline">
                    {accounts[platform] === undefined
                      ? "Checking accounts"
                      : accounts[platform] === null
                        ? "Status unavailable"
                        : accounts[platform]
                          ? `${accounts[platform]} connected · approval required`
                          : "Connect an account"}
                  </Badge>
                </div>
              ))}
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" className="w-fit" asChild>
                  <Link href="/publishing/accounts">Manage accounts</Link>
                </Button>
                <Button variant="outline" className="w-fit" asChild>
                  <Link href="/calendar">Open Publishing</Link>
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
  );
  const reviewQueue = view !== "automation" && (
      <Card>
        <CardHeader>
          <CardTitle role="heading" aria-level={2}>
            Review queue
          </CardTitle>
          <CardDescription>
            Preview finished videos and select exact exports for publishing
            review.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {snapshot.jobs.length > 0 && (
            <Field>
              <FieldLabel htmlFor="autopilot-review-filter">
                Show jobs
              </FieldLabel>
              <NativeSelect
                id="autopilot-review-filter"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
              >
                <NativeSelectOption value="all">
                  All recent jobs
                </NativeSelectOption>
                <NativeSelectOption value="review">
                  Ready for approval
                </NativeSelectOption>
                <NativeSelectOption value="attention">
                  Needs attention
                </NativeSelectOption>
              </NativeSelect>
            </Field>
          )}
          {ready.length > 0 && (
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="secondary"
                disabled={
                  busy || offline || !selected.length || selected.length > 75
                }
                onClick={() => void action(loadBatch)}
              >
                Prepare publishing review ({selected.length})
              </Button>
              {posting.length > 0 && (
                <BatchPublish items={posting} disabled={busy || offline} />
              )}
            </div>
          )}
          {!snapshot.jobs.length && (
            <p className="text-sm text-muted-foreground">
              No Autopilot imports yet. Prepared clips appear here when an
              enrolled scan finds eligible footage.
            </p>
          )}
          {snapshot.jobs.length > 0 && !visibleJobs.length && (
            <p className="text-sm text-muted-foreground">
              No jobs match this filter.
            </p>
          )}
          <div className="grid gap-4 xl:grid-cols-2">
            {visibleJobs.map((job) => (
              <Card key={job.clip_id}>
                <CardHeader>
                  <CardTitle>{job.title}</CardTitle>
                  <CardDescription>{job.status}</CardDescription>
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="outline" asChild>
                      <a
                        href={`https://clips.twitch.tv/${job.clip_id}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Twitch original
                      </a>
                    </Button>
                    {job.project_id && (
                      <Button size="sm" variant="outline" asChild>
                        <Link href={`/projects/${job.project_id}`}>
                          Open project
                        </Link>
                      </Button>
                    )}
                    {!job.project_id &&
                      job.status === "failed" &&
                      job.scan_id && (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy || offline}
                          onClick={() =>
                            void action(async () => {
                              await api(
                                `/api/twitch-discovery/scans/${job.scan_id}/retry/${job.clip_id}`,
                                { method: "POST" },
                              );
                              await load();
                            })
                          }
                        >
                          Retry import
                        </Button>
                      )}
                  </div>
                </CardHeader>
                <CardContent className="flex flex-col gap-3">
                  {job.detail && (
                    <Alert>
                      <AlertDescription>{job.detail}</AlertDescription>
                    </Alert>
                  )}
                  {job.clips.map((clip) => (
                    <div key={clip.id} className="flex flex-col gap-2">
                      {clip.ready && (
                        <Field orientation="horizontal">
                          <Checkbox
                            id={`review-${clip.id}`}
                            checked={chosen.includes(clip.id)}
                            onCheckedChange={(value) => {
                              setChosen((current) =>
                                value === true
                                  ? [...current, clip.id]
                                  : current.filter((id) => id !== clip.id),
                              );
                              setPosting([]);
                            }}
                          />
                          <FieldLabel htmlFor={`review-${clip.id}`}>
                            {clip.title}
                          </FieldLabel>
                        </Field>
                      )}
                      {clip.export_id && clip.checks && (
                        <video
                          className="max-h-72 w-full rounded-md bg-muted"
                          controls
                          preload="none"
                          src={`/api/clips/${clip.export_id}/media`}
                          aria-label={clip.title}
                        />
                      )}
                      <Badge variant="outline" className="w-fit">
                        {clip.review === "rejected"
                          ? "Rejected"
                          : clip.checks
                            ? "Render checks passed"
                            : "Waiting for a valid render"}
                      </Badge>
                      {clip.reason && (
                        <p className="text-sm">{clip.reason}</p>
                      )}
                      {clip.warning && (
                        <p className="text-sm text-muted-foreground">
                          {clip.warning}
                        </p>
                      )}
                      {clip.quality && (
                        <Alert>
                          <AlertDescription>{clip.quality}</AlertDescription>
                        </Alert>
                      )}
                      {job.project_id && clip.ready && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="w-fit"
                          disabled={busy || offline}
                          onClick={() =>
                            void action(() =>
                              dismiss(
                                job.project_id!,
                                clip.id,
                                clip.revision,
                              ),
                            )
                          }
                        >
                          Reject draft
                        </Button>
                      )}
                    </div>
                  ))}
                </CardContent>
              </Card>
            ))}
          </div>
        </CardContent>
      </Card>
  );

  return (
    <div className="flex flex-col gap-5">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {runSettings}
      {reviewQueue}
    </div>
  );
}
