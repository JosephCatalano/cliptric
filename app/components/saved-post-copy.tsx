"use client";
import { useEffect, useState } from "react";
import { api, message } from "@/lib/client-api";
import { Button } from "@/components/ui/button";

export function SavedPostCopy({
  exportId,
  onApply,
}: {
  exportId: string;
  onApply: (text: string) => void;
}) {
  const [saved, setSaved] = useState<{
    exportId: string;
    text: string | null;
  } | null>(null);
  const text = saved?.exportId === exportId ? saved.text : null;
  const [failure, setFailure] = useState<{
    exportId: string;
    message: string;
  } | null>(null);
  const error = failure?.exportId === exportId ? failure.message : "";
  useEffect(() => {
    const controller = new AbortController();
    api<{ ready_to_post: string | null }>(`/api/clips/${exportId}/post-copy`, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted)
          setSaved({ exportId, text: value.ready_to_post });
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setFailure({ exportId, message: message(e) });
      });
    return () => controller.abort();
  }, [exportId]);
  return text ? (
    <Button type="button" variant="outline" onClick={() => onApply(text)}>
      Use saved description and hashtags (replaces text)
    </Button>
  ) : error ? (
    <p role="status" className="text-sm text-muted-foreground">
      Saved posting copy could not load: {error}
    </p>
  ) : null;
}
