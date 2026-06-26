import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../api/jobs', () => ({
  fetchJobAssets: vi.fn(),
}));
vi.mock('../../api/episodes', () => ({
  getAuthenticatedStreamUrl: vi.fn((url: string) => `http://localhost${url}?token=test-token`),
}));

import { fetchJobAssets } from '../../api/jobs';
import AssetBrowser from '../AssetBrowser';
import type { JobAsset } from '../../api/jobs';

const mockFetchAssets = vi.mocked(fetchJobAssets);

function asset(name: string, kind: string, contentType: string): JobAsset {
  const path = `jobs/job-1/${name}`;
  return { name, path, url: `/api/stream/${path}`, content_type: contentType, kind };
}

describe('AssetBrowser', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading then empty state when there are no assets', async () => {
    mockFetchAssets.mockResolvedValue({ job_id: 'job-1', assets: [], total: 0 });
    render(<AssetBrowser jobId="job-1" />);
    expect(screen.getByText('Loading assets…')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText('No media assets for this job yet.')).toBeInTheDocument()
    );
  });

  it('renders video, audio and thumbnail sections with players', async () => {
    mockFetchAssets.mockResolvedValue({
      job_id: 'job-1',
      assets: [
        asset('video/job-1.mp4', 'video', 'video/mp4'),
        asset('episode.mp3', 'audio', 'audio/mpeg'),
        asset('thumbnail.png', 'image', 'image/png'),
      ],
      total: 3,
    });

    render(<AssetBrowser jobId="job-1" />);

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Video' })).toBeInTheDocument()
    );
    expect(screen.getByRole('heading', { name: 'Audio' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Thumbnails' })).toBeInTheDocument();

    const videoSource = document.querySelector('video source') as HTMLSourceElement;
    expect(videoSource.src).toContain('/api/stream/jobs/job-1/video/job-1.mp4');
    const audioSource = document.querySelector('audio source') as HTMLSourceElement;
    expect(audioSource.src).toContain('/api/stream/jobs/job-1/episode.mp3');
    const img = screen.getByAltText('thumbnail.png') as HTMLImageElement;
    expect(img.src).toContain('/api/stream/jobs/job-1/thumbnail.png');
  });

  it('renders an error state on failure', async () => {
    mockFetchAssets.mockRejectedValue(new Error('Network error'));
    render(<AssetBrowser jobId="job-1" />);
    await waitFor(() => expect(screen.getByText('Error: Network error')).toBeInTheDocument());
  });
});
