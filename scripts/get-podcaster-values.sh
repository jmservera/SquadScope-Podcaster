#!/usr/bin/env bash
#
# get-podcaster-values.sh
#
# Discover the deployed Podcaster Azure resources at the resource-group level and
# emit ready-to-run `gh secret set` commands with the real resolved values.
#
# Architecture: ACA-only (Container Apps Job + Storage + Azure OpenAI).
# There is no Function App or public HTTP endpoint; SquadScope triggers synthesis
# via the Storage Queue (synthesis-jobs) and the PODCASTER_API_KEY is used for
# queue-message auth validation inside the ACA job.
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

# --- Storage account --------------------------------------------------------
STORAGE_ACCOUNT_NAME="$(az_query "[0].name" storage account list --resource-group "$RESOURCE_GROUP")"
STORAGE_BLOB_ENDPOINT=""
STORAGE_QUEUE_ENDPOINT=""
if [ -n "$STORAGE_ACCOUNT_NAME" ]; then
  STORAGE_BLOB_ENDPOINT="$(az_query "primaryEndpoints.blob" \
    storage account show --resource-group "$RESOURCE_GROUP" --name "$STORAGE_ACCOUNT_NAME")"
  STORAGE_QUEUE_ENDPOINT="$(az_query "primaryEndpoints.queue" \
    storage account show --resource-group "$RESOURCE_GROUP" --name "$STORAGE_ACCOUNT_NAME")"
else
  err "No storage account found in '$RESOURCE_GROUP'."
fi

# --- Azure OpenAI / Cognitive Services --------------------------------------
OPENAI_ACCOUNT_NAME="$(az_query "[?kind=='OpenAI'].name | [0]" \
  cognitiveservices account list --resource-group "$RESOURCE_GROUP")"
if [ -z "$OPENAI_ACCOUNT_NAME" ]; then
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
  err "No Azure OpenAI / Cognitive Services account found in '$RESOURCE_GROUP' (skipping OpenAI secrets)."
fi

# --- Container Apps Job (synthesis) -----------------------------------------
ACA_JOB_NAME="$(az_query "[0].name" containerapp job list --resource-group "$RESOURCE_GROUP" 2>/dev/null)"
if [ -z "$ACA_JOB_NAME" ]; then
  err "No Container Apps Job found in '$RESOURCE_GROUP' (continuing)."
fi

# --- Podcaster API key (read from ACA job env if available) -----------------
PODCASTER_API_KEY=""
if [ -n "$ACA_JOB_NAME" ]; then
  PODCASTER_API_KEY="$(az containerapp job show \
    --resource-group "$RESOURCE_GROUP" --name "$ACA_JOB_NAME" \
    --query "properties.template.containers[0].env[?name=='PODCASTER_API_KEY'].value | [0]" \
    --output tsv 2>/dev/null || true)"
fi
if [ -z "$PODCASTER_API_KEY" ]; then
  err "Could not discover PODCASTER_API_KEY from ACA job env. It is set during deployment from the GitHub secret."
  err "If you need to rotate it, update the PODCASTER_API_KEY secret in the 'prod' environment and re-deploy."
fi

# --- Emit gh secret set commands -------------------------------------------
emit() {
  cat <<EMIT
# ---------------------------------------------------------------------------
# Discovered in resource group: ${RESOURCE_GROUP}
#   Storage account: ${STORAGE_ACCOUNT_NAME:-<none>}${STORAGE_BLOB_ENDPOINT:+ (${STORAGE_BLOB_ENDPOINT})}
#   Queue endpoint:  ${STORAGE_QUEUE_ENDPOINT:-<none>}
#   Azure OpenAI:    ${OPENAI_ACCOUNT_NAME:-<none>}${OPENAI_ENDPOINT:+ (${OPENAI_ENDPOINT})}
#   ACA Job:         ${ACA_JOB_NAME:-<none>}
#
# Review each command, then run it from a trusted local shell with the GitHub
# CLI authenticated (gh auth status). Values below are REAL secrets.
# ---------------------------------------------------------------------------
EMIT

  if [ -n "$PODCASTER_API_KEY" ]; then
    cat <<EMIT

# SquadScope caller secrets (repo: ${SQUADSCOPE_REPO}) — matches docs/integration-contract.md
gh secret set PODCASTER_API_KEY --repo ${SQUADSCOPE_REPO} --body '${PODCASTER_API_KEY}'
EMIT
  fi

  if [ -n "$STORAGE_QUEUE_ENDPOINT" ]; then
    cat <<EMIT
gh variable set PODCASTER_QUEUE_ENDPOINT --repo ${SQUADSCOPE_REPO} --body '${STORAGE_QUEUE_ENDPOINT}'
EMIT
  fi

  if [ -n "$OPENAI_ENDPOINT" ] && [ -n "$OPENAI_API_KEY" ]; then
    cat <<EMIT

# Podcaster service secrets (repo: ${PODCASTER_REPO}) — Azure OpenAI for synthesis
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
