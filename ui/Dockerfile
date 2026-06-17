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
FROM nginxinc/nginx-unprivileged:1.28-alpine

# Remove default site.
USER root
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
