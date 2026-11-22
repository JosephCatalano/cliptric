"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type PointerEvent,
  type KeyboardEvent,
} from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Field,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Progress } from "@/components/ui/progress";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import {
  defaultSplitLayout,
  type FramingMode,
  type SourceRegion,
  type SplitLayout,
} from "../../lib/projects";

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function FramingEditor({
  mode,
  layout,
  onChange,
  sourceUrl,
  timestamp = 0,
  disabled = false,
  downloadProgress,
}: {
  mode: FramingMode;
  layout: SplitLayout | null;
  onChange(mode: FramingMode, layout: SplitLayout | null): void;
  sourceUrl?: string;
  timestamp?: number;
  disabled?: boolean;
  downloadProgress?: number | null;
}) {
  const id = useId();
  const [regionName, setRegionName] = useState<"webcam" | "gameplay">("webcam");
  const [mediaError, setMediaError] = useState(false);
  const picture = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const drag = useRef<{
    pointer: number;
    x: number;
    y: number;
    boxWidth: number;
    boxHeight: number;
    region: SourceRegion;
    resize: boolean;
  } | null>(null);
  const options = layout ?? defaultSplitLayout();
  const region = options[regionName];

  useEffect(() => {
    const player = video.current;
    const target = canvas.current;
    if (mode !== "split" || !player || !target || !layout) return;
    const configuration = layout;
    const context = target.getContext("2d");
    if (!context) return;
    context.clearRect(0, 0, 270, 480);
    let frame: number | undefined;
    function draw() {
      if (!context || !player || player.readyState < 2) return;
      const panel = (
        crop: SourceRegion,
        top: number,
        height: number,
        fit: boolean,
      ) => {
        const sw = crop.width * player.videoWidth,
          sh = crop.height * player.videoHeight;
        const sx = crop.x * player.videoWidth,
          sy = crop.y * player.videoHeight;
        context.save();
        context.beginPath();
        context.rect(0, top, 270, height);
        context.clip();
        const paint = (cover: boolean) => {
          const scale = (cover ? Math.max : Math.min)(270 / sw, height / sh);
          const width = sw * scale,
            h = sh * scale;
          context.drawImage(
            player,
            sx,
            sy,
            sw,
            sh,
            (270 - width) / 2,
            top + (height - h) / 2,
            width,
            h,
          );
        };
        if (fit) {
          context.filter = "blur(8px)";
          paint(true);
          context.filter = "none";
        }
        paint(!fit);
        context.restore();
      };
      panel(configuration.webcam, 0, 160, false);
      panel(
        configuration.gameplay,
        160,
        320,
        configuration.gameplay_fit === "fit",
      );
    }
    function next() {
      draw();
      if (player && !player.paused && !player.ended)
        frame = player.requestVideoFrameCallback(next);
    }
    function play() {
      if (frame !== undefined) player?.cancelVideoFrameCallback(frame);
      next();
    }
    player.addEventListener("play", play);
    player.addEventListener("seeked", draw);
    player.addEventListener("loadeddata", draw);
    draw();
    if (!player.paused) play();
    return () => {
      if (frame !== undefined) player.cancelVideoFrameCallback(frame);
      player.removeEventListener("play", play);
      player.removeEventListener("seeked", draw);
      player.removeEventListener("loadeddata", draw);
    };
  }, [mode, layout, sourceUrl]);

  function updateRegion(next: SourceRegion) {
    onChange("split", { ...options, [regionName]: next });
  }
  function keyboard(event: KeyboardEvent<HTMLButtonElement>, resize: boolean) {
    const directions: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
    };
    const direction = directions[event.key];
    if (!direction || disabled) return;
    event.preventDefault();
    const step = event.shiftKey ? 0.02 : 0.005;
    const [dx, dy] = direction.map((value) => value * step);
    updateRegion(
      resize
        ? {
            ...region,
            width: clamp(region.width + dx, 0.01, 1 - region.x),
            height: clamp(region.height + dy, 0.01, 1 - region.y),
          }
        : {
            ...region,
            x: clamp(region.x + dx, 0, 1 - region.width),
            y: clamp(region.y + dy, 0, 1 - region.height),
          },
    );
  }
  function startDrag(event: PointerEvent<HTMLElement>, resize: boolean) {
    if (disabled || !picture.current) return;
    event.preventDefault();
    event.stopPropagation();
    const box = picture.current.getBoundingClientRect();
    drag.current = {
      pointer: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      boxWidth: box.width,
      boxHeight: box.height,
      region,
      resize,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function move(event: PointerEvent<HTMLElement>) {
    const current = drag.current;
    if (disabled || !current || event.pointerId !== current.pointer) return;
    const dx = (event.clientX - current.x) / current.boxWidth;
    const dy = (event.clientY - current.y) / current.boxHeight;
    const r = current.region;
    updateRegion(
      current.resize
        ? {
            ...r,
            width: clamp(r.width + dx, 0.01, 1 - r.x),
            height: clamp(r.height + dy, 0.01, 1 - r.y),
          }
        : {
            ...r,
            x: clamp(r.x + dx, 0, 1 - r.width),
            y: clamp(r.y + dy, 0, 1 - r.height),
          },
    );
  }
  function stop(event: PointerEvent<HTMLElement>) {
    if (event.pointerId === drag.current?.pointer) drag.current = null;
  }

  return (
    <FieldSet className="framing-editor" disabled={disabled}>
      <FieldLegend>Framing</FieldLegend>
      <Field>
        <FieldLabel htmlFor={`${id}-mode`}>Video layout</FieldLabel>
        <NativeSelect
          id={`${id}-mode`}
          value={mode}
          onChange={(event) => {
            const next = event.target.value as FramingMode;
            onChange(
              next,
              next === "split" ? (layout ?? defaultSplitLayout()) : null,
            );
          }}
        >
          <NativeSelectOption value="pad">
            Fit video with blur fill
          </NativeSelectOption>
          <NativeSelectOption value="crop">
            Fill frame with centre crop
          </NativeSelectOption>
          <NativeSelectOption value="split">
            Webcam above gameplay
          </NativeSelectOption>
        </NativeSelect>
      </Field>
      {mode === "split" ? (
        <>
          <p>
            Select the webcam and gameplay regions from the original picture.
            Use the move handle and corner to transform the box. Arrow keys move
            or resize the focused handle; Shift makes larger adjustments. The
            webcam fills the top third; its edges may be cropped. Regions remain
            fixed throughout the clip.
          </p>
          {sourceUrl ? (
            <Field>
              <FieldLabel htmlFor={`${id}-region`}>Region to adjust</FieldLabel>
              <NativeSelect
                id={`${id}-region`}
                value={regionName}
                onChange={(event) =>
                  setRegionName(event.target.value as "webcam" | "gameplay")
                }
              >
                <NativeSelectOption value="webcam">Webcam</NativeSelectOption>
                <NativeSelectOption value="gameplay">
                  Gameplay
                </NativeSelectOption>
              </NativeSelect>
            </Field>
          ) : null}
          {sourceUrl ? (
            <div
              ref={picture}
              className="relative overflow-hidden rounded-md bg-muted"
            >
              <video
                ref={video}
                controls
                muted
                playsInline
                preload="metadata"
                className="block w-full"
                src={sourceUrl}
                onError={() => setMediaError(true)}
                onLoadedMetadata={(event) => {
                  setMediaError(false);
                  const player = event.currentTarget;
                  if (Number.isFinite(timestamp))
                    player.currentTime = clamp(
                      timestamp,
                      0,
                      Math.max(0, player.duration - 0.01),
                    );
                }}
              />
              <div
                className="pointer-events-none absolute border-2 border-primary bg-primary/10"
                style={{
                  left: `${region.x * 100}%`,
                  top: `${region.y * 100}%`,
                  width: `${region.width * 100}%`,
                  height: `${region.height * 100}%`,
                }}
              >
                <span className="bg-background px-1 text-xs text-foreground">
                  {regionName === "webcam" ? "Webcam" : "Gameplay"}
                </span>
                <Button
                  type="button"
                  variant="secondary"
                  size="icon-sm"
                  className="pointer-events-auto absolute left-0 top-6 cursor-move touch-none"
                  aria-label={`Move ${regionName} region with arrow keys or drag`}
                  onKeyDown={(event) => keyboard(event, false)}
                  onPointerDown={(event) => startDrag(event, false)}
                  onPointerMove={move}
                  onPointerUp={stop}
                  onPointerCancel={stop}
                >
                  ↔
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="icon-sm"
                  className="pointer-events-auto absolute right-0 bottom-0 cursor-se-resize touch-none"
                  aria-label={`Resize ${regionName} region with arrow keys or drag`}
                  onKeyDown={(event) => keyboard(event, true)}
                  onPointerDown={(event) => startDrag(event, true)}
                  onPointerMove={move}
                  onPointerUp={stop}
                  onPointerCancel={stop}
                >
                  ↘
                </Button>
              </div>
            </div>
          ) : null}
          {mediaError ? (
            <Alert>
              <AlertDescription>
                Source preview could not load. Reload the page to retry before
                adjusting the crop.
              </AlertDescription>
            </Alert>
          ) : null}
          {!sourceUrl ? (
            <div className="flex max-w-sm flex-col gap-2" role="status">
              <p>
                Webcam crop selection will be available when the video finishes
                downloading.{" "}
                {downloadProgress == null
                  ? "Waiting for download progress…"
                  : `${downloadProgress}% downloaded`}
              </p>
              <Progress
                value={downloadProgress ?? undefined}
                aria-label="Video download for webcam cropping"
              />
            </div>
          ) : null}
          <Field>
            <FieldLabel htmlFor={`${id}-fit`}>Gameplay in its panel</FieldLabel>
            <NativeSelect
              id={`${id}-fit`}
              value={options.gameplay_fit}
              onChange={(event) =>
                onChange("split", {
                  ...options,
                  gameplay_fit: event.target
                    .value as SplitLayout["gameplay_fit"],
                })
              }
            >
              <NativeSelectOption value="fit">
                Keep all selected gameplay, with blur fill
              </NativeSelectOption>
              <NativeSelectOption value="fill">
                Fill panel with centre crop
              </NativeSelectOption>
            </NativeSelect>
          </Field>
          {sourceUrl ? (
            <div className="flex flex-col gap-2">
              <p>
                Layout preview (captions and text overlays appear in the
                rendered export)
              </p>
              <canvas
                ref={canvas}
                width={270}
                height={480}
                className="max-w-full rounded-md bg-muted"
                aria-label="Webcam above gameplay preview"
              />
            </div>
          ) : null}
          <p>
            To keep the original webcam out of the gameplay panel, select a
            gameplay region that excludes it. Captions and headlines are applied
            after composition; review their positions so they do not cover the
            webcam.
          </p>
        </>
      ) : null}
    </FieldSet>
  );
}
