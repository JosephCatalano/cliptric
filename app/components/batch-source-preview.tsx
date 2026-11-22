"use client";
import { useId, useState } from "react";
import type { Recording } from "@/lib/projects";
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
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export function BatchSourcePreview({
  recordings,
}: {
  recordings: Recording[];
}) {
  const id = useId();
  const [selected, setSelected] = useState("");
  const [failed, setFailed] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
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
      <video
        key={`${source.id}-${reload}`}
        controls
        playsInline
        preload="metadata"
        className="w-full rounded-lg object-contain"
        src={`/api/recordings/${source.id}/media`}
        poster={`/api/recordings/${source.id}/thumbnail`}
        aria-label={source.name}
        onError={() => setFailed(source.id)}
      />
      {failed === source.id ? (
        <Alert>
          <AlertDescription>
            Browser preview is unavailable. The imported source is still
            available for processing.{" "}
            <Button
              type="button"
              variant="link"
              onClick={() => {
                setFailed(null);
                setReload((value) => value + 1);
              }}
            >
              Retry preview
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}
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
