export interface CampaignSummary {
  id: string;
  name?: string;
  mode?: string;
  sources?: number;
  error?: string;
}

export interface Campaign {
  id: string;
  name: string;
  language: string;
  mode: "clip_source" | "logo_overlay";
  sources: string[];
  logo?: { path: string; position: string; opacity: number };
  required_hashtags: string[];
  required_mentions: string[];
  target_accounts: { platform: string; account_label: string; device_id?: string }[];
  limits: { per_day: number; min_gap_minutes: number; max_claude_requests_per_source: number };
  clip: { min_s: number; max_s: number; min_score: number; max_clips_per_source: number };
  subtitle_preset: "classic" | "bold" | "karaoke";
  output_dir: string;
}

export interface Source {
  id: number;
  sha256: string;
  path: string;
  duration_s: number;
  width: number;
  height: number;
  has_audio: number;
  status: string;
  transcribed: boolean;
}

export interface Clip {
  id: number;
  start: number;
  end: number;
  hook_type: string;
  score: number;
  title_overlay: string;
  description: string;
  hashtags: string[];
  mentions: string[];
  status: string;
  reject_reason: string | null;
  subtitle_preset: string | null;
  speed_factor: number | null;
  has_teaser: number | null;
  has_callouts: number | null;
  zoom_enabled: number | null;
}

export interface Publication {
  id: number;
  clip_id: number;
  platform: string;
  account_label: string;
  status: string;
  attempts: number;
  scheduled_at: string;
  published_at: string | null;
  external_id: string | null;
  last_error: string | null;
  title_overlay: string;
  views: number;
  likes: number;
  comments: number;
}

export interface Job {
  id: number;
  campaign: string;
  kind: string;
  status: string;
  log: string;
  updated_at: string;
}

export interface AnalyticsRow {
  key: string | number | null;
  publications: number;
  views: number;
  likes: number;
  comments: number;
}

export interface Settings {
  whisper_model: "tiny" | "base" | "small" | "medium" | "large-v3";
  prefilter_top_n: number;
  ffmpeg_path: string;
  adb_path: string;
  claude_daily_limit: number;
}

export class ApiError extends Error {}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  if (!r.ok) {
    const body = await r.json().catch(() => ({ detail: r.statusText }));
    const detail = Array.isArray(body.detail) ? body.detail.join("\n") : String(body.detail);
    throw new ApiError(detail);
  }
  return r.json() as Promise<T>;
}

export const sendJson = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
