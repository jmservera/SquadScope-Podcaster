import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockAcquireTokenSilent = vi.fn();
const mockAcquireTokenPopup = vi.fn();
const mockGetAllAccounts = vi.fn();

class MockPublicClientApplication {
  acquireTokenSilent = mockAcquireTokenSilent;
  acquireTokenPopup = mockAcquireTokenPopup;
  getAllAccounts = mockGetAllAccounts;
  initialize = vi.fn().mockResolvedValue(undefined);
}

vi.mock('@azure/msal-browser', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@azure/msal-browser')>();
  return {
    ...actual,
    PublicClientApplication: MockPublicClientApplication,
  };
});

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

describe('apiClient', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.resetModules();
    mockGetAllAccounts.mockReturnValue([
      {
        homeAccountId: 'hid',
        username: 'test@example.com',
        tenantId: 'tid',
        localAccountId: 'lid',
        environment: 'login.microsoftonline.com',
        name: 'Test User',
      },
    ]);
    mockAcquireTokenSilent.mockResolvedValue({ accessToken: 'mock-token' });
    mockFetch.mockResolvedValue(new Response('{}', { status: 200 }));
  });

  it('adds Bearer token to fetch Authorization header', async () => {
    const { authenticatedFetch } = await import('../apiClient');
    await authenticatedFetch('https://api.example.com/data');

    expect(mockFetch).toHaveBeenCalledWith(
      'https://api.example.com/data',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer mock-token',
        }),
      })
    );
  });

  it('falls back to acquireTokenPopup on InteractionRequiredAuthError', async () => {
    const { InteractionRequiredAuthError } = await import('@azure/msal-browser');
    mockAcquireTokenSilent.mockRejectedValueOnce(
      new InteractionRequiredAuthError('interaction_required')
    );
    mockAcquireTokenPopup.mockResolvedValueOnce({ accessToken: 'popup-token' });

    const { authenticatedFetch } = await import('../apiClient');
    await authenticatedFetch('https://api.example.com/data');

    expect(mockAcquireTokenPopup).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith(
      'https://api.example.com/data',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer popup-token',
        }),
      })
    );
  });
});
