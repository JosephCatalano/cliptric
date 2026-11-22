"use client";

import { useId, useState, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { api, message } from "../../lib/client-api";
import { type CaptionSource } from "../../lib/projects";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { parseEmphasis } from "@/lib/projects";

type Props = { name: string; disabled?: boolean; source?: CaptionSource } & (
  | { value: string; onChange: (value: string) => void; defaultValue?: never }
  | { defaultValue: string; value?: never; onChange?: never }
);

function validation(value: string) {
  const words = parseEmphasis(value);
  if (words.length > 20) return "Choose at most 20 words.";
  if (words.some((word) => Array.from(word).length > 48 || /\s/u.test(word)))
    return "Each entry must be one word of at most 48 characters.";
  return "";
}

export function CaptionEmphasisField({
  value,
  defaultValue,
  onChange,
  name,
  disabled,
  source,
}: Props) {
  const id = useId();
  const [local, setLocal] = useState(defaultValue ?? "");
  const current = value ?? local;
  const error = validation(current);
  const [suggestions, setSuggestions] = useState<{
    key: string;
    words: string[];
    usage: number;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [suggestionError, setSuggestionError] = useState("");
  const action = useRef<AbortController | null>(null);
  const sourceKey = JSON.stringify(source);
  useEffect(() => () => action.current?.abort(), []);
  function change(next: string) {
    setLocal(next);
    onChange?.(next);
  }
  async function suggest() {
    if (!source || action.current) return;
    const controller = new AbortController();
    action.current = controller;
    setBusy(true);
    setSuggestionError("");
    try {
      const result = await api<{
        words: string[];
        input_tokens: number;
        output_tokens: number;
      }>(
        `/api/transcriptions/${source.transcriptionId}/emphasis`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            revision: source.revision,
            start: source.start,
            end: source.end,
            remote_consent: true,
          }),
        },
        90_000,
      );
      if (!controller.signal.aborted)
        setSuggestions({
          key: sourceKey,
          words: result.words,
          usage: result.input_tokens + result.output_tokens,
        });
    } catch (reason) {
      if (!controller.signal.aborted)
        setSuggestionError(
          `${message(reason)} No retry was made; a request that reached OpenAI may have been billed.`,
        );
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      action.current = null;
    }
  }
  return (
    <Field data-invalid={!!error}>
      <FieldLabel htmlFor={id}>Words to emphasize (optional)</FieldLabel>
      <Input
        id={id}
        name={name}
        value={current}
        maxLength={1000}
        disabled={disabled}
        placeholder="amazing, secret, burger"
        aria-invalid={!!error}
        aria-describedby={`${id}-help`}
        onChange={(event) => {
          const next = event.target.value;
          event.target.setCustomValidity(validation(next));
          change(next);
        }}
      />
      <FieldDescription id={`${id}-help`}>
        {error ||
          "Separate up to 20 words with commas. Matching spoken words become bold with an accent colour, ignoring case and surrounding punctuation."}
      </FieldDescription>
      {source && (
        <>
          <p>
            Suggesting sends this saved transcript interval to OpenAI with
            billed API usage. Review suggestions and click individual words to
            add or remove them; saving remains separate.
          </p>
          <Button
            type="button"
            variant="outline"
            disabled={
              disabled ||
              busy ||
              !Number.isFinite(source.start) ||
              !Number.isFinite(source.end) ||
              source.end <= source.start
            }
            onClick={() => void suggest()}
          >
            {busy ? "Suggesting emphasis..." : "Suggest emphasis with AI"}
          </Button>
        </>
      )}
      {suggestions !== null && suggestions.key === sourceKey && (
        <>
          <div className="flex flex-wrap gap-2">
            {suggestions.words.map((word) => {
              const words = parseEmphasis(current);
              const selected = words.some(
                (item) => item.toLocaleLowerCase() === word.toLocaleLowerCase(),
              );
              return (
                <Button
                  key={word}
                  type="button"
                  variant={selected ? "default" : "outline"}
                  aria-pressed={selected}
                  disabled={disabled || (!selected && words.length >= 20)}
                  onClick={() =>
                    change(
                      (selected
                        ? words.filter(
                            (item) =>
                              item.toLocaleLowerCase() !==
                              word.toLocaleLowerCase(),
                          )
                        : [...words, word]
                      ).join(", "),
                    )
                  }
                >
                  {word}
                </Button>
              );
            })}
          </div>
          <p role="status">
            {suggestions.words.length
              ? "Suggested from saved source words."
              : "No emphasis suggested."}{" "}
            Usage: {suggestions.usage} tokens.
          </p>
        </>
      )}
      {suggestionError && (
        <Alert variant="destructive">
          <AlertDescription>{suggestionError}</AlertDescription>
        </Alert>
      )}
    </Field>
  );
}
