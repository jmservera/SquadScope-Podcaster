import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../apiClient', () => ({
  authenticatedFetch: vi.fn(),
}));

import { authenticatedFetch } from '../apiClient';
import { fetchPodcastConfig, savePodcastConfig, uploadMusic } from '../podcastConfig';

const mockAuthFetch = vi.mocked(authenticatedFetch);

const sampleConfig = {
  name: 'My Podcast',
  intro_music_url: 'https://example.com/intro.mp3',
  outro_music_url: 'https://example.com/outro.mp3',
  publish_targets: [
    { platform: 'spotify' as const, enabled: true, target_id: 'show123' },
  ],
};

describe('podcastConfig API', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('fetchPodcastConfig', () => {
    it('returns config on success', async () => {
      mockAuthFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(sampleConfig),
      } as Response);

      const result = await fetchPodcastConfig();
      expect(result).toEqual(sampleConfig);
    });

    it('throws on failure', async () => {
      mockAuthFetch.mockResolvedValue({ ok: false, status: 500 } as Response);
      await expect(fetchPodcastConfig()).rejects.toThrow('Failed to fetch podcast config: 500');
    });
  });

  describe('savePodcastConfig', () => {
    it('sends PUT and returns saved config', async () => {
      mockAuthFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(sampleConfig),
      } as Response);

      const result = await savePodcastConfig(sampleConfig);
      expect(result).toEqual(sampleConfig);
      expect(mockAuthFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/podcast-config'),
        expect.objectContaining({ method: 'PUT' }),
      );
    });

    it('throws on failure', async () => {
      mockAuthFetch.mockResolvedValue({ ok: false, status: 422 } as Response);
      await expect(savePodcastConfig(sampleConfig)).rejects.toThrow('Failed to save podcast config: 422');
    });
  });

  describe('uploadMusic', () => {
    it('uploads file and returns URL', async () => {
      mockAuthFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ url: 'https://blob.example.com/intro.mp3' }),
      } as Response);

      const file = new File(['audio'], 'intro.mp3', { type: 'audio/mpeg' });
      const result = await uploadMusic(file, 'intro');
      expect(result.url).toBe('https://blob.example.com/intro.mp3');
      expect(mockAuthFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/podcast-config/music'),
        expect.objectContaining({ method: 'POST' }),
      );
    });

    it('throws on failure', async () => {
      mockAuthFetch.mockResolvedValue({ ok: false, status: 413 } as Response);
      const file = new File(['audio'], 'big.mp3', { type: 'audio/mpeg' });
      await expect(uploadMusic(file, 'outro')).rejects.toThrow('Failed to upload music: 413');
    });
  });
});
