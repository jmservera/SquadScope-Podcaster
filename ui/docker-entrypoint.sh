#!/bin/sh
# Write runtime environment variables into env-config.js so the SPA
# can read them via window.__ENV without rebuilding the image.
set -e

cat > /usr/share/nginx/html/env-config.js <<EOF
window.__ENV = {
  VITE_MSAL_CLIENT_ID: "${VITE_MSAL_CLIENT_ID:-}",
  VITE_MSAL_AUTHORITY: "${VITE_MSAL_AUTHORITY:-}",
  VITE_API_BASE_URL: "${VITE_API_BASE_URL:-}"
};
EOF
