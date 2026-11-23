export const MAX_VIDEO_SECONDS = 6 * 3600;

export type Recording = {
  id: string;
  name: string;
  duration: number;
  has_audio: boolean;
  size_bytes?: number;
};
export type Preset =
  | "none"
  | "classic"
  | "bold"
  | "boxed"
  | "flow"
  | "glow"
  | "impact"
  | "studio"
  | "focus";
export type CaptionCustomization = {
  grouping: "auto" | "one" | "two" | "three" | "four";
  capitalization: "original" | "upper" | "lower";
  font_size: number;
  font_family?: "Arial" | "Georgia" | "Courier New";
  font_weight?: "preset" | "regular" | "bold";
  text_colour?: string | null;
  outline_colour?: string | null;
  shadow_colour?: string | null;
  background_colour?: string | null;
  emphasis_colour?: string | null;
  background?: "preset" | "none" | "box";
  animation?: "preset" | "none" | "pop" | "fade";
  profanity?: "keep" | "symbols" | "replace";
  censor_words?: string[];
  replacement?: string;
};
export type CaptionStyle = {
  preset: Preset;
  position: "auto" | "lower" | "centre" | "upper";
  customization: CaptionCustomization;
};
export type CaptionSource = {
  transcriptionId: string;
  revision: number;
  start: number;
  end: number;
};
export const defaultCaptionCustomization: CaptionCustomization = {
  grouping: "auto",
  capitalization: "original",
  font_size: 80,
};
export type YouTubeSource = Recording & {
  url: string;
  preview_image_url?: string | null;
};
export type FramingMode = "pad" | "crop" | "split" | "follow" | "auto";
export type SourceRegion = {
  x: number;
  y: number;
  width: number;
  height: number;
};
export type SplitLayout = {
  webcam: SourceRegion;
  gameplay: SourceRegion;
  gameplay_fit: "fit" | "fill";
};
export function defaultSplitLayout(): SplitLayout {
  return {
    webcam: { x: 0.75, y: 0, width: 0.25, height: 0.25 },
    gameplay: { x: 0, y: 0, width: 1, height: 1 },
    gameplay_fit: "fit",
  };
}
export type Removal = {
  start: number;
  end: number;
  reason: "silence" | "filler" | "manual";
  enabled: boolean;
};
export type SmartFraming = {
  detector: "faces" | "motion";
  strategy: "crop" | "fit";
  width: number;
  height: number;
  points: { at: number; x: number; y: number }[];
  protected_regions: SourceRegion[];
  warnings: string[];
};
export type FollowPlan = SmartFraming & {
  version: 1;
  start: number;
  end: number;
};
export type AutoLayoutPlan = {
  version: 1;
  start: number;
  end: number;
  mode: "pad" | "crop" | "split" | "follow";
  crop_region: SourceRegion | null;
  split_layout: SplitLayout | null;
  headline_position: "upper" | "centre" | null;
  summary: string;
  warnings: string[];
};
export type QualityOptions = {
  cover_at?: number | null;
  cleanup: "off" | "light" | "standard";
  normalize: boolean;
  removals: Removal[];
  smart_framing: SmartFraming | null;
};
export const defaultQuality: QualityOptions = {
  cleanup: "off",
  normalize: false,
  removals: [],
  smart_framing: null,
};
export type ContentProfile =
  | "auto"
  | "comedy"
  | "podcast"
  | "gaming"
  | "interview"
  | "education"
  | "entertainment";

