import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../api/episodes', () => ({
  fetchEpisodes: vi.fn(),
  getAuthenticatedAudioUrl: vi.fn((url: string) => `http://localhost${url}?token=test-token`),
  getAuthenticatedStreamUrl: vi.fn((url: string) => `http://localhost${url}?token=test-token`),
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
  video_path: null,
  video_url: null,
  quality_score: 0.85,
  publish_status: 'published',
  artifacts: [],
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
      expect(screen.getByRole('heading', { name: 'Episodes' })).toBeInTheDocument();
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

  it('shows video player and artifact downloads when present', async () => {
    mockFetchEpisodes.mockResolvedValue({
      episodes: [
        {
          ...sampleEpisode,
          video_path: 'job-1/video/job-1.mp4',
          video_url: '/api/stream/job-1/video/job-1.mp4',
          artifacts: [
            {
              name: 'wav',
              path: 'job-1/episode.wav',
              url: '/api/stream/job-1/episode.wav',
              content_type: 'audio/wav',
            },
          ],
        },
      ],
      total: 1,
    });

    render(<EpisodeList />);

    await waitFor(() => {
      expect(screen.getByText('Test Episode')).toBeInTheDocument();
    });

    const row = screen.getByText('Test Episode').closest('tr')!;
    await userEvent.click(row);

    expect(screen.getByText('Video Preview')).toBeInTheDocument();
    const videoSource = document.querySelector('video source') as HTMLSourceElement;
    expect(videoSource).not.toBeNull();
    expect(videoSource.src).toContain('/api/stream/job-1/video/job-1.mp4');
    expect(videoSource.type).toBe('video/mp4');

    expect(screen.getByText('Artifacts')).toBeInTheDocument();
    expect(screen.getByText('Download wav')).toBeInTheDocument();
  });
});
