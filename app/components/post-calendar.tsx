"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ChevronLeftIcon, ChevronRightIcon, PlusIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Empty,
  EmptyHeader,
  EmptyTitle,
  EmptyDescription,
} from "@/components/ui/empty";
import { cn } from "@/lib/utils";
import { platformNames, type Platform } from "@/lib/social";
import { api, message } from "@/lib/client-api";

type ScheduledPost = {
  id: string;
  export_id: string;
  platform: Platform;
  account_id: string;
  scheduled_at: string;
  batch_id?: string | null;
  status: string;
  error: string | null;
  title: string;
};
type ExportChoice = { id: string; name: string; start: number; end: number };
const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const statusLabels: Record<string, string> = {
  scheduled: "Scheduled",
  dispatching: "Starting upload",
  submitted: "Sent to publishing",
  failed: "Needs attention",
  unknown: "Check publishing status",
  cancelled: "Cancelled",
  missed: "Missed",
};
const postStatus = (post: ScheduledPost) =>
  post.batch_id && post.status === "scheduled"
    ? "Queued"
    : (statusLabels[post.status] ?? post.status);
const dateKey = (date: Date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

export function PostCalendar() {
  const [clock, setClock] = useState(() => Date.now());
  const [month, setMonth] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });
  const [view, setView] = useState("queue");
  const [schedulerRunning, setSchedulerRunning] = useState<boolean | null>(
    null,
  );
  const [posts, setPosts] = useState<ScheduledPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState("");
  const [platform, setPlatform] = useState("youtube");
  const [exportId, setExportId] = useState("");
  const [exports, setExports] = useState<ExportChoice[]>([]);
  const [exportOffset, setExportOffset] = useState(0);
  const [exportsLoading, setExportsLoading] = useState(false);
  const [exportsError, setExportsError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 60000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!month) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let healthTimer: ReturnType<typeof setTimeout>;
    const start = new Date(month.getFullYear(), month.getMonth(), 1);
    start.setDate(start.getDate() - start.getDay());
    const end = new Date(start);
    end.setDate(end.getDate() + 42);
    async function load() {
      try {
        const data = await api<ScheduledPost[]>(
          `/api/schedule?${new URLSearchParams({ start: start.toISOString(), end: end.toISOString() })}`,
          { signal: controller.signal },
        );
        if (!controller.signal.aborted) {
          setPosts(data);
          setError("");
        }
      } catch (error) {
        if (!controller.signal.aborted) setError(message(error));
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          timer = setTimeout(load, 15000);
        }
      }
    }
    async function checkScheduler() {
      try {
        const health = await api<{ running: boolean }>("/api/schedule/health", {
          signal: controller.signal,
        });
        if (!controller.signal.aborted) setSchedulerRunning(health.running);
      } catch {
        if (!controller.signal.aborted) setSchedulerRunning(false);
      } finally {
        if (!controller.signal.aborted)
          healthTimer = setTimeout(checkScheduler, 15000);
      }
    }
    void checkScheduler();
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
      clearTimeout(healthTimer);
    };
  }, [month, reload]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    api<ExportChoice[]>(`/api/schedule/exports?offset=${exportOffset}`, {
      signal: controller.signal,
    })
      .then((data) => {
        if (!controller.signal.aborted) setExports(data);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setExportsError(message(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setExportsLoading(false);
      });
    return () => controller.abort();
  }, [open, exportOffset]);

  function schedule(date?: Date) {
    const choice = date ? new Date(date) : new Date(Date.now() + 3600000);
    if (date) choice.setHours(12, 0, 0, 0);
    setAt(
      `${dateKey(choice)}T${String(choice.getHours()).padStart(2, "0")}:${String(choice.getMinutes()).padStart(2, "0")}`,
    );
    resetExports(0);
    setOpen(true);
  }
  function resetExports(offset: number) {
    setExportsLoading(true);
    setExportsError("");
    setExports([]);
    setExportId("");
    setExportOffset(offset);
  }
  function changeMonth(value: Date) {
    setLoading(true);
    setPosts([]);
    setMonth(value);
  }
  function refreshCalendar() {
    setLoading(true);
    setPosts([]);
    setReload((value) => value + 1);
  }
  async function cancel(id: string) {
    setBusy(id);
    setError("");
    try {
      await api(`/api/schedule/${id}/cancel`, { method: "POST" });
      refreshCalendar();
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(null);
    }
  }

  const first = month ? new Date(month) : null;
  if (first) first.setDate(1 - first.getDay());
  const days = first
    ? Array.from(
        { length: 42 },
        (_, index) =>
          new Date(
            first.getFullYear(),
            first.getMonth(),
            first.getDate() + index,
          ),
      )
    : [];
  const byDate = new Map<string, ScheduledPost[]>();
  for (const post of posts) {
    const key = dateKey(new Date(post.scheduled_at));
    const items = byDate.get(key) ?? [];
    items.push(post);
    byDate.set(key, items);
  }
  const today = dateKey(new Date(clock));
  const visiblePosts = posts
    .filter((post) => {
      if (view === "queue")
        return ["scheduled", "dispatching"].includes(post.status);
      if (view === "sent") return post.status === "submitted";
      if (view === "attention")
        return ["failed", "unknown", "missed", "cancelled"].includes(
          post.status,
        );
      return true;
    })
    .sort((a, b) => Date.parse(a.scheduled_at) - Date.parse(b.scheduled_at));

  return (
    <section className="project-workspace">
      <header className="workspace-heading">
        <div>
          <h1>Publishing</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Plan and schedule posts for your connected accounts.
          </p>
        </div>
        <Button onClick={() => schedule()}>
          <PlusIcon data-icon="inline-start" />
          Schedule post
        </Button>
      </header>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border bg-card px-4 py-3">
        <Badge
          variant={
            schedulerRunning === null
              ? "secondary"
              : schedulerRunning
                ? "success"
                : "warning"
          }
        >
          {schedulerRunning === null
            ? "Checking scheduler"
            : schedulerRunning
              ? "Local scheduler connected"
              : "Scheduler unavailable"}
        </Badge>
        <p className="text-sm text-muted-foreground">
          Keep the local API running and your computer awake for automatic
          uploads. Times use your device’s time zone.
        </p>
      </div>
      <div
        role="group"
        aria-label="Publishing views"
        className="flex w-fit flex-wrap gap-1 rounded-lg border bg-card p-1"
      >
        {[
          ["queue", "Queue"],
          ["calendar", "Calendar"],
          ["sent", "Sent"],
          ["attention", "History & issues"],
        ].map(([value, label]) => (
          <Button
            key={value}
            type="button"
            size="sm"
            variant={view === value ? "secondary" : "ghost"}
            aria-pressed={view === value}
            onClick={() => setView(value)}
          >
            {label}
          </Button>
        ))}
      </div>
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>
            {error}{" "}
            <Button variant="outline" size="sm" onClick={refreshCalendar}>
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-semibold" aria-live="polite">
          {month?.toLocaleDateString(undefined, {
            month: "long",
            year: "numeric",
          }) ?? "Loading calendar"}
        </h2>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              const now = new Date();
              changeMonth(new Date(now.getFullYear(), now.getMonth(), 1));
            }}
          >
            Today
          </Button>
          <Button
            variant="outline"
            size="icon"
            aria-label="Previous month"
            onClick={() =>
              changeMonth(
                new Date(month.getFullYear(), month.getMonth() - 1, 1),
              )
            }
          >
            <ChevronLeftIcon />
          </Button>
          <Button
            variant="outline"
            size="icon"
            aria-label="Next month"
            onClick={() =>
              changeMonth(
                new Date(month.getFullYear(), month.getMonth() + 1, 1),
              )
            }
          >
            <ChevronRightIcon />
          </Button>
        </div>
      </div>
      {loading ? (
        <Skeleton className="h-96 w-full" />
      ) : view === "calendar" ? (
        <div
          className="overflow-x-auto rounded-lg border"
          tabIndex={0}
          aria-label="Monthly post calendar"
        >
          <table className="w-full min-w-[630px] table-fixed border-collapse">
            <thead>
              <tr>
                {dayNames.map((day) => (
                  <th
                    key={day}
                    scope="col"
                    className="border-b bg-muted/40 p-3 text-left text-xs font-medium text-muted-foreground"
                  >
                    {day}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: 6 }, (_, week) => (
                <tr key={week}>
                  {days.slice(week * 7, week * 7 + 7).map((day) => {
                    const key = dateKey(day);
                    return (
                      <td
                        key={key}
                        className={cn(
                          "h-14 border-b border-r p-2 align-top last:border-r-0",
                          day.getMonth() !== month?.getMonth() &&
                            "bg-muted/20 text-muted-foreground",
                        )}
                      >
                        <Button
                          size="sm"
                          variant={key === today ? "default" : "ghost"}
                          aria-current={key === today ? "date" : undefined}
                          aria-label={`Schedule a post for ${day.toLocaleDateString(undefined, { dateStyle: "full" })}`}
                          onClick={() => schedule(day)}
                        >
                          {day.getDate()}
                        </Button>
                        <div className="mt-1 flex flex-col gap-1">
                          {(byDate.get(key) ?? []).map((post) => (
                            <Link
                              key={post.id}
                              href={`#post-${post.id}`}
                              className="truncate rounded bg-accent px-2 py-1 text-xs text-accent-foreground focus-visible:outline-2 focus-visible:outline-ring"
                              title={`${post.title} (${postStatus(post)})`}
                            >
                              {new Date(post.scheduled_at).toLocaleTimeString(
                                [],
                                { hour: "numeric", minute: "2-digit" },
                              )}{" "}
                              {post.title}
                            </Link>
                          ))}
                        </div>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {!loading && !error && visiblePosts.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>
              {view === "sent"
                ? "No uploads sent"
                : view === "attention"
                  ? "No posts in this view"
                  : "Nothing scheduled"}
            </EmptyTitle>
            <EmptyDescription>
              {view === "sent"
                ? "Submitted uploads appear here for this date range."
                : "Schedule a finished clip or change the visible month."}
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : null}
      <div className="flex flex-col gap-3" aria-label="Posts in this view">
        {visiblePosts.map((post) => (
          <article
            id={`post-${post.id}`}
            key={post.id}
            className="flex scroll-mt-6 flex-wrap items-center justify-between gap-3 rounded-lg border p-4"
          >
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <p className="truncate font-medium">{post.title}</p>
              <p className="text-sm text-muted-foreground">
                {platformNames[post.platform]} ·{" "}
                {new Date(post.scheduled_at).toLocaleString()}
                {" · "}Account {post.account_id.slice(0, 8)}
              </p>
              <Badge
                variant={
                  post.status === "failed" ||
                  post.status === "unknown" ||
                  post.status === "missed"
                    ? "destructive"
                    : "secondary"
                }
              >
                {postStatus(post)}
              </Badge>
              {post.error ? (
                <p className="text-sm text-destructive">{post.error}</p>
              ) : null}
            </div>
            <div className="flex gap-2">
              <Button asChild size="sm" variant="outline">
                <Link
                  href={`/publish/${post.platform}/${post.export_id}?${new URLSearchParams({ account_id: post.account_id })}`}
                >
                  Publishing review
                </Link>
              </Button>
              {post.status === "scheduled" ? (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy !== null}
                  onClick={() => void cancel(post.id)}
                >
                  {busy === post.id ? "Cancelling..." : "Cancel"}
                </Button>
              ) : null}
            </div>
          </article>
        ))}
      </div>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="overflow-y-auto">
          <SheetHeader>
            <SheetTitle>Schedule post</SheetTitle>
            <SheetDescription>
              Select a finished clip and time, then review the account and
              posting settings before approval.
            </SheetDescription>
          </SheetHeader>
          <div className="flex flex-col gap-5 px-4 pb-6">
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="calendar-at">Date and time</FieldLabel>
                <Input
                  id="calendar-at"
                  type="datetime-local"
                  value={at}
                  onChange={(event) => setAt(event.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="calendar-platform">Platform</FieldLabel>
                <NativeSelect
                  id="calendar-platform"
                  value={platform}
                  onChange={(event) => setPlatform(event.target.value)}
                >
                  <NativeSelectOption value="youtube">
                    YouTube Shorts
                  </NativeSelectOption>
                  <NativeSelectOption value="tiktok">TikTok</NativeSelectOption>
                  <NativeSelectOption value="instagram">
                    Instagram
                  </NativeSelectOption>
                  <NativeSelectOption value="x">X</NativeSelectOption>
                  <NativeSelectOption value="facebook">
                    Facebook Page Reel
                  </NativeSelectOption>
                </NativeSelect>
              </Field>
              <Field>
                <FieldLabel htmlFor="calendar-export">Finished clip</FieldLabel>
                <NativeSelect
                  id="calendar-export"
                  value={exportId}
                  disabled={exportsLoading}
                  onChange={(event) => setExportId(event.target.value)}
                >
                  <NativeSelectOption value="">
                    {exportsLoading ? "Loading clips..." : "Choose a clip"}
                  </NativeSelectOption>
                  {exports.map((clip) => (
                    <NativeSelectOption key={clip.id} value={clip.id}>
                      {clip.name} ({clip.start.toFixed(1)}–{clip.end.toFixed(1)}
                      s)
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </Field>
            </FieldGroup>
            {exportsError ? (
              <Alert variant="destructive">
                <AlertDescription>{exportsError}</AlertDescription>
              </Alert>
            ) : null}
            {!exportsLoading && !exportsError && exports.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No finished clips on this page.{" "}
                <Link href="/projects" className="underline">
                  Create or export a clip
                </Link>{" "}
                first.
              </p>
            ) : null}
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={exportsLoading || exportOffset === 0}
                onClick={() => resetExports(Math.max(0, exportOffset - 100))}
              >
                Newer clips
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={exportsLoading || exports.length < 100}
                onClick={() => resetExports(exportOffset + 100)}
              >
                Older clips
              </Button>
            </div>
            {exportId &&
            at &&
            Number.isFinite(new Date(at).getTime()) &&
            new Date(at).getTime() > clock + 60000 ? (
              <Button asChild>
                <Link
                  href={`/publish/${platform}/${exportId}?${new URLSearchParams({ at })}`}
                >
                  Continue to review
                </Link>
              </Button>
            ) : (
              <Button disabled>Continue to review</Button>
            )}
            <p className="text-sm text-muted-foreground">
              Choose a time at least one minute ahead. Connecting an account or
              opening the review never starts an upload.
            </p>
          </div>
        </SheetContent>
      </Sheet>
    </section>
  );
}
