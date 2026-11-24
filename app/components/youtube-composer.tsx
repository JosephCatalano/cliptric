"use client";
import type { ReactNode } from "react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { PublishWorkspace } from "./publish-workspace";
import { ScheduleFields } from "./schedule-fields";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { api, message } from "@/lib/client-api";
import {
  youtubeDefaults,
  youtubeStatus,
  type YouTubeConnection,
  type YouTubeCreator,
  type YouTubeExport,
  type YouTubePost,
} from "@/lib/youtube-publishing";

const root = "/api/youtube-publishing";

export function YouTubeComposer({
  exportId,
  scheduledAt = "",
  accountId,
  destination,
}: {
  exportId: string;
  accountId: string;
  destination: ReactNode;
  scheduledAt?: string;
}) {
  const [scheduleTime, setScheduleTime] = useState(scheduledAt);
  const [scheduled, setScheduled] = useState(false);
  const [connection, setConnection] = useState<YouTubeConnection | null>(null);
  const [creator, setCreator] = useState<YouTubeCreator | null>(null);
  const [media, setMedia] = useState<YouTubeExport | null>(null);
  const [job, setJob] = useState<YouTubePost | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [title, setTitle] = useState("");
  const [descriptionDraft, setDescription] = useState<string | null>(null);
  const [savedDescription, setSavedDescription] = useState("");
  const [copyError, setCopyError] = useState("");
  const description = descriptionDraft ?? savedDescription;
  const [categorySelection, setCategory] = useState<string | null>(null);
  const category =
    categorySelection ??
    (creator?.categories.some((item) => item.id === youtubeDefaults.categoryId)
      ? youtubeDefaults.categoryId
      : "");
  const [privacy, setPrivacy] = useState(youtubeDefaults.privacy);
  const [kids, setKids] = useState(youtubeDefaults.kids);
  const [synthetic, setSynthetic] = useState(youtubeDefaults.synthetic);
  const [notify, setNotify] = useState(youtubeDefaults.notify);
  const [consent, setConsent] = useState(false);
  const token = useRef<{ signature: string; id: string } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const [status, video, posts, copy] = await Promise.all([
          api<YouTubeConnection>(root, { signal: controller.signal }),
          api<YouTubeExport>(`${root}/exports/${exportId}`, {
            signal: controller.signal,
          }),
          api<YouTubePost[]>(`${root}/posts?export_id=${exportId}`, {
            signal: controller.signal,
          }),
          api<{ ready_to_post: string | null }>(
            `/api/clips/${exportId}/post-copy`,
            {
              signal: controller.signal,
            },
          )
            .then((value) => ({ text: value.ready_to_post ?? "", error: "" }))
            .catch((error) => ({ text: "", error: message(error) })),
        ]);
        if (controller.signal.aborted) return;
        setConnection(status);
        setMedia(video);
        setJob(posts.find((post) => post.account_id === accountId) ?? null);
        setCreator(null);
        setConsent(false);
        setSavedDescription(copy.text);
        setCopyError(copy.error);
        setError("");
        if (status.connected) {
          const profile = await api<YouTubeCreator>(
            `${root}/creator?account_id=${accountId}`,
            {
              signal: controller.signal,
            },
          );
          if (!controller.signal.aborted) setCreator(profile);
        }
      } catch (error) {
        if (!controller.signal.aborted) setError(message(error));
      }
    }
    void load();
    return () => controller.abort();
  }, [exportId, reload, accountId]);

  const jobId = job?.id;
  useEffect(() => {
    if (!jobId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const deadline = Date.now() + 10 * 60 * 1000;
    async function poll() {
      try {
        const value = await api<YouTubePost>(`${root}/posts/${jobId}/refresh`, {
          method: "POST",
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setJob(value);
        if (["ready", "failed", "paused", "unknown"].includes(value.status))
          return;
      } catch (error) {
        if (controller.signal.aborted) return;
        setError(message(error));
        return;
      }
      if (Date.now() < deadline) timer = setTimeout(poll, 10000);
    }
    timer = setTimeout(poll, 2000);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [jobId, reload]);

  async function updatePost(action: "refresh" | "resume") {
    if (!job) return;
    setBusy(true);
    setError("");
    try {
      setJob(
        await api<YouTubePost>(`${root}/posts/${job.id}/${action}`, {
          method: "POST",
        }),
      );
      if (action === "resume") setReload((value) => value + 1);
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }

  async function publish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!creator || !consent || !kids || !synthetic) return;
    const payload = {
      export_id: exportId,
      account_id: creator.account_id,
      title,
      description,
      category_id: category,
      privacy,
      made_for_kids: kids === "yes",
      contains_synthetic_media: synthetic === "yes",
      notify_subscribers: notify,
      consent,
    };
    const signature = JSON.stringify({ payload, scheduleTime });
    if (
      token.current?.signature !== signature ||
      (job?.status === "failed" && token.current?.id === job.id)
    )
      token.current = { signature, id: crypto.randomUUID() };
    setBusy(true);
    setError("");
    try {
      if (scheduleTime) {
        await api("/api/schedule", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            scheduled_at: new Date(scheduleTime).toISOString(),
            post: {
              platform: "youtube",
              request: { ...payload, id: token.current.id },
            },
          }),
        });
        setScheduled(true);
        return;
      }
      setJob(
        await api<YouTubePost>(`${root}/posts`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...payload, id: token.current.id }),
        }),
      );
    } catch (error) {
      setError(
        `${message(error)} Refresh saved status after a lost connection. Retrying unchanged details reuses this upload token.`,
      );
    } finally {
      setBusy(false);
    }
  }

  const belongsToAccount = job?.account_id === creator?.account_id;
  const submitted =
    scheduled ||
    (!!job && job.status !== "failed" && (!creator || belongsToAccount));
  const descriptionBytes = new TextEncoder().encode(description).length;
  return (
    <PublishWorkspace
      title="Upload YouTube Short"
      preview={
        <>
          {media ? (
            <>
              <video
                className="tiktok-preview"
                controls
                playsInline
                aria-label="Saved clip preview"
                preload="metadata"
                src={`/api/clips/${exportId}/media`}
              />
              <p>
                {media.duration.toFixed(1)} seconds · {media.width} ×{" "}
                {media.height} · {(media.size_bytes / 1024 ** 2).toFixed(1)} MiB
              </p>
              {media.caption_warning ? (
                <Alert>
                  <AlertDescription>
                    {media.caption_warning} Review the rendered captions before
                    uploading.
                  </AlertDescription>
                </Alert>
              ) : null}
            </>
          ) : (
            <p role="status">Loading saved export...</p>
          )}
        </>
      }
    >
      {destination}
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {scheduled ? (
        <Alert>
          <AlertDescription>
            Post scheduled. <Link href="/calendar">View calendar</Link> to check
            or cancel it.
          </AlertDescription>
        </Alert>
      ) : null}
      {job ? (
        <section
          aria-label="Saved YouTube upload"
          className="flex flex-col gap-4"
        >
          <h2>{youtubeStatus[job.status]}</h2>
          <p>
            {job.request.title} · {job.channel.title} · Requested visibility:{" "}
            {job.request.privacy}
          </p>
          <p role="status">
            YouTube confirmed{" "}
            {((job.uploaded_bytes / job.total_bytes) * 100).toFixed(0)}% of the
            file.
            {job.actual_privacy
              ? ` Last confirmed visibility: ${job.actual_privacy}.`
              : ""}
          </p>
          {job.error ? (
            <Alert>
              <AlertDescription>{job.error}</AlertDescription>
            </Alert>
          ) : null}
          {creator && !belongsToAccount ? (
            <p>This saved attempt belongs to a previously connected channel.</p>
          ) : null}
          {job.video_id ? (
            <Button asChild variant="outline">
              <a
                href={`https://www.youtube.com/watch?v=${job.video_id}`}
                target="_blank"
                rel="noreferrer"
              >
                Open video on YouTube
              </a>
            </Button>
          ) : null}
          <div className="project-actions">
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void updatePost("refresh")}
            >
              Check upload status
            </Button>
            {job.can_resume && ["paused", "unknown"].includes(job.status) ? (
              <Button
                disabled={busy || !belongsToAccount}
                onClick={() => void updatePost("resume")}
              >
                Resume approved upload
              </Button>
            ) : null}
          </div>
        </section>
      ) : null}
      {!connection?.connected ? (
        <p>
          <Link href="/publishing/accounts">Connect YouTube</Link> to upload this
          finished clip.
        </p>
      ) : null}
      {creator && media && !submitted ? (
        <form onSubmit={publish}>
          <FieldSet disabled={busy} onChange={() => setConsent(false)}>
            <FieldLegend>Upload to {creator.channel.title}</FieldLegend>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="short-title">Title</FieldLabel>
                <Input
                  id="short-title"
                  required
                  maxLength={100}
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                />
              </Field>
              <Field data-invalid={descriptionBytes > 5000}>
                <FieldLabel htmlFor="short-description">
                  Description and hashtags
                </FieldLabel>
                <Textarea
                  id="short-description"
                  maxLength={5000}
                  value={description}
                  aria-invalid={descriptionBytes > 5000}
                  onChange={(event) => setDescription(event.target.value)}
                />
                <FieldDescription>
                  {copyError
                    ? `Saved posting copy could not load. ${copyError} `
                    : ""}
                  {descriptionBytes}/5000 UTF-8 bytes
                </FieldDescription>
              </Field>
              <FieldSet className="rounded-lg border p-3">
                <FieldLegend>Platform settings</FieldLegend>
                <FieldGroup className="grid items-start gap-4 sm:grid-cols-2">
                  <Field>
                    <FieldLabel htmlFor="short-category">Category</FieldLabel>
                    <NativeSelect
                      id="short-category"
                      required
                      value={category}
                      onChange={(event) => setCategory(event.target.value)}
                    >
                      <NativeSelectOption value="" disabled>
                        Choose category
                      </NativeSelectOption>
                      {creator.categories.map((item) => (
                        <NativeSelectOption key={item.id} value={item.id}>
                          {item.title}
                        </NativeSelectOption>
                      ))}
                    </NativeSelect>
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="short-privacy">Visibility</FieldLabel>
                    <NativeSelect
                      id="short-privacy"
                      required
                      value={privacy}
                      onChange={(event) => setPrivacy(event.target.value)}
                    >
                      <NativeSelectOption value="" disabled>
                        Choose visibility
                      </NativeSelectOption>
                      <NativeSelectOption value="private">
                        Private
                      </NativeSelectOption>
                      <NativeSelectOption value="unlisted">
                        Unlisted
                      </NativeSelectOption>
                      <NativeSelectOption value="public">
                        Public
                      </NativeSelectOption>
                    </NativeSelect>
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="short-kids">
                      Is this video made for kids?
                    </FieldLabel>
                    <NativeSelect
                      id="short-kids"
                      required
                      value={kids}
                      onChange={(event) => setKids(event.target.value)}
                    >
                      <NativeSelectOption value="" disabled>
                        Choose audience
                      </NativeSelectOption>
                      <NativeSelectOption value="yes">
                        Yes, made for kids
                      </NativeSelectOption>
                      <NativeSelectOption value="no">
                        No, not made for kids
                      </NativeSelectOption>
                    </NativeSelect>
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="short-synthetic">
                      Realistic altered or synthetic content?
                    </FieldLabel>
                    <NativeSelect
                      id="short-synthetic"
                      required
                      value={synthetic}
                      onChange={(event) => setSynthetic(event.target.value)}
                    >
                      <NativeSelectOption value="" disabled>
                        Choose disclosure
                      </NativeSelectOption>
                      <NativeSelectOption value="yes">Yes</NativeSelectOption>
                      <NativeSelectOption value="no">No</NativeSelectOption>
                    </NativeSelect>
                    <FieldDescription>
                      Disclose realistic altered/generated scenes or speech, and
                      AI-generated music. Automatic captions alone do not
                      require this label. See YouTube&apos;s{" "}
                      <a
                        href="https://support.google.com/youtube/answer/14328491"
                        target="_blank"
                        rel="noreferrer"
                      >
                        disclosure guidance
                      </a>
                      .
                    </FieldDescription>
                  </Field>
                  <Field orientation="horizontal" className="sm:col-span-2">
                    <Checkbox
                      id="short-notify"
                      checked={notify}
                      onCheckedChange={(value) => {
                        setNotify(value === true);
                        setConsent(false);
                      }}
                    />
                    <FieldLabel htmlFor="short-notify">
                      Notify channel subscribers
                    </FieldLabel>
                  </Field>
                </FieldGroup>
              </FieldSet>
            </FieldGroup>
          </FieldSet>
          <FieldSet disabled={busy} className="mt-5 rounded-lg border p-4">
            <FieldLegend>Review and approve</FieldLegend>
            <ScheduleFields
              value={scheduleTime}
              onChange={(value) => {
                setScheduleTime(value);
                setConsent(false);
              }}
            />
            <Field orientation="horizontal">
              <Checkbox
                id="short-consent"
                checked={consent}
                onCheckedChange={(value) => setConsent(value === true)}
              />
              <FieldLabel htmlFor="short-consent">
                I reviewed this exact video and approve sending it with these
                settings to {creator.channel.title}.
              </FieldLabel>
            </Field>
            <Button
              type="submit"
              disabled={
                busy ||
                !consent ||
                !title.trim() ||
                !privacy ||
                !category ||
                !kids ||
                !synthetic ||
                descriptionBytes > 5000
              }
            >
              {busy
                ? "Saving..."
                : scheduleTime
                  ? "Approve and schedule post"
                  : "Approve and upload to YouTube"}
            </Button>
          </FieldSet>
        </form>
      ) : null}
      <p className="text-sm text-muted-foreground">
        YouTube classifies square or vertical videos up to three minutes as
        Shorts. Upload completion does not guarantee public availability or
        Shorts classification. Processing can continue while the local API is
        running.
      </p>
      <Button
        variant="ghost"
        size="sm"
        className="self-start"
        disabled={busy}
        onClick={() => setReload((value) => value + 1)}
      >
        Refresh account and saved uploads
      </Button>
    </PublishWorkspace>
  );
}
