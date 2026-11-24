"use client";

import { type FormEvent, useEffect, useState } from "react";
import Link from "next/link";
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
import {
  Empty,
  EmptyHeader,
  EmptyTitle,
  EmptyDescription,
} from "@/components/ui/empty";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";

type Settings = {
  name: string;
  source: "game" | "streamers";
  streamers: string[];
  game_id: string;
  game_name: string;
  include: string[];
  exclude: string[];
  period: string;
  start: string | null;
  end: string | null;
  order: string;
  limit: number;
  minimum_views: number;
  minimum_duration: number;
  maximum_duration: number;
  languages: string[];
  enabled: boolean;
  interval_minutes: number;
  auto_prepare: boolean;
  import_limit: number;
  daily_limit: number;
  daily_ai_limit: number;
  storage_limit_mib: number;
  preset_id: string | null;
  ai_titles: boolean;
  ai_copy: boolean;
  ai_consent: boolean;
  content_focus: string;
};
type Form = Omit<
  Settings,
  "streamers" | "include" | "exclude" | "languages" | "start" | "end"
> & {
  streamers: string;
  include: string;
  exclude: string;
  languages: string;
  start: string;
  end: string;
};
type Scan = {
  id: string;
  settings: Settings;
  revision: number;
  next_run: string | null;
};
type Run = {
  id: string;
  started: number;
  finished: number | null;
  count: number;
  status: string;
  detail: string | null;
};
type Clip = {
  id: string;
  broadcaster_name: string;
  thumbnail_url: string;
  title: string;
  view_count: number;
  duration: number;
  created_at: string;
  rank: number;
  state: "new" | "reviewed" | "hidden";
  broadcaster_id: string;
  can_request_download: boolean;
  intake_status: string | null;
  same_moment: boolean;
};
type Draft = {
  clip_id: string;
  project_id: string;
  status: string;
  project_status: string | null;
  detail: string | null;
};
type Detail = Scan & { runs: Run[]; clips: Clip[]; intake: Draft[] };
type Match = { id: string; name: string; login?: string };
type Workspace = {
  configured: boolean;
  scans: Scan[];
  intake: Draft[];
  editor: {
    available: boolean;
    profile: { editor_id: string; login: string } | null;
  };
};
const endpoint = "/api/twitch-discovery";
const initial: Form = {
  name: "",
  source: "streamers",
  streamers: "",
  game_id: "",
  game_name: "",
  include: "",
  exclude: "",
  period: "24h",
  start: "",
  end: "",
  order: "views",
  limit: 15,
  minimum_views: 0,
  minimum_duration: 0,
  maximum_duration: 60,
  languages: "",
  enabled: false,
  interval_minutes: 60,
  auto_prepare: false,
  import_limit: 15,
  daily_limit: 30,
  daily_ai_limit: 10,
  storage_limit_mib: 4096,
  preset_id: null,
  ai_titles: false,
  ai_copy: false,
  ai_consent: false,
  content_focus: "any",
};

function split(value: string) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function dateField(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

function formFor(settings: Settings): Form {
  return {
    ...settings,
    start: dateField(settings.start),
    end: dateField(settings.end),
    streamers: settings.streamers.join(", "),
    include: settings.include.join(", "),
    exclude: settings.exclude.join(", "),
    languages: settings.languages.join(", "),
  };
}

async function api<T>(path = "", options?: RequestInit): Promise<T> {
  const timeout = AbortSignal.timeout(25_000);
  const signal = options?.signal
    ? AbortSignal.any([timeout, options.signal])
    : timeout;
  const response = await fetch(endpoint + path, {
    cache: "no-store",
    ...options,
    signal,
  });
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const issue = Array.isArray(data?.detail) ? data.detail[0] : null;
    throw new Error(
      issue?.msg
        ? `${issue.loc?.at(-1) ?? "Settings"}: ${issue.msg}`
        : typeof data?.detail === "string"
          ? data.detail
          : "Could not reach Twitch Discovery.",
    );
  }
  return data as T;
}

function message(error: unknown) {
  return error instanceof Error
    ? error.message
    : "Twitch Discovery could not complete the action.";
}

function json(method: string, data: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  };
}

