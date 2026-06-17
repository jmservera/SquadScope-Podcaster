import { authenticatedFetch } from './apiClient';

const API_BASE = import.meta.env.VITE_MONITORING_API_URL || '';

export interface Episode {
  job_id: string;
  title: string | null;
  status: string;
  created_at: string | null;
  audio_path: string | null;
  audio_url: string | null;
  quality_score: number | null;
  publish_status: string | null;
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

/** Resolve an episode's audio_url path to an absolute URL. */
export function resolveAudioUrl(audioUrl: string): string {
  if (audioUrl.startsWith('http')) return audioUrl;
  return `${API_BASE}${audioUrl}`;
}
