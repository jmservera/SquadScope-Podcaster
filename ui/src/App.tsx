import React from 'react';
import type { IPublicClientApplication } from '@azure/msal-browser';
import { Routes, Route, Link } from 'react-router-dom';
import { useIsAuthenticated } from '@azure/msal-react';
import AuthProvider from './components/AuthProvider';
import LoginButton from './components/LoginButton';
import Dashboard from './components/Dashboard';
import ProtectedRoute from './components/ProtectedRoute';

interface AppProps {
  msalInstance?: IPublicClientApplication;
}

const LandingPage: React.FC = () => {
  const isAuthenticated = useIsAuthenticated();

  return (
    <div>
      <h1>SquadScope Podcaster</h1>
      <LoginButton />
      {isAuthenticated ? (
        <div>
          <p>
            You are signed in. Visit the <Link to="/dashboard">Dashboard</Link>.
          </p>
          <Dashboard />
        </div>
      ) : (
        <p>Sign in to access the dashboard.</p>
      )}
    </div>
  );
};

const App: React.FC<AppProps> = ({ msalInstance }) => {
  return (
    <AuthProvider msalInstance={msalInstance}>
      <Routes>
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <LandingPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          }
        />
      </Routes>
    </AuthProvider>
  );
};

export default App;
