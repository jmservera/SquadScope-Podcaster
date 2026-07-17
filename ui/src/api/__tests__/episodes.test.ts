import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../env', () => ({
  env: {
    VITE_MONITORING_API_URL: 'https://api.example.com',
    VITE_API_BASE_URL: '',
  },
}));

vi.mock('../apiClient', () => ({
  authenticatedFetch: vi.fn(),
}));

import { authenticatedFetch } from '../apiClient';
import { getScopedStreamUrl } from '../episodes';

const mockAuthenticatedFetch = vi.mocked(authenticatedFetch);

describe('episodes stream URLs', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('mints and appends a scoped stream token', async () => {
    mockAuthenticatedFetch.mockResolvedValue(
      new Response(JSON.stringify({ token: 'scoped-token' }), { status: 200 })
    );

    const url = await getScopedStreamUrl('/api/stream/jobs/job-1/episode.mp3');

    expect(mockAuthenticatedFetch).toHaveBeenCalledWith(
      'https://api.example.com/api/stream-token?path=jobs%2Fjob-1%2Fepisode.mp3'
    );
    expect(url).toBe(
      'https://api.example.com/api/stream/jobs/job-1/episode.mp3?token=scoped-token'
    );
  });

  it('falls back to the plain URL when auth is open', async () => {
    mockAuthenticatedFetch.mockResolvedValue(
      new Response(JSON.stringify({ token: '' }), { status: 200 })
    );

    await expect(getScopedStreamUrl('/api/stream/jobs/job-1/episode.mp3')).resolves.toBe(
      'https://api.example.com/api/stream/jobs/job-1/episode.mp3'
    );
  });
});
