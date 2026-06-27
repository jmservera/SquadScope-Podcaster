#!/usr/bin/env bash
# Pre-push hook: build the container images to catch broken builds locally
# before they reach CI.
#
# DevSecOps Guardrails epic (jmservera/SquadScope-Coordinator#33), issue #522.
# Mirrors the image-build steps in .github/workflows/reusable-ci.yml:
#   docker build -f Containerfile      -t podcaster-synthesis:ci .
#   docker build -f Containerfile.api  -t podcaster-api:ci       .
#   docker build -f ui/Dockerfile      -t podcaster-ui:ci        ui/
set -euo pipefail

# dockerfile|tag|context  (mirrors reusable-ci.yml)
BUILDS=(
  "Containerfile|podcaster-synthesis:precommit|."
  "Containerfile.api|podcaster-api:precommit|."
  "ui/Dockerfile|podcaster-ui:precommit|ui"
)

present=()
for spec in "${BUILDS[@]}"; do
  dockerfile="${spec%%|*}"
  [ -f "$dockerfile" ] && present+=("$spec")
done

if [ "${#present[@]}" -eq 0 ]; then
  echo "docker-build: no Containerfile/Dockerfile found — skipping."
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker-build: container files present but docker is not installed." >&2
  echo "docker-build: install Docker or push with --no-verify (then fix locally)." >&2
  exit 1
fi

for spec in "${present[@]}"; do
  dockerfile="${spec%%|*}"
  rest="${spec#*|}"
  tag="${rest%%|*}"
  context="${rest##*|}"
  echo "docker-build: building $dockerfile -> $tag (context: $context) ..."
  docker build -f "$dockerfile" -t "$tag" "$context"
done

echo "docker-build: all images built successfully."
