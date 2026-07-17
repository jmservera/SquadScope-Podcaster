# syntax=docker/dockerfile:1
#
# Management UI — static React SPA served by nginx (#264).
# Multi-stage build: Node builds the Vite app, nginx serves the dist.
# Runtime configuration (MSAL client ID, API base URL) is injected at
# container start via env-config.js so the same image works across environments.
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts

COPY . .
RUN npm run build

# --- runtime stage ---
FROM nginxinc/nginx-unprivileged:1.31-alpine@sha256:cc300bd2e8776708a8c2c8686a74f06eadf221c5ce01e85d0e821955d7a730ab

USER root

# Patch OS packages for known HIGH CVEs without changing the base image pin,
# ahead of a refreshed upstream image:
#   - libexpat CVE-2026-45186
#   - curl/libcurl CVE-2026-5773, CVE-2026-6276
#   - c-ares CVE-2026-33630
# Pin the known-fixed minimum versions so the build is deterministic and fails
# fast if the patched packages are unavailable.
RUN apk add --no-cache --upgrade \
    "libexpat>=2.8.1-r0" \
    "curl>=8.20.0-r0" \
    "libcurl>=8.20.0-r0" \
    "c-ares>=1.34.8-r0"

# Remove default site.
RUN rm -rf /usr/share/nginx/html/*

# Custom nginx config for SPA routing (all routes → index.html).
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

# Entrypoint script that writes runtime env vars into env-config.js
# so the SPA can read window.__ENV at boot.
COPY docker-entrypoint.sh /docker-entrypoint.d/40-env-config.sh
RUN chmod +x /docker-entrypoint.d/40-env-config.sh \
    && chown -R nginx:nginx /usr/share/nginx/html

USER nginx

EXPOSE 8080
