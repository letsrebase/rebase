#!/usr/bin/env bash
# Verifies the routing the web container's nginx decides (`deploy/nginx/spa.conf`) against the
# real nginx, not the Vite dev server. `pnpm dev` does not exercise that file at all, so `/`,
# `/privacy`, `/termini`, `/app` and the `/login` redirect can only be proven here.
#
# The stack is its own compose project (`pigrocrm-e2e`) with throwaway values, never the
# project the deploy names `pigrocrm` and never a `.env` of this checkout: `down -v` here can
# only ever remove what this script started. The data directory is a temporary one.
#
#   apps/web/scripts/e2e-compose.sh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$PROJECT_ROOT"

work="$(mktemp -d)"
export POSTGRES_PASSWORD=e2e-compose-never-used
export PIGROCRM_APP_PASSWORD=e2e-compose-never-used
export PIGROCRM_JWT_SECRET=e2e-compose-never-used-and-long-enough-for-the-validator
export PIGROCRM_DATA_DIR="$work/postgres"
export PIGROCRM_DOCUMENTS_DIR="$work/documents"
export PIGROCRM_DB_PORT=127.0.0.1:55436
export PIGROCRM_WEB_PORT=127.0.0.1:8080
mkdir -p "$PIGROCRM_DATA_DIR" "$PIGROCRM_DOCUMENTS_DIR"
compose() { docker compose -p pigrocrm-e2e "$@"; }

cleanup() {
  compose down -v --remove-orphans
  # Postgres wrote its files as its own user: remove them from inside a container.
  docker run --rm -v "$work:/w" --entrypoint rm postgres:17-alpine -rf /w/postgres /w/documents >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

compose up -d --build

for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS http://127.0.0.1:8080/health >/dev/null

cd "$PROJECT_ROOT/apps/web"
pnpm exec playwright test --config playwright.compose.config.ts
