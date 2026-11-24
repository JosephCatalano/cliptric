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
  platformNames,
  platformRoot,
  type SocialConnection,
} from "@/lib/social";
import { privacyLabels, type TikTokCreator } from "@/lib/tiktok";
import { youtubeDefaults, type YouTubeCreator } from "@/lib/youtube-publishing";

const platforms = ["youtube", "x", "tiktok", "instagram", "facebook"] as const;
type Platform = (typeof platforms)[number];
const emptySelection = {
  youtube: false,
  x: false,
  tiktok: false,
  instagram: false,
  facebook: false,
};
const emptyAccounts = {
  youtube: "",
  x: "",
  tiktok: "",
  instagram: "",
  facebook: "",
};
const captionLimits: Record<Platform, number> = {
  youtube: 5000,
  x: 280,
  tiktok: 2200,
  instagram: 2200,
  facebook: 5000,
};
const tiktokDefaults = {
  privacy: "",
  allow_comments: false,
  allow_duet: false,
  allow_stitch: false,
  your_brand: false,
  branded_content: false,
  is_aigc: false,
};
const dispatch = "/api/schedule/batch";

type Receipt = {
  id: string;
  posts: {
    id: string;
    platform: Platform;
    export_id: string;
    account_id: string;
    status: string;
    post_status?: string;
    error: string | null;
  }[];
};

