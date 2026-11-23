export type YouTubeConnection = {
  configured: boolean;
  connected: boolean;
  account_id: string | null;
  accounts: { account_id: string; label: string }[];
};
export const youtubeDefaults = {
  categoryId: "20",
  privacy: "public",
  kids: "no",
  synthetic: "no",
  notify: true,
};
export type YouTubeCreator = {
  account_id: string;
  channel: { id: string; title: string };
  categories: { id: string; title: string }[];
};
export type YouTubeExport = {
  id: string;
  duration: number;
  width: number;
  height: number;
  size_bytes: number;
  caption_warning: string | null;
};
export type YouTubePost = {
  id: string;
  export_id: string;
  account_id: string;
  channel: { id: string; title: string };
  status:
    | "queued"
    | "initializing"
    | "uploading"
    | "paused"
    | "processing"
    | "ready"
    | "failed"
    | "unknown";
  total_bytes: number;
  uploaded_bytes: number;
  video_id: string | null;
  actual_privacy: "private" | "unlisted" | "public" | null;
  can_resume: boolean;
  resume_after: number;
  error: string | null;
  request: { title: string; description: string; privacy: string };
};
export const youtubeStatus: Record<YouTubePost["status"], string> = {
  queued: "Waiting to upload",
  initializing: "Starting YouTube upload",
  uploading: "Sending video to YouTube",
  paused: "Upload incomplete",
  processing: "Uploaded; YouTube is processing",
  ready: "YouTube processing complete",
  failed: "Upload needs attention",
  unknown: "Upload outcome needs checking",
};
