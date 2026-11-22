export const platforms = [
  "youtube",
  "tiktok",
  "instagram",
  "x",
  "facebook",
] as const;
export type Platform = (typeof platforms)[number];
export const platformNames: Record<Platform, string> = {
  youtube: "YouTube",
  tiktok: "TikTok",
  instagram: "Instagram",
  x: "X",
  facebook: "Facebook",
};
export const platformRoot = (platform: Platform) =>
  `/api/${platform === "youtube" ? "youtube-publishing" : platform}`;
export type ConnectedAccount = { account_id: string; label: string };
export type SocialConnection = {
  configured: boolean;
  connected: boolean;
  account_id: string | null;
  accounts: ConnectedAccount[];
  audited?: boolean;
};
export type SocialPost = {
  id: string;
  account_id: string;
  status: string;
  total_bytes: number;
  uploaded_bytes: number;
  media_id: string | null;
  post_id: string | null;
  error: string | null;
};
