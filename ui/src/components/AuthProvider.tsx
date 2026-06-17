import React from 'react';
import { MsalProvider } from '@azure/msal-react';
import {
  PublicClientApplication,
  type IPublicClientApplication,
} from '@azure/msal-browser';
import { msalConfig } from '../authConfig';

interface AuthProviderProps {
  children: React.ReactNode;
  msalInstance?: IPublicClientApplication;
}

const defaultInstance = new PublicClientApplication(msalConfig);

const AuthProvider: React.FC<AuthProviderProps> = ({
  children,
  msalInstance = defaultInstance,
}) => {
  return <MsalProvider instance={msalInstance}>{children}</MsalProvider>;
};

export default AuthProvider;
