"use client";

import { useState } from "react";
import { Trash2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogTrigger,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
} from "@/components/ui/alert-dialog";
import { api, ApiError, message } from "@/lib/client-api";

export function DeleteAction({
  path,
  label,
  name,
  description,
  disabled,
  onDeleted,
}: {
  path: string;
  label: string;
  name: string;
  description: string;
  disabled?: boolean;
  onDeleted(): void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [deleted, setDeleted] = useState(false);
  async function remove() {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ cleanup_pending: boolean }>(path, {
        method: "DELETE",
      });
      if (result.cleanup_pending) setDeleted(true);
      else {
        setOpen(false);
        onDeleted();
      }
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) {
        setOpen(false);
        onDeleted();
      } else setError(message(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <AlertDialog
      open={open}
      onOpenChange={(value) => {
        if (busy) return;
        setOpen(value);
        if (!value && deleted) onDeleted();
        if (value) setError("");
      }}
    >
      <AlertDialogTrigger asChild>
        <Button type="button" variant="destructive" disabled={disabled}>
          <Trash2Icon data-icon="inline-start" />
          {label}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {deleted ? "Deleted" : `${label}?`}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {deleted ? (
              "Deleted from Cliptric. Some files are still in use; cleanup will retry after the API restarts. Downloaded copies remain on your device."
            ) : (
              <>
                <strong>{name}</strong>. {description} This cannot be undone.
              </>
            )}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>
            {deleted ? "Done" : "Keep it"}
          </AlertDialogCancel>
          {!deleted && (
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => void remove()}
            >
              {busy ? "Deleting…" : label}
            </Button>
          )}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
