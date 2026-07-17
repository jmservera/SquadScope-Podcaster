import { authenticatedFetch } from './apiClient';
import { env } from '../env';

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

/** Build a plain resolved stream URL without embedding the full login JWT. */
export function getAuthenticatedStreamUrl(streamUrl: string): string {
  return resolveStreamUrl(streamUrl);
}

/** Build a stream URL carrying a short-lived, blob-scoped query token. */
export async function getScopedStreamUrl(streamUrl: string): Promise<string> {
  const resolved = resolveStreamUrl(streamUrl);
  const marker = '/api/stream/';
  const markerIndex = resolved.indexOf(marker);
  if (markerIndex === -1) return resolved;

  const pathWithSuffix = resolved.slice(markerIndex + marker.length);
  const blobPath = decodeURIComponent(pathWithSuffix.split(/[?#]/, 1)[0]);
  if (!blobPath) return resolved;

  try {
    const resp = await authenticatedFetch(
      `${API_BASE}/api/stream-token?path=${encodeURIComponent(blobPath)}`
    );
    if (!resp.ok) return resolved;
    const data = (await resp.json()) as { token?: string };
    if (!data.token) return resolved;
    const separator = resolved.includes('?') ? '&' : '?';
    return `${resolved}${separator}token=${encodeURIComponent(data.token)}`;
  } catch {
    return resolved;
  }
}

/** Resolve an episode's audio_url path to an absolute URL. */
export function resolveAudioUrl(audioUrl: string): string {
  return resolveStreamUrl(audioUrl);
}

/** Build an audio URL with token query param for browser media elements. */
export function getAuthenticatedAudioUrl(audioUrl: string): string {
  return getAuthenticatedStreamUrl(audioUrl);
}
