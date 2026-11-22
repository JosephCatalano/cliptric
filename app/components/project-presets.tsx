"use client";

import { useEffect, useId, useState } from "react";
import { api, message } from "@/lib/client-api";
import type { ProjectSettings } from "@/lib/projects";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldDescription,
} from "@/components/ui/field";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Alert, AlertDescription } from "@/components/ui/alert";

type Preset = {
  id: string;
  name: string;
  workflow: "long" | "batch";
  settings: ProjectSettings;
};

export function ProjectPresets({
  workflow,
  settings,
  disabled,
  onApply,
}: {
  workflow: Preset["workflow"];
  settings: ProjectSettings;
  disabled: boolean;
  onApply: (settings: ProjectSettings) => void;
}) {
  const id = useId();
  const [values, setValues] = useState<Preset[]>([]);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    api<Preset[]>("/api/project-presets", { signal: controller.signal })
      .then((values) => {
        if (!controller.signal.aborted) {
          setValues(values);
          setLoading(false);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setError(message(error));
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [refresh]);
  async function save(remove = false) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (remove) {
        await api(`/api/project-presets/${selected}`, { method: "DELETE" });
        setSelected("");
        setNotice("Preset removed. Your current settings are kept.");
      } else {
        const result = await api<Preset>("/api/project-presets", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, workflow, settings }),
        });
        setSelected(result.id);
        setName("");
        setNotice("Project preset saved.");
      }
      setLoading(true);
      setRefresh((value) => value + 1);
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  const preset = values.find((value) => value.id === selected);
  return (
    <FieldGroup>
      <Field>
        <FieldLabel htmlFor={`${id}-preset`}>Project preset</FieldLabel>
        <NativeSelect
          id={`${id}-preset`}
          value={selected}
          disabled={disabled || busy || loading}
          onChange={(event) => setSelected(event.target.value)}
        >
          <NativeSelectOption value="">
            {loading ? "Loading presets..." : "Choose saved settings"}
          </NativeSelectOption>
          {values
            .filter((value) => value.workflow === workflow)
            .map((value) => (
              <NativeSelectOption key={value.id} value={value.id}>
                {value.name}
              </NativeSelectOption>
            ))}
        </NativeSelect>
        <FieldDescription>
          Reuse captions, framing, headlines, duration preferences and AI
          instructions. The current video range and sharing consent stay
          separate.
        </FieldDescription>
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          disabled={!preset || disabled || busy || loading}
          onClick={() => {
            if (!preset) return;
            onApply({
              ...preset.settings,
              analysis_start: settings.analysis_start,
              analysis_end: settings.analysis_end,
              duration_mode:
                workflow === "batch" ? "full" : preset.settings.duration_mode,
            });
            setNotice(
              "Preset applied. Check the settings below before creating clips.",
            );
          }}
        >
          Apply preset
        </Button>
        <Button
          type="button"
          variant="ghost"
          disabled={!preset || disabled || busy || loading}
          onClick={() => void save(true)}
        >
          Remove preset
        </Button>
      </div>
      <Field>
        <FieldLabel htmlFor={`${id}-name`}>Save these settings as</FieldLabel>
        <div className="flex flex-wrap gap-2">
          <Input
            className="max-w-sm"
            id={`${id}-name`}
            value={name}
            maxLength={80}
            placeholder="e.g. Gaming campaign"
            disabled={disabled || busy || loading}
            onChange={(event) => setName(event.target.value)}
          />
          <Button
            type="button"
            variant="outline"
            disabled={!name.trim() || disabled || busy || loading}
            onClick={() => void save()}
          >
            {busy ? "Saving…" : "Save preset"}
          </Button>
        </div>
      </Field>
      {notice ? (
        <p role="status" className="text-sm text-muted-foreground">
          {notice}
        </p>
      ) : null}
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>
            {error}{" "}
            <Button
              type="button"
              variant="link"
              onClick={() => {
                setError("");
                setRefresh((v) => v + 1);
              }}
            >
              Reload presets
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}
    </FieldGroup>
  );
}
