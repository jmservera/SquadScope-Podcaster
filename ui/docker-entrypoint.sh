#!/bin/sh
# Write runtime environment variables into env-config.js so the SPA
# can read them via window.__ENV without rebuilding the image.
# Values are JSON-escaped to prevent script injection from quotes,
# backslashes, newlines, or </script> in env var values.
set -e

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/	/\\t/g; s/</\\u003c/g' | \
    tr '\n' '\036' | sed 's/\x1e/\\n/g' | tr '\r' '\036' | sed 's/\x1e/\\r/g'
}

cat > /usr/share/nginx/html/env-config.js <<EOF
window.__ENV = {
  VITE_MSAL_CLIENT_ID: "$(json_escape "${VITE_MSAL_CLIENT_ID:-}")",
  VITE_MSAL_AUTHORITY: "$(json_escape "${VITE_MSAL_AUTHORITY:-}")",
  VITE_API_BASE_URL: "$(json_escape "${VITE_API_BASE_URL:-}")"
};
EOF
