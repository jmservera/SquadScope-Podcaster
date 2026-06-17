import React from 'react';
import { useMsal } from '@azure/msal-react';

const Dashboard: React.FC = () => {
  const { accounts } = useMsal();
  const account = accounts[0];

  return (
    <div>
      <h1>Welcome, {account?.name}</h1>
      <section>
        <h2>Account Info</h2>
        <dl>
          <dt>Username</dt>
          <dd>{account?.username}</dd>
          <dt>Tenant ID</dt>
          <dd>{account?.tenantId}</dd>
          <dt>Home Account ID</dt>
          <dd>{account?.homeAccountId}</dd>
        </dl>
      </section>
    </div>
  );
};

export default Dashboard;
