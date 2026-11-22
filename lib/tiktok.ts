export type TikTokConnection = {
  configured: boolean;
  connected: boolean;
  account_id: string | null;
  audited: boolean;
};
export type TikTokCreator = {
  account_id: string;
  creator_username: string;
  creator_nickname: string;
  privacy_level_options: string[];
  comment_disabled: boolean;
  duet_disabled: boolean;
  stitch_disabled: boolean;
  max_video_post_duration_sec: number;
  audited: boolean;
};
export type TikTokPost = {
  id: string;
  export_id: string;
  account_id: string;
  status: string;
  publish_id: string | null;
  uploaded_bytes: number;
  total_bytes: number;
  post_ids: string[];
  error: string | null;
};
export const privacyLabels: Record<string, string> = {
  PUBLIC_TO_EVERYONE: "Everyone",
  MUTUAL_FOLLOW_FRIENDS: "Friends",
  FOLLOWER_OF_CREATOR: "Followers",
  SELF_ONLY: "Only me",
};
