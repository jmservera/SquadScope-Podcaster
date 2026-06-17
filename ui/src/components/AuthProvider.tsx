import React, { createContext, useContext, useState, useCallback } from 'react';
// MSAL imports kept for future optional use — bypassed by simple auth (#273)
// import { MsalProvider } from '@azure/msal-react';
// import { PublicClientApplication, type IPublicClientApplication } from '@azure/msal-browser';
// import { msalConfig } from '../authConfig';
import {
  getAuthToken,
  getAuthUsername,
  setAuthSession,
  clearAuthSession,
} from '../env';

interface AuthContextValue {
  isAuthenticated: boolean;
  username: string | null;
  token: string | null;
  login: (token: string, username: string) => void;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue>({
  isAuthenticated: false,
  username: null,
  token: null,
  login: () => {},
  logout: () => {},
});

export function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}

interface AuthProviderProps {
  children: React.ReactNode;
  msalInstance?: unknown; // kept for API compat; ignored
}

const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const [token, setToken] = useState<string | null>(getAuthToken());
  const [username, setUsername] = useState<string | null>(getAuthUsername());

  const login = useCallback((newToken: string, newUsername: string) => {
    setAuthSession(newToken, newUsername);
    setToken(newToken);
    setUsername(newUsername);
  }, []);

  const logout = useCallback(() => {
    clearAuthSession();
    setToken(null);
    setUsername(null);
  }, []);

  const value: AuthContextValue = {
    isAuthenticated: token !== null,
    username,
    token,
    login,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export default AuthProvider;
