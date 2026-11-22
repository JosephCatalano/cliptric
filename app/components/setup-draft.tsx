"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError, message } from "@/lib/client-api";
import Link from "next/link";
import type { ProjectSettings } from "@/lib/projects";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export type SetupDraft = {
  title: string;
  settings: ProjectSettings;
  recording_ids: string[];
  creation_token?: string | null;
  creation_signature?: string | null;
};

export function useSetupDraft(
  key: string,
  value: SetupDraft,
  enabled: boolean,
) {
  const [candidate, setCandidate] = useState<unknown>(null);
  const [loadedKey, setLoadedKey] = useState("");
  const [status, setStatus] = useState("");
  const [lastSaved, setLastSaved] = useState("");
  const [storageError, setStorageError] = useState("");
  const stopped = useRef(false);
  const serialized = JSON.stringify(value);
  useEffect(() => {
    let cancelled = false;
    Promise.resolve().then(() => {
      if (cancelled) return;
      setLastSaved("");
      setStatus("");
      try {
        const saved = localStorage.getItem(key);
        setCandidate(saved ? JSON.parse(saved) : null);
        stopped.current = false;
      } catch {
        setStatus(
          "Saved setup could not be read. Draft recovery is unavailable in this browser.",
        );
      }
      setLoadedKey(key);
    });
    return () => {
      cancelled = true;
    };
  }, [key]);
  useEffect(() => {
    if (loadedKey !== key || candidate || !enabled || stopped.current) return;
    const timer = setTimeout(() => {
      try {
        localStorage.setItem(key, serialized);
        setStatus("Setup saved on this browser.");
        setLastSaved(serialized);
        setStorageError("");
      } catch {
        setStorageError(
          "Setup could not be saved. Keep this page open until you create the project.",
        );
      }
    }, 600);
    return () => clearTimeout(timer);
  }, [key, serialized, loadedKey, candidate, enabled]);
  function clear() {
    stopped.current = true;
    try {
      localStorage.removeItem(key);
    } catch {
      setStatus("Could not remove the saved setup from this browser.");
    }
    setCandidate(null);
  }
  function persistCurrent() {
    try {
      localStorage.setItem(key, serialized);
    } catch {
      setStorageError(
        "Setup could not be saved. Check Projects after a connection loss before creating another project.",
      );
    }
  }
  function persistToken(token: { id: string; signature: string }) {
    try {
      localStorage.setItem(
        key,
        JSON.stringify({
          ...value,
          creation_token: token.id,
          creation_signature: token.signature,
        }),
      );
    } catch {
      setStatus(
        "Creation token could not be saved. Check Projects before retrying after a reload.",
      );
    }
  }
  return {
    candidate: loadedKey === key ? candidate : null,
    status:
      storageError ||
      (loadedKey === key && enabled && !candidate && lastSaved !== serialized
        ? "Saving setup…"
        : status),
    clear,
    persistCurrent,
    persistToken,
    dismiss: () => {
      clear();
      stopped.current = false;
    },
    restored: () => setCandidate(null),
  };
}

export function SetupDraftNotice({
  draft,
  onRestore,
}: {
  draft: ReturnType<typeof useSetupDraft>;
  onRestore: (value: SetupDraft) => void | Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [existing, setExisting] = useState<string | null>(null);
  async function restore() {
    setBusy(true);
    setError("");
    try {
      const value = await api<SetupDraft>("/api/setup/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft.candidate),
      });
      if (value.creation_token) {
        try {
          await api(`/api/projects/${value.creation_token}`);
          setExisting(value.creation_token);
          return;
        } catch (error) {
          if (!(error instanceof ApiError) || error.status !== 404) throw error;
        }
      }
      await onRestore(value);
      draft.restored();
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      {draft.candidate ? (
        <Alert>
          <AlertDescription>
            An unfinished setup is saved on this browser. Imported videos can be
            recovered; files that were not imported must be selected again.
            Sharing consent must be confirmed again.
            <div className="flex flex-wrap gap-2">
              {existing ? (
                <Button asChild variant="outline">
                  <Link href={`/projects/${existing}`} onClick={draft.clear}>
                    Open the project already created from this setup
                  </Link>
                </Button>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void restore()}
                >
                  {busy ? "Restoring…" : "Restore setup"}
                </Button>
              )}
              <Button
                type="button"
                variant="ghost"
                disabled={busy}
                onClick={draft.dismiss}
              >
                Discard saved setup
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      ) : (
        <p role="status" className="text-sm text-muted-foreground">
          {draft.status}
        </p>
      )}
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
    </>
  );
}
