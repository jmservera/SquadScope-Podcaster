// MSAL imports kept as dead code for future optional use (#273)
// import {
//   PublicClientApplication,
//   InteractionRequiredAuthError,
// } from '@azure/msal-browser';
// import type { AccountInfo } from '@azure/msal-browser';
// import { msalConfig, apiConfig } from '../authConfig';

import { getAuthToken } from '../env';

export async function authenticatedFetch(
  url: string,
  options: RequestInit = {}
): Promise<Response> {
  const headers = new Headers(options.headers as HeadersInit | undefined);
  const token = getAuthToken();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  return fetch(url, {
    ...options,
    headers,
  });
}
