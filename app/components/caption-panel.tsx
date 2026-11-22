import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Input } from "@/components/ui/input";
import { FieldLabel, Field, FieldGroup } from "@/components/ui/field";
import { useId, useEffect, useRef, useState } from "react";
import { api, ApiError, message } from "../../lib/client-api";
import { LoadingStatus } from "./workspace-loading";

type Transcription = {
  id: string;
  recording_id: string;
  status:
    | "queued"
    | "preparing"
    | "transcribing"
    | "cancelling"
    | "succeeded"
    | "failed"
    | "cancelled";
  progress: number;
  model: string;
  language: string;
  error: string | null;
  warning: string | null;
  word_count: number;
  revision: number;
};
type Word = {
  id: number;
  start: number;
  end: number;
  text: string;
  probability: number;
  edited: boolean;
  original_start: number;
  original_end: number;
  timing_edited: boolean;
};
type WordResponse = { id: string; revision: number; words: Word[] };
export type CaptionPosition = "auto" | "lower" | "centre" | "upper";
export type CaptionSelection = {
  transcription_id: string;
  revision: number;
  position: CaptionPosition;
};
export type CaptionState = {
  recordingId: string;
  start: number;
  end: number;
  transcriptionId: string | null;
  revision: number;
  ready: boolean;
  dirty: boolean;
  busy: boolean;
};
type Props = {
  recordingId: string;
  hasAudio: boolean;
  recordingDuration: number;
  start: number;
  end: number;
  validRange: boolean;
  disabled: boolean;
  onState: (state: CaptionState) => void;
};

const processing = (job: Transcription) =>
  ["queued", "preparing", "transcribing", "cancelling"].includes(job.status);
const timestamp = (time: number) => `${time.toFixed(2)}s`;

