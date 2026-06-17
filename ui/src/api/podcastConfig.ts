import { authenticatedFetch } from './apiClient';
import { env } from '../env';

const API_BASE = env.VITE_MONITORING_API_URL || env.VITE_API_BASE_URL || '';

export interface PublishTarget {
  platform: 'spotify' | 'youtube' | 'rss';
  enabled: boolean;
  /** Platform-specific identifier (Spotify show ID, YouTube channel ID, RSS feed URL). */
  target_id: string;
}

export interface PublishingPreferences {
  auto_publish: boolean;
  schedule_cron?: string;
}

export interface PodcastConfigData {
  name: string;
  intro_music_url: string;
  outro_music_url: string;
  publish_targets: PublishTarget[];
  publishing_preferences?: PublishingPreferences;
}

export async function fetchPodcastConfig(): Promise<PodcastConfigData> {
  const resp = await authenticatedFetch(`${API_BASE}/api/podcast-config`);
  if (!resp.ok) throw new Error(`Failed to fetch podcast config: ${resp.status}`);
  return resp.json();
}

export async function savePodcastConfig(config: PodcastConfigData): Promise<PodcastConfigData> {
  const resp = await authenticatedFetch(`${API_BASE}/api/podcast-config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
  if (!resp.ok) throw new Error(`Failed to save podcast config: ${resp.status}`);
  return resp.json();
}

export async function uploadMusic(
  file: File,
  slot: 'intro' | 'outro',
): Promise<{ url: string }> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('slot', slot);

  const resp = await authenticatedFetch(`${API_BASE}/api/podcast-config/music`, {
    method: 'POST',
    body: formData,
  });
  if (!resp.ok) throw new Error(`Failed to upload music: ${resp.status}`);
  return resp.json();
}
