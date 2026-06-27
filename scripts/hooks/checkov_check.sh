#!/usr/bin/env bash
# Pre-push hook: Checkov IaC (Bicep) + container (Dockerfile) scan.
#
# DevSecOps Guardrails epic (jmservera/SquadScope-Coordinator#33), issue #522.
# Mirrors the Checkov steps in .github/workflows/reusable-ci.yml so the same
# findings gate locally as in CI. Keep the skip-check list and baseline usage
# below IN SYNC with reusable-ci.yml — local and CI must agree.
set -euo pipefail

if ! command -v checkov >/dev/null 2>&1; then
  echo "checkov: not installed — install with 'pip install checkov==3.2.533'." >&2
  echo "checkov: or push with --no-verify (then fix locally / let CI catch it)." >&2
  exit 1
fi

# Infra (Bicep): gate on NEW findings while deferring the accepted ACA-only
# baseline. This skip list MUST match reusable-ci.yml's "Run Checkov" step.
INFRA_SKIP="CKV_AZURE_35,CKV_AZURE_43,CKV_AZURE_206,CKV_AZURE_225,CKV_AZURE_15,CKV_AZURE_212,CKV_AZURE_78,CKV_AZURE_213,CKV_AZURE_222,CKV_AZURE_67,CKV_AZURE_18,CKV_AZURE_17,CKV_AZURE_59,CKV_AZURE_134,CKV_AZURE_233,CKV_AZURE_139,CKV_AZURE_163,CKV_AZURE_166"

echo "checkov: scanning infra/ (Bicep) ..."
checkov --directory infra --framework bicep \
  --skip-check "$INFRA_SKIP" \
  --compact --quiet

# Dockerfiles: warning-only via baseline (only NEW issues surface). Matches the
# "Run Checkov (Dockerfiles, warning-only)" step in reusable-ci.yml.
echo "checkov: scanning Dockerfiles (baseline) ..."
checkov --directory . --framework dockerfile \
  --skip-path .worktrees --skip-path ui/node_modules \
  --baseline .checkov.baseline \
  --soft-fail --compact --quiet

echo "checkov: passed."
