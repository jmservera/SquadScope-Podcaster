import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../api/episodes', () => ({
  fetchEpisodes: vi.fn(),
  fetchEpisodeAudioUrl: vi.fn(),
}));

import { fetchEpisodes } from '../../api/episodes';
import EpisodeList from '../EpisodeList';

const mockFetchEpisodes = vi.mocked(fetchEpisodes);

describe('EpisodeList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders loading state initially', () => {
    mockFetchEpisodes.mockReturnValue(new Promise(() => {}));
    render(<EpisodeList />);
    expect(screen.getByText('Loading episodes…')).toBeInTheDocument();
  });

  it('renders episode list after loading', async () => {
    mockFetchEpisodes.mockResolvedValue({
      episodes: [
        {
          job_id: 'job-1',
          title: 'Test Episode',
          week: '2026-W24',
          status: 'published',
          audio_url: 'https://example.com/audio.mp3',
          duration_seconds: 180,
          quality_score: 0.85,
          published_at: '2026-06-10',
          created_at: '2026-06-09',
        },
      ],
      total: 1,
    });

    render(<EpisodeList />);

    await waitFor(() => {
      expect(screen.getByText('Episodes (1)')).toBeInTheDocument();
    });
    expect(screen.getByText('Test Episode')).toBeInTheDocument();
    expect(screen.getByText('2026-W24')).toBeInTheDocument();
    expect(screen.getByText('3:00')).toBeInTheDocument();
    expect(screen.getByText('85%')).toBeInTheDocument();
  });

  it('renders error state on failure', async () => {
    mockFetchEpisodes.mockRejectedValue(new Error('Network error'));

    render(<EpisodeList />);

    await waitFor(() => {
      expect(screen.getByText('Error: Network error')).toBeInTheDocument();
    });
  });
});
