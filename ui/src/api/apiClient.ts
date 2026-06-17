import {
  PublicClientApplication,
  InteractionRequiredAuthError,
} from '@azure/msal-browser';
import type { AccountInfo } from '@azure/msal-browser';
import { msalConfig, apiConfig } from '../authConfig';

const msalInstance = new PublicClientApplication(msalConfig);

async function getAccessToken(account: AccountInfo): Promise<string> {
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
  const accounts = msalInstance.getAllAccounts();
  if (accounts.length === 0) {
    throw new Error('No authenticated account found');
  }
  const token = await getAccessToken(accounts[0]);
  return fetch(url, {
    ...options,
    headers: {
      ...options.headers,
      Authorization: `Bearer ${token}`,
    },
  });
}

export { msalInstance };