export type ProjectSettings = {
  batch_mode?: "keep" | "auto" | "highlights";
  clips_per_source?: number;
  presentation_mode?: "off" | "auto" | "talking_head" | "podcast" | "gameplay";
  ai_instructions?: string;
  quality_mode?: boolean;
  content_profile?: ContentProfile;
  multimodal?: boolean;
  audio_cleanup?: "off" | "light" | "standard";
  normalize_audio?: boolean;
  analysis_start: number;
  analysis_end: number;
  duration_mode: "auto" | "manual" | "full";
  headline_enabled?: boolean;
  headline_position?: "auto" | "upper" | "centre";
  min_seconds: number;
  max_seconds: number;
  max_clips: number;
  caption_preset: Preset;
  caption_customization: CaptionCustomization;
  emphasis_words: string[];
  caption_position: "auto" | "lower" | "centre" | "upper";
  framing: FramingMode;
  split_layout?: SplitLayout | null;
  crop_region?: SourceRegion | null;
};
export type TextOverlay = {
  text: string;
  kind: "title" | "headline" | "cta";
  start: number;
  end: number;
  position: "auto" | "upper" | "centre" | "lower";
  style: "card" | "outline" | "violet";
};
export type PostCopyOption = { description: string; hashtags: string[] };
export type PostCopy = {
  recommended: PostCopyOption;
  alternatives: (PostCopyOption & {
    style: "curiosity" | "conversation" | "funny_or_relatable";
  })[];
  reason: string;
  evidence_quote: string;
};
export type ProjectItem = {
  post_copy?: PostCopy | null;
  post_copy_selected?: PostCopyOption | null;
  post_copy_attempted?: boolean;
  post_copy_error?: string | null;
  post_copy_request_id?: string | null;
  post_copy_stale?: boolean;
  transcription_backend?: "cpu" | "cuda" | null;
  posted?: boolean;
  presentation_complete?: boolean;
  presentation_summary?: string | null;
  presentation_warnings?: string[];
  source_item_id?: string | null;
  analysis_attempted?: boolean;
  analysis_complete?: boolean;
  analysis_reason?: string | null;
  selection_reason?: string | null;
  selection_score?: number | null;
  selection_evidence?: string | null;
  selection_visual?: { frame_times: number[]; description: string } | null;
  analysis_start?: number | null;
  analysis_end?: number | null;
  analysis_error?: string | null;
  analysis_choice?: "keep_full" | "trim" | "manual" | null;
  analysis_input_tokens?: number;
  analysis_output_tokens?: number;
  recording_id?: string | null;
  source_name?: string | null;
  source_duration?: number | null;
  transcription_id?: string | null;
  transcript_revision?: number | null;
  transcription_warning?: string | null;
  title_attempted?: boolean;
  title_complete?: boolean;
  title_error?: string | null;
  title_input_tokens?: number;
  title_output_tokens?: number;
  quality: QualityOptions;
  editorial_quality?: {
    hook: number;
    context: number;
    payoff: number;
    standalone: number;
  } | null;
  framing?: FramingMode | null;
  split_layout?: SplitLayout | null;
  crop_region?: SourceRegion | null;
  overlays: TextOverlay[];
  opening_text: string | null;
  headline_enabled: boolean;
  headline_position: "auto" | "upper" | "centre";
  id: string;
  title: string;
  start: number;
  end: number;
  revision: number;
  review: "unreviewed" | "accepted" | "rejected";
  caption_preset: Preset;
  caption_customization: CaptionCustomization;
  emphasis_words: string[];
  caption_position: ProjectSettings["caption_position"];
  error: string | null;
  thumbnail: boolean;
  thumbnail_export_id?: string | null;
  thumbnail_error: string | null;
  export_id: string | null;
  pending_export_id: string | null;
  export: {
    follow_plan?: FollowPlan | null;
    auto_layout?: AutoLayoutPlan | null;
    performance?: {
      backend: "cpu" | "nvenc";
      seconds: number;
      warning: string | null;
    } | null;
    id: string;
    status: string;
    error: string | null;
    duration?: number | null;
    created_at?: string;
  } | null;
};
export type Project = {
  step_progress?: number | null;
  step_started_at?: string | null;
  step_updated_at?: string | null;
  stage_seconds?: Record<string, number>;
  transcription_backend?: "cpu" | "cuda" | null;
  analysis_cache_hit?: boolean;
  parent_project_id?: string | null;
  avoid_used_clips?: boolean;
  recording_ids?: string[];
  generate_titles?: boolean;
  transcription_id: string | null;
  transcript_revision: number | null;
  id: string;
  recording_id: string | null;
  youtube_source_id: string | null;
  source_thumbnail_url: string | null;
  progress: number;
  media_analysis_warning?: string | null;
  title: string;
  created_at: string;
  settings: ProjectSettings;
  status: string;
  stage: string;
  transcription_warning: string | null;
  error: string | null;
  thumbnail: boolean;
  thumbnail_error: string | null;
  analysis_attempted: boolean;
  selection_complete: boolean;
  items: ProjectItem[];
};
export const active = (status: string) =>
  [
    "queued",
    "downloading",
    "transcribing",
    "analysing",
    "rendering",
    "cancelling",
  ].includes(status);
export const ready = (item: ProjectItem) =>
  item.export?.status === "succeeded" && !item.error;
export const deliverable = (item: ProjectItem) =>
  ready(item) && item.review !== "rejected";
export const presets: { value: Preset; label: string }[] = [
  { value: "impact", label: "Impact" },
  { value: "studio", label: "Studio" },
  { value: "focus", label: "Focus" },
  { value: "flow", label: "Flow · word highlight" },
  { value: "glow", label: "Glow · active word" },
  { value: "none", label: "No captions" },
];

export const parseEmphasis = (value: string) =>
  value
    .split(",")
    .map((word) => word.trim())
    .filter(Boolean);
