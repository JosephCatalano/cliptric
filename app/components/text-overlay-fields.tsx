"use client";

import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import type { TextOverlay } from "../../lib/projects";

export function readOverlays(form: FormData): TextOverlay[] {
  return form.getAll("overlay_text").map((text, index) => ({
    text: String(text),
    kind: form.getAll("overlay_kind")[index] as TextOverlay["kind"],
    start: Number(form.getAll("overlay_start")[index]),
    end: Number(form.getAll("overlay_end")[index]),
    position: form.getAll("overlay_position")[index] as TextOverlay["position"],
    style: form.getAll("overlay_style")[index] as TextOverlay["style"],
  }));
}

export function TextOverlayFields({
  initial,
  duration,
  onChange,
}: {
  initial: TextOverlay[];
  duration: number;
  onChange(overlays: TextOverlay[]): void;
}) {
  const id = useId();
  const [rows, setRows] = useState(() =>
    initial.map((overlay, index) => ({ id: `saved-${index}`, overlay })),
  );
  return (
    <FieldSet
      onChangeCapture={(event) => {
        const form = event.currentTarget.form;
        if (form) onChange(readOverlays(new FormData(form)));
      }}
    >
      <FieldLegend>Text overlays</FieldLegend>
      <p>
        Titles, headlines and CTAs use seconds from the start of this trimmed
        clip. Auto placement avoids captions and other text. Conflicting
        placements must be adjusted before saving.
      </p>
      {rows.map(({ id: rowId, overlay }, index) => (
        <FieldSet key={rowId}>
          <FieldLegend>Overlay {index + 1}</FieldLegend>
          <Field>
            <FieldLabel htmlFor={`${id}-${rowId}-text`}>Text</FieldLabel>
            <Input
              id={`${id}-${rowId}-text`}
              name="overlay_text"
              maxLength={120}
              required
              defaultValue={overlay.text}
            />
          </Field>
          <FieldGroup className="grid gap-4 sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor={`${id}-${rowId}-kind`}>Purpose</FieldLabel>
              <NativeSelect
                id={`${id}-${rowId}-kind`}
                name="overlay_kind"
                defaultValue={overlay.kind}
              >
                <NativeSelectOption value="title">Title</NativeSelectOption>
                <NativeSelectOption value="headline">
                  Headline
                </NativeSelectOption>
                <NativeSelectOption value="cta">CTA</NativeSelectOption>
              </NativeSelect>
            </Field>
            <Field>
              <FieldLabel htmlFor={`${id}-${rowId}-style`}>Style</FieldLabel>
              <NativeSelect
                id={`${id}-${rowId}-style`}
                name="overlay_style"
                defaultValue={overlay.style}
              >
                <NativeSelectOption value="card">White card</NativeSelectOption>
                <NativeSelectOption value="outline">
                  White outlined text
                </NativeSelectOption>
                <NativeSelectOption value="violet">
                  Violet card
                </NativeSelectOption>
              </NativeSelect>
            </Field>
            <Field>
              <FieldLabel htmlFor={`${id}-${rowId}-start`}>
                Show from (clip seconds)
              </FieldLabel>
              <Input
                id={`${id}-${rowId}-start`}
                name="overlay_start"
                type="number"
                min={0}
                max={duration}
                step="0.01"
                required
                defaultValue={overlay.start}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor={`${id}-${rowId}-end`}>
                Hide at (clip seconds)
              </FieldLabel>
              <Input
                id={`${id}-${rowId}-end`}
                name="overlay_end"
                type="number"
                min={0.1}
                max={duration}
                step="0.01"
                required
                defaultValue={overlay.end}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor={`${id}-${rowId}-position`}>
                Placement
              </FieldLabel>
              <NativeSelect
                id={`${id}-${rowId}-position`}
                name="overlay_position"
                defaultValue={overlay.position}
              >
                <NativeSelectOption value="auto">Auto</NativeSelectOption>
                <NativeSelectOption value="upper">Top</NativeSelectOption>
                <NativeSelectOption value="centre">Centre</NativeSelectOption>
                <NativeSelectOption value="lower">Bottom</NativeSelectOption>
              </NativeSelect>
            </Field>
          </FieldGroup>
          <Button
            type="button"
            variant="outline"
            onClick={(event) => {
              const form = event.currentTarget.form;
              if (form)
                onChange(
                  readOverlays(new FormData(form)).filter(
                    (_, at) => at !== index,
                  ),
                );
              setRows((current) => current.filter((row) => row.id !== rowId));
            }}
          >
            Remove overlay {index + 1}
          </Button>
        </FieldSet>
      ))}
      <Button
        type="button"
        variant="outline"
        disabled={rows.length >= 6}
        onClick={(event) => {
          const overlay: TextOverlay = {
            text: "",
            kind: "cta",
            start: 0,
            end: Math.min(5, duration),
            position: "auto",
            style: "card",
          };
          const row = { id: crypto.randomUUID(), overlay };
          const form = event.currentTarget.form;
          if (form) onChange([...readOverlays(new FormData(form)), overlay]);
          setRows((current) => [...current, row]);
        }}
      >
        Add text overlay
      </Button>
    </FieldSet>
  );
}
