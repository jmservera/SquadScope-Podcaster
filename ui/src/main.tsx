import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
// MSAL imports kept as dead code for future optional use (#273)
// import { PublicClientApplication } from '@azure/msal-browser';
// import { msalConfig } from './authConfig';
import App from './App';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