export function BatchPublish({
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
          Publish selected clips ({items.length})
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Publish selected clips</DialogTitle>
          <DialogDescription>
            Choose destinations and review the exact videos and posting text
            before approving.
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
      copies: { ...emptyAccounts },
      warning: item.transcription_warning,
    })),
  );
  const [connections, setConnections] = useState<
    Partial<Record<Platform, SocialConnection>>
  >({});
  const [selected, setSelected] = useState(emptySelection);
  const [accounts, setAccounts] = useState(emptyAccounts);
  const [profile, setProfile] = useState<YouTubeCreator | null>(null);
  const [tiktokProfile, setTiktokProfile] = useState<TikTokCreator | null>(
    null,
  );
  const [tiktok, setTiktok] = useState(tiktokDefaults);
  const [loadedCopy, setLoadedCopy] = useState(false);
  const [settings, setSettings] = useState(youtubeDefaults);
  const [shareToInstagramFeed, setShareToInstagramFeed] = useState(false);
  const [facebookDisclosure, setFacebookDisclosure] = useState(false);
  const [approved, setApproved] = useState(false);
  const [error, setError] = useState("");
  const [profileError, setProfileError] = useState({ youtube: "", tiktok: "" });
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const attempt = useRef<{ key: string; id: string; ids: string[] } | null>(
    null,
  );
  const creator = profile?.account_id === accounts.youtube ? profile : null;
  const tiktokCreator =
    tiktokProfile?.account_id === accounts.tiktok ? tiktokProfile : null;
  const destinations = platforms.filter((platform) => selected[platform]);
  const category = creator?.categories.some(
    (option) => option.id === settings.categoryId,
  )
    ? settings.categoryId
    : "";

  useEffect(() => {
    const controller = new AbortController();
    for (const platform of platforms) {
      void api<SocialConnection>(platformRoot(platform), {
        signal: controller.signal,
      })
        .then((status) => {
          if (controller.signal.aborted) return;
          setConnections((previous) => ({ ...previous, [platform]: status }));
          setAccounts((previous) => ({
            ...previous,
            [platform]: status.account_id ?? "",
          }));
        })
        .catch((error) => {
          if (!controller.signal.aborted)
            setError(
              `${platformNames[platform]} accounts could not load. ${message(error)}`,
            );
        });
    }
    async function load() {
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
            copies: {
              youtube: descriptions[index],
              x: descriptions[index],
              tiktok: descriptions[index],
              instagram: descriptions[index],
              facebook: descriptions[index],
            },
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
    if (!selected.youtube || !accounts.youtube) return;
    const controller = new AbortController();
    api<YouTubeCreator>(
      `${platformRoot("youtube")}/creator?account_id=${encodeURIComponent(accounts.youtube)}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setProfile(value);
      })
      .catch((failure) => {
        if (!controller.signal.aborted)
          setProfileError((previous) => ({
            ...previous,
            youtube: message(failure),
          }));
      });
    return () => controller.abort();
  }, [accounts.youtube, selected.youtube]);

  useEffect(() => {
    if (!selected.tiktok || !accounts.tiktok) return;
    const controller = new AbortController();
    void api<TikTokCreator>(
      `${platformRoot("tiktok")}/creator?account_id=${encodeURIComponent(accounts.tiktok)}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setTiktokProfile(value);
      })
      .catch((failure) => {
        if (!controller.signal.aborted)
          setProfileError((previous) => ({
            ...previous,
            tiktok: message(failure),
          }));
      });
    return () => controller.abort();
  }, [accounts.tiktok, selected.tiktok]);

  function updateDraft(
    index: number,
    field: Platform | "title",
    value: string,
  ) {
    setDrafts((previous) =>
      previous.map((draft, position) =>
        position === index
          ? field === "title"
            ? { ...draft, title: value }
            : { ...draft, copies: { ...draft.copies, [field]: value } }
          : draft,
      ),
    );
    setApproved(false);
  }

  function updateSetting(key: keyof typeof settings, value: string | boolean) {
    setSettings((previous) => ({ ...previous, [key]: value }));
    setApproved(false);
  }

  function updateTikTok<K extends keyof typeof tiktok>(
    key: K,
    value: (typeof tiktok)[K],
  ) {
    setTiktok((previous) => ({
      ...previous,
      [key]: value,
      ...(key === "privacy" && value === "SELF_ONLY"
        ? { branded_content: false }
        : {}),
    }));
    setApproved(false);
  }

  const tiktokPrivacy =
    tiktokCreator?.privacy_level_options.filter(
      (value) => tiktokCreator.audited || value === "SELF_ONLY",
    ) ?? [];
  const tiktokBlocked =
    !!tiktokCreator &&
    !tiktokCreator.audited &&
    tiktokCreator.privacy_level_options.includes("PUBLIC_TO_EVERYONE");
  const tiktokTooLong =
    !!tiktokCreator &&
    items.some(
      (item) =>
        (item.export?.duration ?? item.end - item.start) >
        tiktokCreator.max_video_post_duration_sec,
    );
  const ready =
    loadedCopy &&
    destinations.length > 0 &&
    destinations.length * drafts.length <= 225 &&
    destinations.every((platform) => accounts[platform]) &&
    (!selected.youtube || !!category) &&
    (!selected.tiktok ||
      (!tiktokBlocked &&
        !tiktokTooLong &&
        tiktokPrivacy.includes(tiktok.privacy)));
  const valid = drafts.every(
    (draft) =>
      (!selected.youtube ||
        (!!draft.title.trim() &&
          new TextEncoder().encode(draft.copies.youtube).length <= 5000)) &&
      (!selected.x || Array.from(draft.copies.x).length <= 280) &&
      (!selected.tiktok ||
        draft.copies.tiktok.length <= captionLimits.tiktok) &&
      (!selected.instagram ||
        draft.copies.instagram.length <= captionLimits.instagram) &&
      (!selected.facebook ||
        draft.copies.facebook.length <= captionLimits.facebook),
  );

  const receiptId = receipt?.id;
  useEffect(() => {
    if (!receiptId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const snapshot = await api<Receipt>(`${dispatch}/${receiptId}`, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setReceipt(snapshot);
        if (
          snapshot.posts.some(
            (post) =>
              ["scheduled", "dispatching"].includes(post.status) ||
              ["queued", "initializing", "uploading", "publishing"].includes(
                post.post_status ?? "",
              ),
          )
        )
          timer = setTimeout(poll, 5000);
      } catch (failure) {
        if (!controller.signal.aborted) setError(message(failure));
      }
    }
    timer = setTimeout(poll, 2000);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [receiptId]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !approved || !ready || !valid) return;
    const posts = drafts.flatMap((draft) =>
      destinations.map((platform) => {
        const basics = {
          export_id: draft.exportId,
          account_id: accounts[platform],
          consent: true,
        };
        if (platform === "youtube")
          return {
            platform,
            request: {
              ...basics,
              title: draft.title.trim(),
              description: draft.copies.youtube,
              category_id: category,
              privacy: settings.privacy,
              made_for_kids: settings.kids === "yes",
              contains_synthetic_media: settings.synthetic === "yes",
              notify_subscribers: settings.notify,
            },
          };
        if (platform === "tiktok")
          return {
            platform,
            request: {
              ...basics,
              ...tiktok,
              caption: draft.copies.tiktok,
              commercial: tiktok.your_brand || tiktok.branded_content,
            },
          };
        const caption = draft.copies[platform];
        if (platform === "instagram")
          return {
            platform,
            request: {
              ...basics,
              caption,
              share_to_feed: shareToInstagramFeed,
            },
          };
        if (platform === "facebook")
          return {
            platform,
            request: {
              ...basics,
              caption,
              is_ai_generated: facebookDisclosure,
            },
          };
        return { platform, request: { ...basics, caption } };
      }),
    );
    const key = JSON.stringify(posts);
    if (attempt.current?.key !== key)
      attempt.current = {
        key,
        id: crypto.randomUUID(),
        ids: posts.map(() => crypto.randomUUID()),
      };
    const { id, ids } = attempt.current;
    onBusy(true);
    setError("");
    try {
      setReceipt(
        await api<Receipt>(
          dispatch,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              id,
              posts: posts.map((post, index) => ({
                ...post,
                request: { ...post.request, id: ids[index] },
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

  if (receipt)
    return (
      <FieldGroup>
        <Alert>
          <AlertDescription>
            {receipt.posts.length} approved posts. Follow or cancel pending
            posts in the{" "}
            <Link href="/calendar" className="underline">
              calendar
            </Link>
            .
          </AlertDescription>
        </Alert>
        <ul className="flex flex-col gap-2">
          {receipt.posts.map((post) => (
            <li key={post.id}>
              {drafts.find((draft) => draft.exportId === post.export_id)?.title}{" "}
              · {platformNames[post.platform]} ·{" "}
              {post.post_status?.replaceAll("_", " ") ??
                (post.status === "scheduled" ? "Queued" : post.status)}
              {post.post_status ? (
                <>
                  {" "}
                  ·{" "}
                  <Link
                    className="underline underline-offset-4"
                    href={`/publish/${post.platform}/${post.export_id}?account_id=${post.account_id}`}
                  >
                    View post
                  </Link>
                </>
              ) : null}
              {post.error ? (
                <p className="text-sm text-muted-foreground">{post.error}</p>
              ) : null}
            </li>
          ))}
        </ul>
        {error ? (
          <Alert>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
      </FieldGroup>
    );

  return (
    <form onSubmit={submit}>
      <FieldSet disabled={busy}>
        <FieldLegend>Destinations for all selected clips</FieldLegend>
        <FieldGroup>
          <Button
            type="button"
            variant="outline"
            className="w-fit"
            disabled={
              !platforms.some(
                (platform) => connections[platform]?.accounts.length,
              )
            }
            onClick={() => {
              setSelected({
                ...emptySelection,
                ...Object.fromEntries(
                  platforms.map((platform) => [
                    platform,
                    Boolean(connections[platform]?.accounts.length),
                  ]),
                ),
              });
              setApproved(false);
            }}
          >
            Select all connected platforms
          </Button>
          {destinations.length * drafts.length > 225 && (
            <Alert variant="destructive">
              <AlertDescription>
                A single batch can contain up to 225 posts. Choose fewer clips
                or platforms.
              </AlertDescription>
            </Alert>
          )}
          {error || profileError.youtube || profileError.tiktok ? (
            <Alert variant="destructive">
              <AlertDescription>
                {error || profileError.youtube || profileError.tiktok}
              </AlertDescription>
            </Alert>
          ) : null}
          {platforms.map((platform) => (
            <FieldGroup key={platform} className="gap-2">
              <Field orientation="horizontal">
                <Checkbox
                  id={`batch-destination-${platform}`}
                  checked={selected[platform]}
                  disabled={!connections[platform]?.accounts.length}
                  onCheckedChange={(value) => {
                    setSelected((previous) => ({
                      ...previous,
                      [platform]: value === true,
                    }));
                    setProfileError((previous) => ({
                      ...previous,
                      [platform]: "",
                    }));
                    setApproved(false);
                  }}
                />
                <FieldLabel htmlFor={`batch-destination-${platform}`}>
                  {platformNames[platform]}
                </FieldLabel>
                {connections[platform]?.accounts.length === 0 ? (
                  <Link href="/publishing/accounts" className="text-sm underline">
                    Connect {platformNames[platform]}
                  </Link>
                ) : null}
              </Field>
              {selected[platform] ? (
                <Field>
                  <FieldLabel htmlFor={`batch-account-${platform}`}>
                    {platformNames[platform]} account
                  </FieldLabel>
                  <NativeSelect
                    id={`batch-account-${platform}`}
                    required
                    value={accounts[platform]}
                    onChange={(event) => {
                      const account = event.target.value;
                      setAccounts((previous) => ({
                        ...previous,
                        [platform]: account,
                      }));
                      setProfileError((previous) => ({
                        ...previous,
                        [platform]: "",
                      }));
                      if (platform === "tiktok") setTiktok(tiktokDefaults);
                      setApproved(false);
                    }}
                  >
                    <NativeSelectOption value="" disabled>
                      Choose an account
                    </NativeSelectOption>
                    {connections[platform]?.accounts.map((account) => (
                      <NativeSelectOption
                        key={account.account_id}
                        value={account.account_id}
                      >
                        {account.label}
                      </NativeSelectOption>
                    ))}
                  </NativeSelect>
                </Field>
              ) : null}
            </FieldGroup>
          ))}
          {selected.instagram && (
            <Field orientation="horizontal">
              <Checkbox
                id="batch-instagram-feed"
                checked={shareToInstagramFeed}
                onCheckedChange={(value) => {
                  setShareToInstagramFeed(value === true);
                  setApproved(false);
                }}
              />
              <FieldLabel htmlFor="batch-instagram-feed">
                Share Instagram Reels to the feed
              </FieldLabel>
            </Field>
          )}
          {selected.facebook && (
            <Field orientation="horizontal">
              <Checkbox
                id="batch-facebook-ai"
                checked={facebookDisclosure}
                onCheckedChange={(value) => {
                  setFacebookDisclosure(value === true);
                  setApproved(false);
                }}
              />
              <FieldLabel htmlFor="batch-facebook-ai">
                Disclose AI-generated media on Facebook
              </FieldLabel>
            </Field>
          )}
          {selected.youtube ? (
            <FieldSet>
              <FieldLegend>YouTube settings</FieldLegend>
              <FieldGroup>
                <FieldGroup className="grid gap-4 sm:grid-cols-2">
                  <Field>
                    <FieldLabel htmlFor="batch-youtube-category">
                      Category
                    </FieldLabel>
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
                      <NativeSelectOption value="public">
                        Public
                      </NativeSelectOption>
                      <NativeSelectOption value="unlisted">
                        Unlisted
                      </NativeSelectOption>
                      <NativeSelectOption value="private">
                        Private
                      </NativeSelectOption>
                    </NativeSelect>
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="batch-youtube-kids">
                      Made for kids?
                    </FieldLabel>
                    <NativeSelect
                      id="batch-youtube-kids"
                      value={settings.kids}
                      onChange={(event) =>
                        updateSetting("kids", event.target.value)
                      }
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
              </FieldGroup>
            </FieldSet>
          ) : null}
          {selected.x ? (
            <p className="text-sm text-muted-foreground">
              X uses the selected account’s visibility settings. X API usage may
              incur charges.
            </p>
          ) : null}
          {selected.tiktok && tiktokCreator ? (
            <FieldSet>
              <FieldLegend>
                TikTok settings for @{tiktokCreator.creator_username}
              </FieldLegend>
              <FieldGroup>
                {!tiktokCreator.audited ? (
                  <p>
                    Sandbox requires a private TikTok account and Only me
                    visibility.
                  </p>
                ) : null}
                {tiktokTooLong ? (
                  <Alert variant="destructive">
                    <AlertDescription>
                      At least one clip exceeds this account’s{" "}
                      {tiktokCreator.max_video_post_duration_sec}-second limit.
                    </AlertDescription>
                  </Alert>
                ) : null}
                <Field>
                  <FieldLabel htmlFor="batch-tiktok-privacy">
                    Visibility
                  </FieldLabel>
                  <NativeSelect
                    id="batch-tiktok-privacy"
                    required
                    value={tiktok.privacy}
                    onChange={(event) =>
                      updateTikTok("privacy", event.target.value)
                    }
                  >
                    <NativeSelectOption value="" disabled>
                      Choose visibility
                    </NativeSelectOption>
                    {tiktokPrivacy.map((value) => (
                      <NativeSelectOption key={value} value={value}>
                        {privacyLabels[value] ?? value}
                      </NativeSelectOption>
                    ))}
                  </NativeSelect>
                </Field>
                {(
                  [
                    [
                      "allow_comments",
                      "Allow comments",
                      tiktokCreator.comment_disabled,
                    ],
                    ["allow_duet", "Allow Duet", tiktokCreator.duet_disabled],
                    [
                      "allow_stitch",
                      "Allow Stitch",
                      tiktokCreator.stitch_disabled,
                    ],
                    ["your_brand", "Disclose your brand", false],
                    [
                      "branded_content",
                      "Disclose a paid partnership",
                      tiktok.privacy === "SELF_ONLY",
                    ],
                    ["is_aigc", "Label AI-generated content", false],
                  ] as const
                ).map(([key, label, disabled]) => (
                  <Field key={key} orientation="horizontal">
                    <Checkbox
                      id={`batch-tiktok-${key}`}
                      disabled={disabled}
                      checked={tiktok[key]}
                      onCheckedChange={(value) =>
                        updateTikTok(key, value === true)
                      }
                    />
                    <FieldLabel htmlFor={`batch-tiktok-${key}`}>
                      {label}
                    </FieldLabel>
                  </Field>
                ))}
                <p>
                  By posting, you agree to TikTok’s{" "}
                  <a
                    className="underline"
                    href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en"
                    target="_blank"
                    rel="noreferrer"
                  >
                    Music Usage Confirmation
                  </a>
                  {tiktok.branded_content ? (
                    <>
                      {" "}
                      and{" "}
                      <a
                        className="underline"
                        href="https://www.tiktok.com/legal/page/global/bc-policy/en"
                        target="_blank"
                        rel="noreferrer"
                      >
                        Branded Content Policy
                      </a>
                    </>
                  ) : null}
                  .
                </p>
              </FieldGroup>
            </FieldSet>
          ) : null}
          {destinations.length ? (
            <p>Posts publish as soon as each platform is ready.</p>
          ) : null}
          {!loadedCopy && !error ? (
            <p role="status">Loading saved descriptions…</p>
          ) : null}
          {destinations.length
            ? drafts.map((draft, index) => {
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
                      {selected.youtube ? (
                        <Field>
                          <FieldLabel htmlFor={`batch-title-${draft.exportId}`}>
                            YouTube title
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
                      ) : null}
                      {destinations.map((platform) => {
                        const copy = draft.copies[platform];
                        const count =
                          platform === "youtube"
                            ? new TextEncoder().encode(copy).length
                            : platform === "x"
                              ? Array.from(copy).length
                              : copy.length;
                        const limit = captionLimits[platform];
                        const field = `batch-copy-${platform}-${draft.exportId}`;
                        return (
                          <Field key={platform} data-invalid={count > limit}>
                            <FieldLabel htmlFor={field}>
                              {platformNames[platform]}{" "}
                              {platform === "youtube"
                                ? "description"
                                : "caption"}{" "}
                              and hashtags
                            </FieldLabel>
                            <Textarea
                              id={field}
                              disabled={!loadedCopy}
                              value={copy}
                              aria-invalid={count > limit}
                              onChange={(event) =>
                                updateDraft(index, platform, event.target.value)
                              }
                            />
                            <FieldDescription>
                              {count}/{limit}{" "}
                              {platform === "youtube"
                                ? "UTF-8 bytes"
                                : "characters"}
                              {count > limit
                                ? " · Shorten this text before approving."
                                : ""}
                            </FieldDescription>
                          </Field>
                        );
                      })}
                    </FieldGroup>
                  </FieldSet>
                );
              })
            : null}
          <Field orientation="horizontal">
            <Checkbox
              id="batch-youtube-approve"
              checked={approved}
              disabled={!ready || !valid}
              onCheckedChange={(value) => setApproved(value === true)}
            />
            <FieldLabel htmlFor="batch-youtube-approve">
              I have permission to share these videos. I reviewed each video,
              its text, settings, and the selected accounts, and approve all{" "}
              {drafts.length * destinations.length} posts.
            </FieldLabel>
          </Field>
          <Button
            type="submit"
            disabled={busy || !approved || !ready || !valid}
          >
            {busy
              ? "Saving approvals…"
              : `Approve and publish ${drafts.length * destinations.length} posts`}
          </Button>
        </FieldGroup>
      </FieldSet>
    </form>
  );
}
