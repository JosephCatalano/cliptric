"use client";

import { useId } from "react";
import { Field, FieldLabel, FieldDescription } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";

export function AiInstructionsField({
  value,
  onChange,
  mode,
  disabled,
}: {
  value: string;
  onChange(value: string): void;
  mode: "highlights" | "titles" | "review";
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <Field data-disabled={disabled}>
      <FieldLabel htmlFor={id}>Extra AI instructions (optional)</FieldLabel>
      <Textarea
        id={id}
        name="ai_instructions"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        maxLength={2000}
        rows={4}
        aria-describedby={`${id}-help`}
        placeholder={
          mode === "highlights"
            ? "Prioritize practical advice for new creators. Avoid off-topic banter. Keep titles concise and conversational."
            : "Write concise, conversational titles for new creators. Avoid clickbait and use the campaign name only when supported by the speech."
        }
      />
      <FieldDescription id={`${id}-help`}>
        {mode === "review"
          ? "Guides whether to keep each video whole or select a tighter cut, and its title."
          : mode === "highlights"
            ? "Guides AI moment selection and titles."
            : "Guides titles when Generate titles with AI is enabled."}{" "}
        Saved with this project and shared with OpenAI during those AI requests.
        Use the editing controls for caption styling, framing and audio changes.
        Leave blank to use the default instructions. {value.length}/2,000
        characters.
      </FieldDescription>
    </Field>
  );
}
