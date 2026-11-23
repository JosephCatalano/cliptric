"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
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
import type { ProjectItem } from "@/lib/projects";
import {
  youtubeDefaults,
  type YouTubeConnection,
  type YouTubeCreator,
  type YouTubePost,
} from "@/lib/youtube-publishing";

const root = "/api/youtube-publishing";

export function YouTubeBatchUpload({
  items,
  disabled,
}: {
  items: ProjectItem[];
  disabled: boolean;
}) {
  const [review, setReview] = useState<ProjectItem[] | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open={review !== null}
      onOpenChange={(open) => {
        if (!busy) setReview(open ? items : null);
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" disabled={disabled || !items.length}>
          Upload selected to YouTube ({items.length})
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Review YouTube uploads</DialogTitle>
          <DialogDescription>
            Review each video and its text. Approved videos upload one at a
            time.
          </DialogDescription>
        </DialogHeader>
        {review ? (
          <BatchReview items={review} busy={busy} onBusy={setBusy} />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function BatchReview({
  items,
  busy,
  onBusy,
}: {
  items: ProjectItem[];
  busy: boolean;
  onBusy: (busy: boolean) => void;
}) {
  const [drafts, setDrafts] = useState(() =>
    items.map((item) => ({
      exportId: item.export_id!,
      title: Array.from(item.title).slice(0, 100).join(""),
      description: "",
      warning: item.transcription_warning,
    })),
  );
  const [accounts, setAccounts] = useState<
    YouTubeConnection["accounts"] | null
  >(null);
  const [accountId, setAccountId] = useState("");
  const [profile, setProfile] = useState<YouTubeCreator | null>(null);
  const [loadedCopy, setLoadedCopy] = useState(false);
  const [settings, setSettings] = useState(youtubeDefaults);
  const [approved, setApproved] = useState(false);
  const [error, setError] = useState("");
  const [profileError, setProfileError] = useState("");
  const [posts, setPosts] = useState<YouTubePost[]>([]);
  const attempt = useRef<{ key: string; ids: string[] } | null>(null);
  const creator = profile?.account_id === accountId ? profile : null;
  const category = creator?.categories.some(
    (option) => option.id === settings.categoryId,
  )
    ? settings.categoryId
    : "";

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      const status = await api<YouTubeConnection>(root, {
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setAccounts(status.accounts);
      setAccountId(status.account_id ?? "");
      const descriptions: string[] = [];
      // Bound parallel lookups so selecting a complete project stays responsive.
      for (let index = 0; index < items.length; index += 8) {
        const group = await Promise.all(
          items
            .slice(index, index + 8)
            .map((item) =>
              api<{ ready_to_post: string | null }>(
                `/api/clips/${item.export_id}/post-copy`,
                { signal: controller.signal },
              ),
            ),
        );
        descriptions.push(...group.map((copy) => copy.ready_to_post ?? ""));
      }
      if (!controller.signal.aborted) {
        setDrafts((previous) =>
          previous.map((draft, index) => ({
            ...draft,
            description: descriptions[index],
          })),
        );
        setLoadedCopy(true);
      }
    }
    void load().catch((failure) => {
      if (!controller.signal.aborted) setError(message(failure));
    });
    return () => controller.abort();
  }, [items]);

  useEffect(() => {
    if (!accountId) return;
    const controller = new AbortController();
    api<YouTubeCreator>(
      `${root}/creator?account_id=${encodeURIComponent(accountId)}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setProfile(value);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setProfileError(message(failure));
      });
    return () => controller.abort();
  }, [accountId]);

  function updateDraft(
    index: number,
    field: "description" | "title",
    value: string,
  ) {
    setDrafts((previous) =>
      previous.map((draft, position) =>
        position === index ? { ...draft, [field]: value } : draft,
      ),
    );
    setApproved(false);
  }

  function updateSetting(key: keyof typeof settings, value: string | boolean) {
    setSettings((previous) => ({ ...previous, [key]: value }));
    setApproved(false);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!creator || busy || !approved || !category || !loadedCopy) return;
    const uploads = drafts.map((draft) => ({
      export_id: draft.exportId,
      account_id: creator.account_id,
      title: draft.title.trim(),
      description: draft.description,
      category_id: category,
      privacy: settings.privacy,
      made_for_kids: settings.kids === "yes",
      contains_synthetic_media: settings.synthetic === "yes",
      notify_subscribers: settings.notify,
      consent: true,
    }));
    const key = JSON.stringify(uploads);
    if (attempt.current?.key !== key)
      attempt.current = { key, ids: uploads.map(() => crypto.randomUUID()) };
    const ids = attempt.current.ids;
    onBusy(true);
    setError("");
    try {
      setPosts(
        await api<YouTubePost[]>(
          `${root}/posts/batch`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              uploads: uploads.map((upload, index) => ({
                ...upload,
                id: ids[index],
              })),
            }),
          },
          300_000,
        ),
      );
    } catch (failure) {
      setError(
        `${message(failure)} If the connection dropped, retry the same details to check the original approval.`,
      );
    } finally {
      onBusy(false);
    }
  }

  if (posts.length)
    return (
      <FieldGroup>
        <Alert>
          <AlertDescription>
            {posts.length} uploads accepted. Cliptric sends them in order while
            the backend is running.
          </AlertDescription>
        </Alert>
        <ul className="flex flex-col gap-2">
          {posts.map((post) => (
            <li key={post.id}>
              <Link
                className="underline underline-offset-4"
                href={`/publish/youtube/${post.export_id}?account_id=${post.account_id}`}
              >
                {post.request.title}
              </Link>
            </li>
          ))}
        </ul>
      </FieldGroup>
    );

  return (
    <form onSubmit={submit}>
      <FieldSet disabled={busy}>
        <FieldLegend>Upload settings for all selected videos</FieldLegend>
        <FieldGroup>
          {error || profileError ? (
            <Alert variant="destructive">
              <AlertDescription>{error || profileError}</AlertDescription>
            </Alert>
          ) : null}
          {accounts?.length === 0 ? (
            <p>
              <Link className="underline" href="/connections">
                Connect a YouTube channel
              </Link>{" "}
              to continue.
            </p>
          ) : null}
          <Field>
            <FieldLabel htmlFor="batch-youtube-channel">Channel</FieldLabel>
            <NativeSelect
              id="batch-youtube-channel"
              value={accountId}
              required
              disabled={!accounts?.length}
              onChange={(event) => {
                setAccountId(event.target.value);
                setProfileError("");
                setApproved(false);
              }}
            >
              <NativeSelectOption value="" disabled>
                {accounts ? "Choose a channel" : "Loading channels…"}
              </NativeSelectOption>
              {accounts?.map((account) => (
                <NativeSelectOption
                  key={account.account_id}
                  value={account.account_id}
                >
                  {account.label}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>
          <FieldGroup className="grid gap-4 sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor="batch-youtube-category">Category</FieldLabel>
              <NativeSelect
                id="batch-youtube-category"
                value={category}
                required
                disabled={!creator}
                onChange={(event) =>
                  updateSetting("categoryId", event.target.value)
                }
              >
                <NativeSelectOption value="" disabled>
                  Choose category
                </NativeSelectOption>
                {creator?.categories.map((option) => (
                  <NativeSelectOption key={option.id} value={option.id}>
                    {option.title}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </Field>
            <Field>
              <FieldLabel htmlFor="batch-youtube-privacy">
                Visibility
              </FieldLabel>
              <NativeSelect
                id="batch-youtube-privacy"
                value={settings.privacy}
                onChange={(event) =>
                  updateSetting("privacy", event.target.value)
                }
              >
                <NativeSelectOption value="public">Public</NativeSelectOption>
                <NativeSelectOption value="unlisted">
                  Unlisted
                </NativeSelectOption>
                <NativeSelectOption value="private">Private</NativeSelectOption>
              </NativeSelect>
            </Field>
            <Field>
              <FieldLabel htmlFor="batch-youtube-kids">
                Made for kids?
              </FieldLabel>
              <NativeSelect
                id="batch-youtube-kids"
                value={settings.kids}
                onChange={(event) => updateSetting("kids", event.target.value)}
              >
                <NativeSelectOption value="no">No</NativeSelectOption>
                <NativeSelectOption value="yes">Yes</NativeSelectOption>
              </NativeSelect>
            </Field>
            <Field>
              <FieldLabel htmlFor="batch-youtube-synthetic">
                Realistic altered or synthetic content?
              </FieldLabel>
              <NativeSelect
                id="batch-youtube-synthetic"
                value={settings.synthetic}
                onChange={(event) =>
                  updateSetting("synthetic", event.target.value)
                }
              >
                <NativeSelectOption value="no">No</NativeSelectOption>
                <NativeSelectOption value="yes">Yes</NativeSelectOption>
              </NativeSelect>
            </Field>
          </FieldGroup>
          <Field orientation="horizontal">
            <Checkbox
              id="batch-youtube-notify"
              checked={settings.notify}
              onCheckedChange={(value) =>
                updateSetting("notify", value === true)
              }
            />
            <FieldLabel htmlFor="batch-youtube-notify">
              Notify channel subscribers
            </FieldLabel>
          </Field>
          <p>Publish time: As soon as each upload is ready.</p>
          {!loadedCopy && !error ? (
            <p role="status">Loading saved descriptions…</p>
          ) : null}
          {drafts.map((draft, index) => {
            const bytes = new TextEncoder().encode(draft.description).length;
            return (
              <FieldSet
                className="min-w-0 rounded-md border p-4"
                key={draft.exportId}
              >
                <FieldLegend>{items[index].title}</FieldLegend>
                <FieldGroup>
                  <video
                    className="max-h-64 max-w-full"
                    controls
                    preload="none"
                    src={`/api/clips/${draft.exportId}/media`}
                    aria-label={`Preview ${items[index].title}`}
                  />
                  {draft.warning ? (
                    <p className="text-sm text-muted-foreground">
                      {draft.warning}
                    </p>
                  ) : null}
                  <Field>
                    <FieldLabel htmlFor={`batch-title-${draft.exportId}`}>
                      Title
                    </FieldLabel>
                    <Input
                      id={`batch-title-${draft.exportId}`}
                      required
                      maxLength={100}
                      value={draft.title}
                      onChange={(event) =>
                        updateDraft(index, "title", event.target.value)
                      }
                    />
                  </Field>
                  <Field data-invalid={bytes > 5000}>
                    <FieldLabel htmlFor={`batch-copy-${draft.exportId}`}>
                      Description and hashtags
                    </FieldLabel>
                    <Textarea
                      id={`batch-copy-${draft.exportId}`}
                      disabled={!loadedCopy}
                      value={draft.description}
                      maxLength={5000}
                      aria-invalid={bytes > 5000}
                      onChange={(event) =>
                        updateDraft(index, "description", event.target.value)
                      }
                    />
                    <FieldDescription>
                      {bytes}/5000 UTF-8 bytes
                    </FieldDescription>
                  </Field>
                </FieldGroup>
              </FieldSet>
            );
          })}
          <Field orientation="horizontal">
            <Checkbox
              id="batch-youtube-approve"
              checked={approved}
              disabled={!creator || !loadedCopy || !category}
              onCheckedChange={(value) => setApproved(value === true)}
            />
            <FieldLabel htmlFor="batch-youtube-approve">
              I reviewed every video and approve uploading all {drafts.length}{" "}
              to {creator?.channel.title ?? "the selected channel"} with these
              settings.
            </FieldLabel>
          </Field>
          <Button
            type="submit"
            disabled={
              busy ||
              !approved ||
              !category ||
              !loadedCopy ||
              drafts.some(
                (draft) =>
                  !draft.title.trim() ||
                  new TextEncoder().encode(draft.description).length > 5000,
              )
            }
          >
            {busy
              ? "Saving approvals…"
              : `Approve and upload ${drafts.length} to YouTube`}
          </Button>
        </FieldGroup>
      </FieldSet>
    </form>
  );
}
