import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../apiClient', () => ({
  authenticatedFetch: vi.fn(),
}));

import { authenticatedFetch } from '../apiClient';
import { fetchCredentials, saveCredential, deleteCredential } from '../credentials';

const mockAuthFetch = vi.mocked(authenticatedFetch);

describe('credentials API', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('fetchCredentials', () => {
    it('returns credential list on success', async () => {
      const payload = {
        credentials: [
          { id: 'c1', type: 'spotify', label: 'Prod', created_at: '2026-01-01', updated_at: '2026-01-02', is_set: true },
        ],
      };
      mockAuthFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(payload),
      } as Response);

      const result = await fetchCredentials();
      expect(result).toEqual(payload);
      expect(mockAuthFetch).toHaveBeenCalledWith(expect.stringContaining('/api/credentials'));
    });

    it('throws on non-ok response', async () => {
      mockAuthFetch.mockResolvedValue({ ok: false, status: 500 } as Response);
      await expect(fetchCredentials()).rejects.toThrow('Failed to fetch credentials: 500');
    });
  });

  describe('saveCredential', () => {
    it('posts credential and returns summary', async () => {
      const saved = { id: 'c2', type: 'spotify', label: 'New', created_at: '2026-01-01', updated_at: '2026-01-01', is_set: true };
      mockAuthFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(saved),
      } as Response);

      const result = await saveCredential({
        type: 'spotify',
        label: 'New',
        values: { SP_DC: 'abc', SP_KEY: 'def' },
      });

      expect(result).toEqual(saved);
      expect(mockAuthFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/credentials'),
        expect.objectContaining({ method: 'POST' }),
      );
    });

    it('throws on failure', async () => {
      mockAuthFetch.mockResolvedValue({ ok: false, status: 400 } as Response);
      await expect(
        saveCredential({ type: 'spotify', label: 'X', values: {} }),
      ).rejects.toThrow('Failed to save credential: 400');
    });
  });

  describe('deleteCredential', () => {
    it('sends DELETE request', async () => {
      mockAuthFetch.mockResolvedValue({ ok: true } as Response);
      await deleteCredential('c1');
      expect(mockAuthFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/credentials/c1'),
        expect.objectContaining({ method: 'DELETE' }),
      );
    });

    it('throws on failure', async () => {
      mockAuthFetch.mockResolvedValue({ ok: false, status: 404 } as Response);
      await expect(deleteCredential('c1')).rejects.toThrow('Failed to delete credential: 404');
    });
  });
});
