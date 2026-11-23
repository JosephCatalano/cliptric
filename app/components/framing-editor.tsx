"use client";

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type PointerEvent,
  type KeyboardEvent,
} from "react";
import { createPortal } from "react-dom";
import { Move, Scaling } from "lucide-react";
import {
  fitPortraitRegion,
  followRegion,
  pixelRegion,
  portraitRegion,
  resizePortraitRegion,
} from "@/lib/video-framing";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Field,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Progress } from "@/components/ui/progress";
import { previewIntervals } from "@/lib/clip-preview";
import { LiveClipStage, type LiveClipOptions } from "./live-clip-stage";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import {
  defaultSplitLayout,
  type FramingMode,
  type FollowPlan,
  type AutoLayoutPlan,
  type SourceRegion,
  type SplitLayout,
  type QualityOptions,
} from "../../lib/projects";

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function FramingEditor({
  mode,
  layout,
  cropRegion = null,
  followPlan,
  autoLayout,
  onPlayer,
  end,
  onChange,
  sourceUrl,
  timestamp = 0,
  disabled = false,
  downloadProgress,
  allowSplit = true,
  previewSlot,
  previewOptions,
  quality,
}: {
  mode: FramingMode;
  layout: SplitLayout | null;
  cropRegion?: SourceRegion | null;
  followPlan?: FollowPlan | null;
  autoLayout?: AutoLayoutPlan | null;
  onPlayer?: (player: HTMLVideoElement | null) => void;
  end?: number;
  onChange(
    mode: FramingMode,
    layout: SplitLayout | null,
    crop: SourceRegion | null,
  ): void;
  sourceUrl?: string;
  timestamp?: number;
  disabled?: boolean;
  downloadProgress?: number | null;
  allowSplit?: boolean;
  previewSlot?: HTMLElement | null;
  previewOptions?: LiveClipOptions;
  quality?: QualityOptions;
}) {
  const id = useId();
  const [regionName, setRegionName] = useState<"webcam" | "gameplay">("webcam");
  const [mediaError, setMediaError] = useState(false);
  const picture = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const [previewPlayer, setPreviewPlayer] = useState<HTMLVideoElement | null>(
    null,
  );
  const livePreview = previewOptions !== undefined;
  const mountVideo = useCallback(
    (node: HTMLVideoElement | null) => {
      video.current = node;
      if (livePreview) setPreviewPlayer(node);
      onPlayer?.(node);
    },
    [onPlayer, livePreview],
  );
  const [dimensions, setDimensions] = useState({
    width: 16,
    height: 9,
    source: "",
  });
  const [reload, setReload] = useState(0);
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
  const region =
    mode === "crop"
      ? cropRegion
        ? fitPortraitRegion(cropRegion, dimensions.width, dimensions.height)
        : portraitRegion(dimensions.width, dimensions.height)
      : options[regionName];
  const rangeStart =
    Number.isFinite(timestamp) && timestamp >= 0 ? timestamp : 0;
  const rangeEnd =
    end !== undefined && Number.isFinite(end) && end > rangeStart
      ? end
      : undefined;
  const removals = quality?.removals;
  const kept = useMemo(
    () =>
      rangeEnd === undefined
        ? []
        : previewIntervals(rangeStart, rangeEnd, removals ?? []),
    [rangeStart, rangeEnd, removals],
  );
  const tracking =
    followPlan && followPlan.start === rangeStart && followPlan.end === rangeEnd
      ? followPlan
      : null;
  const decision =
    autoLayout && autoLayout.start === rangeStart && autoLayout.end === rangeEnd
      ? autoLayout
      : null;
  const viewMode = mode === "auto" ? (decision?.mode ?? "pad") : mode;
  const smart =
    mode === "pad" &&
    !cropRegion &&
    quality?.smart_framing?.points[0]?.at === rangeStart &&
    (quality?.smart_framing?.points.at(-1)?.at ?? Infinity) <= (rangeEnd ?? -1)
      ? quality.smart_framing
      : null;
  const regionLabel = mode === "crop" ? "portrait crop" : regionName;

  useEffect(() => {
    const player = video.current;
    const target = canvas.current;
    if (!player || !target) return;
    const configuration =
      (mode === "auto" ? decision?.split_layout : layout) ??
      defaultSplitLayout();
    const context = target.getContext("2d");
    if (!context) return;
    context.setTransform(target.width / 270, 0, 0, target.height / 480, 0, 0);
    context.clearRect(0, 0, 270, 480);
    let frame: number | undefined;
    const cancelFrame = () => {
      if (frame !== undefined) window.cancelAnimationFrame(frame);
    };
    function draw() {
      if (!context || !player || player.readyState < 2) return;
      if (
        !player.paused &&
        !player.seeking &&
        rangeEnd !== undefined &&
        !kept.some(
          ([from, to]) => from <= player.currentTime && player.currentTime < to,
        )
      ) {
        const next = kept.find(([from]) => from > player.currentTime);
        if (next) {
          player.currentTime = next[0];
          return;
        }
        player.pause();
        player.currentTime = Math.max(
          rangeStart,
          (kept.at(-1)?.[1] ?? rangeEnd) - 0.01,
        );
      }
      const panel = (
        crop: SourceRegion,
        top: number,
        height: number,
        fit: boolean,
      ) => {
        const pixels = pixelRegion(
          crop,
          player.videoWidth,
          player.videoHeight,
          fit ? undefined : [270, height],
        );
        const { x: sx, y: sy, width: sw, height: sh } = pixels;
        if (sw < 2 || sh < 2) return;
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
      context.clearRect(0, 0, 270, 480);
      if (viewMode === "follow" || smart) {
        const moving = smart ?? tracking;
        panel(
          moving
            ? followRegion(moving, player.currentTime)
            : { x: 0, y: 0, width: 1, height: 1 },
          0,
          480,
          !moving || moving.strategy === "fit",
        );
      } else if (viewMode === "split") {
        panel(configuration.webcam, 0, 160, false);
        panel(
          configuration.gameplay,
          160,
          320,
          configuration.gameplay_fit === "fit",
        );
      } else {
        panel(
          viewMode === "crop"
            ? ((mode === "auto" ? decision?.crop_region : cropRegion) ??
                portraitRegion(player.videoWidth, player.videoHeight))
            : { x: 0, y: 0, width: 1, height: 1 },
          0,
          480,
          viewMode === "pad",
        );
      }
    }
    function next() {
      draw();
      if (player && !player.paused && !player.ended)
        frame = window.requestAnimationFrame(next);
    }
    function play() {
      cancelFrame();
      next();
    }
    player.addEventListener("play", play);
    player.addEventListener("seeked", draw);
    player.addEventListener("loadeddata", draw);
    draw();
    if (!player.paused) play();
    return () => {
      cancelFrame();
      player.removeEventListener("play", play);
      player.removeEventListener("seeked", draw);
      player.removeEventListener("loadeddata", draw);
    };
  }, [
    mode,
    viewMode,
    decision,
    layout,
    cropRegion,
    tracking,
    sourceUrl,
    video,
    reload,
    previewSlot,
    rangeStart,
    rangeEnd,
    kept,
    smart,
  ]);

  useEffect(() => {
    const player = video.current;
    if (player && player.readyState >= 1 && Number.isFinite(timestamp)) {
      player.currentTime = clamp(
        timestamp,
        0,
        Math.max(0, player.duration - 0.01),
      );
    }
  }, [timestamp, sourceUrl, video]);

  const retryPreview = () => {
    setMediaError(false);
    setReload((value) => value + 1);
  };

  function updateRegion(next: SourceRegion) {
    if (mode === "crop") onChange("crop", null, next);
    else onChange("split", { ...options, [regionName]: next }, null);
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
      resize && mode === "crop"
        ? resizePortraitRegion(
            region,
            dx,
            dy,
            dimensions.width / dimensions.height,
          )
        : resize
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
      current.resize && mode === "crop"
        ? resizePortraitRegion(r, dx, dy, dimensions.width / dimensions.height)
        : current.resize
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
      {previewSlot && previewOptions
        ? createPortal(
            <LiveClipStage
              key={sourceUrl}
              player={previewPlayer}
              playerRef={video}
              start={rangeStart}
              end={rangeEnd}
              kept={kept}
              options={{
                ...previewOptions,
                coverAt: quality?.cover_at,
                exportAudio:
                  !!quality && (quality.normalize || quality.cleanup !== "off"),
              }}
              split={viewMode === "split"}
              pendingPresentation={
                (mode === "auto" && !decision) ||
                (viewMode === "follow" && !tracking)
              }
              invalidRemovals={
                !!removals?.some((cut) => cut.enabled) &&
                kept.length === 1 &&
                kept[0][0] === rangeStart &&
                kept[0][1] === rangeEnd
              }
              mediaError={mediaError}
              onRetry={retryPreview}
            >
              <canvas
                ref={canvas}
                width={432}
                height={768}
                className="size-full"
                aria-label="Live portrait composition"
              />
            </LiveClipStage>,
            previewSlot,
          )
        : null}
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
              next === "split"
                ? (layout ?? decision?.split_layout ?? defaultSplitLayout())
                : null,
              next === "crop"
                ? (cropRegion ??
                    decision?.crop_region ??
                    (decision?.mode === "follow" && tracking
                      ? followRegion(
                          tracking,
                          video.current?.currentTime ?? rangeStart,
                        )
                      : null) ??
                    (dimensions.source === sourceUrl
                      ? portraitRegion(dimensions.width, dimensions.height)
                      : null))
                : null,
            );
          }}
        >
          <NativeSelectOption value="auto">
            Auto: choose for each clip
          </NativeSelectOption>
          <NativeSelectOption value="pad">
            Fit video with blur fill
          </NativeSelectOption>
          <NativeSelectOption value="follow">
            Auto follow one person
          </NativeSelectOption>
          <NativeSelectOption value="crop">
            Fill frame (move and zoom)
          </NativeSelectOption>
          {allowSplit ? (
            <NativeSelectOption value="split">
              Webcam above gameplay
            </NativeSelectOption>
          ) : null}
        </NativeSelect>
      </Field>
      <>
        {mode === "auto" ? (
          <div className="flex flex-col gap-2" role="status">
            <p>
              {decision?.summary ??
                "Chooses webcam + source, webcam focus, face follow or full picture for each generated clip."}
            </p>
            {decision ? (
              decision.warnings.map((warning) => (
                <p key={warning} className="text-sm text-muted-foreground">
                  {warning}
                </p>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">
                The preview shows the source until generation. Every automatic
                choice can be changed afterward.
              </p>
            )}
          </div>
        ) : null}
        {mode === "follow" ? (
          <div className="flex flex-col gap-2" role="status">
            <p>
              Follows one visible person locally. Uncertain tracking keeps the
              full picture. Choose Fill frame for manual control.
            </p>
            {!tracking ? (
              <p className="text-sm text-muted-foreground">
                Tracking is analyzed per clip after generation. The preview
                currently shows the full source. Clips over 10 minutes keep the
                full picture.
              </p>
            ) : (
              tracking.warnings.map((warning) => (
                <p key={warning} className="text-sm text-muted-foreground">
                  {warning}
                </p>
              ))
            )}
          </div>
        ) : null}
        {mode === "crop" || mode === "split" ? (
          <p>
            {mode === "crop"
              ? "Drag the move handle to choose what fills the clip. Resize its corner to zoom."
              : "Choose the webcam and gameplay regions. The webcam fills the top third."}{" "}
            Arrow keys move or resize a focused handle; Shift makes larger
            adjustments.
          </p>
        ) : null}
        {sourceUrl && mode === "split" ? (
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
              <NativeSelectOption value="gameplay">Gameplay</NativeSelectOption>
            </NativeSelect>
          </Field>
        ) : null}
        <div className="framing-pictures">
          {sourceUrl ? (
            <div
              ref={picture}
              className="relative overflow-hidden rounded-md bg-editor-preview"
              style={{
                width: "100%",
                maxWidth: `${(55 * dimensions.width) / dimensions.height}dvh`,
              }}
            >
              <video
                ref={mountVideo}
                key={`${sourceUrl}-${reload}`}
                aria-label="Source video for framing"
                controls
                muted
                playsInline
                preload="metadata"
                className="block w-full"
                style={{ maxHeight: "none", height: "auto" }}
                src={sourceUrl}
                onError={() => setMediaError(true)}
                onSeeking={(event) => {
                  const player = event.currentTarget;
                  const last = Math.max(
                    rangeStart,
                    Math.min(rangeEnd ?? player.duration, player.duration) -
                      0.01,
                  );
                  let at = clamp(player.currentTime, rangeStart, last);
                  if (
                    livePreview &&
                    kept.length &&
                    !kept.some(([from, to]) => from <= at && at < to)
                  ) {
                    at =
                      kept.find(([from]) => from > at)?.[0] ??
                      Math.max(rangeStart, kept[kept.length - 1][1] - 0.01);
                  }
                  if (Math.abs(at - player.currentTime) > 0.001)
                    player.currentTime = at;
                }}
                onPlay={(event) => {
                  const player = event.currentTarget;
                  if (
                    player.currentTime < rangeStart ||
                    (rangeEnd !== undefined &&
                      player.currentTime >= rangeEnd - (livePreview ? 0.01 : 0))
                  )
                    player.currentTime = kept[0]?.[0] ?? rangeStart;
                }}
                onTimeUpdate={(event) => {
                  const player = event.currentTarget;
                  if (
                    rangeEnd !== undefined &&
                    !player.paused &&
                    player.currentTime >= rangeEnd
                  ) {
                    player.pause();
                    player.currentTime = rangeEnd;
                  }
                }}
                onLoadedMetadata={(event) => {
                  setMediaError(false);
                  const player = event.currentTarget;
                  setDimensions({
                    width: player.videoWidth,
                    height: player.videoHeight,
                    source: sourceUrl,
                  });
                  if (Number.isFinite(rangeStart))
                    player.currentTime = clamp(
                      kept[0]?.[0] ?? rangeStart,
                      0,
                      Math.max(0, player.duration - 0.01),
                    );
                }}
              />
              {mode === "crop" || mode === "split" ? (
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
                    {mode === "crop"
                      ? "Captured area"
                      : regionName === "webcam"
                        ? "Webcam"
                        : "Gameplay"}
                  </span>
                  <Button
                    type="button"
                    disabled={
                      disabled || mediaError || dimensions.source !== sourceUrl
                    }
                    variant="secondary"
                    size="icon-sm"
                    className="pointer-events-auto absolute left-0 top-6 cursor-move touch-none"
                    aria-label={`Move ${regionLabel} region with arrow keys or drag`}
                    onKeyDown={(event) => keyboard(event, false)}
                    onPointerDown={(event) => startDrag(event, false)}
                    onPointerMove={move}
                    onPointerUp={stop}
                    onPointerCancel={stop}
                    onLostPointerCapture={stop}
                  >
                    <Move />
                  </Button>
                  <Button
                    type="button"
                    disabled={
                      disabled || mediaError || dimensions.source !== sourceUrl
                    }
                    variant="secondary"
                    size="icon-sm"
                    className="pointer-events-auto absolute right-0 bottom-0 cursor-se-resize touch-none"
                    aria-label={`Resize ${regionLabel} region with arrow keys or drag`}
                    onKeyDown={(event) => keyboard(event, true)}
                    onPointerDown={(event) => startDrag(event, true)}
                    onPointerMove={move}
                    onPointerUp={stop}
                    onPointerCancel={stop}
                    onLostPointerCapture={stop}
                  >
                    <Scaling />
                  </Button>
                </div>
              ) : null}
            </div>
          ) : null}
          {sourceUrl && !previewOptions ? (
            <div className="flex flex-col gap-2">
              <p>
                {mode === "auto"
                  ? decision
                    ? "Saved automatic layout preview. Save changes to render text and other effects."
                    : "Source preview. Auto chooses after clip generation."
                  : mode === "follow"
                    ? tracking
                      ? "Saved tracking preview. Save changes to render text and other effects."
                      : "Source preview. Auto follow is applied during clip generation."
                    : "Live framing preview. Save to render captions and headlines."}
              </p>
              <canvas
                ref={canvas}
                width={270}
                height={480}
                className="w-full max-w-[320px] rounded-md bg-editor-preview"
                aria-label="Live clip framing preview"
              />
            </div>
          ) : null}
        </div>
        {sourceUrl && dimensions.source !== sourceUrl && !mediaError ? (
          <p role="status">Loading clip preview...</p>
        ) : null}
        {mediaError && !livePreview ? (
          <Alert>
            <AlertDescription>
              The source preview could not load. Retry after checking your
              connection to Cliptric. If it still fails, reopen the recording.{" "}
              <Button type="button" variant="link" onClick={retryPreview}>
                Retry preview
              </Button>
            </AlertDescription>
          </Alert>
        ) : null}
        {!sourceUrl && !previewOptions ? (
          <div className="flex max-w-sm flex-col gap-2" role="status">
            <p>
              Video preview and crop selection will be available when the video
              finishes downloading.{" "}
              {downloadProgress == null
                ? "Waiting for download progress…"
                : `${downloadProgress}% downloaded`}
            </p>
            <Progress
              value={downloadProgress ?? undefined}
              aria-label="Video download for preview"
            />
          </div>
        ) : null}
        {mode === "split" ? (
          <Field>
            <FieldLabel htmlFor={`${id}-fit`}>Gameplay in its panel</FieldLabel>
            <NativeSelect
              id={`${id}-fit`}
              value={options.gameplay_fit}
              onChange={(event) =>
                onChange(
                  "split",
                  {
                    ...options,
                    gameplay_fit: event.target
                      .value as SplitLayout["gameplay_fit"],
                  },
                  null,
                )
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
        ) : null}
        {mode === "crop" ? (
          <Button
            type="button"
            variant="outline"
            disabled={disabled || dimensions.source !== sourceUrl || mediaError}
            onClick={() =>
              onChange(
                "crop",
                null,
                portraitRegion(dimensions.width, dimensions.height),
              )
            }
          >
            Reset crop
          </Button>
        ) : null}

        {mode === "split" ? (
          <p>
            To keep the original webcam out of the gameplay panel, select a
            gameplay region that excludes it. Captions and headlines are applied
            after composition; review their positions so they do not cover the
            webcam.
          </p>
        ) : null}
      </>
    </FieldSet>
  );
}
