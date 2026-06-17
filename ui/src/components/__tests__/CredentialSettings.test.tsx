import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../api/credentials', () => ({
  fetchCredentials: vi.fn(),
  saveCredential: vi.fn(),
  deleteCredential: vi.fn(),
}));

import { fetchCredentials, saveCredential, deleteCredential } from '../../api/credentials';
import CredentialSettings from '../CredentialSettings';

const mockFetch = vi.mocked(fetchCredentials);
const mockSave = vi.mocked(saveCredential);
const mockDelete = vi.mocked(deleteCredential);

const sampleCredential = {
  id: 'c1',
  type: 'spotify' as const,
  label: 'Production Spotify',
  created_at: '2026-01-01',
  updated_at: '2026-01-02',
  is_set: true,
};

describe('CredentialSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading state initially', () => {
    mockFetch.mockReturnValue(new Promise(() => {}));
    render(<CredentialSettings />);
    expect(screen.getByText('Loading credentials…')).toBeInTheDocument();
  });

  it('renders credentials table after loading', async () => {
    mockFetch.mockResolvedValue({ credentials: [sampleCredential] });
    render(<CredentialSettings />);

    await waitFor(() => {
      expect(screen.getByText('Credential Settings')).toBeInTheDocument();
    });
    expect(screen.getByText('Production Spotify')).toBeInTheDocument();
    expect(screen.getByText('Spotify')).toBeInTheDocument();
    expect(screen.getByText('✓ Set')).toBeInTheDocument();
  });

  it('shows empty state when no credentials', async () => {
    mockFetch.mockResolvedValue({ credentials: [] });
    render(<CredentialSettings />);

    await waitFor(() => {
      expect(screen.getByText('No credentials configured yet.')).toBeInTheDocument();
    });
  });

  it('shows error on fetch failure', async () => {
    mockFetch.mockRejectedValue(new Error('Network error'));
    render(<CredentialSettings />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Network error');
    });
  });

  it('opens add form and submits credential', async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValue({ credentials: [] });
    mockSave.mockResolvedValue(sampleCredential);

    render(<CredentialSettings />);

    await waitFor(() => {
      expect(screen.getByText('Add Credential')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Add Credential'));
    expect(screen.getByLabelText('Label')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Label'), 'My Spotify');
    await user.type(screen.getByLabelText('SP_DC'), 'dc-value');
    await user.type(screen.getByLabelText('SP_KEY'), 'key-value');
    await user.click(screen.getByText('Save Credential'));

    expect(mockSave).toHaveBeenCalledWith({
      type: 'spotify',
      label: 'My Spotify',
      values: { SP_DC: 'dc-value', SP_KEY: 'key-value' },
    });
  });

  it('validates required fields before submit', async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValue({ credentials: [] });

    render(<CredentialSettings />);

    await waitFor(() => {
      expect(screen.getByText('Add Credential')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Add Credential'));
    await user.click(screen.getByText('Save Credential'));

    expect(screen.getByRole('alert')).toHaveTextContent('All fields are required');
    expect(mockSave).not.toHaveBeenCalled();
  });

  it('deletes a credential', async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValue({ credentials: [sampleCredential] });
    mockDelete.mockResolvedValue(undefined);

    render(<CredentialSettings />);

    await waitFor(() => {
      expect(screen.getByText('Production Spotify')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Delete'));
    expect(mockDelete).toHaveBeenCalledWith('c1');
  });

  it('uses password inputs for credential values', async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValue({ credentials: [] });

    render(<CredentialSettings />);
    await waitFor(() => {
      expect(screen.getByText('Add Credential')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Add Credential'));

    const spDcInput = screen.getByLabelText('SP_DC');
    expect(spDcInput).toHaveAttribute('type', 'password');
  });

  it('cancels form and hides it', async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValue({ credentials: [] });

    render(<CredentialSettings />);
    await waitFor(() => {
      expect(screen.getByText('Add Credential')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Add Credential'));
    expect(screen.getByLabelText('Label')).toBeInTheDocument();

    await user.click(screen.getByText('Cancel'));
    expect(screen.queryByLabelText('Label')).not.toBeInTheDocument();
  });
});
