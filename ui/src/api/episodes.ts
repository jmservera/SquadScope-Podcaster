import { authenticatedFetch } from './apiClient';

const API_BASE = import.meta.env.VITE_MONITORING_API_URL || '';

export interface Episode {
  job_id: string;
  title: string;
  week: string | null;
  status: string;
  audio_url: string | null;
  duration_seconds: number | null;
  quality_score: number | null;
  published_at: string | null;
  created_at: string | null;
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

export async function fetchEpisodeAudioUrl(jobId: string): Promise<string> {
  const resp = await authenticatedFetch(
    `${API_BASE}/api/episodes/${encodeURIComponent(jobId)}/audio`
  );
  if (!resp.ok) throw new Error(`Failed to fetch audio URL: ${resp.status}`);
  const data = await resp.json();
  return data.url;
}
