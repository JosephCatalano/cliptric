import type { ProjectItem } from "./projects";

export const itemEdit = (item: ProjectItem) => ({
  revision: item.revision,
  auto_presentation: false,
  title: item.title,
  review: item.review,
  start: item.start,
  end: item.end,
  caption_preset: item.caption_preset,
  caption_customization: item.caption_customization,
  emphasis_words: item.emphasis_words,
  caption_position: item.caption_position,
  headline_enabled: item.headline_enabled,
  headline_position: item.headline_position,
  opening_text: item.opening_text,
  overlays: item.overlays,
  quality: item.quality,
  framing: item.framing ?? null,
  split_layout: item.split_layout ?? null,
  crop_region: item.crop_region ?? null,
});

export const reviewNeeded = (item: ProjectItem) =>
  item.review === "unreviewed" ||
  Boolean(
    item.transcription_warning ||
    item.title_error ||
    item.presentation_warnings?.length,
  );
