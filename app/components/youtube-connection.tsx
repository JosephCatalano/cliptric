"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import { api, message } from "@/lib/client-api";
import type {
  YouTubeConnection as Connection,
  YouTubeCreator,
} from "@/lib/youtube-publishing";

export function YouTubeConnection() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [creator, setCreator] = useState<YouTubeCreator | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const value = await api<Connection>("/api/youtube-publishing", {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setConnection(value);
        setCreator(null);
        setError("");
        if (
          new URLSearchParams(window.location.search).get("youtube") ===
          "cancelled"
        )
          setNotice("YouTube connection was cancelled. You can try again.");
        if (value.connected) {
          const profile = await api<YouTubeCreator>(
            "/api/youtube-publishing/creator",
            { signal: controller.signal },
          );
          if (!controller.signal.aborted) setCreator(profile);
        }
      } catch (error) {
        if (!controller.signal.aborted) setError(message(error));
      }
    }
    void load();
    return () => controller.abort();
  }, [reload]);
  async function changeConnection(disconnect: boolean) {
    setBusy(true);
    setError("");
    try {
      if (disconnect) {
        const result = await api<{ warning: string | null }>(
          "/api/youtube-publishing/disconnect",
          { method: "POST" },
        );
        setNotice(result.warning ?? "YouTube disconnected.");
        setReload((value) => value + 1);
      } else {
        const result = await api<{ url: string }>(
          "/api/youtube-publishing/connect",
          { method: "POST" },
        );
        window.location.assign(result.url);
      }
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      aria-labelledby="youtube-connection-title"
      className="flex flex-col gap-4"
    >
      <Separator />
      <h2 id="youtube-connection-title">YouTube Shorts</h2>
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {notice ? <p role="status">{notice}</p> : null}
      {!connection ? (
        <p role="status">Loading YouTube connection...</p>
      ) : (
        <>
          <p>
            {connection.connected
              ? `Connected${creator ? ` to ${creator.channel.title}` : ""}.`
              : "Connect your YouTube channel to upload finished clips as Shorts."}
          </p>
          {!connection.configured ? (
            <p>
              Configure Google Desktop app credentials in the local backend,
              following YouTube setup in the application README, then restart
              the API.
            </p>
          ) : null}
          <div className="project-actions">
            <Button
              variant={connection.connected ? "outline" : "default"}
              disabled={
                busy || (!connection.connected && !connection.configured)
              }
              onClick={() => void changeConnection(connection.connected)}
            >
              {connection.connected ? "Disconnect YouTube" : "Connect YouTube"}
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setReload((value) => value + 1)}
            >
              Refresh YouTube connection
            </Button>
          </div>
          <p>
            Every upload needs your review and approval. Disconnecting leaves
            uploaded videos on YouTube.
          </p>
        </>
      )}
    </section>
  );
}