export function TwitchDiscovery({
  visible: panelVisible = true,
  onAutomate,
}: { visible?: boolean; onAutomate?: (id: string) => void } = {}) {
  const [list, setList] = useState<Workspace>();
  const [form, setForm] = useState<Form>(initial);
  const [baseline, setBaseline] = useState<Form>(initial);
  const [active, setActive] = useState<Scan>();
  const [detail, setDetail] = useState<Detail>();
  const [selectedRun, setSelectedRun] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [showReviewed, setShowReviewed] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<Match[]>([]);
  const [lookup, setLookup] = useState({ loading: false, error: "" });
  const [preview, setPreview] = useState("");
  const [host, setHost] = useState("");
  const [presets, setPresets] = useState<
    { id: string; name: string; workflow: string }[]
  >([]);
  const dirty = JSON.stringify(form) !== JSON.stringify(baseline);
  const id = active?.id;

  useEffect(() => {
    const controller = new AbortController();
    api<Workspace>("", { signal: controller.signal })
      .then(setList)
      .catch((cause) => {
        if (!controller.signal.aborted) setError(message(cause));
      });
    fetch("/api/project-presets", {
      signal: controller.signal,
      cache: "no-store",
    })
      .then(async (response) => {
        if (!response.ok)
          throw new Error("Could not load supplied-video presets.");
        setPresets(await response.json());
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(message(cause));
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    function guard(event: BeforeUnloadEvent) {
      event.preventDefault();
    }
    if (dirty) window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);

  useEffect(() => {
    if (!id || !panelVisible) return;
    const controller = new AbortController();
    async function refresh() {
      try {
        const suffix = selectedRun ? `?run=${selectedRun}` : "";
        const result = await api<Detail>(`/scans/${id}${suffix}`, {
          signal: controller.signal,
        });
        if (!controller.signal.aborted) setDetail(result);
      } catch (cause) {
        if (!controller.signal.aborted) setError(message(cause));
      }
    }
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, 3000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [id, selectedRun, panelVisible]);

  useEffect(() => {
    if (
      !query.trim() ||
      !list?.configured ||
      (form.source === "streamers" && query.trim().length < 2)
    )
      return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLookup({ loading: true, error: "" });
      const params = new URLSearchParams({
        query: query.trim(),
        kind: form.source === "game" ? "game" : "streamer",
      });
      api<Match[]>(`/lookup?${params}`, { signal: controller.signal })
        .then((results) => {
          setMatches(results);
          setLookup({
            loading: false,
            error: results.length ? "" : "No matches found.",
          });
        })
        .catch((cause) => {
          if (!controller.signal.aborted)
            setLookup({ loading: false, error: message(cause) });
        });
    }, 350);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [form.source, list?.configured, query]);

  function edit<K extends keyof Form>(key: K, value: Form[K]) {
    setForm((previous) => ({
      ...previous,
      ...([
        "source",
        "streamers",
        "game_id",
        "include",
        "exclude",
        "ai_titles",
        "ai_copy",
        "daily_ai_limit",
      ].includes(key)
        ? { ai_consent: false }
        : {}),
      [key]: value,
    }));
  }

  function changeQuery(value: string) {
    setQuery(value);
    setMatches([]);
    setLookup({ loading: false, error: "" });
  }

  function choose(scan?: Scan, clone = false, force = false) {
    if (!force && dirty && !window.confirm("Discard unsaved scan changes?"))
      return;
    setActive(clone ? undefined : scan);
    setDetail(undefined);
    setSelectedRun("");
    setSelected([]);
    setPreview("");
    changeQuery("");
    const next = scan ? formFor(scan.settings) : initial;
    setForm(
      clone
        ? {
            ...next,
            name: `${next.name} copy`.slice(0, 100),
            enabled: false,
            auto_prepare: false,
            ai_titles: false,
            ai_copy: false,
            ai_consent: false,
          }
        : next,
    );
    setBaseline(clone ? initial : next);
  }

  async function action(callback: () => Promise<void>) {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await callback();
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  async function refreshList() {
    setList(await api<Workspace>());
  }

  async function save(run = false, automate = false) {
    const settings: Settings = {
      ...form,
      streamers: form.source === "streamers" ? split(form.streamers) : [],
      game_id: form.source === "game" ? form.game_id : "",
      game_name: form.source === "game" ? form.game_name : "",
      include: form.source === "game" ? split(form.include) : [],
      exclude: form.source === "game" ? split(form.exclude) : [],
      languages: split(form.languages.toLowerCase()),
      start:
        form.period === "custom" && form.start
          ? new Date(form.start).toISOString()
          : null,
      end:
        form.period === "custom" && form.end
          ? new Date(form.end).toISOString()
          : null,
    };
    const saved = await api<Scan>(
      id ? `/scans/${id}?revision=${active.revision}` : "/scans",
      json(id ? "PUT" : "POST", settings),
    );
    setActive(saved);
    setForm(formFor(saved.settings));
    setBaseline(formFor(saved.settings));
    await refreshList();
    if (run) await runScan(saved.id);
    if (automate) onAutomate?.(saved.id);
  }

  async function runScan(scanId: string) {
    await api(`/scans/${scanId}/run`, { method: "POST" });
    setSelectedRun("");
    setSelected([]);
    setDetail(await api<Detail>(`/scans/${scanId}`));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    const intent = submitter?.getAttribute("value");
    void action(() => save(intent === "run", intent === "automation"));
  }

  async function setRecurring(enabled: boolean) {
    if (!active) return;
    const saved = await api<Scan>(
      `/scans/${active.id}?revision=${active.revision}`,
      json("PUT", { ...active.settings, enabled }),
    );
    setActive(saved);
    edit("enabled", enabled);
    setBaseline((previous) => ({ ...previous, enabled }));
    await refreshList();
  }

  async function review(state: Clip["state"]) {
    await api(`/scans/${id}/review`, json("POST", { ids: selected, state }));
    setSelected([]);
    setDetail(
      await api<Detail>(
        `/scans/${id}${selectedRun ? `?run=${selectedRun}` : ""}`,
      ),
    );
  }

  async function prepare() {
    const ids = eligibleSelection.map((clip) => clip.id);
    const result = await api<{ queued: number }>(
      `/scans/${id}/prepare`,
      json("POST", { ids }),
    );
    setNotice(
      result.queued < ids.length
        ? `Queued ${result.queued} of ${ids.length}. Check the editor connection and processing limits for the remaining clips.`
        : `Queued ${result.queued} ${result.queued === 1 ? "clip" : "clips"}.`,
    );
    setSelected([]);
  }

  async function purge(clipId: string) {
    const result = await api<{ cleanup_pending: boolean }>(
      `/imports/${clipId}`,
      { method: "DELETE" },
    );
    await refreshList();
    if (result.cleanup_pending)
      setError(
        "The import was removed. Cliptric will retry cleaning up a file that is in use.",
      );
    if (id)
      setDetail(
        await api<Detail>(
          `/scans/${id}${selectedRun ? `?run=${selectedRun}` : ""}`,
        ),
      );
  }

  const latest = detail?.runs[0];
  const displayed = detail?.runs.find(
    (run) => run.id === (selectedRun || latest?.id),
  );
  const lastSuccess = detail?.runs.find((run) => run.status === "complete");
  const visible = (detail?.clips ?? []).filter(
    (clip) =>
      (showHidden || clip.state !== "hidden") &&
      (showReviewed || clip.state !== "reviewed"),
  );
  const eligibleSelection = visible.filter(
    (clip) =>
      selected.includes(clip.id) &&
      clip.can_request_download &&
      (!clip.intake_status || clip.intake_status === "recording"),
  );
  const running = latest?.status === "running" || latest?.status === "queued";
  const history = active ? detail?.intake : list?.intake;
  const numberFields = [
    ["limit", "Results", 1, 100],
    ["minimum_views", "Minimum views", 0, 2147483647],
    ["minimum_duration", "Minimum seconds", 0, 60],
    ["maximum_duration", "Maximum seconds", 1, 60],
  ] as const;

  return (
    <div className="flex flex-col gap-5">
      {!list && !error && <Skeleton className="h-28 w-full" />}
      {list && !list.configured && (
        <Alert>
          <AlertDescription>
            Configure Twitch app credentials in the backend and restart the API
            to run scans.
          </AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {notice && (
        <Alert role="status">
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      )}
      <Card>
        <CardHeader>
          <CardTitle role="heading" aria-level={2}>
            Twitch editor access
          </CardTitle>
          <CardDescription>
            Find public clips from any channel. To download clips, connect the
            broadcaster or a Twitch Editor for that channel.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            {list?.editor.profile ? (
              <>
                <Badge variant="secondary">
                  Connected as {list.editor.profile.login}
                </Badge>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    void action(async () => {
                      await api("/disconnect", { method: "POST" });
                      await refreshList();
                    })
                  }
                >
                  Disconnect
                </Button>
              </>
            ) : (
              <Button
                variant="outline"
                disabled={busy || !list?.editor.available}
                onClick={() =>
                  void action(async () => {
                    window.location.assign(
                      (
                        await api<{ url: string }>("/connect", {
                          method: "POST",
                        })
                      ).url,
                    );
                  })
                }
              >
                Connect Twitch editor
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(15rem,1fr)_minmax(0,3fr)]">
        <Card>
          <CardHeader>
            <CardTitle role="heading" aria-level={2}>
              Saved scans
            </CardTitle>
            <CardDescription>
              Scheduled scans run while the API and computer are awake.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => choose()}
            >
              New scan
            </Button>
            {list?.scans.map((scan) => (
              <Button
                key={scan.id}
                type="button"
                variant={id === scan.id ? "secondary" : "ghost"}
                className="justify-start"
                disabled={busy}
                onClick={() => choose(scan)}
              >
                <span className="truncate">{scan.settings.name}</span>
                <Badge variant="outline">
                  {scan.settings.enabled ? "Recurring" : "Manual"}
                </Badge>
              </Button>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle role="heading" aria-level={2}>
              {id ? "Edit Twitch scan" : "New Twitch scan"}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={submit} className="flex flex-col gap-5">
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="scan-name">Scan name</FieldLabel>
                  <Input
                    id="scan-name"
                    required
                    maxLength={100}
                    value={form.name}
                    onChange={(e) => edit("name", e.target.value)}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="scan-source">Source</FieldLabel>
                  <NativeSelect
                    id="scan-source"
                    value={form.source}
                    onChange={(e) => {
                      edit("source", e.target.value as Form["source"]);
                      changeQuery("");
                    }}
                  >
                    <NativeSelectOption value="streamers">
                      Streamers
                    </NativeSelectOption>
                    <NativeSelectOption value="game">
                      Game category
                    </NativeSelectOption>
                  </NativeSelect>
                </Field>
                <Field>
                  <FieldLabel htmlFor="scan-search">
                    {form.source === "game"
                      ? "Search categories"
                      : "Look up a streamer"}
                  </FieldLabel>
                  <Input
                    id="scan-search"
                    value={query}
                    maxLength={120}
                    onChange={(e) => changeQuery(e.target.value)}
                    placeholder={
                      form.source === "game"
                        ? "Grand Theft Auto V"
                        : "Username or Twitch profile URL"
                    }
                  />
                  <FieldDescription role="status">
                    {lookup.loading ? "Searching Twitch…" : lookup.error}
                  </FieldDescription>
                  {matches.map((match) => (
                    <Button
                      key={match.id}
                      type="button"
                      variant="outline"
                      className="w-fit"
                      onClick={() => {
                        if (form.source === "game")
                          setForm({
                            ...form,
                            game_id: match.id,
                            game_name: match.name,
                            ai_consent: false,
                          });
                        else if (match.login)
                          edit(
                            "streamers",
                            [
                              ...new Set([
                                ...split(form.streamers),
                                match.login,
                              ]),
                            ].join(", "),
                          );
                        changeQuery("");
                      }}
                    >
                      {match.name}
                    </Button>
                  ))}
                  {form.source === "game" && form.game_name && (
                    <FieldDescription>
                      Selected category: {form.game_name}
                    </FieldDescription>
                  )}
                </Field>
                {form.source === "streamers" ? (
                  <Field>
                    <FieldLabel htmlFor="scan-streamers">
                      Streamer names or profile URLs
                    </FieldLabel>
                    <Input
                      id="scan-streamers"
                      required
                      value={form.streamers}
                      maxLength={850}
                      onChange={(e) => edit("streamers", e.target.value)}
                    />
                    <FieldDescription>
                      Separate up to eight streamers with commas.
                    </FieldDescription>
                  </Field>
                ) : (
                  <div className="grid gap-4 sm:grid-cols-2">
                    {(["include", "exclude"] as const).map((key) => (
                      <Field key={key}>
                        <FieldLabel htmlFor={`scan-${key}`}>
                          {key === "include"
                            ? "Include broadcasters"
                            : "Exclude broadcasters"}
                        </FieldLabel>
                        <Input
                          id={`scan-${key}`}
                          maxLength={850}
                          value={form[key]}
                          onChange={(e) => edit(key, e.target.value)}
                          placeholder="Comma-separated usernames"
                        />
                      </Field>
                    ))}
                  </div>
                )}
              </FieldGroup>
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="scan-preset">Output preset</FieldLabel>
                  <NativeSelect
                    id="scan-preset"
                    value={form.preset_id ?? ""}
                    onChange={(e) => edit("preset_id", e.target.value || null)}
                  >
                    <NativeSelectOption value="">
                      Automatic layout and classic captions
                    </NativeSelectOption>
                    {presets
                      .filter((preset) => preset.workflow === "batch")
                      .map((preset) => (
                        <NativeSelectOption key={preset.id} value={preset.id}>
                          {preset.name}
                        </NativeSelectOption>
                      ))}
                  </NativeSelect>
                </Field>
                <Field orientation="horizontal">
                  <Checkbox
                    id="scan-prepare"
                    checked={form.auto_prepare}
                    onCheckedChange={(value) =>
                      edit("auto_prepare", value === true)
                    }
                  />
                  <FieldLabel htmlFor="scan-prepare">
                    Automatically prepare eligible clips after a scan
                  </FieldLabel>
                </Field>
                <div className="grid gap-4 sm:grid-cols-2">
                  {(
                    [
                      ["import_limit", "Imports per scan", 1, 20],
                      ["daily_limit", "Imports per 24 hours", 1, 100],
                      ["daily_ai_limit", "AI requests per 24 hours", 0, 100],
                      [
                        "storage_limit_mib",
                        "Twitch source storage (MiB)",
                        128,
                        65536,
                      ],
                    ] as const
                  ).map(([key, label, min, max]) => (
                    <Field key={key}>
                      <FieldLabel htmlFor={`scan-${key}`}>{label}</FieldLabel>
                      <Input
                        id={`scan-${key}`}
                        required
                        type="number"
                        min={min}
                        max={max}
                        value={form[key]}
                        onChange={(e) => edit(key, e.target.valueAsNumber)}
                      />
                    </Field>
                  ))}
                </div>
                <Field orientation="horizontal">
                  <Checkbox
                    id="scan-titles"
                    checked={form.ai_titles}
                    onCheckedChange={(value) =>
                      edit("ai_titles", value === true)
                    }
                  />
                  <FieldLabel htmlFor="scan-titles">
                    Generate AI headlines
                  </FieldLabel>
                </Field>
                <Field orientation="horizontal">
                  <Checkbox
                    id="scan-copy"
                    checked={form.ai_copy}
                    onCheckedChange={(value) => edit("ai_copy", value === true)}
                  />
                  <FieldLabel htmlFor="scan-copy">
                    Generate AI descriptions and hashtags
                  </FieldLabel>
                </Field>
                {(form.ai_titles || form.ai_copy) && (
                  <Field>
                    <FieldDescription>
                      Each enabled AI option uses one request per clip. This
                      daily budget covers up to{" "}
                      {Math.floor(
                        form.daily_ai_limit /
                          (Number(form.ai_titles) + Number(form.ai_copy)),
                      )}{" "}
                      clips before any additional Autopilot editorial review.
                    </FieldDescription>
                    <Field orientation="horizontal">
                      <Checkbox
                        id="scan-ai-consent"
                        checked={form.ai_consent}
                        onCheckedChange={(value) =>
                          edit("ai_consent", value === true)
                        }
                      />
                      <FieldLabel htmlFor="scan-ai-consent">
                        I approve sharing eligible transcripts and paying for
                        these AI requests within the saved budget.
                      </FieldLabel>
                    </Field>
                  </Field>
                )}
              </FieldGroup>
              <FieldGroup className="grid gap-4 sm:grid-cols-2">
                <Field>
                  <FieldLabel htmlFor="scan-period">
                    Clip creation period
                  </FieldLabel>
                  <NativeSelect
                    id="scan-period"
                    value={form.period}
                    onChange={(e) => edit("period", e.target.value)}
                  >
                    {Object.entries({
                      "24h": "Last 24 hours",
                      "7d": "Last 7 days",
                      "30d": "Last 30 days",
                      "90d": "Last 90 days",
                      all: "All available history",
                      custom: "Custom dates",
                    }).map(([value, label]) => (
                      <NativeSelectOption key={value} value={value}>
                        {label}
                      </NativeSelectOption>
                    ))}
                  </NativeSelect>
                </Field>
                <Field>
                  <FieldLabel htmlFor="scan-order">Ranking</FieldLabel>
                  <NativeSelect
                    id="scan-order"
                    value={form.order}
                    onChange={(e) => edit("order", e.target.value)}
                  >
                    <NativeSelectOption value="views">
                      Most viewed
                    </NativeSelectOption>
                    <NativeSelectOption value="recent">
                      Newest among fetched clips
                    </NativeSelectOption>
                    <NativeSelectOption value="momentum">
                      Views per hour of age estimate
                    </NativeSelectOption>
                  </NativeSelect>
                </Field>
                <Field>
                  <FieldLabel htmlFor="scan-focus">Clip topic</FieldLabel>
                  <NativeSelect
                    id="scan-focus"
                    value={form.content_focus}
                    onChange={(e) => edit("content_focus", e.target.value)}
                  >
                    {Object.entries({
                      any: "Any topic",
                      gameplay: "Gameplay",
                      roleplay: "Roleplay",
                      funny: "Funny moments",
                      fail: "Fails",
                      chase: "Police chases",
                      glitch: "Glitches",
                      reaction: "Reactions",
                    }).map(([value, label]) => (
                      <NativeSelectOption value={value} key={value}>
                        {label}
                      </NativeSelectOption>
                    ))}
                  </NativeSelect>
                  <FieldDescription>
                    Matches words in Twitch titles.
                  </FieldDescription>
                </Field>
                {form.period === "custom" &&
                  (["start", "end"] as const).map((key) => (
                    <Field key={key}>
                      <FieldLabel htmlFor={`scan-${key}`}>
                        {key === "start"
                          ? "Start (local time)"
                          : "End (local time)"}
                      </FieldLabel>
                      <Input
                        id={`scan-${key}`}
                        required
                        type="datetime-local"
                        value={form[key]}
                        onChange={(e) => edit(key, e.target.value)}
                      />
                    </Field>
                  ))}
                {numberFields.map(([key, label, min, max]) => (
                  <Field key={key}>
                    <FieldLabel htmlFor={`scan-${key}`}>{label}</FieldLabel>
                    <Input
                      id={`scan-${key}`}
                      required
                      type="number"
                      min={min}
                      max={max}
                      value={form[key]}
                      onChange={(e) => edit(key, e.target.valueAsNumber)}
                    />
                  </Field>
                ))}
                <Field>
                  <FieldLabel htmlFor="scan-langs">
                    Broadcaster languages
                  </FieldLabel>
                  <Input
                    id="scan-langs"
                    maxLength={70}
                    placeholder="Any, or e.g. en, fr"
                    value={form.languages}
                    onChange={(e) => edit("languages", e.target.value)}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="scan-interval">
                    Check every (minutes)
                  </FieldLabel>
                  <Input
                    id="scan-interval"
                    required
                    type="number"
                    min={10}
                    max={10080}
                    value={form.interval_minutes}
                    onChange={(e) =>
                      edit("interval_minutes", e.target.valueAsNumber)
                    }
                  />
                </Field>
                <Field orientation="horizontal">
                  <Checkbox
                    id="scan-enabled"
                    checked={form.enabled}
                    onCheckedChange={(value) => edit("enabled", value === true)}
                  />
                  <FieldLabel htmlFor="scan-enabled">
                    Enable recurring scan
                  </FieldLabel>
                </Field>
              </FieldGroup>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="submit"
                  disabled={busy || (form.source === "game" && !form.game_id)}
                >
                  {id ? "Save changes" : "Save scan"}
                </Button>
                <Button
                  type="submit"
                  value="run"
                  variant="secondary"
                  disabled={
                    busy ||
                    !list?.configured ||
                    running ||
                    (form.source === "game" && !form.game_id)
                  }
                >
                  Save and run
                </Button>
                {onAutomate && (
                  <Button
                    type="submit"
                    value="automation"
                    variant="outline"
                    disabled={busy || (form.source === "game" && !form.game_id)}
                  >
                    Save and set up automation
                  </Button>
                )}
                {dirty && (
                  <Button
                    type="button"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => choose(active, false, true)}
                  >
                    Cancel changes
                  </Button>
                )}
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
      {active && (
        <Card>
          <CardHeader>
            <CardTitle role="heading" aria-level={2}>
              Scan runs
            </CardTitle>
            <CardDescription>
              {latest
                ? `Last run ${new Date(latest.started * 1000).toLocaleString()} · ${latest.status}`
                : "No runs yet."}
              {lastSuccess &&
                ` · Last successful run ${new Date(lastSuccess.started * 1000).toLocaleString()}`}
              {(detail?.next_run ?? active.next_run) &&
                active.settings.enabled &&
                ` · Next run ${new Date((detail?.next_run ?? active.next_run)!).toLocaleString()}`}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={busy || running || !list?.configured}
                onClick={() => void action(() => runScan(active.id))}
              >
                Run now
              </Button>
              <Button
                variant="outline"
                disabled={busy || dirty}
                onClick={() =>
                  void action(() => setRecurring(!active.settings.enabled))
                }
              >
                {active.settings.enabled ? "Pause" : "Resume"}
              </Button>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => choose(active, true)}
              >
                Clone
              </Button>
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button variant="outline" disabled={busy || running}>
                    Delete scan
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Delete this saved scan?</AlertDialogTitle>
                    <AlertDialogDescription>
                      This removes its settings and scan history.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Keep scan</AlertDialogCancel>
                    <AlertDialogAction
                      onClick={() =>
                        void action(async () => {
                          await api(`/scans/${active.id}`, {
                            method: "DELETE",
                          });
                          choose(undefined, false, true);
                          await refreshList();
                        })
                      }
                    >
                      Delete scan
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
            {!!detail?.runs.length && (
              <Field>
                <FieldLabel htmlFor="scan-run">Results from</FieldLabel>
                <NativeSelect
                  id="scan-run"
                  value={selectedRun}
                  onChange={(e) => {
                    setSelectedRun(e.target.value);
                    setSelected([]);
                    setPreview("");
                  }}
                >
                  <NativeSelectOption value="">Latest run</NativeSelectOption>
                  {detail.runs.map((run) => (
                    <NativeSelectOption key={run.id} value={run.id}>
                      {new Date(run.started * 1000).toLocaleString()} ·{" "}
                      {run.status} · {run.count} clips
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </Field>
            )}
            {displayed?.detail && (
              <Alert
                variant={
                  displayed.status === "failed" ? "destructive" : "default"
                }
              >
                <AlertDescription>{displayed.detail}</AlertDescription>
              </Alert>
            )}
            {displayed && ["queued", "running"].includes(displayed.status) && (
              <p role="status">Checking Twitch…</p>
            )}
            {displayed &&
              !visible.length &&
              ["complete", "partial"].includes(displayed.status) && (
                <Empty>
                  <EmptyHeader>
                    <EmptyTitle>No clips to show</EmptyTitle>
                    <EmptyDescription>
                      Results may be filtered out or expired. Adjust filters or
                      run the scan again.
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              )}
            {!!detail?.clips.length && (
              <>
                <div className="flex flex-wrap items-center gap-3">
                  <Field orientation="horizontal" className="w-auto">
                    <Checkbox
                      id="reviewed"
                      checked={showReviewed}
                      onCheckedChange={(value) => {
                        setShowReviewed(value === true);
                        setSelected([]);
                      }}
                    />
                    <FieldLabel htmlFor="reviewed">Show reviewed</FieldLabel>
                  </Field>
                  <Field orientation="horizontal" className="w-auto">
                    <Checkbox
                      id="hidden"
                      checked={showHidden}
                      onCheckedChange={(value) => {
                        setShowHidden(value === true);
                        setSelected([]);
                      }}
                    />
                    <FieldLabel htmlFor="hidden">Show hidden</FieldLabel>
                  </Field>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy || !selected.length}
                    onClick={() => void action(() => review("reviewed"))}
                  >
                    Mark reviewed
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy || !selected.length}
                    onClick={() => void action(() => review("hidden"))}
                  >
                    Hide selected
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy || !selected.length}
                    onClick={() => void action(() => review("new"))}
                  >
                    Restore selected
                  </Button>
                  <Button
                    size="sm"
                    disabled={
                      busy ||
                      !eligibleSelection.length ||
                      eligibleSelection.length > active.settings.import_limit
                    }
                    onClick={() => void action(prepare)}
                  >
                    Prepare selected
                  </Button>
                  {eligibleSelection.length > active.settings.import_limit && (
                    <span className="text-sm text-muted-foreground">
                      Select up to {active.settings.import_limit} eligible
                      clips.
                    </span>
                  )}
                </div>
                <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
                  {visible.map((clip) => (
                    <Card key={clip.id}>
                      <CardHeader>
                        <CardTitle className="break-words">
                          <Checkbox
                            aria-label={`Select ${clip.title}`}
                            className="mr-2"
                            checked={selected.includes(clip.id)}
                            onCheckedChange={(value) =>
                              setSelected(
                                value === true
                                  ? [...selected, clip.id]
                                  : selected.filter((id) => id !== clip.id),
                              )
                            }
                          />
                          {clip.title || "Untitled Twitch clip"}
                        </CardTitle>
                        <CardDescription>
                          {clip.broadcaster_name} · {clip.duration}s ·{" "}
                          {clip.view_count.toLocaleString()} views ·{" "}
                          {new Date(clip.created_at).toLocaleString()}
                        </CardDescription>
                      </CardHeader>
                      <CardContent className="flex flex-col gap-3">
                        {clip.thumbnail_url && (
                          // Twitch returns remote thumbnails from validated distribution hosts.
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={clip.thumbnail_url}
                            alt=""
                            loading="lazy"
                            className="aspect-video w-full rounded-md object-contain"
                            referrerPolicy="no-referrer"
                          />
                        )}
                        <div className="flex flex-wrap gap-2">
                          <Badge variant="secondary">Rank {clip.rank}</Badge>
                          <Badge variant="outline">Discoverable</Badge>
                          {clip.state !== "new" && (
                            <Badge variant="outline">{clip.state}</Badge>
                          )}
                          {clip.intake_status && (
                            <Badge variant="outline">
                              {clip.intake_status === "project"
                                ? "In Projects"
                                : clip.intake_status === "recording"
                                  ? "In Editor"
                                  : clip.intake_status}
                            </Badge>
                          )}
                          {clip.same_moment && (
                            <Badge variant="outline">
                              Possible duplicate moment
                            </Badge>
                          )}
                        </div>
                        {!clip.can_request_download && (
                          <p className="text-sm text-muted-foreground">
                            Connect a Twitch editor to prepare this clip.
                          </p>
                        )}
                        <div className="flex flex-wrap gap-2">
                          <Button asChild variant="outline" size="sm">
                            <a
                              href={`https://clips.twitch.tv/${clip.id}`}
                              target="_blank"
                              rel="noreferrer"
                            >
                              Open on Twitch
                            </a>
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              setHost(
                                window.location.protocol === "https:" ||
                                  window.location.hostname === "localhost"
                                  ? window.location.hostname
                                  : "",
                              );
                              setPreview(preview === clip.id ? "" : clip.id);
                            }}
                          >
                            {preview === clip.id ? "Close preview" : "Preview"}
                          </Button>
                          <Button asChild variant="ghost" size="sm">
                            <Link
                              href={{
                                pathname: "/projects/batch",
                                query: { twitchClip: clip.id },
                              }}
                            >
                              Download &amp; edit
                            </Link>
                          </Button>
                        </div>
                        {preview === clip.id &&
                          (host ? (
                            <div className="overflow-x-auto">
                              <iframe
                                title={`Twitch preview of ${clip.title}`}
                                src={`https://clips.twitch.tv/embed?clip=${clip.id}&parent=${encodeURIComponent(host)}&autoplay=false`}
                                className="h-[300px] min-w-[400px] w-full"
                                allowFullScreen
                              />
                            </div>
                          ) : (
                            <p className="text-sm text-muted-foreground">
                              Open the clip on Twitch to preview from this
                              address.
                            </p>
                          ))}
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      )}
      {!!history?.length && (
        <Card>
          <CardHeader>
            <CardTitle role="heading" aria-level={2}>
              Processing history
            </CardTitle>
            <CardDescription>
              {active
                ? "Imports from this scan."
                : "Recent imports from all scans."}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {history.map((job) => (
              <div
                key={job.clip_id}
                className="flex flex-wrap items-center gap-2 text-sm"
              >
                <a
                  className="underline"
                  href={`https://clips.twitch.tv/${job.clip_id}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  Twitch source
                </a>
                <Badge variant="outline">
                  {job.status === "project"
                    ? (job.project_status ?? "Project removed")
                    : job.status}
                </Badge>
                {job.detail && <span>{job.detail}</span>}
                {job.project_status && (
                  <Button asChild variant="outline" size="sm">
                    <Link href={`/projects/${job.project_id}`}>
                      Open project
                    </Link>
                  </Button>
                )}
                {id && job.status === "failed" && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      void action(() =>
                        api(`/scans/${id}/retry/${job.clip_id}`, {
                          method: "POST",
                        }).then(() => undefined),
                      )
                    }
                  >
                    Retry import
                  </Button>
                )}
                {!job.project_status && job.status !== "running" && (
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button variant="ghost" size="sm" disabled={busy}>
                        Purge local import
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>
                          Purge this local import?
                        </AlertDialogTitle>
                        <AlertDialogDescription>
                          This removes the intake record and local source data.
                          Pause or update scans for this channel to prevent
                          another automatic import. Publishing records remain
                          available for reconciliation.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Keep import</AlertDialogCancel>
                        <AlertDialogAction
                          onClick={() => void action(() => purge(job.clip_id))}
                        >
                          Purge local import
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
