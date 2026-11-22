"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import { api, message } from "@/lib/client-api";
import type {
  InstagramConnection as Connection,
  InstagramCreator,
} from "@/lib/instagram";

export function InstagramConnection() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [creator, setCreator] = useState<InstagramCreator | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const value = await api<Connection>("/api/instagram", {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setConnection(value);
        setCreator(null);
        setError("");
        if (
          new URLSearchParams(window.location.search).get("instagram") ===
          "cancelled"
        )
          setNotice("Instagram connection was cancelled. You can try again.");
        if (value.connected) {
          const profile = await api<InstagramCreator>(
            "/api/instagram/creator",
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
        const result = await api<{ warning: string }>(
          "/api/instagram/disconnect",
          { method: "POST" },
        );
        setNotice(result.warning);
        setReload((value) => value + 1);
      } else {
        const result = await api<{ url: string }>("/api/instagram/connect", {
          method: "POST",
        });
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
      aria-labelledby="instagram-connection-title"
      className="flex flex-col gap-4"
    >
      <Separator />
      <h2 id="instagram-connection-title">Instagram Reels</h2>
      {error ? (
        <Alert role="alert" variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {notice ? <p role="status">{notice}</p> : null}
      {!connection ? (
        <p role="status">Loading Instagram connection...</p>
      ) : (
        <>
          <p>
            {connection.connected
              ? `Connected${creator ? ` to @${creator.username}` : ""}.`
              : "Connect Instagram to publish finished clips as Reels."}
          </p>
          {!connection.configured ? (
            <p>
              Configure the Meta app and Facebook Page using Instagram setup in
              the application README, then restart the API.
            </p>
          ) : null}
          <p>
            A Business or Creator account linked to a Facebook Page is required.
            Each Reel needs your review and approval.
          </p>
          <div className="project-actions">
            <Button
              variant={connection.connected ? "outline" : "default"}
              disabled={
                busy || (!connection.connected && !connection.configured)
              }
              onClick={() => void changeConnection(connection.connected)}
            >
              {connection.connected
                ? "Disconnect Instagram"
                : "Connect Instagram"}
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setReload((value) => value + 1)}
            >
              Refresh Instagram connection
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
