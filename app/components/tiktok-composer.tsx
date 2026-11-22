"use client";
import { SavedPostCopy } from "./saved-post-copy";

import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { FieldSet, FieldLabel, Field, FieldGroup } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Checkbox } from "@/components/ui/checkbox";

import { useId, useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { ScheduleFields } from "./schedule-fields";
import { api, message } from "../../lib/client-api";
import {
  privacyLabels,
  type TikTokConnection,
  type TikTokCreator,
  type TikTokPost,
} from "../../lib/tiktok";

export function TikTokComposer({
  exportId,
  scheduledAt = "",
  accountId,
}: {
  exportId: string;
  accountId: string;
  scheduledAt?: string;
}) {
  const [scheduleTime, setScheduleTime] = useState(scheduledAt);
  const [scheduled, setScheduled] = useState(false);
  const fieldId = useId();
  const [connection, setConnection] = useState<TikTokConnection | null>(null);
  const [creator, setCreator] = useState<TikTokCreator | null>(null);
  const [media, setMedia] = useState<{
    duration: number;
    size_bytes: number;
    caption_warning: string | null;
  } | null>(null);
  const [job, setJob] = useState<TikTokPost | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [caption, setCaption] = useState("");
  const [privacy, setPrivacy] = useState("");
  const [commercial, setCommercial] = useState(false);
  const [yourBrand, setYourBrand] = useState(false);
  const [branded, setBranded] = useState(false);
  const [consent, setConsent] = useState(false);
  const token = useRef<{ signature: string; id: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const [status, video, posts] = await Promise.all([
          api<TikTokConnection>("/api/tiktok", { signal: controller.signal }),
          api<{
            duration: number;
            size_bytes: number;
            caption_warning: string | null;
          }>(`/api/tiktok/exports/${exportId}`, { signal: controller.signal }),
          api<TikTokPost[]>(`/api/tiktok/posts?export_id=${exportId}`, {
            signal: controller.signal,
          }),
        ]);
        if (controller.signal.aborted) return;
        setConnection(status);
        setMedia(video);
        setJob(posts.find((post) => post.account_id === accountId) ?? null);
        setCreator(null);
        setPrivacy("");
        setConsent(false);
        setError("");
        if (status.connected) {
          const profile = await api<TikTokCreator>(
            `/api/tiktok/creator?account_id=${accountId}`,
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
  const jobStatus = job?.status;
  const canReconcile = !!job?.publish_id;
  useEffect(() => {
    if (
      !jobId ||
      ["published", "failed"].includes(jobStatus ?? "") ||
      (jobStatus === "unknown" && !canReconcile)
    )
      return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const deadline = Date.now() + 10 * 60 * 1000;
    async function poll() {
      try {
        const value = await api<TikTokPost>(
          `/api/tiktok/posts/${jobId}/refresh`,
          { method: "POST", signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        setJob(value);
        setError("");
        if (["published", "failed"].includes(value.status)) return;
      } catch (error) {
        if (controller.signal.aborted) return;
        setError(message(error));
      }
      if (Date.now() < deadline) timer = setTimeout(poll, 10000);
    }
    timer = setTimeout(poll, 2000);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [jobId, jobStatus, canReconcile]);
  async function refreshPost() {
    if (!job) return;
    setBusy(true);
    setError("");
    try {
      setJob(
        await api<TikTokPost>(`/api/tiktok/posts/${job.id}/refresh`, {
          method: "POST",
        }),
      );
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  async function publish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!creator) return;
    const form = new FormData(event.currentTarget);
    const payload = {
      export_id: exportId,
      account_id: creator.account_id,
      caption,
      privacy,
      consent,
      allow_comments: form.has("comments"),
      allow_duet: form.has("duet"),
      allow_stitch: form.has("stitch"),
      commercial,
      your_brand: yourBrand,
      branded_content: branded,
      is_aigc: form.has("aigc"),
    };
    const signature = JSON.stringify({ payload, scheduleTime });
    if (token.current?.signature !== signature)
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
              platform: "tiktok",
              request: { ...payload, id: token.current.id },
            },
          }),
        });
        setScheduled(true);
        return;
      }
      setJob(
        await api<TikTokPost>("/api/tiktok/posts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...payload, id: token.current.id }),
        }),
      );
    } catch (error) {
      setError(
        `${message(error)} Refresh saved status after a lost connection. Retrying unchanged details reuses this posting token.`,
      );
    } finally {
      setBusy(false);
    }
  }
  const submitted =
    scheduled ||
    (!!job &&
      job.status !== "failed" &&
      (!creator || job.account_id === creator.account_id));
  const tooLong =
    !!creator &&
    !!media &&
    media.duration > creator.max_video_post_duration_sec;
  const publicTestAccount =
    !!creator &&
    !creator.audited &&
    creator.privacy_level_options.includes("PUBLIC_TO_EVERYONE");
  return (
    <section className="project-workspace tiktok-workspace">
      <header className="project-heading">
        <h1>Post to TikTok</h1>
        <div className="project-actions">
          <Button asChild variant="outline">
            <Link href="/projects">Your projects</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/connections">Connected accounts</Link>
          </Button>
        </div>
      </header>
      {error ? (
        <Alert role="alert" variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {media ? (
        <>
          <video
            className="tiktok-preview"
            controls
            preload="metadata"
            src={`/api/clips/${exportId}/media`}
          />
          <p>
            {media.duration.toFixed(1)} seconds ·{" "}
            {(media.size_bytes / 1024 ** 2).toFixed(1)} MiB
          </p>
        </>
      ) : (
        <p role="status">Loading saved export…</p>
      )}
      {job ? (
        <>
          {creator && job.account_id !== creator.account_id ? (
            <p>
              This saved posting attempt belongs to a previously connected
              account.
            </p>
          ) : null}
          <section
            className="tiktok-post-status"
            aria-label="Saved TikTok post"
          >
            <p role="status">
              <strong>{job.status}</strong>
              {job.status === "uploading"
                ? `: ${Math.round((job.uploaded_bytes / job.total_bytes) * 100)}% uploaded`
                : ""}
            </p>
            {job.error ? <p>{job.error}</p> : null}
            {job.status === "published" ? (
              <p>
                TikTok confirmed publication. Visibility depends on your privacy
                choice and TikTok processing.
              </p>
            ) : null}
            {job.post_ids.map((id) => (
              <p key={id}>TikTok post ID: {id}</p>
            ))}
            <div className="project-actions">
              <Button disabled={busy} onClick={refreshPost} variant="outline">
                Refresh saved status
              </Button>
              {job.status === "failed" ? (
                <Button
                  disabled={busy}
                  onClick={() => {
                    setJob(null);
                    token.current = null;
                    setConsent(false);
                  }}
                  variant="outline"
                >
                  Prepare another attempt
                </Button>
              ) : null}
            </div>
            {job.status === "unknown" ? (
              <p>
                Do not repost until the outcome is checked on TikTok. Refreshing
                checks this attempt and never uploads the video again.
              </p>
            ) : null}
          </section>
        </>
      ) : null}
      {media?.caption_warning ? (
        <Alert role="alert" variant="destructive">
          <AlertDescription>
            Caption review needed: {media.caption_warning} Review this clip
            before approving a TikTok post.
          </AlertDescription>
        </Alert>
      ) : null}
      {connection && !connection.connected ? (
        <p>
          <Link href="/connections">Connect TikTok</Link> before posting this
          clip.
        </p>
      ) : null}
      {creator ? (
        <>
          <h2>Posting as {creator.creator_nickname}</h2>
          <p>@{creator.creator_username}</p>
          {!creator.audited ? (
            <p>
              Test mode supports Only me visibility on a private TikTok account.
            </p>
          ) : null}
          {publicTestAccount ? (
            <Alert role="alert" variant="destructive">
              <AlertDescription>
                This account is public. TikTok requires a private account for
                testing an unaudited app.
              </AlertDescription>
            </Alert>
          ) : null}
          {tooLong ? (
            <Alert role="alert" variant="destructive">
              <AlertDescription>
                Trim this clip to at most {creator.max_video_post_duration_sec}{" "}
                seconds for this account.
              </AlertDescription>
            </Alert>
          ) : null}
          {scheduled ? (
            <Alert>
              <AlertDescription>
                Post scheduled. <Link href="/calendar">View calendar</Link> to
                check or cancel it.
              </AlertDescription>
            </Alert>
          ) : null}
          <form
            onSubmit={publish}
            onChange={(event) => {
              if (
                !(event.target instanceof HTMLInputElement) ||
                event.target.name !== "consent"
              )
                setConsent(false);
            }}
          >
            <FieldSet
              disabled={busy || submitted || tooLong || publicTestAccount}
            >
              <Field>
                <SavedPostCopy
                  exportId={exportId}
                  onApply={(text) => {
                    setCaption(text);
                    setConsent(false);
                  }}
                />
                <FieldLabel htmlFor={`${fieldId}-1`}>Post caption</FieldLabel>
                <Textarea
                  id={`${fieldId}-1`}
                  name="caption"
                  maxLength={2200}
                  rows={4}
                  value={caption}
                  onChange={(e) => setCaption(e.target.value)}
                ></Textarea>
              </Field>
              <Field>
                <FieldLabel htmlFor={`${fieldId}-2`}>Who can watch</FieldLabel>
                <NativeSelect
                  id={`${fieldId}-2`}
                  required
                  name="privacy"
                  value={privacy}
                  onChange={(e) => setPrivacy(e.target.value)}
                  className="w-full"
                >
                  <NativeSelectOption value="" disabled>
                    Choose visibility
                  </NativeSelectOption>
                  {creator.privacy_level_options.map((value) => (
                    <NativeSelectOption
                      key={value}
                      value={value}
                      disabled={
                        (!creator.audited && value !== "SELF_ONLY") ||
                        (branded && value === "SELF_ONLY")
                      }
                    >
                      {privacyLabels[value] ?? value}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </Field>
              <FieldGroup className="project-fields grid gap-4 sm:grid-cols-2">
                {(
                  [
                    ["comments", "Allow comments", creator.comment_disabled],
                    ["duet", "Allow Duet", creator.duet_disabled],
                    ["stitch", "Allow Stitch", creator.stitch_disabled],
                  ] as const
                ).map(([name, label, disabled]) => (
                  <Field
                    className="project-check"
                    key={name}
                    orientation="horizontal"
                  >
                    <Checkbox
                      id={`${fieldId}-3-${name}`}
                      name={name}
                      disabled={disabled}
                    />
                    <FieldLabel htmlFor={`${fieldId}-3-${name}`}>
                      {label}
                      {disabled ? " (disabled by this account)" : ""}
                    </FieldLabel>
                  </Field>
                ))}
              </FieldGroup>
              <Field className="project-check" orientation="horizontal">
                <Checkbox
                  id={`${fieldId}-4`}
                  name="commercial"
                  checked={commercial}
                  onCheckedChange={(checked) => {
                    setCommercial(checked === true);
                    if (!(checked === true)) {
                      setYourBrand(false);
                      setBranded(false);
                    }
                  }}
                />
                <FieldLabel htmlFor={`${fieldId}-4`}>
                  Disclose commercial content
                </FieldLabel>
              </Field>
              {commercial ? (
                <>
                  <Field className="project-check" orientation="horizontal">
                    <Checkbox
                      id={`${fieldId}-5`}
                      name="your_brand"
                      checked={yourBrand}
                      onCheckedChange={(checked) =>
                        setYourBrand(checked === true)
                      }
                    />
                    <FieldLabel htmlFor={`${fieldId}-5`}>Your brand</FieldLabel>
                  </Field>
                  <Field className="project-check" orientation="horizontal">
                    <Checkbox
                      id={`${fieldId}-6`}
                      name="branded"
                      checked={branded}
                      disabled={privacy === "SELF_ONLY"}
                      onCheckedChange={(checked) =>
                        setBranded(checked === true)
                      }
                    />
                    <FieldLabel htmlFor={`${fieldId}-6`}>
                      Branded content (requires friends/public visibility)
                    </FieldLabel>
                  </Field>
                  {yourBrand || branded ? (
                    <p>
                      Your video will be labelled{" "}
                      {branded ? "Paid partnership" : "Promotional content"}.
                    </p>
                  ) : (
                    <p>Select your brand, branded content, or both.</p>
                  )}
                </>
              ) : null}
              <Field className="project-check" orientation="horizontal">
                <Checkbox id={`${fieldId}-7`} name="aigc" />
                <FieldLabel htmlFor={`${fieldId}-7`}>
                  Label AI-generated content
                </FieldLabel>
              </Field>
              <p>
                By posting, you agree to TikTok&apos;s{" "}
                {branded ? (
                  <>
                    <a
                      href="https://www.tiktok.com/legal/page/global/bc-policy/en"
                      target="_blank"
                      rel="noreferrer"
                    >
                      Branded Content Policy
                    </a>{" "}
                    and{" "}
                  </>
                ) : null}
                <a
                  href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en"
                  target="_blank"
                  rel="noreferrer"
                >
                  Music Usage Confirmation
                </a>
                .
              </p>
              <Field className="project-check" orientation="horizontal">
                <Checkbox
                  id={`${fieldId}-8`}
                  required
                  name="consent"
                  checked={consent}
                  onCheckedChange={(checked) => setConsent(checked === true)}
                />
                <FieldLabel htmlFor={`${fieldId}-8`}>
                  I approve sending this exact video and these settings to this
                  TikTok account, and have permission to share it.
                </FieldLabel>
              </Field>
              <ScheduleFields
                value={scheduleTime}
                onChange={(value) => {
                  setScheduleTime(value);
                  setConsent(false);
                }}
              />
              <Button
                type="submit"
                disabled={
                  !consent || !privacy || (commercial && !yourBrand && !branded)
                }
                variant="default"
              >
                {busy
                  ? "Saving..."
                  : scheduleTime
                    ? "Approve and schedule post"
                    : "Approve and post to TikTok"}
              </Button>
            </FieldSet>
          </form>
          <p>
            TikTok may take a few minutes to process the video. You can leave
            this page while the local API is running.
          </p>
        </>
      ) : null}
      <Button
        disabled={busy}
        onClick={() => setReload((value) => value + 1)}
        variant="outline"
      >
        Refresh account and saved status
      </Button>
    </section>
  );
}
