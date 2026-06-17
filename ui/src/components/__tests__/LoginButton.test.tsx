import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  InteractionStatus,
  Logger,
  type AccountInfo,
  type IPublicClientApplication,
} from '@azure/msal-browser';
import LoginButton from '../LoginButton';

const mockLoginPopup = vi.fn().mockResolvedValue({});
const mockLogoutPopup = vi.fn().mockResolvedValue({});

vi.mock('@azure/msal-react', () => ({
  useMsal: vi.fn(),
  useIsAuthenticated: vi.fn(),
}));

import { useMsal, useIsAuthenticated } from '@azure/msal-react';

const mockUseMsal = vi.mocked(useMsal);
const mockUseIsAuthenticated = vi.mocked(useIsAuthenticated);
const logger = new Logger({});

type MsalContext = ReturnType<typeof useMsal>;

function createMsalContext(accounts: AccountInfo[]): MsalContext {
  return {
    instance: {
      loginPopup: mockLoginPopup,
      logoutPopup: mockLogoutPopup,
    } as unknown as IPublicClientApplication,
    accounts,
    inProgress: InteractionStatus.None,
    logger,
  };
}

describe('LoginButton', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders Sign In button when not authenticated', () => {
    mockUseIsAuthenticated.mockReturnValue(false);
    mockUseMsal.mockReturnValue(createMsalContext([]));

    render(<LoginButton />);
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument();
  });

  it('calls loginPopup when Sign In is clicked', () => {
    mockUseIsAuthenticated.mockReturnValue(false);
    mockUseMsal.mockReturnValue(createMsalContext([]));

    render(<LoginButton />);
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }));
    expect(mockLoginPopup).toHaveBeenCalledTimes(1);
  });

  it('renders Sign Out button and user name when authenticated', () => {
    mockUseIsAuthenticated.mockReturnValue(true);
    mockUseMsal.mockReturnValue(
      createMsalContext([
        {
          name: 'Test User',
          username: 'test@example.com',
          tenantId: 'tid',
          homeAccountId: 'hid',
          environment: 'login.microsoftonline.com',
          localAccountId: 'lid',
        },
      ])
    );

    render(<LoginButton />);
    expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument();
    expect(screen.getByText('Test User')).toBeInTheDocument();
  });

  it('calls logoutPopup when Sign Out is clicked', () => {
    mockUseIsAuthenticated.mockReturnValue(true);
    mockUseMsal.mockReturnValue(
      createMsalContext([
        {
          name: 'Test User',
          username: 'test@example.com',
          tenantId: 'tid',
          homeAccountId: 'hid',
          environment: 'login.microsoftonline.com',
          localAccountId: 'lid',
        },
      ])
    );

    render(<LoginButton />);
    fireEvent.click(screen.getByRole('button', { name: /sign out/i }));
    expect(mockLogoutPopup).toHaveBeenCalledTimes(1);
  });
});
