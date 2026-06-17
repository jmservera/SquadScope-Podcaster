import React from 'react';
import {
  AuthenticatedTemplate,
  UnauthenticatedTemplate,
  useIsAuthenticated,
} from '@azure/msal-react';

interface ProtectedRouteProps {
  children: React.ReactNode;
}

const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children }) => {
  const isAuthenticated = useIsAuthenticated();

  return (
    <>
      <AuthenticatedTemplate>{isAuthenticated ? children : null}</AuthenticatedTemplate>
      <UnauthenticatedTemplate>
        {!isAuthenticated ? <div>Please sign in</div> : null}
      </UnauthenticatedTemplate>
    </>
  );
};

export default ProtectedRoute;
