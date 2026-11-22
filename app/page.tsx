"use client";

import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { FieldLabel, Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ArrowRightIcon,
  CalendarDaysIcon,
  CaptionsIcon,
  LoaderCircleIcon,
  ScissorsIcon,
  SparklesIcon,
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
import { HomeProjects } from "./components/home-projects";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, message, uploadRecording } from "../lib/client-api";
import type { Recording, YouTubeSource } from "../lib/projects";

export default function HomePage() {
  const router = useRouter();
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
    if (file.size === 0 || file.size > 2 * 1024 ** 3) {
      setError("Choose a non-empty video up to 2 GiB.");
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
        "/api/youtube-sources",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url }),
        },
        65_000,
      );
      router.push(`/projects/new/youtube/${value.id}`);
    } catch (error) {
      setError(message(error));
      setBusy(false);
      setLoadingDetails(false);
    }
  }
  const working = busy;
  return (
    <section className="project-workspace home-workspace">
      <header className="home-heading">
        <h1>Your next great clip starts here.</h1>
        <p>Find moments in a long video or finish clips you already have.</p>
        <Button asChild variant="outline" disabled={working}>
          <Link
            href="/projects/batch"
            aria-disabled={working}
            onClick={(event) => {
              if (working) event.preventDefault();
            }}
          >
            Batch edit supplied clips
          </Link>
        </Button>
      </header>
      <div className="home-start-grid">
        <Card className="home-import" aria-labelledby="source-title">
          <CardHeader>
            <CardTitle role="heading" aria-level={2} id="source-title">
              Create clips
            </CardTitle>
            <CardDescription>
              Add a YouTube link or upload a video to get started.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-5">
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
                  aria-label="YouTube link"
                >
                  <LinkIcon />
                  <span>
                    YouTube<span className="hidden sm:inline"> link</span>
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
                      YouTube video link
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
                    Choose your moments, captions, and framing next.
                  </p>
                </form>
              </TabsContent>
              <TabsContent value="upload">
                <div className="source-upload">
                  <Field>
                    <FieldLabel htmlFor="source-upload-file">
                      <UploadIcon data-icon="inline-start" />
                      <span>Upload a video</span>
                    </FieldLabel>
                    <Input
                      id="source-upload-file"
                      type="file"
                      accept=".mp4,.m4v,.mov,.mkv,.webm"
                      disabled={working}
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (file) void importFile(file);
                        event.target.value = "";
                      }}
                    />
                  </Field>
                </div>
                <p className="mt-3 text-xs">
                  MP4, MOV, MKV or WebM, up to 2 GiB and six hours. Import
                  videos you own or have permission to repurpose.
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
        <aside className="home-workflow" aria-labelledby="workflow-title">
          <h2 id="workflow-title">Made for your workflow</h2>
          <ul>
            <li>
              <SparklesIcon aria-hidden="true" />
              <div>
                <h3>Find the moments</h3>
                <p>AI suggestions grounded in your transcript.</p>
              </div>
            </li>
            <li>
              <CaptionsIcon aria-hidden="true" />
              <div>
                <h3>Make them yours</h3>
                <p>Refine captions, titles, and vertical framing.</p>
              </div>
            </li>
            <li>
              <CalendarDaysIcon aria-hidden="true" />
              <div>
                <h3>Share with intention</h3>
                <p>Review each clip before publishing.</p>
              </div>
            </li>
          </ul>
        </aside>
      </div>
      <HomeProjects disabled={working} />
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
        <section
          className="home-imported"
          aria-labelledby="imported-videos-title"
        >
          <h2 id="imported-videos-title">Or reuse an imported video</h2>
          <ul>
            {recent.map((source) => (
              <li key={source.id}>
                {working ? (
                  <span>{source.name}</span>
                ) : (
                  <Link href={`/projects/new/${source.id}`}>{source.name}</Link>
                )}
                <span>
                  {source.duration < 60
                    ? `${Math.round(source.duration)} sec`
                    : `${Math.floor(source.duration / 60)} min`}
                </span>
              </li>
            ))}
          </ul>
          {!working ? <Link href="/studio">Browse imported videos</Link> : null}
        </section>
      ) : null}
      <Separator />
      <div className="home-shortcuts">
        <h2>Looking for hands-on control?</h2>
        <Button asChild variant="outline" disabled={working}>
          <Link
            href="/studio"
            aria-disabled={working}
            onClick={(event) => {
              if (working) event.preventDefault();
            }}
          >
            <ScissorsIcon data-icon="inline-start" />
            Open clip editor
          </Link>
        </Button>
        <Button asChild variant="ghost" disabled={working}>
          <Link
            href="/calendar"
            aria-disabled={working}
            onClick={(event) => {
              if (working) event.preventDefault();
            }}
          >
            Plan your posts
            <ArrowRightIcon data-icon="inline-end" />
          </Link>
        </Button>
      </div>
    </section>
  );
}
