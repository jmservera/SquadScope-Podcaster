import React from 'react';
// MSAL import kept as dead code for future optional use (#273)
// import type { IPublicClientApplication } from '@azure/msal-browser';
import { Routes, Route, Link } from 'react-router-dom';
// import { useIsAuthenticated } from '@azure/msal-react';
import AuthProvider from './components/AuthProvider';
import { useAuth } from './components/AuthProvider';
import LoginButton from './components/LoginButton';
import Dashboard from './components/Dashboard';
import JobMonitor from './components/JobMonitor';
import RunComparison from './components/RunComparison';
import EpisodeList from './components/EpisodeList';
import CredentialSettings from './components/CredentialSettings';
import PodcastConfigEditor from './components/PodcastConfigEditor';
import ProtectedRoute from './components/ProtectedRoute';
import Layout from './components/Layout';
import './layout.css';

const LandingPage: React.FC = () => {
  const { isAuthenticated } = useAuth();

  return (
    <div className="landing-page">
      <h1>SquadScope Podcaster</h1>
      <LoginButton />
      {isAuthenticated ? (
        <div className="landing-actions">
          <p>
            You are signed in. Visit the <Link to="/dashboard">Dashboard</Link>{' '}
            or the <Link to="/jobs">Job Monitor</Link>{' '}
            or browse <Link to="/episodes">Episodes</Link>{' '}
            or manage <Link to="/credentials">Credentials</Link>{' '}
            and <Link to="/podcast-config">Podcast Config</Link>.
          </p>
          <Dashboard />
        </div>
      ) : (
        <p>Sign in to access the dashboard.</p>
      )}
    </div>
  );
};

const App: React.FC = () => {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <Layout>
                <Dashboard />
              </Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/jobs"
          element={
            <ProtectedRoute>
              <Layout>
                <JobMonitor />
              </Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/comparison"
          element={
            <ProtectedRoute>
              <Layout>
                <RunComparison />
              </Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/episodes"
          element={
            <ProtectedRoute>
              <Layout>
                <EpisodeList />
              </Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/credentials"
          element={
            <ProtectedRoute>
              <Layout>
                <CredentialSettings />
              </Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/podcast-config"
          element={
            <ProtectedRoute>
              <Layout>
                <PodcastConfigEditor />
              </Layout>
            </ProtectedRoute>
          }
        />
      </Routes>
    </AuthProvider>
  );
};

export default App;
