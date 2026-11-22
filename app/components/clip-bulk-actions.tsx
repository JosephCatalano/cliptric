"use client";

import { useState } from "react";
import type { Project } from "@/lib/projects";
import { api, message } from "@/lib/client-api";
import { Button } from "@/components/ui/button";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription } from "@/components/ui/alert";

export function ClipBulkActions({
  project,
  selected,
  disabled,
  onSaved,
  onBusy,
}: {
  project: Project;
  selected: Set<string>;
  disabled: boolean;
  onSaved: (value: Project) => void;
  onBusy: (value: boolean) => void;
}) {
  const [action, setAction] = useState("posted");
  const [position, setPosition] = useState("auto");
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const count = project.items.filter((item) => selected.has(item.id)).length;
  async function apply() {
    onBusy(true);
    setError("");
    setNotice("");
    const targets = project.items
      .filter((item) => selected.has(item.id))
      .map((item) => ({ id: item.id, revision: item.revision }));
    const operation =
      action === "posted" || action === "unposted"
        ? { action: "posted", posted: action === "posted" }
        : action === "accepted" || action === "rejected"
          ? { action: "review", review: action }
          : {
              action: "headline",
              headline_enabled: action === "headline",
              headline_position: position,
              opening_text: text.trim() || null,
            };
    try {
      const value = await api<Project>(`/api/projects/${project.id}/bulk`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ targets, ...operation }),
      });
      onSaved(value);
      setNotice(
        `Updated ${targets.length} clips.${operation.action === "headline" ? " Changed clips are rendering again." : ""}`,
      );
    } catch (error) {
      setError(message(error));
    } finally {
      onBusy(false);
    }
  }
  return (
    <FieldGroup className="selection-actions">
      <Field>
        <FieldLabel className="sr-only" htmlFor="clip-bulk-action">
          Action for {count} selected clips
        </FieldLabel>
        <NativeSelect
          id="clip-bulk-action"
          value={action}
          disabled={disabled}
          onChange={(event) => setAction(event.target.value)}
        >
          <NativeSelectOption value="posted">Mark posted</NativeSelectOption>
          <NativeSelectOption value="unposted">
            Mark unposted
          </NativeSelectOption>
          <NativeSelectOption value="accepted">Accept</NativeSelectOption>
          <NativeSelectOption value="rejected">Reject</NativeSelectOption>
          <NativeSelectOption value="headline">
            Set opening headlines
          </NativeSelectOption>
          <NativeSelectOption value="no-headline">
            Disable opening headlines
          </NativeSelectOption>
        </NativeSelect>
      </Field>
      {action === "headline" ? (
        <>
          <Field>
            <FieldLabel htmlFor="bulk-headline">
              Headline text (leave blank to use each clip title)
            </FieldLabel>
            <Input
              id="bulk-headline"
              maxLength={120}
              value={text}
              disabled={disabled}
              onChange={(event) => setText(event.target.value)}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="bulk-headline-position">
              Headline position
            </FieldLabel>
            <NativeSelect
              id="bulk-headline-position"
              value={position}
              disabled={disabled}
              onChange={(event) => setPosition(event.target.value)}
            >
              <NativeSelectOption value="auto">Auto</NativeSelectOption>
              <NativeSelectOption value="upper">Upper</NativeSelectOption>
              <NativeSelectOption value="centre">Centre</NativeSelectOption>
            </NativeSelect>
          </Field>
        </>
      ) : null}
      <div>
        <Button
          type="button"
          variant="outline"
          disabled={disabled || !count}
          onClick={() => void apply()}
        >
          Apply
        </Button>
      </div>
      {notice ? <p role="status">{notice}</p> : null}
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
    </FieldGroup>
  );
}
