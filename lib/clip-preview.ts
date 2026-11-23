import type { CaptionStyle, Removal, TextOverlay } from "./projects";

export type PreviewHeadline = {
  text: string;
  position: "auto" | "upper" | "centre";
  seconds: number;
  appearance?: "classic" | "cover" | "profile";
};

type Region = Exclude<TextOverlay["position"], "auto">;
export type PreviewWord = { start: number; end: number; text: string };
export type ClipInterval = [start: number, end: number];

export function previewIntervals(start: number, end: number, removals: Removal[]) {
  const original: ClipInterval[] = [[start, end]];
  const kept: ClipInterval[] = [];
  let cursor = start;
  const sorted = removals.filter((cut) => cut.enabled).sort((a, b) => a.start - b.start);
  for (const cut of sorted) {
    if (cut.start < cursor || cut.end <= cut.start || cut.end > end) return original;
    if (cut.start - cursor >= 0.02) kept.push([cursor, cut.start]);
    else if (cut.start > cursor) return original;
    cursor = cut.end;
  }
  if (end - cursor >= 0.02) kept.push([cursor, end]);
  else if (end > cursor) return original;
  return clipTime(end, kept) >= Math.max(0.1, (end - start) * 0.5) ? kept : original;
}

export function clipTime(at: number, kept: ClipInterval[]) {
  return kept.reduce((result, [start, end]) => result + Math.max(0, Math.min(at, end) - start), 0);
}

export function sourceTime(at: number, kept: ClipInterval[]) {
  for (const [start, end] of kept) {
    if (at < end - start) return start + at;
    at -= end - start;
  }
  return kept.at(-1)?.[1] ?? 0;
}

export function previewTextLayout(
  overlays: TextOverlay[],
  headline: PreviewHeadline | null,
  captions: CaptionStyle | undefined,
  duration: number,
  split: boolean,
) {
  const captionRegions: (Region | null)[] = !captions || captions.preset === "none"
    ? [null]
    : captions.position === "auto"
      ? split ? ["lower", "centre"] : ["lower", "centre", "upper"]
      : [captions.position];
  const ordered = overlays
    .filter((overlay) => overlay.text.trim() && overlay.end > overlay.start)
    .sort((left, right) => Number(left.position === "auto") - Number(right.position === "auto"));
  for (const caption of captionRegions) {
    const headlineRegion = headline?.position === "auto"
      ? caption === "upper" ? "centre" : "upper"
      : headline?.position;
    if (headlineRegion && caption === headlineRegion && captions?.position === "auto") continue;
    const occupied: { position: Region; start: number; end: number }[] = [];
    if (caption) occupied.push({ position: caption, start: 0, end: duration });
    if (headlineRegion && headline)
      occupied.push({ position: headlineRegion, start: 0, end: headline.seconds });
    const placed = [];
    for (const overlay of ordered) {
      const regions: Region[] = overlay.position === "auto"
        ? ["upper", "centre", "lower"] : [overlay.position];
      const position = regions.find((region) => !occupied.some((item) =>
        item.position === region && item.start < overlay.end && item.end > overlay.start,
      ));
      if (!position) break;
      const next = { ...overlay, position };
      placed.push(next);
      occupied.push(next);
    }
    if (placed.length === ordered.length) return { caption, headlineRegion, overlays: placed };
  }
  return { caption: captionRegions[0], headlineRegion: "upper" as const, overlays: null };
}

export function previewCaptionCues(
  words: PreviewWord[],
  kept: ClipInterval[],
  grouping: CaptionStyle["customization"]["grouping"],
) {
  const maximum = { auto: 6, one: 1, two: 2, three: 3, four: 4 }[grouping];
  const cues: { start: number; end: number; words: PreviewWord[] }[] = [];
  let cue: (typeof cues)[number] | undefined;
  for (const word of words) {
    const start = clipTime(word.start, kept);
    const end = clipTime(word.end, kept);
    const text = word.text.trim();
    if (!text || end <= start) continue;
    if (cue && (
      cue.words.length >= maximum ||
      cue.words.map((part) => part.text).join(" ").length + text.length + 1 > 48 ||
      Math.max(end, cue.end) - cue.start > 3 ||
      start - cue.end > 0.45
    )) cue = undefined;
    if (!cue) {
      cue = { start, end, words: [] };
      cues.push(cue);
    }
    cue.words.push({ start, end, text });
    cue.end = Math.max(cue.end, end);
    if (cue.words.length >= 2 && /[.!?]["')\]]?$/.test(text)) cue = undefined;
  }
  return cues;
}
