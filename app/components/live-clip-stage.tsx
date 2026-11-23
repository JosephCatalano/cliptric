"use client";

import {
  useEffect,
  useMemo,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { Button } from "@/components/ui/button";
import { api, message } from "@/lib/client-api";
import {
  clipTime,
  previewCaptionCues,
  previewTextLayout,
  sourceTime,
  type ClipInterval,
  type PreviewHeadline,
  type PreviewWord,
} from "@/lib/clip-preview";
import type { CaptionStyle, TextOverlay } from "@/lib/projects";

const noOverlays: TextOverlay[] = [];
const wordWindow = 30;

export type LiveClipOptions = {
  captions?: CaptionStyle;
  transcriptionId?: string | null;
  transcriptRevision?: number | null;
  emphasis?: string[];
  headline?: PreviewHeadline | null;
  overlays?: TextOverlay[];
  coverAt?: number | null;
  exportAudio?: boolean;
  savedExportId?: string | null;
  onPlaybackTarget?(player: HTMLVideoElement | null): void;
};

export function LiveClipStage({
  children,
  player,
  playerRef,
  start,
  end,
  kept,
  options,
  split,
  pendingPresentation,
  invalidRemovals,
  mediaError,
  onRetry,
}: {
  children: ReactNode;
  player: HTMLVideoElement | null;
  playerRef: RefObject<HTMLVideoElement | null>;
  start: number;
  end?: number;
  kept: ClipInterval[];
  options: LiveClipOptions;
  split: boolean;
  pendingPresentation: boolean;
  invalidRemovals: boolean;
  mediaError: boolean;
  onRetry(): void;
}) {
  const [time, setTime] = useState(start);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(true);
  const [ready, setReady] = useState(false);
  const [saved, setSaved] = useState(false);
  const [savedPlayer, setSavedPlayer] = useState<HTMLVideoElement | null>(null);
  const [playError, setPlayError] = useState("");
  const [words, setWords] = useState<{
    key: string;
    data: PreviewWord[];
  } | null>(null);
  const [wordError, setWordError] = useState<{
    key: string;
    detail: string;
  } | null>(null);
  const [wordRetry, setWordRetry] = useState(0);
  const valid = end !== undefined && end > start;
  const duration = valid ? clipTime(end, kept) : 0;
  const position = clipTime(time, kept);
  const captions =
    options.captions?.preset !== "none" ? options.captions : undefined;
  const offset = Math.max(0, Math.floor((time - start) / wordWindow));
  const lower = Math.max(start, start + offset * wordWindow - 3);
  const upper = Math.min(end ?? start, start + (offset + 1) * wordWindow + 3);
  const transcription = captions ? options.transcriptionId : null;
  const revision = options.transcriptRevision;
  const wordsKey = `${transcription}:${revision}:${lower}:${upper}`;
  const showSaved = options.savedExportId && (saved || !player || mediaError);
  const onPlaybackTarget = options.onPlaybackTarget;

  useEffect(() => {
    onPlaybackTarget?.(showSaved ? savedPlayer : player);
    return () => onPlaybackTarget?.(null);
  }, [onPlaybackTarget, player, savedPlayer, showSaved]);

  useEffect(() => {
    if (!player) return;
    let frame = 0;
    let last = -1;
    function sync() {
      if (!player) return;
      setReady(player.readyState >= 2);
      setTime(player.currentTime);
      setPlaying(!player.paused && !player.ended);
      setMuted(player.muted);
    }
    function tick(at: number) {
      if (at - last > 75) {
        last = at;
        sync();
      }
      if (player && !player.paused && !player.ended)
        frame = requestAnimationFrame(tick);
    }
    function play() {
      cancelAnimationFrame(frame);
      sync();
      frame = requestAnimationFrame(tick);
    }
    const events = [
      "loadeddata",
      "seeked",
      "pause",
      "ended",
      "emptied",
      "volumechange",
    ];
    for (const event of events) player.addEventListener(event, sync);
    player.addEventListener("play", play);
    if (player.paused) sync();
    else play();
    return () => {
      cancelAnimationFrame(frame);
      for (const event of events) player.removeEventListener(event, sync);
      player.removeEventListener("play", play);
    };
  }, [player]);

  useEffect(() => {
    if (!transcription || upper - lower < 0.1) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void api<{ revision: number; words: PreviewWord[] }>(
        `/api/transcriptions/${transcription}/words?start=${lower}&end=${upper}`,
        { signal: controller.signal },
      )
        .then((result) => {
          if (controller.signal.aborted) return;
          if (revision != null && revision !== result.revision) {
            setWordError({
              key: wordsKey,
              detail:
                "The transcript changed. Reopen the editor to use the latest saved captions.",
            });
            return;
          }
          setWords({ key: wordsKey, data: result.words });
          setWordError(null);
        })
        .catch((error) => {
          if (!controller.signal.aborted)
            setWordError({ key: wordsKey, detail: message(error) });
        });
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [transcription, revision, lower, upper, wordsKey, wordRetry]);

  const grouping = captions?.customization.grouping ?? "auto";
  const cues = useMemo(
    () =>
      words?.key === wordsKey
        ? previewCaptionCues(words.data, kept, grouping)
        : [],
    [words, wordsKey, kept, grouping],
  );
  const cue = cues.find(
    (part) => part.start <= position && position < part.end,
  );
  const headline = options.headline?.text.trim() ? options.headline : null;
  const overlays = (options.overlays ?? noOverlays)
    .filter(
      (overlay) =>
        overlay.start >= 0 && overlay.end <= (end ?? start) - start + 1e-8,
    )
    .map((overlay) => ({
      ...overlay,
      start: clipTime(start + overlay.start, kept),
      end: clipTime(start + overlay.end, kept),
    }))
    .filter((overlay) => overlay.end - overlay.start >= 0.1 - 1e-8);
  const layout = previewTextLayout(
    overlays,
    headline,
    captions,
    duration,
    split,
  );
  const cover = Boolean(
    headline &&
    headline.appearance !== "classic" &&
    headline.appearance !== "cover" &&
    layout.caption !== "centre" &&
    !layout.overlays?.some(
      (overlay) => overlay.position === "centre" && overlay.start < 0.25,
    ) &&
    position < 0.25,
  );
  const controlsReady = Boolean(player && ready && valid && !mediaError);
  const setting = captions?.customization;
  const coverAt = options.coverAt;
  const captionAnimation =
    (setting?.animation ?? "preset") === "preset"
      ? captions?.preset === "impact"
        ? "pop"
        : captions?.preset === "studio"
          ? "fade"
          : "none"
      : setting?.animation;
  const seek = (at: number) => {
    const player = playerRef.current;
    if (!player) return;
    player.currentTime = Math.min(
      sourceTime(at, kept),
      Math.max(start, (end ?? start) - 0.01),
    );
    setTime(player.currentTime);
  };

  return (
    <section className="live-clip-preview" aria-label="Clip preview">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">
          {showSaved ? "Last export" : "Live clip preview"}
        </h3>
        {options.savedExportId && player && !mediaError ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              player.pause();
              setPlayError("");
              setSaved(!saved);
            }}
          >
            {saved ? "Show live preview" : "View last export"}
          </Button>
        ) : null}
      </div>
      <div className="live-clip-frame">
        {children}
        {showSaved ? (
          <video
            ref={setSavedPlayer}
            className="absolute inset-0 size-full bg-editor-preview object-contain"
            key={options.savedExportId}
            aria-label="Last rendered export"
            src={`/api/clips/${options.savedExportId}/media`}
            preload="metadata"
            playsInline
            controls
            onError={() =>
              setPlayError(
                "The saved export could not play. Try downloading it from the clip’s menu.",
              )
            }
          />
        ) : controlsReady ? (
          <div
            className="pointer-events-none absolute inset-0 overflow-hidden"
            aria-hidden="true"
          >
            {headline && position < Math.min(duration, headline.seconds) && (
              <div
                className="live-clip-headline"
                data-position={cover ? "cover" : layout.headlineRegion}
                data-appearance={headline.appearance ?? "profile"}
                style={{
                  opacity: Math.min(
                    1,
                    (Math.min(duration, headline.seconds) - position) / 0.25,
                    headline.appearance === "classic" ? position / 0.25 : 1,
                  ),
                }}
              >
                {headline.text}
              </div>
            )}
            {layout.overlays
              ?.filter(
                (overlay) =>
                  overlay.start <= position && position < overlay.end,
              )
              .map((overlay, index) => (
                <div
                  key={index}
                  className="live-clip-overlay"
                  data-position={overlay.position}
                  data-style={overlay.style}
                >
                  {overlay.text}
                </div>
              ))}
            {cue && captions && setting && (
              <div
                className="live-clip-captions"
                data-position={layout.caption}
                data-box={
                  setting.background === "box" ||
                  (setting.background !== "none" &&
                    ["boxed", "studio"].includes(captions.preset))
                }
                style={{
                  color:
                    setting.text_colour ??
                    (captions.preset === "bold" ? "#ffef00" : "#fff"),
                  fontFamily: setting.font_family ?? "Arial, sans-serif",
                  fontSize: `${setting.font_size / 10.8}cqw`,
                  fontWeight:
                    setting.font_weight === "regular" ||
                    (setting.font_weight !== "bold" && options.emphasis?.length)
                      ? 400
                      : 700,
                  textTransform:
                    setting.capitalization === "upper"
                      ? "uppercase"
                      : setting.capitalization === "lower"
                        ? "lowercase"
                        : undefined,
                  WebkitTextStrokeColor: setting.outline_colour ?? "#101010",
                  textShadow: `1px 2px 2px ${setting.shadow_colour ?? "#000"}`,
                  opacity:
                    captionAnimation === "fade"
                      ? Math.min(
                          1,
                          Math.min(position - cue.start, cue.end - position) /
                            Math.min(0.12, (cue.end - cue.start) / 3),
                        )
                      : undefined,
                  scale:
                    captionAnimation === "pop"
                      ? 0.85 +
                        0.15 *
                          Math.min(
                            1,
                            (position - cue.start) /
                              Math.min(0.12, (cue.end - cue.start) / 2),
                          )
                      : undefined,
                  backgroundColor:
                    setting.background === "box" ||
                    (setting.background !== "none" &&
                      ["boxed", "studio"].includes(captions.preset))
                      ? `${setting.background_colour ?? (captions.preset === "studio" ? "#34224c" : "#000000")}aa`
                      : undefined,
                }}
              >
                {cue.words.map((word, index) => {
                  const emphasized = options.emphasis?.includes(
                    word.text.toLowerCase().replace(/[^\p{L}\p{N}'’]/gu, ""),
                  );
                  const active =
                    (setting.animation ?? "preset") === "preset" &&
                    ["focus", "flow", "glow"].includes(captions.preset) &&
                    position >= word.start &&
                    position < word.end;
                  let text = word.text;
                  if (setting.profanity && setting.profanity !== "keep") {
                    text = text.replace(
                      /[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu,
                      (token) =>
                        setting.censor_words?.includes(token.toLowerCase())
                          ? setting.profanity === "symbols"
                            ? "*".repeat(token.length)
                            : (setting.replacement ?? "…")
                          : token,
                    );
                  }
                  const accent =
                    setting.emphasis_colour ??
                    (captions.preset === "focus" ? "#66ddee" : "#ffef00");
                  return (
                    <span
                      key={index}
                      style={{
                        color:
                          emphasized || (active && captions.preset !== "glow")
                            ? accent
                            : undefined,
                        fontWeight: emphasized ? 700 : undefined,
                        textShadow:
                          active && captions.preset === "glow"
                            ? `0 0 0.75cqw ${setting.emphasis_colour ?? "#bb99ff"}`
                            : undefined,
                      }}
                    >
                      {index ? " " : ""}
                      {text}
                    </span>
                  );
                })}
              </div>
            )}
          </div>
        ) : (
          <p
            role="status"
            className="absolute inset-0 grid place-items-center p-5 text-center text-sm text-muted-foreground"
          >
            {mediaError
              ? "The source could not play."
              : !valid
                ? "Choose a valid clip range."
                : !player
                  ? "Select a source video to preview the clip."
                  : "Loading source video…"}
          </p>
        )}
      </div>
      {!showSaved && controlsReady && player ? (
        <div className="flex flex-col gap-2">
          <input
            type="range"
            aria-label="Clip preview playhead"
            aria-valuetext={`${position.toFixed(1)} of ${duration.toFixed(1)} seconds`}
            min={0}
            max={duration}
            step="0.01"
            value={Math.min(position, duration)}
            className="w-full accent-primary"
            onChange={(event) => seek(Number(event.target.value))}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              onClick={() => {
                setPlayError("");
                if (playing) {
                  player.pause();
                  return;
                }
                if (position >= duration - 0.04) seek(0);
                void player
                  .play()
                  .catch(() =>
                    setPlayError(
                      "Playback could not start. Try playing the preview again.",
                    ),
                  );
              }}
            >
              {playing ? "Pause" : "Play"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => seek(0)}
            >
              Restart
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                const player = playerRef.current;
                if (!player) return;
                player.muted = !player.muted;
              }}
            >
              {muted ? "Unmute" : "Mute"}
            </Button>
            <span className="ml-auto tabular-nums text-xs text-muted-foreground">
              {position.toFixed(1)} / {duration.toFixed(1)} s
            </span>
          </div>
          {coverAt != null &&
            kept.some(([from, to]) => from <= coverAt && coverAt < to) && (
              <Button
                type="button"
                size="sm"
                variant="link"
                className="self-start"
                onClick={() => {
                  player.pause();
                  seek(clipTime(coverAt, kept));
                }}
              >
                Inspect cover frame
              </Button>
            )}
        </div>
      ) : null}
      {mediaError && (
        <Button type="button" variant="outline" size="sm" onClick={onRetry}>
          Retry live preview
        </Button>
      )}
      {playError && (
        <p role="status" className="text-sm text-destructive">
          {playError}
        </p>
      )}
      {!showSaved && pendingPresentation && (
        <p className="text-xs text-muted-foreground">
          Automatic framing is calculated during export. The preview shows the
          current available framing.
        </p>
      )}
      {!showSaved && invalidRemovals && (
        <p role="status" className="text-xs text-destructive">
          The pacing cuts cannot be applied within this trim. Adjust the cuts or
          the trim. The preview is playing the full selected range.
        </p>
      )}
      {!showSaved && options.exportAudio && (
        <p className="text-xs text-muted-foreground">
          Audio cleanup and leveling apply on export.
        </p>
      )}
      {!showSaved && layout.overlays === null && (
        <p role="status" className="text-xs text-destructive">
          Text placements overlap. Adjust timing or placement to preview the
          overlays.
        </p>
      )}
      {!showSaved && captions && !transcription && (
        <p className="text-xs text-muted-foreground">
          Save a transcript to preview spoken captions.
        </p>
      )}
      {!showSaved && wordError?.key === wordsKey && (
        <p role="status" className="text-xs text-destructive">
          {wordError.detail}{" "}
          <Button
            type="button"
            variant="link"
            size="sm"
            onClick={() => setWordRetry((value) => value + 1)}
          >
            Retry captions
          </Button>
        </p>
      )}
    </section>
  );
}
