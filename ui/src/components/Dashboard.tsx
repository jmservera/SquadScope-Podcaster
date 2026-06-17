import React from 'react';
// MSAL import kept as dead code for future optional use (#273)
// import { useMsal } from '@azure/msal-react';
import { useAuth } from './AuthProvider';

const Dashboard: React.FC = () => {
  const { username } = useAuth();

  return (
    <div>
      <h1>Welcome, {username}</h1>
      <section>
        <h2>Account Info</h2>
        <dl>
          <dt>Username</dt>
          <dd>{username}</dd>
        </dl>
      </section>
    </div>
  );
};

export default Dashboard;
