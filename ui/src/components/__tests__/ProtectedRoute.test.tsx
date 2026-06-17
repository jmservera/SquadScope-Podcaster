import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import ProtectedRoute from '../ProtectedRoute';

const mockUseIsAuthenticated = vi.fn();

vi.mock('@azure/msal-react', () => ({
  useIsAuthenticated: () => mockUseIsAuthenticated(),
  AuthenticatedTemplate: ({
    children,
  }: {
    children: React.ReactNode;
  }) => (mockUseIsAuthenticated() ? <div data-testid="auth">{children}</div> : null),
  UnauthenticatedTemplate: ({
    children,
  }: {
    children: React.ReactNode;
  }) => (!mockUseIsAuthenticated() ? <div data-testid="unauth">{children}</div> : null),
}));

describe('ProtectedRoute', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows children when authenticated', () => {
    mockUseIsAuthenticated.mockReturnValue(true);

    render(
      <ProtectedRoute>
        <div>Protected Content</div>
      </ProtectedRoute>
    );

    expect(screen.getByText('Protected Content')).toBeInTheDocument();
    expect(screen.getByTestId('auth')).toBeInTheDocument();
    expect(screen.queryByText('Please sign in')).not.toBeInTheDocument();
  });

  it('shows sign-in message when not authenticated', () => {
    mockUseIsAuthenticated.mockReturnValue(false);

    render(
      <ProtectedRoute>
        <div>Protected Content</div>
      </ProtectedRoute>
    );

    expect(screen.queryByText('Protected Content')).not.toBeInTheDocument();
    expect(screen.getByTestId('unauth')).toBeInTheDocument();
    expect(screen.getByText('Please sign in')).toBeInTheDocument();
  });
});
