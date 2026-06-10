#!/usr/bin/env bash
set -euo pipefail

# Usage: scripts/get-podcaster-values.sh [resourceGroup] [functionAppName] [storageAccountName] [deploymentName]
RG=${1:-podcaster-rg}
FUNCTION_APP=${2:-podcaster-func}
STORAGE_ACCOUNT=${3:-podcasterstorage}
DEPLOYMENT_NAME=${4:-}

echo "Resource Group: $RG"
echo "Function App: $FUNCTION_APP"
echo "Storage Account: $STORAGE_ACCOUNT"

echo "\nFetching Function App details..."
az webapp show -g "$RG" -n "$FUNCTION_APP" -o json | jq '{defaultHostName: .defaultHostName, identity: .identity}'

echo "\nFetching Storage Account details..."
az storage account show -g "$RG" -n "$STORAGE_ACCOUNT" -o json | jq '{name: .name, primaryEndpoints: .primaryEndpoints}'

if [ -n "$DEPLOYMENT_NAME" ]; then
  echo "\nFetching deployment outputs for $DEPLOYMENT_NAME..."
  az deployment group show -g "$RG" --name "$DEPLOYMENT_NAME" -o json | jq '.properties.outputs'
else
  echo "\nDeployment name not provided; to query deployment outputs pass the deployment name as 4th arg."
fi

cat <<INSTR

To set GitHub secrets (placeholder example; do NOT run here):

# Example (replace <token> with the real token)
# gh secret set PODCASTER_TTS_API_TOKEN --repo jmservera/SquadScope-Podcaster --body "<token>"

This script does not attempt to set any secrets. Run the gh command shown above from your machine with gh CLI authenticated to GitHub.
INSTR
