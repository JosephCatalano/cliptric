"use client";
import { SavedPostCopy } from "./saved-post-copy";

import type { ReactNode } from "react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
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
import { Textarea } from "@/components/ui/textarea";
import { Progress } from "@/components/ui/progress";
import { PublishWorkspace } from "./publish-workspace";
import { ScheduleFields } from "./schedule-fields";
import { api, message } from "@/lib/client-api";
import type {
  InstagramConnection,
  InstagramCreator,
  InstagramExport,
  InstagramPost,
} from "@/lib/instagram";

const root = "/api/instagram";
const terminal = new Set(["published", "failed", "unknown"]);

export function InstagramComposer({
  exportId,
  accountId,
  destination,
  scheduledAt = "",
}: {
  exportId: string;
  accountId: string;
  destination: ReactNode;
  scheduledAt?: string;
}) {
  const [scheduleTime, setScheduleTime] = useState(scheduledAt);
  const [scheduled, setScheduled] = useState(false);
  const [connection, setConnection] = useState<InstagramConnection | null>(
    null,
  );
  const [creator, setCreator] = useState<InstagramCreator | null>(null);
  const [media, setMedia] = useState<InstagramExport | null>(null);
  const [job, setJob] = useState<InstagramPost | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [caption, setCaption] = useState("");
  const [feed, setFeed] = useState(false);
  const [consent, setConsent] = useState(false);
  const token = useRef<{ signature: string; id: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const [status, video, posts] = await Promise.all([
          api<InstagramConnection>(root, { signal: controller.signal }),
          api<InstagramExport>(`${root}/exports/${exportId}`, {
            signal: controller.signal,
          }),
          api<InstagramPost[]>(`${root}/posts?export_id=${exportId}`, {
            signal: controller.signal,
          }),
        ]);
        if (controller.signal.aborted) return;
        setConnection(status);
        setMedia(video);
        setJob(posts.find((post) => post.account_id === accountId) ?? null);
        setCreator(null);
        setConsent(false);
        setError("");
        if (status.connected) {
          const profile = await api<InstagramCreator>(
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
    const deadline = Date.now() + 35 * 60 * 1000;
    async function poll() {
      try {
        const value = await api<InstagramPost>(
          `${root}/posts/${jobId}/refresh`,
          { method: "POST", signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        setJob(value);
        if (terminal.has(value.status)) return;
        if (Date.now() < deadline) timer = setTimeout(poll, 5000);
      } catch (error) {
        if (!controller.signal.aborted) setError(message(error));
      }
    }
    timer = setTimeout(poll, 2000);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [jobId, reload]);
  async function refresh() {
    setBusy(true);
    setError("");
    try {
      if (job)
        setJob(
          await api<InstagramPost>(`${root}/posts/${job.id}/refresh`, {
            method: "POST",
          }),
        );
      setReload((value) => value + 1);
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  async function publish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!creator || !consent) return;
    const payload = {
      export_id: exportId,
      account_id: creator.account_id,
      caption,
      share_to_feed: feed,
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
              platform: "instagram",
              request: { ...payload, id: token.current.id },
            },
          }),
        });
        setScheduled(true);
      } else {
        setJob(
          await api<InstagramPost>(`${root}/posts`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...payload, id: token.current.id }),
          }),
        );
      }
      setConsent(false);
    } catch (error) {
      setError(
        `${message(error)} Refresh saved status after a lost connection. Unchanged details reuse the same posting token.`,
      );
    } finally {
      setBusy(false);
    }
  }
  const blocked =
    scheduled ||
    (!!job &&
      job.status !== "failed" &&
      (!creator || job.account_id === creator.account_id));
  return (
    <PublishWorkspace
      title="Upload Instagram Reel"
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
                {media.height} · {(media.size_bytes / 1_000_000).toFixed(1)} MB
              </p>
              {media.caption_warning ? (
                <Alert>
                  <AlertDescription>{media.caption_warning}</AlertDescription>
                </Alert>
              ) : null}
            </>
          ) : (
            <p role="status">
              {error ? "Export review is unavailable." : "Loading export..."}
            </p>
          )}
        </>
      }
    >
      {destination}
      {error ? (
        <Alert role="alert" variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {connection && !connection.connected ? (
        <p>
          <Link href="/connections">Connect Instagram</Link> before publishing.
        </p>
      ) : null}
      {creator ? (
        <form onSubmit={publish}>
          <FieldSet disabled={busy || blocked || !media}>
            <FieldLegend>Post to @{creator.username}</FieldLegend>
            <FieldDescription>
              Instagram account privacy and remix settings apply. Review the
              complete video before approving publication.
            </FieldDescription>
            <FieldGroup>
              <ScheduleFields
                value={scheduleTime}
                onChange={(value) => {
                  setScheduleTime(value);
                  setConsent(false);
                }}
              />
              <SavedPostCopy
                exportId={exportId}
                onApply={(text) => {
                  setCaption(text);
                  setConsent(false);
                }}
              />
              <Field>
                <FieldLabel htmlFor="instagram-caption">Caption</FieldLabel>
                <Textarea
                  id="instagram-caption"
                  name="caption"
                  maxLength={2200}
                  value={caption}
                  onChange={(event) => {
                    setCaption(event.target.value);
                    setConsent(false);
                  }}
                />
                <FieldDescription>
                  {caption.length}/2200 characters
                </FieldDescription>
              </Field>
              <Field orientation="horizontal">
                <Checkbox
                  id="instagram-feed"
                  checked={feed}
                  onCheckedChange={(value) => {
                    setFeed(value === true);
                    setConsent(false);
                  }}
                />
                <FieldLabel htmlFor="instagram-feed">
                  Also share to feed
                </FieldLabel>
              </Field>
              <Field orientation="horizontal">
                <Checkbox
                  id="instagram-consent"
                  checked={consent}
                  onCheckedChange={(value) => setConsent(value === true)}
                />
                <FieldLabel htmlFor="instagram-consent">
                  I reviewed this exact video, caption and destination account,
                  have permission to publish it, and approve posting this Reel.
                </FieldLabel>
              </Field>
              <Button
                type="submit"
                disabled={!consent || busy || blocked || !media}
              >
                {busy
                  ? "Submitting..."
                  : scheduleTime
                    ? "Approve scheduled Reel"
                    : "Publish Instagram Reel"}
              </Button>
            </FieldGroup>
          </FieldSet>
        </form>
      ) : null}
      {scheduled ? (
        <p role="status">
          Instagram Reel scheduled. Keep the local API running and your computer
          awake. <Link href="/calendar">View schedule</Link>.
        </p>
      ) : null}
      {job ? (
        <section
          aria-label="Instagram posting status"
          className="flex flex-col gap-4"
        >
          <p role="status">Instagram: {job.status}</p>
          <Progress
            aria-label="Instagram upload progress"
            value={
              job.total_bytes ? (job.uploaded_bytes / job.total_bytes) * 100 : 0
            }
          />
          <p>
            {(job.uploaded_bytes / 1_000_000).toFixed(1)} /{" "}
            {(job.total_bytes / 1_000_000).toFixed(1)} MB uploaded
          </p>
          {job.error ? (
            <Alert>
              <AlertDescription>{job.error}</AlertDescription>
            </Alert>
          ) : null}
          {job.status === "published" ? (
            <p>
              Published to Instagram.{" "}
              {job.media_id
                ? `Media ID: ${job.media_id}`
                : "Check your Instagram profile to view the Reel."}
            </p>
          ) : null}
          {job.status === "unknown" ? (
            <p>
              The outcome is uncertain. Check Instagram before trying again;
              this attempt will not publish automatically.
            </p>
          ) : null}
        </section>
      ) : null}
      <Button variant="outline" disabled={busy} onClick={() => void refresh()}>
        Refresh saved status
      </Button>
    </PublishWorkspace>
  );
}
