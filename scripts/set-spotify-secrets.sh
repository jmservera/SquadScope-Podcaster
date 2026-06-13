#!/usr/bin/env bash
# set-spotify-secrets.sh — Update SP_DC and SP_KEY in GitHub repo secrets and Azure Container App
#
# Usage: ./scripts/set-spotify-secrets.sh [path/to/.env]
#
# Reads SP_DC and SP_KEY from an .env file (default: .env in repo root).
# Requires: gh CLI (authenticated), az CLI (logged in)

set +x
set -euo pipefail

REPO="jmservera/SquadScope-Podcaster"
RESOURCE_GROUP="squadscope-podcaster"
CONTAINER_APP="${RESOURCE_GROUP}-api"

ENV_FILE="${1:-.env}"

echo "🔑 Spotify for Creators — Cookie Secret Rotation"
echo "================================================="
echo ""

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ .env file not found: $ENV_FILE"
    echo ""
    echo "Create one with:"
    echo "  SP_DC=your_sp_dc_cookie_value"
    echo "  SP_KEY=your_sp_key_cookie_value"
    echo ""
    echo "Extract cookies from your browser:"
    echo "  1. Log in to https://podcasters.spotify.com"
    echo "  2. Open DevTools → Application → Cookies"
    echo "  3. Copy sp_dc and sp_key values"
    exit 1
fi

echo "📄 Reading from: $ENV_FILE"

# Source .env file (supports KEY=VALUE and KEY="VALUE" formats)
SP_DC=""
SP_KEY=""
while IFS='=' read -r key value; do
    # Skip comments and empty lines
    [[ -z "$key" || "$key" =~ ^# ]] && continue
    # Trim whitespace and quotes
    value="${value#\"}"
    value="${value%\"}"
    value="${value#\'}"
    value="${value%\'}"
    case "$key" in
        SP_DC)  SP_DC="$value" ;;
        SP_KEY) SP_KEY="$value" ;;
    esac
done < "$ENV_FILE"

if [[ -z "$SP_DC" || -z "$SP_KEY" ]]; then
    echo "❌ Both SP_DC and SP_KEY must be set in $ENV_FILE"
    exit 1
fi

echo "  ✅ SP_DC and SP_KEY loaded"

echo ""
echo "📦 Updating GitHub repo secrets..."
echo "$SP_DC" | gh secret set SP_DC --repo "$REPO"
echo "$SP_KEY" | gh secret set SP_KEY --repo "$REPO"
echo "  ✅ GitHub secrets updated"

echo ""
echo "☁️  Updating Azure Container App secrets..."
az containerapp secret set \
    --name "$CONTAINER_APP" \
    --resource-group "$RESOURCE_GROUP" \
    --secrets "sp-dc=$SP_DC" "sp-key=$SP_KEY" \
    --output none

echo "  ✅ Azure secrets updated"

echo ""
echo "🔄 Updating Container App env vars to reference secrets..."
az containerapp update \
    --name "$CONTAINER_APP" \
    --resource-group "$RESOURCE_GROUP" \
    --set-env-vars "SP_DC=secretref:sp-dc" "SP_KEY=secretref:sp-key" \
    --output none

echo "  ✅ Environment variables linked to secrets"

echo ""
echo "✅ Done! Spotify publish credentials rotated in both GitHub and Azure."
echo "   Test with: gh workflow run deploy-azure.yml --repo $REPO"
