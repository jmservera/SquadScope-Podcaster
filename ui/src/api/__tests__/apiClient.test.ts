import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../env', () => ({
  getAuthToken: vi.fn(),
}));

import { getAuthToken } from '../../env';
const mockGetAuthToken = vi.mocked(getAuthToken);

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

describe('apiClient', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.resetModules();
    mockFetch.mockResolvedValue(new Response('{}', { status: 200 }));
  });

  it('adds Bearer token to fetch Authorization header when token exists', async () => {
    mockGetAuthToken.mockReturnValue('test-jwt-token');

    const { authenticatedFetch } = await import('../apiClient');
    await authenticatedFetch('https://api.example.com/data');

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe('https://api.example.com/data');
    expect(options.headers).toBeInstanceOf(Headers);
    expect(options.headers.get('Authorization')).toBe('Bearer test-jwt-token');
  });

  it('sends request without Authorization header when no token', async () => {
    mockGetAuthToken.mockReturnValue(null);

    const { authenticatedFetch } = await import('../apiClient');
    await authenticatedFetch('https://api.example.com/data');

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [, options] = mockFetch.mock.calls[0];
    expect(options.headers.get('Authorization')).toBeNull();
  });
});
