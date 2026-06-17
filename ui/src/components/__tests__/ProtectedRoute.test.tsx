import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import ProtectedRoute from '../ProtectedRoute';
import { useAuth } from '../AuthProvider';

vi.mock('../AuthProvider', () => ({
  useAuth: vi.fn(),
}));

const mockUseAuth = vi.mocked(useAuth);

describe('ProtectedRoute', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows children when authenticated', () => {
    mockUseAuth.mockReturnValue({ isAuthenticated: true, username: 'admin', token: 'tok', login: vi.fn(), logout: vi.fn() });

    render(
      <MemoryRouter>
        <ProtectedRoute>
          <div>Protected Content</div>
        </ProtectedRoute>
      </MemoryRouter>
    );

    expect(screen.getByText('Protected Content')).toBeInTheDocument();
  });

  it('redirects when not authenticated', () => {
    mockUseAuth.mockReturnValue({ isAuthenticated: false, username: null, token: null, login: vi.fn(), logout: vi.fn() });

    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <ProtectedRoute>
          <div>Protected Content</div>
        </ProtectedRoute>
      </MemoryRouter>
    );

    expect(screen.queryByText('Protected Content')).not.toBeInTheDocument();
  });
});
