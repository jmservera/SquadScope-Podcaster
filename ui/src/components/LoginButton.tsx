import React from 'react';
import { useMsal, useIsAuthenticated } from '@azure/msal-react';
import { loginRequest } from '../authConfig';

const LoginButton: React.FC = () => {
  const { instance, accounts } = useMsal();
  const isAuthenticated = useIsAuthenticated();

  const handleLogin = () => {
    instance.loginPopup(loginRequest).catch(console.error);
  };

  const handleLogout = () => {
    instance.logoutPopup().catch(console.error);
  };

  if (isAuthenticated) {
    const account = accounts[0];
    return (
      <div>
        <span>{account?.name}</span>
        <button onClick={handleLogout}>Sign Out</button>
      </div>
    );
  }

  return <button onClick={handleLogin}>Sign In</button>;
};

export default LoginButton;
