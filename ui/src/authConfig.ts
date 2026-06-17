import { LogLevel } from '@azure/msal-browser';
import type { Configuration } from '@azure/msal-browser';
import { env } from './env';

const clientId = env.VITE_MSAL_CLIENT_ID || env.VITE_AZURE_CLIENT_ID || 'YOUR_CLIENT_ID';
const tenantId = env.VITE_AZURE_TENANT_ID || 'YOUR_TENANT_ID';
const authority = env.VITE_MSAL_AUTHORITY || `https://login.microsoftonline.com/${tenantId}`;

export const msalConfig: Configuration = {
  auth: {
    clientId,
    authority,
    redirectUri: typeof window !== 'undefined' ? window.location.origin : 'http://localhost',
  },
  cache: {
    cacheLocation: 'sessionStorage',
  },
  system: {
    loggerOptions: {
      loggerCallback: (level, message, containsPii) => {
        if (containsPii) return;
        switch (level) {
          case LogLevel.Error:
            console.error(message);
            break;
          case LogLevel.Warning:
            console.warn(message);
            break;
          default:
            console.log(message);
        }
      },
    },
  },
};

export const loginRequest = {
  scopes: ['User.Read'],
};

export const apiConfig = {
  scopes: [env.VITE_API_SCOPE || `api://${clientId}/access_as_user`],
};
