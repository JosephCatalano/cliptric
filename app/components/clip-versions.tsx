"use client";

import { useEffect, useState } from "react";
import { api, message } from "@/lib/client-api";
import type { Project, ProjectItem } from "@/lib/projects";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Field, FieldLabel } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/skeleton";

type Version = {
  id: string;
  created_at: string;
  export_id: string | null;
  playable: boolean;
  settings: ProjectItem;
};

export function ClipVersions({
  project,
  item,
  locked,
  onSaved,
}: {
  project: Project;
  item: ProjectItem;
  locked: boolean;
  onSaved: (project: Project) => void;
}) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Version[]>([]);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const path = `/api/projects/${project.id}/clips/${item.id}`;
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    api<Version[]>(`${path}/versions`, { signal: controller.signal })
      .then((versions) => {
        if (controller.signal.aborted) return;
        setValues(versions);
        setSelected(versions[0]?.id ?? "");
        setError("");
        setLoading(false);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setError(message(error));
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [open, path, refresh]);
  const version = loading
    ? undefined
    : values.find((value) => value.id === selected);
  async function restore() {
    if (!version) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<Project>(`${path}/restore`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: version.id, revision: item.revision }),
      });
      onSaved(result);
      setOpen(false);
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!busy) {
          setOpen(value);
          if (value) setLoading(true);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" variant="outline" disabled={locked}>
          Versions / undo
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Restore a previous version</DialogTitle>
          <DialogDescription>
            {item.title}. Restore settings from before an edit. Matching saved
            video is reused; otherwise only this clip is rendered again. Posted
            status and publishing approvals stay separate.
          </DialogDescription>
        </DialogHeader>
        {loading ? (
          <Skeleton className="h-24" />
        ) : !values.length && !error ? (
          <p>
            No earlier edits are saved yet. Your next edit will create an undo
            point.
          </p>
        ) : null}
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>
              {error}{" "}
              <Button
                type="button"
                variant="link"
                disabled={busy}
                onClick={() => {
                  setLoading(true);
                  setRefresh((value) => value + 1);
                }}
              >
                Reload versions
              </Button>
            </AlertDescription>
          </Alert>
        ) : null}
        {values.length ? (
          <Field>
            <FieldLabel htmlFor={`versions-${item.id}`}>
              Earlier version
            </FieldLabel>
            <NativeSelect
              id={`versions-${item.id}`}
              value={selected}
              disabled={busy || loading}
              onChange={(event) => setSelected(event.target.value)}
            >
              {values.map((value, index) => (
                <NativeSelectOption key={value.id} value={value.id}>
                  {index === 0 ? "Undo last edit: " : ""}
                  {new Date(value.created_at).toLocaleString()} —{" "}
                  {value.settings.title}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>
        ) : null}
        {version ? (
          <>
            <p>
              {version.settings.start.toFixed(1)}–
              {version.settings.end.toFixed(1)}s ·{" "}
              {version.settings.caption_preset} captions ·{" "}
              {version.settings.review}
            </p>
            <div className="grid gap-4 sm:grid-cols-2">
              {item.export?.status === "succeeded" ? (
                <div>
                  <p>Current export</p>
                  <video
                    className="max-h-[45dvh] w-full object-contain"
                    controls
                    preload="metadata"
                    src={`/api/clips/${item.export.id}/media`}
                    aria-label="Current export preview"
                    onError={() =>
                      setError(
                        "Current preview could not load. Reload versions to retry.",
                      )
                    }
                  />
                </div>
              ) : (
                <p>Current export is not ready.</p>
              )}
              {version.playable ? (
                <div>
                  <p>Earlier export</p>
                  <video
                    key={version.export_id}
                    className="max-h-[45dvh] w-full object-contain"
                    controls
                    preload="metadata"
                    src={`/api/clips/${version.export_id}/media`}
                    aria-label="Earlier export preview"
                    onError={() =>
                      setError(
                        "Earlier preview could not load. Reload versions to retry.",
                      )
                    }
                  />
                </div>
              ) : (
                <p>
                  Settings are available. This version needs a fresh render.
                </p>
              )}
            </div>
            <Button
              type="button"
              disabled={busy || locked || loading}
              onClick={() => void restore()}
            >
              {busy ? "Restoring…" : "Restore this version"}
            </Button>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
