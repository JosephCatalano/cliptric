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
  ClapperboardIcon,
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
  async function importFile(file: File) {
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
      router.push(`/projects/new/${source.id}`);
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
    setBusy(true);
    setLoadingDetails(true);
    setError("");
    try {
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
  return (
    <section className="project-workspace home-workspace">
      <header className="workspace-heading">
        <div>
          <h1>Create something worth sharing.</h1>
          <p>Turn long videos into clips ready to edit, publish, and share.</p>
        </div>
        <Button asChild variant="outline" disabled={working}>
          <Link href="/projects/batch" aria-disabled={working} onClick={(event) => {
            if (working) event.preventDefault();
          }}>
            <ClapperboardIcon data-icon="inline-start" />
            Batch editor
          </Link>
        </Button>
      </header>
      <div className="home-start-grid">
        <Card className="home-import" aria-labelledby="source-title">
          <CardHeader>
            <CardTitle role="heading" aria-level={2} id="source-title">
              Start a new project
            </CardTitle>
            <CardDescription>
              Paste a YouTube or Twitch link or choose a recording from your device.
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
                      YouTube video or Twitch VOD link
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
                    Public YouTube videos and completed Twitch VODs.
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
                    if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget))
                      setDragging(false);
                  }}
                  onDrop={(event) => {
                    if (!event.dataTransfer.types.includes("Files")) return;
                    event.preventDefault();
                    setDragging(false);
                    if (working || limits.maxBytes === null) return;
                    if (event.dataTransfer.files.length !== 1) {
                      setError("Choose one video for this project.");
                      return;
                    }
                    void importFile(event.dataTransfer.files[0]);
                  }}
                >
                  <Field>
                    <FieldLabel htmlFor="source-upload-file">
                      <UploadIcon data-icon="inline-start" />
                      <span>Drop a video here or browse files</span>
                    </FieldLabel>
                    <Input
                      id="source-upload-file"
                      type="file"
                      accept=".mp4,.m4v,.mov,.mkv,.webm"
                      disabled={limits.maxBytes === null || working}
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (file) void importFile(file);
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
                <Link href="/studio">View library <ArrowRightIcon data-icon="inline-end" /></Link>
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
                <FilmIcon aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
                {working ? (
                  <span className="min-w-0 flex-1 truncate">{source.name}</span>
                ) : (
                  <Link className="min-w-0 flex-1 truncate font-medium" href={`/projects/new/${source.id}`}>{source.name}</Link>
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
        <p className="text-sm text-muted-foreground">Imported recordings will appear here for reuse.</p>
      ) : null}
        </aside>
      </div>
      <HomeProjects disabled={working} />
    </section>
  );
}
