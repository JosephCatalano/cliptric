"use client";
import { useRef, useState } from "react";
import { api, message } from "@/lib/client-api";
import type { Project, ProjectItem } from "@/lib/projects";
import { formatPostCopy, splitPostCopy } from "@/lib/post-copy";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";

export function PostCopyEditor({
  projectId,
  item,
  disabled,
  onSaved,
}: {
  projectId: string;
  item: ProjectItem;
  disabled: boolean;
  onSaved: (project: Project) => void;
}) {
  const [open, setOpen] = useState(false);
  const [dirty, setDirty] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && dirty && !window.confirm("Discard unsaved posting copy?"))
          return;
        setOpen(next);
        if (!next) setDirty(false);
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" variant="outline" size="sm">
          {item.post_copy_error || item.post_copy_stale
            ? "Review post copy"
            : item.post_copy || item.post_copy_selected
              ? "Post copy"
              : "Description & tags"}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Description and hashtags</DialogTitle>
          <DialogDescription>
            {item.title}. Posting text is separate from video captions.
          </DialogDescription>
        </DialogHeader>
        {open ? (
          <CopyForm
            key={`${item.id}-${item.revision}-${item.post_copy_request_id}`}
            projectId={projectId}
            item={item}
            disabled={disabled}
            onSaved={(value) => {
              setDirty(false);
              onSaved(value);
            }}
            onDirty={setDirty}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function CopyForm({
  projectId,
  item,
  disabled,
  onSaved,
  onDirty,
}: {
  projectId: string;
  item: ProjectItem;
  disabled: boolean;
  onSaved: (project: Project) => void;
  onDirty: (dirty: boolean) => void;
}) {
  const initial = formatPostCopy(
    item.post_copy_selected ?? item.post_copy?.recommended,
  );
  const [text, setText] = useState(initial);
  const content = splitPostCopy(text);
  const valid =
    !!content.description &&
    content.description.length <= 500 &&
    content.hashtags.length <= 5;
  const [choice, setChoice] = useState("recommended");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [generationFailed, setGenerationFailed] = useState(false);
  const token = useRef<string | null>(null);
  const path = `/api/projects/${projectId}/clips/${item.id}/post-copy`;
  async function run(generate: boolean, newAttempt = false) {
    if (
      generate &&
      text !== initial &&
      !window.confirm("Generate new options and replace unsaved posting text?")
    )
      return;
    setGenerationFailed(false);
    setBusy(true);
    setError("");
    if (generate && (newAttempt || !token.current))
      token.current = crypto.randomUUID();
    try {
      const result = await api<Project>(path, {
        method: generate ? "POST" : "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          generate
            ? { request_id: token.current, revision: item.revision, consent }
            : {
                revision: item.revision,
                content,
              },
        ),
      });
      onSaved(result);
    } catch (e) {
      setError(message(e));
      if (generate) setGenerationFailed(true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <FieldGroup>
      {item.post_copy_error ? (
        <Alert>
          <AlertDescription>
            {item.post_copy_error} Check API usage before starting another paid
            attempt.
          </AlertDescription>
        </Alert>
      ) : null}
      {item.post_copy_stale ? (
        <Alert>
          <AlertDescription>
            The clip interval changed. Review and save this text or generate new
            options before posting.
          </AlertDescription>
        </Alert>
      ) : null}
      {item.post_copy ? (
        <>
          <Field>
            <FieldLabel htmlFor={`copy-choice-${item.id}`}>
              Writing angle
            </FieldLabel>
            <NativeSelect
              id={`copy-choice-${item.id}`}
              value={choice}
              disabled={busy || disabled}
              onChange={(e) => {
                setChoice(e.target.value);
                const value =
                  e.target.value === "recommended"
                    ? item.post_copy!.recommended
                    : item.post_copy!.alternatives.find(
                        (a) => a.style === e.target.value,
                      )!;
                const next = formatPostCopy(value);
                setText(next);
                onDirty(next !== initial);
              }}
            >
              <NativeSelectOption value="recommended">
                Best overall
              </NativeSelectOption>
              {item.post_copy.alternatives.map((a) => (
                <NativeSelectOption key={a.style} value={a.style}>
                  {a.style.replaceAll("_", " ")}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>
          <p className="text-sm text-muted-foreground">
            {item.post_copy.reason}
          </p>
        </>
      ) : null}
      <Field>
        <FieldLabel htmlFor={`copy-description-${item.id}`}>
          Description and hashtags
        </FieldLabel>
        <Textarea
          id={`copy-description-${item.id}`}
          value={text}
          rows={5}
          disabled={busy || disabled}
          onChange={(e) => {
            setText(e.target.value);
            onDirty(e.target.value !== initial);
          }}
        />
        <FieldDescription>
          Use up to 500 characters for the description and put up to five
          hashtags on their own line at the end.
        </FieldDescription>
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          disabled={busy || disabled || !valid}
          onClick={() => void run(false)}
        >
          Save posting copy
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={!text.trim()}
          onClick={() => {
            void navigator.clipboard
              .writeText(text.trim())
              .catch((e) => setError(message(e)));
          }}
        >
          Copy text
        </Button>
      </div>
      <Field orientation="horizontal">
        <Checkbox
          id={`copy-consent-${item.id}`}
          checked={consent}
          disabled={busy || disabled}
          onCheckedChange={(value) => setConsent(value === true)}
        />
        <FieldLabel htmlFor={`copy-consent-${item.id}`}>
          Share this clip’s saved transcript with OpenAI to generate
          descriptions and hashtags. API charges apply.
        </FieldLabel>
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="secondary"
          disabled={busy || disabled || !consent}
          onClick={() => void run(true)}
        >
          {busy
            ? "Working…"
            : item.post_copy_attempted
              ? "Generate options (paid)"
              : "Generate options"}
        </Button>
        {error && generationFailed ? (
          <Button
            type="button"
            variant="outline"
            disabled={busy || disabled || !consent}
            onClick={() => void run(true, true)}
          >
            Start a new paid attempt
          </Button>
        ) : null}
      </div>
      <p className="text-sm text-muted-foreground">
        Generated from speech, without claiming to see the footage. Review
        facts, names and tags. Generation does not publish anything.
      </p>
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
    </FieldGroup>
  );
}
