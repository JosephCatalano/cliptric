"use client";

import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { FieldLabel, Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ArrowRightIcon,
  LoaderCircleIcon,
  FilmIcon,
  UploadIcon,
  LinkIcon,
} from "lucide-react";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { useImportLimits, ImportLimitNotice } from "./components/import-limits";
import { HomeProjects } from "./components/home-projects";
import { NewBatch } from "./components/new-batch";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, message, uploadRecording } from "../lib/client-api";
import type { Recording, YouTubeSource } from "../lib/projects";

export default function HomePage() {
  const router = useRouter();
  const limits = useImportLimits();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingDetails, setLoadingDetails] = useState(false);
  const [error, setError] = useState("");
  const [recent, setRecent] = useState<Recording[]>([]);
  const [recentLoading, setRecentLoading] = useState(true);
  const [recentError, setRecentError] = useState("");
  const [recentRefresh, setRecentRefresh] = useState(0);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [fileName, setFileName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [recording, setRecording] = useState<Recording | null>(null);
  const [batchMode, setBatchMode] = useState<"keep" | "highlights" | null>(
    null,
  );
  const upload = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api<Recording[]>("/api/recordings?limit=6", { signal: controller.signal })
      .then((values) => {
        if (controller.signal.aborted) return;
        setRecent(values);
        setRecentError("");
      })
      .catch((error) => {
        if (!controller.signal.aborted) setRecentError(message(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setRecentLoading(false);
      });
    return () => controller.abort();
  }, [recentRefresh]);
  useEffect(() => () => upload.current?.abort(), []);
  async function importFile(file: File, intent: "highlights" | "edit") {
    if (busy) return;
    if (limits.maxBytes === null) {
      setError(
        "Wait for the upload limit to load, or retry the API connection.",
      );
      return;
    }
    if (file.size === 0 || file.size > (limits.maxBytes ?? 0)) {
      setError(`Choose a non-empty video up to ${limits.label}.`);
      return;
    }
    setBusy(true);
    setError("");
    setUploadProgress(0);
    setFileName(file.name);
    const controller = new AbortController();
    upload.current = controller;
    try {
      const source = await uploadRecording<Recording>(
        file,
        controller.signal,
        setUploadProgress,
      );
      router.push(
        intent === "edit"
          ? `/studio?recording=${source.id}`
          : `/projects/new/${source.id}`,
      );
    } catch (error) {
      if (!controller.signal.aborted) setError(message(error));
      else
        setError(
          "Import stopped. Check your imported videos before retrying; the source may already have been saved.",
        );
      setBusy(false);
    }
  }
  async function importYouTube(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setError("");
    try {
      const link = new URL(url.trim());
      if (
        link.protocol === "https:" &&
        !link.username &&
        !link.password &&
        !link.port
      ) {
        const slug =
          link.hostname === "clips.twitch.tv"
            ? /^\/([A-Za-z0-9_-]{1,150})\/?$/.exec(link.pathname)?.[1]
            : ["www.twitch.tv", "twitch.tv"].includes(link.hostname)
              ? /^\/[A-Za-z0-9_]{1,25}\/clip\/([A-Za-z0-9_-]{1,150})\/?$/.exec(
                  link.pathname,
                )?.[1]
              : undefined;
        if (slug && slug.toLowerCase() !== "embed") {
          router.push(`/studio?twitchClip=${encodeURIComponent(slug)}`);
          return;
        }
      }
      setBusy(true);
      setLoadingDetails(true);
      const value = await api<YouTubeSource>(
        "/api/video-sources",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url }),
        },
        65_000,
      );
      router.push(`/projects/new/video/${value.id}`);
    } catch (error) {
      setError(message(error));
      setBusy(false);
      setLoadingDetails(false);
    }
  }
  const [dragging, setDragging] = useState(false);
  const working = busy;
  function stageFiles(added: File[]) {
    if (!added.length) return;
    if (
      added.length > 15 ||
      added.some(
        (file) =>
          !/\.(mp4|m4v|mov|mkv|webm)$/i.test(file.name) ||
          file.size === 0 ||
          file.size > (limits.maxBytes ?? 0),
      )
    ) {
      setError(
        `Choose up to 15 supported, non-empty videos, each up to ${limits.label}.`,
      );
      return;
    }
    setError("");
    setRecording(null);
    setFiles(added);
  }
  function continueWith(intent: "highlights" | "edit" | "keep") {
    if (intent === "keep" || (intent === "highlights" && files.length > 1)) {
      setBatchMode(intent === "keep" ? "keep" : "highlights");
      return;
    }
    if (recording) {
      router.push(
        intent === "edit"
          ? `/studio?recording=${recording.id}`
          : `/projects/new/${recording.id}`,
      );
      return;
    }
    if (files.length === 1) void importFile(files[0], intent);
  }
  if (batchMode)
    return (
      <NewBatch
        initialFiles={files}
        initialRecordings={recording ? [recording] : []}
        initialMode={batchMode}
        onChooseSource={() => setBatchMode(null)}
      />
    );
  return (
    <section className="project-workspace home-workspace">
      <header className="workspace-heading">
        <div>
          <h1>Create</h1>
          <p>
            Choose a source, then find highlights, edit a moment, or finish the
            full video.
          </p>
        </div>
        <Button asChild variant="outline" disabled={working}>
          <Link
            href="/studio"
            aria-disabled={working}
            onClick={(event) => {
              if (working) event.preventDefault();
            }}
          >
            Open Editor
          </Link>
        </Button>
      </header>
      <div className="home-start-grid">
        <Card className="home-import" aria-labelledby="source-title">
          <CardHeader>
            <CardTitle role="heading" aria-level={2} id="source-title">
              Choose footage
            </CardTitle>
            <CardDescription>
              Paste a public video link, upload up to 15 videos, or pick a saved
              recording.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-5">
            <ImportLimitNotice limits={limits} />
            {error ? (
              <Alert role="alert" variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}
            <Tabs defaultValue="youtube" className="gap-5">
              <TabsList
                aria-label="Video source"
                className="grid h-auto w-full max-w-sm grid-cols-2"
              >
                <TabsTrigger
                  value="youtube"
                  disabled={working}
                  aria-label="YouTube or Twitch VOD link"
                >
                  <LinkIcon />
                  <span>
                    Video<span className="hidden sm:inline"> link</span>
                  </span>
                </TabsTrigger>
                <TabsTrigger
                  value="upload"
                  disabled={working}
                  aria-label="Upload video"
                >
                  <UploadIcon />
                  <span>
                    Upload<span className="hidden sm:inline"> video</span>
                  </span>
                </TabsTrigger>
              </TabsList>
              <TabsContent value="youtube">
                <form onSubmit={importYouTube} className="flex flex-col gap-3">
                  <Field>
                    <FieldLabel htmlFor="youtube-url">
                      YouTube video or Twitch link
                    </FieldLabel>
                    <div className="source-url-row">
                      <Input
                        id="youtube-url"
                        type="url"
                        inputMode="url"
                        required
                        maxLength={2048}
                        placeholder="https://www.youtube.com/watch?v=…"
                        value={url}
                        onChange={(event) => setUrl(event.target.value)}
                        disabled={working}
                      />
                      <Button
                        disabled={working}
                        type="submit"
                        variant="default"
                      >
                        {loadingDetails ? (
                          <LoaderCircleIcon
                            data-icon="inline-start"
                            className="animate-spin"
                          />
                        ) : null}
                        {loadingDetails ? "Loading video…" : "Continue"}
                        {!loadingDetails ? (
                          <ArrowRightIcon data-icon="inline-end" />
                        ) : null}
                      </Button>
                    </div>
                  </Field>
                  <p className="text-xs">
                    Public YouTube videos and completed Twitch VODs open setup.
                    Twitch Clips open in the Editor.
                  </p>
                </form>
              </TabsContent>
              <TabsContent value="upload">
                <div
                  className="source-dropzone"
                  data-dragging={dragging}
                  onDragOver={(event) => {
                    if (!event.dataTransfer.types.includes("Files")) return;
                    event.preventDefault();
                    if (!working && limits.maxBytes !== null) setDragging(true);
                  }}
                  onDragLeave={(event) => {
                    if (
                      !(event.relatedTarget instanceof Node) ||
                      !event.currentTarget.contains(event.relatedTarget)
                    )
                      setDragging(false);
                  }}
                  onDrop={(event) => {
                    if (!event.dataTransfer.types.includes("Files")) return;
                    event.preventDefault();
                    setDragging(false);
                    if (working || limits.maxBytes === null) return;
                    stageFiles(Array.from(event.dataTransfer.files));
                  }}
                >
                  <Field>
                    <FieldLabel htmlFor="source-upload-file">
                      <UploadIcon data-icon="inline-start" />
                      <span>Drop videos here or browse files</span>
                    </FieldLabel>
                    <Input
                      id="source-upload-file"
                      type="file"
                      multiple
                      accept=".mp4,.m4v,.mov,.mkv,.webm"
                      disabled={limits.maxBytes === null || working}
                      onChange={(event) => {
                        stageFiles(Array.from(event.target.files ?? []));
                        event.target.value = "";
                      }}
                    />
                  </Field>
                </div>
                <p className="mt-3 text-xs">
                  MP4, MOV, MKV or WebM, up to {limits.label} and six hours.
                </p>
              </TabsContent>
            </Tabs>
            {(recording || files.length > 0) && (
              <Field>
                <p className="text-sm font-medium break-words">
                  {recording?.name ??
                    (files.length === 1
                      ? files[0].name
                      : `${files.length} videos selected`)}
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button
                    disabled={working}
                    onClick={() => continueWith("highlights")}
                  >
                    Find highlights
                  </Button>
                  {files.length <= 1 && (
                    <Button
                      variant="outline"
                      disabled={working}
                      onClick={() => continueWith("edit")}
                    >
                      Edit manually
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    disabled={working}
                    onClick={() => continueWith("keep")}
                  >
                    Finish full length
                  </Button>
                </div>
              </Field>
            )}
            {busy ? (
              <div className="flex flex-col gap-3">
                <p role="status" className="flex items-center gap-2">
                  <LoaderCircleIcon
                    aria-hidden="true"
                    className="size-4 shrink-0 animate-spin"
                  />
                  {loadingDetails
                    ? "Loading video title, thumbnail, and duration…"
                    : uploadProgress < 100
                      ? "Transferring your video…"
                      : "Saving and checking your video…"}
                </p>
                {!loadingDetails ? (
                  <>
                    <p
                      className="truncate text-sm text-muted-foreground"
                      title={fileName}
                    >
                      {fileName}
                    </p>
                    <Progress
                      value={uploadProgress}
                      aria-label="Video transfer progress"
                    />
                    <p className="text-sm text-muted-foreground">
                      {Math.floor(uploadProgress)}% transferred. Keep this page
                      open until setup appears.
                    </p>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => upload.current?.abort()}
                    >
                      Stop import
                    </Button>
                  </>
                ) : null}
              </div>
            ) : null}
          </CardContent>
        </Card>
        <aside className="home-sources" aria-labelledby="imported-videos-title">
          <div className="flex items-center justify-between gap-3">
            <h2 id="imported-videos-title">Imported videos</h2>
            {!working && recent.length > 0 && (
              <Button asChild variant="ghost" size="sm">
                <Link href="/studio">
                  View library <ArrowRightIcon data-icon="inline-end" />
                </Link>
              </Button>
            )}
          </div>
          {recentLoading ? (
            <div role="status" className="flex flex-col gap-3">
              <span className="sr-only">Loading imported videos…</span>
              {[0, 1, 2].map((key) => (
                <Skeleton key={key} className="h-12 w-full" />
              ))}
            </div>
          ) : null}
          {recentError ? (
            <Alert variant="destructive">
              <AlertDescription>
                {recentError}
                <Button
                  variant="outline"
                  disabled={recentLoading}
                  onClick={() => {
                    setRecentLoading(true);
                    setRecentRefresh((value) => value + 1);
                  }}
                >
                  Retry imported videos
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}
          {recent.length ? (
            <ul className="home-source-list">
              {recent.map((source) => (
                <li key={source.id}>
                  <FilmIcon
                    aria-hidden="true"
                    className="size-4 shrink-0 text-muted-foreground"
                  />
                  {working ? (
                    <span className="min-w-0 flex-1 truncate">
                      {source.name}
                    </span>
                  ) : (
                    <Button
                      type="button"
                      variant="ghost"
                      aria-pressed={recording?.id === source.id}
                      className="min-w-0 flex-1 justify-start"
                      onClick={() => {
                        setFiles([]);
                        setRecording(source);
                        setError("");
                      }}
                    >
                      <span className="truncate">{source.name}</span>
                    </Button>
                  )}
                  <span>
                    {source.duration < 60
                      ? `${Math.round(source.duration)} sec`
                      : `${Math.floor(source.duration / 60)} min`}
                  </span>
                </li>
              ))}
            </ul>
          ) : !recentLoading && !recentError ? (
            <p className="text-sm text-muted-foreground">
              Imported recordings will appear here for reuse.
            </p>
          ) : null}
        </aside>
      </div>
      <HomeProjects disabled={working} />
    </section>
  );
}
