"use client";
import { useId, useState } from "react";
import { FramingEditor } from "./framing-editor";
import type { Recording, SourceRegion } from "@/lib/projects";
import { Field, FieldLabel } from "@/components/ui/field";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import {
  Empty,
  EmptyHeader,
  EmptyTitle,
  EmptyDescription,
} from "@/components/ui/empty";

export function BatchSourcePreview({
  recordings,
  mode,
  cropRegion,
  disabled,
  onChange,
}: {
  recordings: Recording[];
  mode: "pad" | "crop";
  cropRegion: SourceRegion | null;
  disabled: boolean;
  onChange(mode: "pad" | "crop", crop: SourceRegion | null): void;
}) {
  const id = useId();
  const [selected, setSelected] = useState("");
  const source =
    recordings.find((recording) => recording.id === selected) ?? recordings[0];
  return source ? (
    <div className="flex flex-col gap-3">
      <Field>
        <FieldLabel htmlFor={id}>Preview video</FieldLabel>
        <NativeSelect
          id={id}
          value={source.id}
          onChange={(event) => setSelected(event.target.value)}
        >
          {recordings.map((recording) => (
            <NativeSelectOption key={recording.id} value={recording.id}>
              {recording.name}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </Field>
      <FramingEditor
        key={source.id}
        mode={mode}
        layout={null}
        cropRegion={cropRegion}
        sourceUrl={`/api/recordings/${source.id}/media`}
        end={source.duration}
        disabled={disabled}
        allowSplit={false}
        onChange={(next, _layout, crop) => {
          if (next !== "split") onChange(next, crop);
        }}
      />
      <p className="text-muted-foreground text-sm">
        Shared framing applies to all videos. You can adjust each generated clip
        afterward.
      </p>
    </div>
  ) : (
    <Empty>
      <EmptyHeader>
        <EmptyTitle>Add videos to preview</EmptyTitle>
        <EmptyDescription>
          Import files or choose saved videos.
        </EmptyDescription>
      </EmptyHeader>
    </Empty>
  );
}
