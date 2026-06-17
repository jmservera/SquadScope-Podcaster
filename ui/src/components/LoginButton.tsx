import React, { useState } from 'react';
// MSAL imports kept as dead code for future optional use (#273)
// import { useMsal, useIsAuthenticated } from '@azure/msal-react';
// import { loginRequest } from '../authConfig';
import { useAuth } from './AuthProvider';
import { env } from '../env';

const LoginButton: React.FC = () => {
  const { isAuthenticated, username, login, logout } = useAuth();
  const [formUser, setFormUser] = useState('');
  const [formPass, setFormPass] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const baseUrl = env.VITE_MONITORING_API_URL || env.VITE_API_BASE_URL || '';
      const resp = await fetch(`${baseUrl}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: formUser, password: formPass }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({ detail: 'Login failed' }));
        throw new Error(body.detail || 'Login failed');
      }
      const data = await resp.json();
      login(data.token, data.username);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  if (isAuthenticated) {
    return (
      <div>
        <span>{username}</span>{' '}
        <button onClick={logout}>Sign Out</button>
      </div>
    );
  }

  return (
    <form onSubmit={handleLogin} style={{ maxWidth: '320px' }}>
      <div style={{ marginBottom: '8px' }}>
        <label htmlFor="login-username">Username</label>
        <br />
        <input
          id="login-username"
          type="text"
          value={formUser}
          onChange={(e) => setFormUser(e.target.value)}
          autoComplete="username"
          required
          style={{ width: '100%', padding: '6px' }}
        />
      </div>
      <div style={{ marginBottom: '8px' }}>
        <label htmlFor="login-password">Password</label>
        <br />
        <input
          id="login-password"
          type="password"
          value={formPass}
          onChange={(e) => setFormPass(e.target.value)}
          autoComplete="current-password"
          required
          style={{ width: '100%', padding: '6px' }}
        />
      </div>
      {error && (
        <div role="alert" style={{ color: 'red', marginBottom: '8px' }}>
          {error}
        </div>
      )}
      <button type="submit" disabled={loading} style={{ padding: '6px 16px' }}>
        {loading ? 'Signing in…' : 'Sign In'}
      </button>
    </form>
  );
};

export default LoginButton;
