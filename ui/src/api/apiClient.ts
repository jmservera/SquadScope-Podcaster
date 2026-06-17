import {
  PublicClientApplication,
  InteractionRequiredAuthError,
} from '@azure/msal-browser';
import type { AccountInfo } from '@azure/msal-browser';
import { msalConfig, apiConfig } from '../authConfig';

const msalInstance = new PublicClientApplication(msalConfig);
const initialized = msalInstance.initialize();

async function getAccessToken(account: AccountInfo): Promise<string> {
  await initialized;
  const request = {
    scopes: apiConfig.scopes,
    account,
  };

  try {
    const response = await msalInstance.acquireTokenSilent(request);
    return response.accessToken;
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      const response = await msalInstance.acquireTokenPopup(request);
      return response.accessToken;
    }
    throw error;
  }
}

export async function authenticatedFetch(
  url: string,
  options: RequestInit = {}
): Promise<Response> {
  await initialized;
  const accounts = msalInstance.getAllAccounts();
  if (accounts.length === 0) {
    throw new Error('No authenticated account found');
  }
  const token = await getAccessToken(accounts[0]);
  const headers = new Headers(options.headers as HeadersInit | undefined);
  headers.set('Authorization', `Bearer ${token}`);
  return fetch(url, {
    ...options,
    headers,
  });
}

export { msalInstance };
