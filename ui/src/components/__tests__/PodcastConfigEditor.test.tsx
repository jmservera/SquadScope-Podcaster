import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../api/podcastConfig', () => ({
  fetchPodcastConfig: vi.fn(),
  savePodcastConfig: vi.fn(),
  uploadMusic: vi.fn(),
}));

import { fetchPodcastConfig, savePodcastConfig, uploadMusic } from '../../api/podcastConfig';
import PodcastConfigEditor from '../PodcastConfigEditor';

const mockFetchConfig = vi.mocked(fetchPodcastConfig);
const mockSaveConfig = vi.mocked(savePodcastConfig);
const mockUploadMusic = vi.mocked(uploadMusic);

const sampleConfig = {
  name: 'SquadScope Weekly',
  intro_music_url: 'https://blob.example.com/intro.mp3',
  outro_music_url: 'https://blob.example.com/outro.mp3',
  publish_targets: [
    { platform: 'spotify' as const, enabled: true, target_id: 'show-abc123' },
  ],
};

describe('PodcastConfigEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading state initially', () => {
    mockFetchConfig.mockReturnValue(new Promise(() => {}));
    render(<PodcastConfigEditor />);
    expect(screen.getByText('Loading configuration…')).toBeInTheDocument();
  });

  it('renders config form after loading', async () => {
    mockFetchConfig.mockResolvedValue(sampleConfig);
    render(<PodcastConfigEditor />);

    await waitFor(() => {
      expect(screen.getByText('Podcast Configuration')).toBeInTheDocument();
    });
    expect(screen.getByLabelText('Podcast Name')).toHaveValue('SquadScope Weekly');
    expect(screen.getByLabelText('Intro Music URL')).toHaveValue('https://blob.example.com/intro.mp3');
    expect(screen.getByText('Target 1')).toBeInTheDocument();
  });

  it('shows error on fetch failure', async () => {
    mockFetchConfig.mockRejectedValue(new Error('Server error'));
    render(<PodcastConfigEditor />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Server error');
    });
  });

  it('saves config on submit', async () => {
    const user = userEvent.setup();
    mockFetchConfig.mockResolvedValue(sampleConfig);
    mockSaveConfig.mockResolvedValue(sampleConfig);

    render(<PodcastConfigEditor />);

    await waitFor(() => {
      expect(screen.getByLabelText('Podcast Name')).toHaveValue('SquadScope Weekly');
    });

    await user.click(screen.getByText('Save Configuration'));
    expect(mockSaveConfig).toHaveBeenCalledWith(sampleConfig);

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Configuration saved successfully');
    });
  });

  it('validates podcast name is required', async () => {
    const user = userEvent.setup();
    mockFetchConfig.mockResolvedValue({ ...sampleConfig, name: '' });

    render(<PodcastConfigEditor />);

    await waitFor(() => {
      expect(screen.getByLabelText('Podcast Name')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Save Configuration'));
    expect(screen.getByRole('alert')).toHaveTextContent('Podcast name is required');
    expect(mockSaveConfig).not.toHaveBeenCalled();
  });

  it('adds and removes publish targets', async () => {
    const user = userEvent.setup();
    mockFetchConfig.mockResolvedValue({ ...sampleConfig, publish_targets: [] });

    render(<PodcastConfigEditor />);

    await waitFor(() => {
      expect(screen.getByText('No publish targets configured.')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Add Publish Target'));
    expect(screen.getByText('Target 1')).toBeInTheDocument();

    await user.click(screen.getByText('Remove Target'));
    expect(screen.getByText('No publish targets configured.')).toBeInTheDocument();
  });

  it('updates publish target fields', async () => {
    const user = userEvent.setup();
    mockFetchConfig.mockResolvedValue(sampleConfig);

    render(<PodcastConfigEditor />);

    await waitFor(() => {
      expect(screen.getByText('Target 1')).toBeInTheDocument();
    });

    const targetIdInput = screen.getByLabelText('Spotify Show ID');
    expect(targetIdInput).toHaveValue('show-abc123');

    await user.clear(targetIdInput);
    await user.type(targetIdInput, 'new-show-id');
    expect(targetIdInput).toHaveValue('new-show-id');
  });

  it('uploads music file', async () => {
    mockFetchConfig.mockResolvedValue(sampleConfig);
    mockUploadMusic.mockResolvedValue({ url: 'https://blob.example.com/new-intro.mp3' });

    render(<PodcastConfigEditor />);

    await waitFor(() => {
      expect(screen.getByLabelText('Intro Music URL')).toBeInTheDocument();
    });

    const file = new File(['audio-data'], 'intro.mp3', { type: 'audio/mpeg' });
    const input = screen.getByLabelText('Upload intro music') as HTMLInputElement;

    await userEvent.upload(input, file);

    await waitFor(() => {
      expect(mockUploadMusic).toHaveBeenCalledWith(file, 'intro');
    });
  });
});
