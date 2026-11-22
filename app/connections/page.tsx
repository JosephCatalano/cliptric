"use client";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";

import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, message } from "../../lib/client-api";
import type { TikTokConnection, TikTokCreator } from "../../lib/tiktok";
import { YouTubeConnection } from "../components/youtube-connection";
import { InstagramConnection } from "../components/instagram-connection";

export default function ConnectionsPage() {
  const [connection, setConnection] = useState<TikTokConnection | null>(null);
  const [creator, setCreator] = useState<TikTokCreator | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const value = await api<TikTokConnection>("/api/tiktok", {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setConnection(value);
        setCreator(null);
        setError("");
        if (
          new URLSearchParams(window.location.search).get("tiktok") ===
          "cancelled"
        )
          setNotice("TikTok connection was cancelled. You can try again.");
        if (value.connected) {
          const profile = await api<TikTokCreator>("/api/tiktok/creator", {
            signal: controller.signal,
          });
          if (!controller.signal.aborted) setCreator(profile);
        }
      } catch (error) {
        if (!controller.signal.aborted) setError(message(error));
      }
    }
    void load();
    return () => controller.abort();
  }, [refresh]);
  async function connect() {
    setBusy(true);
    setError("");
    try {
      const response = await api<{ url: string }>("/api/tiktok/connect", {
        method: "POST",
      });
      window.location.assign(response.url);
    } catch (error) {
      setError(message(error));
      setBusy(false);
    }
  }
  async function disconnect() {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ warning?: string }>("/api/tiktok/disconnect", {
        method: "POST",
      });
      setNotice(result.warning ?? "TikTok disconnected.");
      setRefresh((value) => value + 1);
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="project-workspace tiktok-workspace">
      <header className="project-heading">
        <h1>Connected accounts</h1>
        <Button asChild variant="outline">
          <Link href="/projects">Your projects</Link>
        </Button>
      </header>
      <YouTubeConnection />
      <InstagramConnection />
      <Card>
        <CardHeader>
          <CardTitle role="heading" aria-level={2}>
            TikTok
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {error ? (
            <Alert role="alert" variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          {notice ? <p role="status">{notice}</p> : null}
          {!connection ? (
            <p role="status">Loading connection…</p>
          ) : (
            <>
              <p>
                {connection.connected
                  ? `Connected${creator ? ` to ${creator.creator_nickname} (@${creator.creator_username})` : ""}.`
                  : "Connect TikTok to post a finished clip from its project."}
              </p>
              {!connection.configured ? (
                <p>
                  The TikTok developer app has not been configured for this
                  installation. Follow the TikTok setup in the application
                  README, then restart the API.
                </p>
              ) : null}
              {!connection.audited ? (
                <p>
                  Test mode: TikTok requires a private account and Only me
                  visibility. Public posting needs TikTok approval for this app.
                </p>
              ) : null}
              <div className="project-actions">
                {connection.connected ? (
                  <Button
                    disabled={busy}
                    onClick={disconnect}
                    variant="outline"
                  >
                    Disconnect TikTok
                  </Button>
                ) : (
                  <Button
                    disabled={busy || !connection.configured}
                    onClick={connect}
                    variant="default"
                  >
                    {busy ? "Connecting…" : "Connect TikTok"}
                  </Button>
                )}
                <Button
                  disabled={busy}
                  onClick={() => setRefresh((value) => value + 1)}
                  variant="outline"
                >
                  Refresh connection
                </Button>
              </div>
              <p>
                Each post needs your review and approval. Disconnecting does not
                remove videos already sent to TikTok.
              </p>
            </>
          )}
        </CardContent>
      </Card>
    </section>
  );
}
