#!/usr/bin/env bash
# Render the Claracle intro + outro HyperFrames compositions to MP4.
#
# Usage:
#   ./render.sh            # render both at high quality
#   ./render.sh draft      # faster, lower-quality preview render
#
# Requires chrome-headless-shell for deterministic (virtual-clock) capture of
# the WebGL Max Headroom background. If it is missing this script installs it.
set -euo pipefail

cd "$(dirname "$0")"

QUALITY="${1:-high}"
FPS=30

# Locate (or install) chrome-headless-shell for the deterministic beginFrame path.
HS_BIN="$(ls chrome-headless-shell/*/chrome-headless-shell-linux64/chrome-headless-shell 2>/dev/null | head -n1 || true)"
if [ -z "${HS_BIN}" ]; then
  echo "chrome-headless-shell not found — installing..."
  npx --yes @puppeteer/browsers install chrome-headless-shell@stable --path "$PWD/chrome-headless-shell"
  HS_BIN="$(ls chrome-headless-shell/*/chrome-headless-shell-linux64/chrome-headless-shell 2>/dev/null | head -n1)"
fi
export HYPERFRAMES_BROWSER_PATH="$PWD/${HS_BIN}"
echo "Using browser: ${HYPERFRAMES_BROWSER_PATH}"

mkdir -p output

echo "Rendering intro (18s)..."
npx hyperframes render -c compositions/intro.html -o output/intro.mp4 --quality "${QUALITY}" --fps "${FPS}" --quiet

echo "Rendering outro (20s)..."
npx hyperframes render -c compositions/outro.html -o output/outro.mp4 --quality "${QUALITY}" --fps "${FPS}" --quiet

echo "Rendering intermission (10s)..."
npx hyperframes render -c compositions/intermission.html -o output/intermission.mp4 --quality "${QUALITY}" --fps "${FPS}" --quiet

echo "Done. Outputs:"
ls -lh output/intro.mp4 output/outro.mp4 output/intermission.mp4
