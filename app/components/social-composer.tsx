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
  FieldGroup,
  FieldLabel,
  FieldDescription,
  FieldSet,
  FieldLegend,
} from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";
import { Progress } from "@/components/ui/progress";
import { api, message } from "@/lib/client-api";
import { platformNames, platformRoot, type SocialPost } from "@/lib/social";
import { PublishWorkspace } from "./publish-workspace";
import { ScheduleFields } from "./schedule-fields";

type Creator = { account_id: string; label: string };
type ExportInfo = {
  duration: number;
  width: number;
  height: number;
  size_bytes: number;
};

export function SocialComposer({
  platform,
  exportId,
  accountId,
  destination,
  scheduledAt = "",
}: {
  platform: "x" | "facebook";
  exportId: string;
  accountId: string;
  destination: ReactNode;
  scheduledAt?: string;
}) {
  const root = platformRoot(platform),
    name = platformNames[platform];
  const [creator, setCreator] = useState<Creator | null>(null);
  const [media, setMedia] = useState<ExportInfo | null>(null);
  const [job, setJob] = useState<SocialPost | null>(null);
  const [caption, setCaption] = useState("");
  const [ai, setAi] = useState(false);
  const [consent, setConsent] = useState(false);
  const [scheduleTime, setScheduleTime] = useState(scheduledAt);
  const [scheduled, setScheduled] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const token = useRef<{ signature: string; id: string } | null>(null);
  const limit = platform === "x" ? 280 : 5000;

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const [profile, video, posts] = await Promise.all([
          api<Creator>(`${root}/creator?account_id=${accountId}`, {
            signal: controller.signal,
          }),
          api<ExportInfo>(`${root}/exports/${exportId}`, {
            signal: controller.signal,
          }),
          api<SocialPost[]>(`${root}/posts?export_id=${exportId}`, {
            signal: controller.signal,
          }),
        ]);
        if (controller.signal.aborted) return;
        setCreator(profile);
        setMedia(video);
        setJob(posts.find((post) => post.account_id === accountId) ?? null);
        setConsent(false);
        setError("");
      } catch (error) {
        if (!controller.signal.aborted) setError(message(error));
      }
    }
    void load();
    return () => controller.abort();
  }, [root, accountId, exportId, reload]);

  const jobId = job?.id;
  useEffect(() => {
    if (!jobId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const deadline = Date.now() + 35 * 60 * 1000;
    async function poll() {
      try {
        const value = await api<SocialPost>(`${root}/posts/${jobId}/refresh`, {
          method: "POST",
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setJob(value);
        if (["published", "failed", "unknown"].includes(value.status)) return;
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
  }, [root, jobId, reload]);

  async function publish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!creator || !media || !consent) return;
    const request = {
      export_id: exportId,
      account_id: creator.account_id,
      caption,
      is_ai_generated: ai,
      consent,
    };
    const signature = JSON.stringify({ request, scheduleTime });
    if (
      token.current?.signature !== signature ||
      (job?.status === "failed" && token.current.id === job.id)
    )
      token.current = { signature, id: crypto.randomUUID() };
    setBusy(true);
    setError("");
    try {
      const approved = { ...request, id: token.current.id };
      if (scheduleTime) {
        await api("/api/schedule", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            scheduled_at: new Date(scheduleTime).toISOString(),
            post: { platform, request: approved },
          }),
        });
        setScheduled(true);
      } else {
        setJob(
          await api<SocialPost>(`${root}/posts`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(approved),
          }),
        );
      }
      setConsent(false);
    } catch (error) {
      setError(
        `${message(error)} Check saved status after a lost connection. Unchanged settings reuse the posting token.`,
      );
    } finally {
      setBusy(false);
    }
  }

  const blocked = scheduled || (!!job && job.status !== "failed");
  const postUrl =
    job?.post_id && /^[0-9]{1,32}$/.test(job.post_id)
      ? platform === "x"
        ? `https://x.com/i/web/status/${job.post_id}`
        : `https://www.facebook.com/reel/${job.post_id}`
      : null;
  return (
    <PublishWorkspace
      title={`Post to ${name}`}
      preview={
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
            {platform === "facebook"
              ? "Facebook Page Reels are public. Supported exports: 3-90 seconds, portrait 9:16, at least 540x960, 24-60 fps, up to 1 GiB in Cliptric. Facebook sharing and remix settings apply."
              : "X videos: up to 20 minutes and 8 GiB in Cliptric, subject to account access. X validates weighted text length, including URLs and emoji. Account visibility settings apply."}
          </p>
          {media ? (
            <p>
              {media.duration.toFixed(1)} seconds · {media.width} ×{" "}
              {media.height} · {(media.size_bytes / 1_000_000).toFixed(1)} MB
            </p>
          ) : (
            <p role="status">
              {error
                ? "Export review unavailable."
                : "Loading export review..."}
            </p>
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
      {creator && media ? (
        <form onSubmit={publish}>
          <FieldSet disabled={busy || blocked}>
            <FieldLegend>Post to {creator.label}</FieldLegend>
            <FieldGroup>
              <Field>
                <SavedPostCopy
                  exportId={exportId}
                  onApply={(text) => {
                    setCaption(text);
                    setConsent(false);
                  }}
                />
                <FieldLabel htmlFor="social-caption">
                  {platform === "x" ? "Post text" : "Caption"}
                </FieldLabel>
                <Textarea
                  id="social-caption"
                  maxLength={limit}
                  value={caption}
                  onChange={(event) => {
                    setCaption(event.target.value);
                    setConsent(false);
                  }}
                />
                <FieldDescription>
                  {caption.length}/{limit} characters
                </FieldDescription>
              </Field>
              {platform === "facebook" ? (
                <Field orientation="horizontal">
                  <Checkbox
                    id="social-ai"
                    checked={ai}
                    onCheckedChange={(value) => {
                      setAi(value === true);
                      setConsent(false);
                    }}
                  />
                  <FieldLabel htmlFor="social-ai">
                    Disclose content created or edited using AI
                  </FieldLabel>
                </Field>
              ) : null}
              <ScheduleFields
                value={scheduleTime}
                onChange={(value) => {
                  setScheduleTime(value);
                  setConsent(false);
                }}
              />
              <Field orientation="horizontal">
                <Checkbox
                  id="social-consent"
                  checked={consent}
                  onCheckedChange={(value) => setConsent(value === true)}
                />
                <FieldLabel htmlFor="social-consent">
                  I reviewed this exact video, text, destination account and
                  publish time, have permission to publish it, and approve this
                  post
                  {platform === "facebook"
                    ? " publicly on this Facebook Page"
                    : ""}
                  .
                </FieldLabel>
              </Field>
              <Button type="submit" disabled={!consent || busy || blocked}>
                {busy
                  ? "Submitting..."
                  : scheduleTime
                    ? "Approve scheduled post"
                    : `Publish to ${name}`}
              </Button>
            </FieldGroup>
          </FieldSet>
        </form>
      ) : null}
      {scheduled ? (
        <p role="status">
          Post scheduled. Keep the local API running and your computer awake.{" "}
          <Link href="/calendar">View schedule</Link>.
        </p>
      ) : null}
      {job ? (
        <section
          aria-label={`${name} publishing status`}
          className="flex flex-col gap-3"
        >
          <p role="status">
            {name}: {job.status}
          </p>
          <Progress
            aria-label={`${name} upload progress`}
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
          {job.status === "published" && postUrl ? (
            <a href={postUrl} target="_blank" rel="noreferrer">
              View published post
            </a>
          ) : null}
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => setReload((value) => value + 1)}
          >
            Refresh saved status
          </Button>
        </section>
      ) : null}
    </PublishWorkspace>
  );
}
