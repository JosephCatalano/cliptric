"use client";

import { useEffect, useId, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { api, message } from "../../lib/client-api";
import { type CaptionStyle } from "../../lib/projects";

export type SavedCaptionStyle = {
  id: string;
  name: string;
  style: CaptionStyle;
};

export function CaptionStyleLibrary({
  value,
  onApply,
  disabled,
}: {
  value: CaptionStyle;
  onApply: (style: CaptionStyle) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const [styles, setStyles] = useState<SavedCaptionStyle[]>([]);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api<SavedCaptionStyle[]>("/api/caption-styles", {
      signal: controller.signal,
    })
      .then(setStyles)
      .catch((reason) => {
        if (!controller.signal.aborted) setError(message(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, []);
  async function save() {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      const saved = await api<SavedCaptionStyle>("/api/caption-styles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), style: value }),
      });
      setStyles((items) => [...items, saved]);
      setSelected(saved.id);
      setName("");
      setStatus("Caption style saved locally.");
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      await api<void>(`/api/caption-styles/${selected}`, { method: "DELETE" });
      setStyles((items) => items.filter((item) => item.id !== selected));
      setSelected("");
      setStatus("Saved style deleted. Existing clips retain their settings.");
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }
  return (
    <FieldGroup>
      <Field>
        <FieldLabel htmlFor={`${id}-saved`}>Saved caption styles</FieldLabel>
        <NativeSelect
          id={`${id}-saved`}
          value={selected}
          disabled={disabled || busy}
          onChange={(event) => setSelected(event.target.value)}
        >
          <NativeSelectOption value="">Choose a saved style</NativeSelectOption>
          {styles.map((style) => (
            <NativeSelectOption key={style.id} value={style.id}>
              {style.name}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        <Button
          type="button"
          variant="outline"
          disabled={
            disabled || busy || !styles.some((style) => style.id === selected)
          }
          onClick={() => {
            const style = styles.find((style) => style.id === selected);
            if (style) {
              onApply(style.style);
              setStatus(
                "Loaded into the editor. Save or create clips to apply.",
              );
            }
          }}
        >
          Load selected style
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={disabled || busy || !selected}
          onClick={() => void remove()}
        >
          Delete selected saved style
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={disabled || busy}
          onClick={() => {
            setError("");
            setBusy(true);
            void api<SavedCaptionStyle[]>("/api/caption-styles")
              .then(setStyles)
              .catch((reason) => setError(message(reason)))
              .finally(() => setBusy(false));
          }}
        >
          Refresh saved styles
        </Button>
      </Field>
      <Field>
        <FieldLabel htmlFor={`${id}-name`}>
          Save current caption style as
        </FieldLabel>
        <Input
          id={`${id}-name`}
          value={name}
          maxLength={60}
          disabled={disabled || busy}
          onChange={(event) => setName(event.target.value)}
        />
        <Button
          type="button"
          variant="outline"
          disabled={disabled || busy || !name.trim()}
          onClick={() => void save()}
        >
          Save my style
        </Button>
      </Field>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {status && <p role="status">{status}</p>}
    </FieldGroup>
  );
}
