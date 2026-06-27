#!/usr/bin/env bash
# Pre-push hook: zizmor GitHub Actions security scan.
#
# DevSecOps Guardrails epic (jmservera/SquadScope-Coordinator#33), issue #522.
# Mirrors .github/workflows/zizmor.yml: scans all repository-owned workflows
# while excluding the upstream-generated Squad workflow files. Blocks on HIGH+
# findings (the bar Phase B cleared); the deferred Medium/Informational findings
# stay in the documented baseline (docs/linting.md).
set -euo pipefail

if ! command -v zizmor >/dev/null 2>&1; then
  echo "zizmor: not installed — install with 'pip install zizmor==1.25.2'." >&2
  echo "zizmor: or push with --no-verify (then fix locally / let CI catch it)." >&2
  exit 1
fi

# Same selection as zizmor.yml: repo-owned workflows only (skip generated ones).
mapfile -t WORKFLOWS < <(
  find .github/workflows -maxdepth 1 -type f \
    \( -name "*.yml" -o -name "*.yaml" \) \
    ! -name "squad-*.yml" \
    ! -name "squad-*.yaml" \
    ! -name "sync-squad-labels.yml" \
    ! -name "sync-squad-labels.yaml" \
    | sort
)

if [ "${#WORKFLOWS[@]}" -eq 0 ]; then
  echo "zizmor: no repository-owned workflows found — skipping."
  exit 0
fi

echo "zizmor: scanning ${#WORKFLOWS[@]} workflow file(s) (min-severity high) ..."
zizmor --min-severity high --no-progress "${WORKFLOWS[@]}"
echo "zizmor: passed."
