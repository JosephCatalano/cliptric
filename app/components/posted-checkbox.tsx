"use client";

import { useId, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldLabel } from "@/components/ui/field";
import { api, message } from "@/lib/client-api";

export function PostedCheckbox({
  path,
  checked = false,
  disabled,
  title,
  onSaved,
}: {
  path: string;
  checked?: boolean;
  disabled?: boolean;
  title: string;
  onSaved(posted: boolean): void;
}) {
  const id = useId();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  async function save(posted: boolean) {
    setSaving(true);
    setError("");
    try {
      await api<unknown>(path, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ posted }),
      });
      onSaved(posted);
    } catch (cause) {
      setError(message(cause));
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="flex flex-col gap-2">
      <Field orientation="horizontal">
        <Checkbox
          id={id}
          aria-label={`Posted: ${title}`}
          checked={checked}
          disabled={disabled || saving}
          onCheckedChange={(value) => void save(value === true)}
        />
        <FieldLabel htmlFor={id}>Posted</FieldLabel>
        {saving && (
          <span role="status" className="text-sm text-muted-foreground">
            Saving…
          </span>
        )}
      </Field>
      {error && (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
    </div>
  );
}
