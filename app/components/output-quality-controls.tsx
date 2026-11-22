"use client";
import { useId, useRef, useEffect, useState } from "react";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldDescription,
  FieldSet,
  FieldLegend,
} from "@/components/ui/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { api, message } from "../../lib/client-api";
import {
  type QualityOptions,
  type Removal,
  type CaptionSource,
  type SmartFraming,
  type SourceRegion,
} from "../../lib/projects";

type Plan = {
  removals: Removal[];
  smart_framing: SmartFraming | null;
  output_duration: number;
  suggested_start: number;
  suggested_end: number;
  warnings: string[];
};
export function OutputQualityControls({
  value,
  onChange,
  recordingId,
  start,
  end,
  source,
  allowSmart,
  disabled,
  allowedStart = 0,
  allowedEnd,
  onBounds,
}: {
  value: QualityOptions;
  onChange(value: QualityOptions): void;
  recordingId: string;
  start: number;
  end: number;
  source?: CaptionSource;
  allowSmart: boolean;
  disabled?: boolean;
  allowedStart?: number;
  allowedEnd: number;
  onBounds?(start: number, end: number): void;
}) {
  const id = useId();
  const [silence, setSilence] = useState(true);
  const [fillers, setFillers] = useState(false);
  const [threshold, setThreshold] = useState(-38);
  const [minimum, setMinimum] = useState(0.8);
  const [pause, setPause] = useState(0.4);
  const [detector, setDetector] = useState<"off" | "faces" | "motion">("off");
  const [protect, setProtect] = useState(false);
  const [region, setRegion] = useState<SourceRegion>({
    x: 0,
    y: 0.8,
    width: 1,
    height: 0.2,
  });
  const [result, setResult] = useState<{ key: string; plan: Plan } | null>(
    null,
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const action = useRef<AbortController | null>(null);
  const key = JSON.stringify({
    recordingId,
    start,
    end,
    source,
    silence,
    fillers,
    threshold,
    minimum,
    pause,
    detector,
    protect,
    region,
  });
  const plan = result?.key === key ? result.plan : null;
  const valid =
    Number.isFinite(start) &&
    Number.isFinite(end) &&
    end > start &&
    end - start <= 120;
  useEffect(() => () => action.current?.abort(), []);
  async function analyze() {
    if (action.current || !valid) return;
    const controller = new AbortController();
    action.current = controller;
    setBusy(true);
    setError("");
    try {
      const result = await api<Plan>(
        `/api/recordings/${recordingId}/quality-plan`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            start,
            end,
            transcription_id: source?.transcriptionId ?? null,
            revision: source?.revision ?? null,
            silence,
            fillers,
            threshold_db: threshold,
            minimum_pause: minimum,
            keep_pause: pause,
            detector: allowSmart ? detector : "off",
            protected_regions: protect ? [region] : [],
          }),
        },
        90_000,
      );
      if (!controller.signal.aborted) setResult({ key, plan: result });
    } catch (reason) {
      if (!controller.signal.aborted) setError(message(reason));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      action.current = null;
    }
  }
  const removed = value.removals
    .filter((cut) => cut.enabled)
    .reduce((sum, cut) => sum + cut.end - cut.start, 0);
  return (
    <FieldSet disabled={disabled || busy}>
      <FieldLegend>Output quality</FieldLegend>
      <FieldDescription>
        Optional, reversible edits. Compare the original source with the new
        rendered clip before publishing. Original media and previous exports are
        preserved. After changing trim boundaries, clear/reanalyze the edit plan
        before saving.
      </FieldDescription>
      <FieldGroup>
        <details>
          <summary>Compare original source</summary>
          <p>
            Original source interval: {start.toFixed(2)}–{end.toFixed(2)}{" "}
            seconds. Review the new exported clip separately after saving.
          </p>
          {recordingId && (
            <video
              controls
              preload="none"
              aria-label="Original source for quality comparison"
              src={`/api/recordings/${recordingId}/media#t=${start},${end}`}
            />
          )}
        </details>
        <Field>
          <FieldLabel htmlFor={`${id}-cleanup`}>Speech cleanup</FieldLabel>
          <NativeSelect
            id={`${id}-cleanup`}
            value={value.cleanup}
            onChange={(event) =>
              onChange({
                ...value,
                cleanup: event.target.value as QualityOptions["cleanup"],
              })
            }
          >
            <NativeSelectOption value="off">Off</NativeSelectOption>
            <NativeSelectOption value="light">
              Light noise reduction
            </NativeSelectOption>
            <NativeSelectOption value="standard">
              Standard noise reduction
            </NativeSelectOption>
          </NativeSelect>
          <FieldDescription>
            May alter music or voice texture. Review intelligibility and
            artifacts.
          </FieldDescription>
        </Field>
        {value.cover_at != null && (
          <Field>
            <FieldLabel htmlFor={`${id}-cover`}>
              Opening cover frame (source seconds)
            </FieldLabel>
            <Input
              id={`${id}-cover`}
              type="number"
              min={start}
              max={Math.max(start, end - 0.01)}
              step="any"
              value={value.cover_at}
              onChange={(event) =>
                onChange({ ...value, cover_at: Number(event.target.value) })
              }
            />
            <FieldDescription>
              Shown behind the opening title for the first quarter second. Audio
              and clip duration stay continuous.
            </FieldDescription>
            <Button
              type="button"
              variant="outline"
              onClick={() => onChange({ ...value, cover_at: null })}
            >
              Use the original first frame
            </Button>
          </Field>
        )}
        <Field orientation="horizontal">
          <Checkbox
            id={`${id}-normalize`}
            checked={value.normalize}
            onCheckedChange={(checked) =>
              onChange({ ...value, normalize: checked === true })
            }
          />
          <FieldLabel htmlFor={`${id}-normalize`}>
            Normalize speech volume
          </FieldLabel>
        </Field>
        <details>
          <summary>Analyze boundaries, pacing, and framing</summary>
          <FieldGroup>
            <Field orientation="horizontal">
              <Checkbox
                id={`${id}-silence`}
                checked={silence}
                onCheckedChange={(checked) => setSilence(checked === true)}
              />
              <FieldLabel htmlFor={`${id}-silence`}>
                Suggest shortening long pauses
              </FieldLabel>
            </Field>
            <Field orientation="horizontal">
              <Checkbox
                id={`${id}-fillers`}
                checked={fillers}
                disabled={!source}
                onCheckedChange={(checked) => setFillers(checked === true)}
              />
              <FieldLabel htmlFor={`${id}-fillers`}>
                Suggest removing saved “um” / “uh” words
              </FieldLabel>
            </Field>
            <Field>
              <FieldLabel htmlFor={`${id}-threshold`}>
                Silence threshold (dB)
              </FieldLabel>
              <Input
                id={`${id}-threshold`}
                type="number"
                min={-50}
                max={-20}
                step={1}
                value={threshold}
                onChange={(event) => setThreshold(Number(event.target.value))}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor={`${id}-minimum`}>
                Minimum pause (seconds)
              </FieldLabel>
              <Input
                id={`${id}-minimum`}
                type="number"
                min={0.4}
                max={3}
                step={0.1}
                value={minimum}
                onChange={(event) => setMinimum(Number(event.target.value))}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor={`${id}-keep`}>
                Pause to retain (seconds)
              </FieldLabel>
              <Input
                id={`${id}-keep`}
                type="number"
                min={0.2}
                max={1}
                step={0.1}
                value={pause}
                onChange={(event) => setPause(Number(event.target.value))}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor={`${id}-detector`}>
                Visual framing analysis
              </FieldLabel>
              <NativeSelect
                id={`${id}-detector`}
                value={allowSmart ? detector : "off"}
                disabled={!allowSmart}
                onChange={(event) =>
                  setDetector(event.target.value as typeof detector)
                }
              >
                <NativeSelectOption value="off">
                  Keep current framing
                </NativeSelectOption>
                <NativeSelectOption value="faces">
                  Track faces conservatively
                </NativeSelectOption>
                <NativeSelectOption value="motion">
                  Track motion conservatively
                </NativeSelectOption>
              </NativeSelect>
              <FieldDescription>
                {allowSmart
                  ? "Sampled detection is not speaker identification or gameplay understanding. Unsafe/ambiguous crops fall back to full-picture fit."
                  : "The manual webcam/gameplay layout preserves your chosen regions. Switch away from split framing to analyze a full-frame crop."}
              </FieldDescription>
            </Field>
            <Field orientation="horizontal">
              <Checkbox
                id={`${id}-protect`}
                checked={protect}
                disabled={!allowSmart || detector === "off"}
                onCheckedChange={(checked) => setProtect(checked === true)}
              />
              <FieldLabel htmlFor={`${id}-protect`}>
                Protect a HUD/context region in the visual proposal
              </FieldLabel>
            </Field>
            {protect &&
              (["x", "y", "width", "height"] as const).map((axis) => (
                <Field key={axis}>
                  <FieldLabel htmlFor={`${id}-${axis}`}>
                    Protected region {axis} (%)
                  </FieldLabel>
                  <Input
                    id={`${id}-${axis}`}
                    type="number"
                    min={axis === "x" || axis === "y" ? 0 : 1}
                    max={100}
                    step={1}
                    value={Math.round(region[axis] * 100)}
                    onChange={(event) =>
                      setRegion((current) => ({
                        ...current,
                        [axis]: Number(event.target.value) / 100,
                      }))
                    }
                  />
                </Field>
              ))}
            <Button
              type="button"
              variant="outline"
              disabled={!valid}
              onClick={() => void analyze()}
            >
              Analyze quality suggestions locally
            </Button>
            {end - start > 120 && (
              <p>
                Local silence/filler and visual detection analyzes up to two
                minutes at a time. You can still export this longer interval;
                choose a shorter interval to run these optional local tools.
              </p>
            )}
            {plan && (
              <>
                {plan.warnings.map((warning) => (
                  <p key={warning}>{warning}</p>
                ))}
                <p>
                  {plan.removals.length} proposed removals. Estimated output:{" "}
                  {plan.output_duration.toFixed(2)} seconds.
                </p>
                {plan.smart_framing && (
                  <p>
                    Visual proposal: {plan.smart_framing.strategy};{" "}
                    {plan.smart_framing.warnings.join(" ")}
                  </p>
                )}
                <Button
                  type="button"
                  variant="outline"
                  onClick={() =>
                    onChange({
                      ...value,
                      removals: plan.removals,
                      smart_framing: plan.smart_framing,
                    })
                  }
                >
                  Stage proposed edit plan (replaces current plan)
                </Button>
                {onBounds && (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={
                      plan.suggested_start < allowedStart ||
                      plan.suggested_end > allowedEnd ||
                      (plan.suggested_start === start &&
                        plan.suggested_end === end)
                    }
                    onClick={() => {
                      onChange({ ...value, removals: [], smart_framing: null });
                      onBounds(plan.suggested_start, plan.suggested_end);
                    }}
                  >
                    Use suggested word-safe boundaries:{" "}
                    {plan.suggested_start.toFixed(2)}–
                    {plan.suggested_end.toFixed(2)}s (clears edit plan)
                  </Button>
                )}
              </>
            )}
          </FieldGroup>
        </details>
        {value.removals.length > 0 && (
          <>
            <p>
              Staged output: {(end - start - removed).toFixed(2)} seconds.
              Uncheck a removal to restore that source interval; save/export to
              apply changes.
            </p>
            {value.removals.map((cut, index) => (
              <Field key={`${cut.start}-${cut.end}`} orientation="horizontal">
                <Checkbox
                  id={`${id}-cut-${index}`}
                  checked={cut.enabled}
                  onCheckedChange={(checked) =>
                    onChange({
                      ...value,
                      removals: value.removals.map((entry, at) =>
                        at === index
                          ? { ...entry, enabled: checked === true }
                          : entry,
                      ),
                    })
                  }
                />
                <FieldLabel htmlFor={`${id}-cut-${index}`}>
                  Remove {cut.reason}: {cut.start.toFixed(2)}–
                  {cut.end.toFixed(2)}s
                </FieldLabel>
              </Field>
            ))}
            <Button
              type="button"
              variant="outline"
              onClick={() =>
                onChange({
                  ...value,
                  removals: value.removals.map((cut) => ({
                    ...cut,
                    enabled: false,
                  })),
                })
              }
            >
              Restore all removed intervals
            </Button>
          </>
        )}
        {value.smart_framing && (
          <>
            <p>
              Staged smart framing: {value.smart_framing.strategy}.{" "}
              {value.smart_framing.warnings.join(" ")}
            </p>
            <Button
              type="button"
              variant="outline"
              onClick={() => onChange({ ...value, smart_framing: null })}
            >
              Restore manual framing
            </Button>
          </>
        )}
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {busy && (
          <p role="status">
            Analyzing source audio and sampled frames locally...
          </p>
        )}
      </FieldGroup>
    </FieldSet>
  );
}
