#!/usr/bin/env bash
#
# get-podcaster-values.sh
#
# Discover the deployed Podcaster Azure resources at the resource-group level and
# emit ready-to-run `gh secret set` commands with the real resolved values.
#
# The operator only needs to provide a resource group name (defaults to the
# deployed `squadscope-podcaster`); everything else is discovered via `az`.
#
# SAFETY
#   * This is a LOCAL operator tool. It prints the `gh secret set` commands —
#     including secret values — to the terminal only.
#   * It NEVER writes secret values to a committed file, a CI log, or
#     $GITHUB_OUTPUT. When it detects a CI environment it refuses to print
#     resolved secret values unless --force-ci is passed.
#   * Use --out <file> to write the commands to a local gitignored path instead
#     of stdout when that is more convenient.
#
# Usage:
#   scripts/get-podcaster-values.sh [options]
#
# Options:
#   -g, --resource-group <name>   Azure resource group (default: squadscope-podcaster)
#       --squadscope-repo <o/r>   SquadScope repo for caller secrets (default: jmservera/SquadScope)
#       --podcaster-repo <o/r>    Podcaster repo for service secrets (default: jmservera/SquadScope-Podcaster)
#       --out <file>              Write commands to <file> instead of stdout (gitignored path recommended)
#       --force-ci                Allow running in a CI environment (NOT recommended)
#   -h, --help                    Show this help and exit

set -euo pipefail

RESOURCE_GROUP="squadscope-podcaster"
SQUADSCOPE_REPO="jmservera/SquadScope"
PODCASTER_REPO="jmservera/SquadScope-Podcaster"
OUT_FILE=""
FORCE_CI=0

err() { printf 'ERROR: %s\n' "$*" >&2; }
die() { err "$*"; exit 1; }

usage() {
  sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    -g|--resource-group) RESOURCE_GROUP="${2:?--resource-group requires a value}"; shift 2;;
    --squadscope-repo) SQUADSCOPE_REPO="${2:?--squadscope-repo requires a value}"; shift 2;;
    --podcaster-repo) PODCASTER_REPO="${2:?--podcaster-repo requires a value}"; shift 2;;
    --out) OUT_FILE="${2:?--out requires a value}"; shift 2;;
    --force-ci) FORCE_CI=1; shift;;
    -h|--help) usage; exit 0;;
    *) die "Unknown argument: $1 (use --help)";;
  esac
done

# Refuse to leak secret values into CI logs / GitHub outputs.
if [ "${CI:-}" = "true" ] || [ -n "${GITHUB_ACTIONS:-}" ]; then
  if [ "$FORCE_CI" -ne 1 ]; then
    die "Refusing to run in CI: this tool prints secret values to the terminal. Run it locally, or pass --force-ci if you really know what you are doing."
  fi
fi

# Preconditions.
command -v az >/dev/null 2>&1 || die "'az' (Azure CLI) is not installed or not on PATH."
if ! az account show >/dev/null 2>&1; then
  die "Not logged in to Azure. Run 'az login' (and 'az account set --subscription <id>') first."
fi

# az helper: query a single value, tolerate empty results.
az_query() {
  # usage: az_query <query> <az args...>
  local query="$1"; shift
  az "$@" --query "$query" --output tsv 2>/dev/null || true
}

printf 'Discovering Podcaster resources in resource group: %s\n' "$RESOURCE_GROUP" >&2

# --- Function App -----------------------------------------------------------
FUNCTION_APP_NAME="$(az_query "[0].name" functionapp list --resource-group "$RESOURCE_GROUP")"
[ -n "$FUNCTION_APP_NAME" ] || die "No Function App (Microsoft.Web/sites, kind functionapp) found in '$RESOURCE_GROUP'."

FUNCTION_HOSTNAME="$(az_query "defaultHostName" functionapp show --resource-group "$RESOURCE_GROUP" --name "$FUNCTION_APP_NAME")"
[ -n "$FUNCTION_HOSTNAME" ] || die "Could not resolve defaultHostName for Function App '$FUNCTION_APP_NAME'."
GENERATE_URL="https://${FUNCTION_HOSTNAME}/api/generate"

# The Function App authenticates callers by comparing the x-podcaster-api-key
# header against its PODCASTER_API_KEY app setting (see podcaster/validation.py).
# That same value is what SquadScope must send, so read it back from app settings.
PODCASTER_API_KEY="$(az_query "[?name=='PODCASTER_API_KEY'].value | [0]" \
  functionapp config appsettings list --resource-group "$RESOURCE_GROUP" --name "$FUNCTION_APP_NAME")"
