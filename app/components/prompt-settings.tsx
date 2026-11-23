"use client";

import { useEffect, useState } from "react";
import { api, message } from "@/lib/client-api";
import { useUnsavedEdits } from "./use-unsaved-edits";
import { Button } from "@/components/ui/button";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from "@/components/ui/accordion";

type Prompt = {
  key: string;
  label: string;
  description: string;
  default: string;
  value: string;
  customized: boolean;
};
type Settings = {
  revision: number;
  protected_rules: string;
  prompts: Prompt[];
};

export function PromptSettings() {
  const [saved, setSaved] = useState<Settings | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [selectedKey, setSelectedKey] = useState("");
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const dirty =
    !!saved &&
    saved.prompts.some((prompt) => drafts[prompt.key] !== prompt.value);
  useUnsavedEdits(dirty);
  useEffect(() => {
    const controller = new AbortController();
    api<Settings>("/api/settings/prompts", { signal: controller.signal })
      .then((value) => {
        if (controller.signal.aborted) return;
        setSaved(value);
        setDrafts(
          Object.fromEntries(
            value.prompts.map((prompt) => [prompt.key, prompt.value]),
          ),
        );
        setError("");
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(message(e));
      });
    return () => controller.abort();
  }, [reload]);
  function load() {
    if (
      dirty &&
      !window.confirm("Discard unsaved prompt edits and reload settings?")
    )
      return;
    setSaved(null);
    setNotice("");
    setError("");
    setReload((value) => value + 1);
  }
  async function save(prompt: Prompt) {
    if (!saved) return;
    const value = drafts[prompt.key] ?? "";
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<Settings>("/api/settings/prompts", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          key: prompt.key,
          revision: saved.revision,
          prompt: value,
        }),
      });
      setSaved(result);
      setDrafts((current) => ({
        ...current,
        [prompt.key]: result.prompts.find((p) => p.key === prompt.key)!.value,
      }));
      setNotice(`${prompt.label} saved. Future requests will use it.`);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="flex min-w-0 flex-col gap-5"
      aria-label="AI prompt settings"
    >
      <div className="flex flex-col gap-2">
        <h2>AI prompts</h2>
        <p className="text-sm text-muted-foreground">
          Edit the instructions used by OpenAI. Changes apply to future requests
          and keep saved clips intact. Finish or cancel queued processing before
          saving.
        </p>
      </div>
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>
            {error}{" "}
            <Button type="button" variant="link" disabled={busy} onClick={load}>
              Reload settings
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}
      {!saved && !error ? <Skeleton className="h-80 w-full" /> : null}
      {saved ? (
        <div className="grid min-w-0 items-start gap-5 lg:grid-cols-[15rem_minmax(0,1fr)]">
          <NativeSelect
            className="w-full lg:hidden"
            aria-label="Prompt stage"
            value={selectedKey || saved.prompts[0]?.key}
            onChange={(event) => {
              setSelectedKey(event.target.value);
              setNotice("");
            }}
          >
            {saved.prompts.map((prompt) => (
              <NativeSelectOption key={prompt.key} value={prompt.key}>
                {prompt.label}
                {drafts[prompt.key] !== prompt.value ? " (draft)" : ""}
              </NativeSelectOption>
            ))}
          </NativeSelect>
          <nav
            aria-label="Prompt stages"
            className="hidden flex-col gap-1 rounded-xl border bg-card p-2 lg:flex"
          >
            {saved.prompts.map((prompt) => (
              <Button
                key={prompt.key}
                type="button"
                aria-current={
                  (selectedKey || saved.prompts[0]?.key) === prompt.key
                    ? "true"
                    : undefined
                }
                variant={
                  (selectedKey || saved.prompts[0]?.key) === prompt.key
                    ? "secondary"
                    : "ghost"
                }
                className="h-auto min-h-9 justify-start whitespace-normal text-left"
                onClick={() => {
                  setSelectedKey(prompt.key);
                  setNotice("");
                }}
              >
                <span className="flex-1">{prompt.label}</span>
                {drafts[prompt.key] !== prompt.value && (
                  <span className="text-xs text-muted-foreground">Draft</span>
                )}
              </Button>
            ))}
          </nav>
          <div className="flex min-w-0 flex-col gap-4">
            {saved.prompts
              .filter(
                (prompt) =>
                  prompt.key === (selectedKey || saved.prompts[0]?.key),
              )
              .map((prompt) => {
                const value = drafts[prompt.key] ?? "";
                const encodedBytes = new TextEncoder().encode(
                  JSON.stringify(value.trim()),
                ).length;
                return (
                  <FieldGroup
                    key={prompt.key}
                    className="rounded-xl border bg-card p-4 md:p-6"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <h3>{prompt.label}</h3>
                        <FieldDescription>
                          {prompt.description}
                        </FieldDescription>
                      </div>
                      <Badge variant="secondary">
                        {value === prompt.value ? "Saved" : "Unsaved changes"}
                      </Badge>
                    </div>
                    <div className="text-sm text-muted-foreground">
                      {encodedBytes.toLocaleString()} / 14,000 bytes
                    </div>
                    <Field data-invalid={encodedBytes > 14000}>
                      <FieldLabel htmlFor={`prompt-${prompt.key}`}>
                        Instructions
                      </FieldLabel>
                      <Textarea
                        id={`prompt-${prompt.key}`}
                        className="min-h-64 resize-y overflow-y-auto"
                        value={value}
                        maxLength={12000}
                        aria-invalid={encodedBytes > 14000}
                        disabled={busy}
                        onChange={(event) => {
                          setDrafts((current) => ({
                            ...current,
                            [prompt.key]: event.target.value,
                          }));
                          setNotice("");
                        }}
                      />
                      <FieldDescription>
                        Plain text. Project instructions are supplied separately
                        when present.
                      </FieldDescription>
                    </Field>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        disabled={
                          busy ||
                          value === prompt.value ||
                          !value.trim() ||
                          encodedBytes > 14000
                        }
                        onClick={() => void save(prompt)}
                      >
                        {busy ? "Saving…" : "Save prompt"}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        disabled={busy || value === prompt.default}
                        onClick={() => {
                          if (
                            !window.confirm(
                              "Replace the current draft with the default instructions?",
                            )
                          )
                            return;
                          setDrafts((current) => ({
                            ...current,
                            [prompt.key]: prompt.default,
                          }));
                          setNotice(
                            "Default restored in the editor. Save to apply.",
                          );
                        }}
                      >
                        Restore default
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        disabled={busy || value === prompt.value}
                        onClick={() =>
                          setDrafts((current) => ({
                            ...current,
                            [prompt.key]: prompt.value,
                          }))
                        }
                      >
                        Discard draft
                      </Button>
                    </div>
                  </FieldGroup>
                );
              })}
            {notice ? (
              <p role="status" className="text-sm text-muted-foreground">
                {notice}
              </p>
            ) : null}
            <Accordion type="single" collapsible>
              <AccordionItem value="contract">
                <AccordionTrigger>Protected application rules</AccordionTrigger>
                <AccordionContent>
                  <p className="mb-3 text-sm text-muted-foreground">
                    These rules are appended to requests. JSON schemas, source
                    validation, API credentials and publication approval stay
                    controlled by the application. Conflicting prompt edits can
                    still produce rejected model responses; review results
                    before posting.
                  </p>
                  <pre className="whitespace-pre-wrap text-sm">
                    {saved.protected_rules}
                  </pre>
                </AccordionContent>
              </AccordionItem>
            </Accordion>
          </div>
        </div>
      ) : null}
    </section>
  );
}
