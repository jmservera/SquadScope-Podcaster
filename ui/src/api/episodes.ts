import { authenticatedFetch } from './apiClient';
import { env, getAuthToken } from '../env';

const API_BASE = env.VITE_MONITORING_API_URL || env.VITE_API_BASE_URL || '';

export interface EpisodeArtifact {
  name: string;
  path: string;
  url: string;
  content_type: string | null;
}

export interface Episode {
  job_id: string;
  title: string | null;
  status: string;
  created_at: string | null;
  audio_path: string | null;
  audio_url: string | null;
  video_path: string | null;
  video_url: string | null;
  quality_score: number | null;
  publish_status: string | null;
  artifacts: EpisodeArtifact[];
}

export interface EpisodeListResponse {
  episodes: Episode[];
  total: number;
}

export async function fetchEpisodes(limit = 20, offset = 0): Promise<EpisodeListResponse> {
  const resp = await authenticatedFetch(
    `${API_BASE}/api/episodes?limit=${limit}&offset=${offset}`
  );
  if (!resp.ok) throw new Error(`Failed to fetch episodes: ${resp.status}`);
  return resp.json();
}

/** Resolve an episode's stream path to an absolute URL. */
export function resolveStreamUrl(streamUrl: string): string {
  if (streamUrl.startsWith('http')) return streamUrl;
  return `${API_BASE}${streamUrl}`;
}

/** Build a stream URL with token query param for browser media/download elements. */
export function getAuthenticatedStreamUrl(streamUrl: string): string {
  const resolved = resolveStreamUrl(streamUrl);
  const token = getAuthToken();
  if (!token) return resolved;
  const separator = resolved.includes('?') ? '&' : '?';
  return `${resolved}${separator}token=${encodeURIComponent(token)}`;
}

/** Resolve an episode's audio_url path to an absolute URL. */
export function resolveAudioUrl(audioUrl: string): string {
  return resolveStreamUrl(audioUrl);
}

/** Build an audio URL with token query param for browser media elements. */
export function getAuthenticatedAudioUrl(audioUrl: string): string {
  return getAuthenticatedStreamUrl(audioUrl);
}
