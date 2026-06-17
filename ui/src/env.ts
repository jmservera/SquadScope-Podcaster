/**
 * Runtime environment helper.
 * Reads from window.__ENV (injected by docker-entrypoint.sh at container start)
 * with fallback to import.meta.env for local development.
 */

declare global {
  interface Window {
    __ENV?: Record<string, string>;
  }
}

function getEnv(key: string): string {
  return window.__ENV?.[key] ?? import.meta.env[key] ?? '';
}

export const env = {
  VITE_MSAL_CLIENT_ID: getEnv('VITE_MSAL_CLIENT_ID'),
  VITE_MSAL_AUTHORITY: getEnv('VITE_MSAL_AUTHORITY'),
  VITE_API_BASE_URL: getEnv('VITE_API_BASE_URL'),
  VITE_AZURE_CLIENT_ID: getEnv('VITE_AZURE_CLIENT_ID'),
  VITE_AZURE_TENANT_ID: getEnv('VITE_AZURE_TENANT_ID'),
  VITE_API_SCOPE: getEnv('VITE_API_SCOPE'),
  VITE_MONITORING_API_URL: getEnv('VITE_MONITORING_API_URL'),
};

const PLACEHOLDER_IDS = ['', 'YOUR_CLIENT_ID', 'your-client-id-here'];

/** Returns true when a real MSAL client ID is configured. */
export function isMsalConfigured(): boolean {
  const clientId = (env.VITE_MSAL_CLIENT_ID || env.VITE_AZURE_CLIENT_ID).trim();
  return clientId !== '' && !PLACEHOLDER_IDS.includes(clientId);
}

// ---------------------------------------------------------------------------
// Simple JWT auth helpers (#273)
// ---------------------------------------------------------------------------

const TOKEN_KEY = 'podcaster_auth_token';
const USERNAME_KEY = 'podcaster_auth_username';

export function getAuthToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function getAuthUsername(): string | null {
  return sessionStorage.getItem(USERNAME_KEY);
}

export function setAuthSession(token: string, username: string): void {
  sessionStorage.setItem(TOKEN_KEY, token);
  sessionStorage.setItem(USERNAME_KEY, username);
}

export function clearAuthSession(): void {
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(USERNAME_KEY);
}

export function isSimpleAuthActive(): boolean {
  return getAuthToken() !== null;
}