export function CaptionPanel({
  recordingId,
  hasAudio,
  recordingDuration,
  start,
  end,
  validRange,
  disabled,
  onState,
}: Props) {
  const [job, setJob] = useState<Transcription | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const pollVersion = useRef(0);
  const action = useRef<AbortController | null>(null);
  const active = !!job && processing(job);

  useEffect(() => {
    const controller = new AbortController();
    const version = ++pollVersion.current;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    async function poll() {
      try {
        const result = await api<Transcription | null>(
          `/api/recordings/${recordingId}/transcription`,
          { signal: controller.signal },
        );
        if (controller.signal.aborted || version !== pollVersion.current)
          return;
        setJob(result);
        setLoading(false);
        setError("");
        failures = 0;
        timer = setTimeout(poll, result && processing(result) ? 1000 : 5000);
      } catch (reason) {
        if (controller.signal.aborted || version !== pollVersion.current)
          return;
        setLoading(false);
        setError(`Transcript status unavailable. Retrying. ${message(reason)}`);
        timer = setTimeout(
          poll,
          Math.min(10_000, 1000 * 2 ** Math.min(++failures, 4)),
        );
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [recordingId, refresh]);

  useEffect(() => () => action.current?.abort(), []);

  useEffect(() => {
    if (job?.status === "succeeded" && validRange) return;
    onState({
      recordingId,
      start,
      end,
      transcriptionId: null,
      revision: 0,
      ready: false,
      dirty: false,
      busy,
    });
  }, [job?.status, validRange, recordingId, start, end, busy, onState]);

  async function updateJob(cancel: boolean) {
    if (action.current || disabled || (cancel && !job)) return;
    const controller = new AbortController();
    action.current = controller;
    pollVersion.current++;
    setBusy(true);
    setActionError("");
    try {
      const result = await api<Transcription>(
        cancel
          ? `/api/transcriptions/${job!.id}/cancel`
          : `/api/recordings/${recordingId}/transcription`,
        { method: "POST", signal: controller.signal },
      );
      if (!controller.signal.aborted) setJob(result);
    } catch (reason) {
      if (!controller.signal.aborted) setActionError(message(reason));
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        setRefresh((value) => value + 1);
      }
      action.current = null;
    }
  }

  function saved(revision: number) {
    // Invalidate an older in-flight status response before publishing the saved revision.
    pollVersion.current++;
    setJob((current) => (current ? { ...current, revision } : current));
    setRefresh((value) => value + 1);
  }

  return (
    <section className="caption-panel" aria-labelledby="caption-title">
      <div className="section-heading">
        <h2 id="caption-title">Transcript and captions</h2>
        <Badge variant="secondary">On-device</Badge>
      </div>
      <p className="caption-help">
        Turn English speech into editable words, right on this computer. Review
        names, uncertain words, and timing before exporting captions.
      </p>
      {!hasAudio ? (
        <p>This recording has no audio track.</p>
      ) : loading ? (
        <LoadingStatus label="Loading transcript status…" />
      ) : null}
      {error && (
        <Alert role="status" variant="default">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {actionError && (
        <Alert role="alert" variant="destructive">
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      )}
      {hasAudio && !loading && !active && job?.status !== "succeeded" && (
        <div className="caption-actions">
          <Button
            type="button"
            disabled={busy || disabled || !!error}
            onClick={() => void updateJob(false)}
            variant="default"
          >
            {busy
              ? "Starting transcription..."
              : job
                ? "Retry transcription"
                : "Transcribe recording"}
          </Button>
          {job?.status === "cancelled" && (
            <p role="status">
              Transcription cancelled. You can retry or export without captions.
            </p>
          )}
          {job?.status === "failed" && (
            <Alert role="alert" variant="destructive">
              <AlertDescription>
                {job.error ||
                  "Transcription failed. Retry or export without captions."}
              </AlertDescription>
            </Alert>
          )}
        </div>
      )}
      {job && active && (
        <div className="transcription-progress">
          <p role="status">
            {job.status === "preparing"
              ? "Preparing audio and speech model..."
              : job.status === "queued"
                ? "Waiting to transcribe..."
                : job.status === "cancelling"
                  ? "Cancelling transcription..."
                  : `Transcribing: ${Math.round(job.progress * 100)}%`}
          </p>
          <Progress
            aria-label="Transcription progress"
            value={(100 * job.progress) / 1}
          />
          <Button
            type="button"
            disabled={busy || disabled || job.status === "cancelling"}
            onClick={() => void updateJob(true)}
            variant="outline"
          >
            Cancel transcription
          </Button>
        </div>
      )}
      {job?.status === "succeeded" && (
        <>
          <p className="caption-help">
            Transcript ready: {job.word_count} words. Corrections are saved
            against the recording and reused for future clips.
          </p>
          {job.warning && (
            <Alert role="status" variant="default">
              <AlertDescription>{job.warning}</AlertDescription>
            </Alert>
          )}
          {job.word_count === 0 && (
            <p role="status">
              No speech was detected. You can still export this recording
              without captions.
            </p>
          )}
          {!validRange ? (
            <p>Choose a valid clip interval to review its words.</p>
          ) : (
            <WordEditor
              key={`${job.id}:${start}:${end}`}
              recordingId={recordingId}
              job={job}
              start={start}
              end={end}
              disabled={disabled || busy}
              statusError={!!error}
              recordingDuration={recordingDuration}
              onState={onState}
              onSaved={saved}
            />
          )}
        </>
      )}
    </section>
  );
}

function WordEditor({
  recordingId,
  job,
  start,
  end,
  disabled,
  statusError,
  recordingDuration,
  onState,
  onSaved,
}: {
  recordingId: string;
  job: Transcription;
  start: number;
  end: number;
  disabled: boolean;
  statusError: boolean;
  recordingDuration: number;
  onState: (state: CaptionState) => void;
  onSaved: (revision: number) => void;
}) {
  const fieldId = useId();
  const [data, setData] = useState<WordResponse | null>(null);
  const [edits, setEdits] = useState<Record<number, string>>({});
  const [timings, setTimings] = useState<
    Record<number, { start: string; end: string }>
  >({});
  const [error, setError] = useState("");
  const [savedMessage, setSavedMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveConflict, setSaveConflict] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const action = useRef<AbortController | null>(null);
  const dirty =
    Object.keys(edits).length > 0 || Object.keys(timings).length > 0;
  const conflict =
    saveConflict || (data !== null && data.revision !== job.revision);
  const visibleWords =
    data?.words.filter((word) => (edits[word.id] ?? word.text).trim()) ?? [];

  useEffect(() => {
    const controller = new AbortController();
    api<WordResponse>(
      `/api/transcriptions/${job.id}/words?start=${start}&end=${end}`,
      { signal: controller.signal },
    )
      .then((result) => {
        if (controller.signal.aborted) return;
        setData(result);
        setEdits({});
        setTimings({});
        setSaveConflict(false);
        setError("");
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(message(reason));
      });
    return () => controller.abort();
  }, [job.id, start, end, refresh]);

  useEffect(() => () => action.current?.abort(), []);

  useEffect(() => {
    onState({
      recordingId,
      start,
      end,
      transcriptionId: job.id,
      revision: data?.revision ?? 0,
      ready:
        !!data &&
        !dirty &&
        !saving &&
        !error &&
        !conflict &&
        !statusError &&
        visibleWords.length > 0,
      dirty,
      busy: saving,
    });
  }, [
    recordingId,
    start,
    end,
    job.id,
    data,
    dirty,
    saving,
    error,
    conflict,
    statusError,
    visibleWords.length,
    onState,
  ]);

  function editWord(word: Word, text: string) {
    setSavedMessage("");
    setEdits((current) => {
      const next = { ...current };
      if (text === word.text) delete next[word.id];
      else next[word.id] = text;
      return next;
    });
  }

  async function save() {
    if (!data || !dirty || saving || action.current || conflict) return;
    const updates = data.words
      .filter((word) => word.id in edits || word.id in timings)
      .map((word) => ({
        id: word.id,
        text: (edits[word.id] ?? word.text).trim().replace(/\s+/g, " "),
        ...(timings[word.id]
          ? {
              start: Number(timings[word.id].start),
              end: Number(timings[word.id].end),
            }
          : {}),
      }));
    if (
      Object.values(timings).some(
        ({ start, end }) =>
          start.trim() === "" ||
          end.trim() === "" ||
          !Number.isFinite(Number(start)) ||
          !Number.isFinite(Number(end)) ||
          Number(start) < 0 ||
          Number(end) <= Number(start) ||
          Number(end) > recordingDuration,
      )
    ) {
      setError(
        "Enter a valid start and later end inside the recording for every edited word.",
      );
      return;
    }
    const controller = new AbortController();
    action.current = controller;
    setSaving(true);
    setError("");
    let revision = data.revision;
    let current = data;
    try {
      // Keep each request within the API limit, recording successful batches so
      // a later failure never repeats an already committed edit.
      // Fifty 48-character fields fit beneath the control-body byte limit even
      // when every character needs four UTF-8 bytes or JSON escaping.
      for (let offset = 0; offset < updates.length; offset += 50) {
        const batch = updates.slice(offset, offset + 50);
        const result = await api<{ id: string; revision: number }>(
          `/api/transcriptions/${job.id}/words`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ revision, updates: batch }),
            signal: controller.signal,
          },
        );
        if (controller.signal.aborted) return;
        revision = result.revision;
        const changes = new Map(batch.map((item) => [item.id, item]));
        current = {
          ...current,
          revision,
          words: current.words.map((word) =>
            changes.has(word.id)
              ? {
                  ...word,
                  ...changes.get(word.id)!,
                  edited:
                    word.edited || changes.get(word.id)!.text !== word.text,
                  timing_edited:
                    changes.get(word.id)!.start !== undefined
                      ? changes.get(word.id)!.start !== word.original_start ||
                        changes.get(word.id)!.end !== word.original_end
                      : word.timing_edited,
                }
              : word,
          ),
        };
        setData(current);
        setEdits((pending) => {
          const remaining = { ...pending };
          batch.forEach((item) => delete remaining[item.id]);
          return remaining;
        });
        setTimings((pending) => {
          const remaining = { ...pending };
          batch.forEach((item) => delete remaining[item.id]);
          return remaining;
        });
        onSaved(revision);
      }
      setSavedMessage("Caption corrections saved.");
    } catch (reason) {
      if (!controller.signal.aborted) {
        const changed = reason instanceof ApiError && reason.status === 409;
        setSaveConflict(changed);
        setError(
          changed
            ? "This transcript changed in another window. Your unsaved corrections are still here. Review them, then reload the saved words before editing again."
            : `Corrections could not all be saved. Your remaining edits are preserved. ${message(reason)}`,
        );
      }
    } finally {
      if (!controller.signal.aborted) setSaving(false);
      action.current = null;
    }
  }

  function reload() {
    setData(null);
    setEdits({});
    setTimings({});
    setError("");
    setSavedMessage("");
    setSaveConflict(false);
    setRefresh((value) => value + 1);
  }

  return (
    <div className="word-editor">
      <p className="caption-help">
        Selected interval: {timestamp(start)} to {timestamp(end)}. Blank a word
        to omit it from captions. Adjust word start/end in source seconds, then
        save. These edits change caption timing, not the audio or video.
        Original ASR timing is retained for restore. Corrections are shared by
        this recording; existing exports keep their snapshots.
      </p>
      {!data && !error && <LoadingStatus label="Loading selected words…" />}
      {error && (
        <Alert role="alert" variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {conflict && (
        <Alert role="status" variant="default">
          <AlertDescription>
            A newer transcript revision is available. Reload saved words before
            exporting captions.
          </AlertDescription>
        </Alert>
      )}
      {data && data.words.length === 0 && (
        <p>
          No speech was detected in this interval. Export without captions or
          choose another moment.
        </p>
      )}
      {data && data.words.length > 0 && (
        <>
          <ol className="caption-words" aria-label="Words in selected interval">
            {data.words.map((word) => (
              <li key={word.id}>
                <FieldGroup>
                  <Field>
                    <FieldLabel htmlFor={`${fieldId}-1-${word.id}`}>
                      <span>
                        {timestamp(word.start)} to {timestamp(word.end)}
                        {word.probability < 0.5 && !word.edited
                          ? " · Review"
                          : ""}
                      </span>
                    </FieldLabel>
                    <Input
                      id={`${fieldId}-1-${word.id}`}
                      aria-label={`Word ${word.id + 1} at ${timestamp(word.start)}`}
                      type="text"
                      maxLength={48}
                      value={edits[word.id] ?? word.text}
                      disabled={saving || disabled}
                      onChange={(event) => editWord(word, event.target.value)}
                    />
                  </Field>
                  {(["start", "end"] as const).map((edge) => (
                    <Field key={edge}>
                      <FieldLabel htmlFor={`${fieldId}-${word.id}-${edge}`}>
                        Word {edge} (seconds)
                      </FieldLabel>
                      <Input
                        id={`${fieldId}-${word.id}-${edge}`}
                        type="number"
                        min={0}
                        max={recordingDuration}
                        step="any"
                        value={timings[word.id]?.[edge] ?? String(word[edge])}
                        disabled={saving || disabled}
                        onChange={(event) => {
                          const value = event.target.value;
                          setSavedMessage("");
                          setTimings((current) => {
                            const next = { ...current };
                            const changed = {
                              ...(next[word.id] ?? {
                                start: String(word.start),
                                end: String(word.end),
                              }),
                              [edge]: value,
                            };
                            if (
                              changed.start === String(word.start) &&
                              changed.end === String(word.end)
                            )
                              delete next[word.id];
                            else next[word.id] = changed;
                            return next;
                          });
                        }}
                      />
                    </Field>
                  ))}
                  {(word.timing_edited || timings[word.id]) && (
                    <Button
                      type="button"
                      variant="outline"
                      disabled={saving || disabled}
                      onClick={() => {
                        setSavedMessage("");
                        setTimings((current) => {
                          const next = { ...current };
                          if (
                            word.start === word.original_start &&
                            word.end === word.original_end
                          )
                            delete next[word.id];
                          else
                            next[word.id] = {
                              start: String(word.original_start),
                              end: String(word.original_end),
                            };
                          return next;
                        });
                      }}
                    >
                      Restore original timing
                    </Button>
                  )}
                </FieldGroup>
              </li>
            ))}
          </ol>
          <p className="caption-help">
            Text review:{" "}
            {visibleWords
              .map((word) => edits[word.id] ?? word.text)
              .join(" ") || "All words are omitted."}
          </p>
          <div className="caption-actions">
            <Button
              type="button"
              disabled={!dirty || saving || disabled || conflict}
              onClick={() => void save()}
              variant="outline"
            >
              {saving ? "Saving corrections..." : "Save corrections"}
            </Button>
            <Button
              type="button"
              disabled={!dirty || saving || disabled}
              onClick={() => {
                setEdits({});
                setTimings({});
                setError("");
                setSavedMessage("");
              }}
              variant="outline"
            >
              Discard unsaved corrections
            </Button>
          </div>
          {dirty && (
            <Alert role="status" variant="default">
              <AlertDescription>
                Save or discard corrections before changing the recording or
                clip boundaries.
              </AlertDescription>
            </Alert>
          )}
          {savedMessage && <p role="status">{savedMessage}</p>}
        </>
      )}
      {(error || conflict) && (
        <Button
          type="button"
          disabled={saving || disabled}
          onClick={reload}
          variant="outline"
        >
          {dirty
            ? "Discard edits and reload saved words"
            : "Reload saved words"}
        </Button>
      )}
    </div>
  );
}
