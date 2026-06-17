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