if [ -z "$PODCASTER_API_KEY" ]; then
  err "PODCASTER_API_KEY app setting not found on '$FUNCTION_APP_NAME'; falling back to the host function key."
  PODCASTER_API_KEY="$(az_query "functionKeys.default" \
    functionapp keys list --resource-group "$RESOURCE_GROUP" --name "$FUNCTION_APP_NAME")"
fi
[ -n "$PODCASTER_API_KEY" ] || die "Could not resolve a Podcaster API key for '$FUNCTION_APP_NAME'."

# --- Storage account --------------------------------------------------------
STORAGE_ACCOUNT_NAME="$(az_query "[0].name" storage account list --resource-group "$RESOURCE_GROUP")"
STORAGE_BLOB_ENDPOINT=""
if [ -n "$STORAGE_ACCOUNT_NAME" ]; then
  STORAGE_BLOB_ENDPOINT="$(az_query "primaryEndpoints.blob" \
    storage account show --resource-group "$RESOURCE_GROUP" --name "$STORAGE_ACCOUNT_NAME")"
else
  err "No storage account found in '$RESOURCE_GROUP' (continuing)."
fi

# --- Azure OpenAI / Cognitive Services (for #60 generation wiring) -----------
OPENAI_ACCOUNT_NAME="$(az_query "[?kind=='OpenAI'].name | [0]" \
  cognitiveservices account list --resource-group "$RESOURCE_GROUP")"
if [ -z "$OPENAI_ACCOUNT_NAME" ]; then
  # Fall back to the first cognitive services account of any kind.
  OPENAI_ACCOUNT_NAME="$(az_query "[0].name" cognitiveservices account list --resource-group "$RESOURCE_GROUP")"
fi
OPENAI_ENDPOINT=""
OPENAI_API_KEY=""
if [ -n "$OPENAI_ACCOUNT_NAME" ]; then
  OPENAI_ENDPOINT="$(az_query "properties.endpoint" \
    cognitiveservices account show --resource-group "$RESOURCE_GROUP" --name "$OPENAI_ACCOUNT_NAME")"
  OPENAI_API_KEY="$(az_query "key1" \
    cognitiveservices account keys list --resource-group "$RESOURCE_GROUP" --name "$OPENAI_ACCOUNT_NAME")"
else
  err "No Azure OpenAI / Cognitive Services account found in '$RESOURCE_GROUP' (skipping #60 secrets)."
fi

# --- Emit gh secret set commands -------------------------------------------
emit() {
  cat <<EMIT
# ---------------------------------------------------------------------------
# Discovered in resource group: ${RESOURCE_GROUP}
#   Function App:    ${FUNCTION_APP_NAME}
#   Generate URL:    ${GENERATE_URL}
#   Storage account: ${STORAGE_ACCOUNT_NAME:-<none>}${STORAGE_BLOB_ENDPOINT:+ (${STORAGE_BLOB_ENDPOINT})}
#   Azure OpenAI:    ${OPENAI_ACCOUNT_NAME:-<none>}
#
# Review each command, then run it from a trusted local shell with the GitHub
# CLI authenticated (gh auth status). Values below are REAL secrets.
# ---------------------------------------------------------------------------

# SquadScope caller secrets (repo: ${SQUADSCOPE_REPO}) — matches docs/integration-contract.md
# Endpoint URL is non-sensitive and stored as a repo variable; the API key is a secret.
gh variable set PODCASTER_ENDPOINT --repo ${SQUADSCOPE_REPO} --body '${GENERATE_URL}'
gh secret set PODCASTER_API_KEY --repo ${SQUADSCOPE_REPO} --body '${PODCASTER_API_KEY}'
EMIT

  if [ -n "$OPENAI_ENDPOINT" ] && [ -n "$OPENAI_API_KEY" ]; then
    cat <<EMIT

# Podcaster service secrets (repo: ${PODCASTER_REPO}) — Azure OpenAI for /api/generate (#60)
gh variable set AZURE_OPENAI_ENDPOINT --repo ${PODCASTER_REPO} --body '${OPENAI_ENDPOINT}'
gh secret set AZURE_OPENAI_API_KEY --repo ${PODCASTER_REPO} --body '${OPENAI_API_KEY}'
EMIT
  fi
}

if [ -n "$OUT_FILE" ]; then
  ( umask 077; emit > "$OUT_FILE" )
  printf 'Wrote gh secret set commands to: %s\n' "$OUT_FILE" >&2
  printf 'This file contains REAL secret values — keep it out of git and delete it when done.\n' >&2
else
  emit
fi
