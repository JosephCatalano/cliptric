"use client";

import { useId, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
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
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { api, message } from "@/lib/client-api";
import type { Project } from "@/lib/projects";

export function MoreClips({
  projectId,
  disabled,
  includeFrames,
}: {
  projectId: string;
  disabled: boolean;
  includeFrames: boolean;
}) {
  const router = useRouter();
  const fieldId = useId();
  const [count, setCount] = useState(3);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const token = useRef<{ count: number; id: string } | null>(null);

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!consent || disabled || busy) return;
    if (token.current?.count !== count)
      token.current = { count, id: crypto.randomUUID() };
    setBusy(true);
    setError("");
    try {
      const project = await api<Project>(
        `/api/projects/${projectId}/more-clips`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            id: token.current.id,
            max_clips: count,
            remote_consent: true,
          }),
        },
      );
      router.push(`/projects/${project.id}`);
    } catch (error) {
      setError(
        `${message(error)} Check Projects after a lost connection. Retrying the same clip count reuses this batch token.`,
      );
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Find more clips</CardTitle>
        <CardDescription>
          Come back to this VOD for another batch. Reuses the saved video,
          current transcript and project settings, and skips sections already
          selected in earlier projects and batches.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={generate}>
          <FieldGroup>
            {error ? (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}
            <Field>
              <FieldLabel htmlFor={`${fieldId}-count`}>
                Maximum new clips
              </FieldLabel>
              <NativeSelect
                id={`${fieldId}-count`}
                value={count}
                disabled={disabled || busy}
                onChange={(event) => {
                  setCount(Number(event.target.value));
                  setConsent(false);
                }}
              >
                {Array.from({ length: 20 }, (_, index) => index + 1).map(
                  (number) => (
                    <NativeSelectOption key={number} value={number}>
                      {number}
                    </NativeSelectOption>
                  ),
                )}
              </NativeSelect>
              <FieldDescription>
                Creates a new batch project within the saved analysis range. You
                may get fewer clips or none when no suitable unused moments
                remain. Deleting a generated clip keeps its section excluded.
              </FieldDescription>
            </Field>
            <Field orientation="horizontal">
              <Checkbox
                id={`${fieldId}-consent`}
                checked={consent}
                disabled={disabled || busy}
                onCheckedChange={(value) => setConsent(value === true)}
              />
              <FieldLabel htmlFor={`${fieldId}-consent`}>
                I approve sending the remaining transcript sections
                {includeFrames ? " and sampled still images with local audio measurements" : ""}
                {" "}for another AI analysis, which may incur API charges.
              </FieldLabel>
            </Field>
            <Button type="submit" disabled={disabled || busy || !consent}>
              {busy ? "Opening new batch..." : `Find up to ${count} more clips`}
            </Button>
          </FieldGroup>
        </form>
      </CardContent>
    </Card>
  );
}
