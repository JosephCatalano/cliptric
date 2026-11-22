export type InstagramConnection = {
  configured: boolean;
  connected: boolean;
  account_id: string | null;
};
export type InstagramCreator = { account_id: string; username: string };
export type InstagramExport = {
  duration: number;
  width: number;
  height: number;
  size_bytes: number;
  caption_warning: string | null;
};
export type InstagramPost = {
  id: string;
  account_id: string;
  status: string;
  total_bytes: number;
  uploaded_bytes: number;
  media_id: string | null;
  error: string | null;
};
