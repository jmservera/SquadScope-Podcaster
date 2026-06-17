import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../api/episodes', () => ({
  fetchEpisodes: vi.fn(),
  resolveAudioUrl: vi.fn((url: string) => `http://localhost${url}`),
}));

import { fetchEpisodes } from '../../api/episodes';
import EpisodeList from '../EpisodeList';

const mockFetchEpisodes = vi.mocked(fetchEpisodes);

const sampleEpisode = {
  job_id: 'job-1',
  title: 'Test Episode',
  status: 'published',
  created_at: '2026-06-09',
  audio_path: 'job-1/output.mp3',
  audio_url: '/api/stream/job-1/output.mp3',
  quality_score: 0.85,
  publish_status: 'published',
};

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
      episodes: [sampleEpisode],
      total: 1,
    });

    render(<EpisodeList />);

    await waitFor(() => {
      expect(screen.getByText('Episodes (1)')).toBeInTheDocument();
    });
    expect(screen.getByText('Test Episode')).toBeInTheDocument();
    expect(screen.getAllByText('published')).toHaveLength(2);
    expect(screen.getByText('85%')).toBeInTheDocument();
  });

  it('renders error state on failure', async () => {
    mockFetchEpisodes.mockRejectedValue(new Error('Network error'));

    render(<EpisodeList />);

    await waitFor(() => {
      expect(screen.getByText('Error: Network error')).toBeInTheDocument();
    });
  });

  it('expands row and shows audio player on click', async () => {
    mockFetchEpisodes.mockResolvedValue({
      episodes: [sampleEpisode],
      total: 1,
    });

    render(<EpisodeList />);

    await waitFor(() => {
      expect(screen.getByText('Test Episode')).toBeInTheDocument();
    });

    const row = screen.getByText('Test Episode').closest('tr')!;
    await userEvent.click(row);

    expect(screen.getByText('Audio Preview')).toBeInTheDocument();
    const sourceEl = document.querySelector('audio source') as HTMLSourceElement;
    expect(sourceEl).not.toBeNull();
    expect(sourceEl.src).toContain('/api/stream/job-1/output.mp3');
  });
});
