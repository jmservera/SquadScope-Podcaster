#!/usr/bin/env bash
cd "$(dirname "$0")/.." || exit 1
mkdir -p .ralph
# Start podcaster agent in background so this script does not block the caller.
# Write PID for later management.
nohup python3 scripts/podcaster_agent.py >> .ralph/agent.log 2>&1 &
echo "$!" > .ralph/agent.pid
exit 0
