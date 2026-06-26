import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from './AuthProvider';

interface LayoutProps {
  children: React.ReactNode;
}

const navItems = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/jobs', label: 'Jobs' },
  { to: '/comparison', label: 'Comparison' },
  { to: '/episodes', label: 'Episodes' },
  { to: '/credentials', label: 'Credentials' },
  { to: '/podcast-config', label: 'Config' },
];

const Layout: React.FC<LayoutProps> = ({ children }) => {
  const location = useLocation();
  const { username, logout } = useAuth();

  return (
    <div className="layout-container">
      <aside className="sidebar">
        <div>
          <div className="sidebar-brand">Claracle</div>
          <nav aria-label="Primary">
            <ul className="sidebar-nav">
              {navItems.map((item) => {
                const isActive = location.pathname === item.to;
                return (
                  <li key={item.to}>
                    <Link className={isActive ? 'active' : undefined} to={item.to}>
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
        </div>

        <div className="sidebar-footer">
          <div className="sidebar-user-label">Signed in as</div>
          <div className="sidebar-user">{username ?? 'Unknown user'}</div>
          <button className="btn btn-secondary" onClick={logout} type="button">
            Sign Out
          </button>
        </div>
      </aside>

      <main className="main-content">{children}</main>
    </div>
  );
};

export default Layout;
