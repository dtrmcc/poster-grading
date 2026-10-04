#!/usr/bin/env bash
# Pull the latest code from GitHub and rebuild/restart the app. Data is untouched.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose -f docker-compose.prod.yml up -d --build
docker image prune -f >/dev/null
docker builder prune -f --filter until=168h >/dev/null || true
docker compose -f docker-compose.prod.yml ps
