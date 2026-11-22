"use client";

import { useId } from "react";
import { Field, FieldLabel, FieldDescription } from "@/components/ui/field";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import type { ProjectSettings } from "@/lib/projects";

export function PresentationField({
  value,
  onChange,
  disabled,
}: {
  value: NonNullable<ProjectSettings["presentation_mode"]>;
  onChange(value: NonNullable<ProjectSettings["presentation_mode"]>): void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <Field>
      <FieldLabel htmlFor={id}>Automatic presentation</FieldLabel>
      <NativeSelect
        id={id}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as typeof value)}
      >
        <NativeSelectOption value="auto">
          Auto: framing, cover and text placement
        </NativeSelectOption>
        <NativeSelectOption value="talking_head">
          Talking head: follow a clear face
        </NativeSelectOption>
        <NativeSelectOption value="podcast">
          Podcast: preserve participants
        </NativeSelectOption>
        <NativeSelectOption value="gameplay">
          Gameplay: preserve picture and HUD
        </NativeSelectOption>
        <NativeSelectOption value="off">
          Off: use my framing and placement
        </NativeSelectOption>
      </NativeSelect>
      <FieldDescription>
        Chooses an opening cover frame, checks caption/title placement, and
        follows a face when a safe crop is clear. Preserves the full picture
        when uncertain. Runs locally and keeps your chosen caption style. Review
        the result in Trim / edit.
      </FieldDescription>
    </Field>
  );
}
