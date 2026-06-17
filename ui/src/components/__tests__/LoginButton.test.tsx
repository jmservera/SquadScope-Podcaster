import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import LoginButton from '../LoginButton';
import { useAuth } from '../AuthProvider';

vi.mock('../AuthProvider', () => ({
  useAuth: vi.fn(),
}));

const mockUseAuth = vi.mocked(useAuth);

describe('LoginButton', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders login form when not authenticated', () => {
    mockUseAuth.mockReturnValue({ isAuthenticated: false, username: null, token: null, login: vi.fn(), logout: vi.fn() });

    render(<LoginButton />);
    expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument();
  });

  it('renders Sign Out button and username when authenticated', () => {
    mockUseAuth.mockReturnValue({ isAuthenticated: true, username: 'admin', token: 'tok', login: vi.fn(), logout: vi.fn() });

    render(<LoginButton />);
    expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument();
    expect(screen.getByText('admin')).toBeInTheDocument();
  });

  it('calls logout when Sign Out is clicked', () => {
    const mockLogout = vi.fn();
    mockUseAuth.mockReturnValue({ isAuthenticated: true, username: 'admin', token: 'tok', login: vi.fn(), logout: mockLogout });

    render(<LoginButton />);
    fireEvent.click(screen.getByRole('button', { name: /sign out/i }));
    expect(mockLogout).toHaveBeenCalledTimes(1);
  });
});
