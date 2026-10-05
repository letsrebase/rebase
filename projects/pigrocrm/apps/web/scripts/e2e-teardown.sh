#!/usr/bin/env bash
# Tears down everything e2e-setup.sh (and Playwright's own webServer, launched
# by the `pnpm exec playwright test` this runs alongside) brought up: the API
# process (by pidfile -- whichever process is currently recorded there, since
# e2e/resilience.spec.ts deliberately kills and relaunches it mid-suite and
# rewrites the pidfile when it does), the disposable Postgres container, and
# the frontend preview server. Safe to run even if setup only got partway, or was
# already torn down -- every step tolerates "already gone".
#
# Fix round 1: the frontend server step is not redundant with Playwright's
# own end-of-run cleanup, even though that cleanup usually runs fine on its
# own. Confirmed live: sending a real SIGINT to this whole process group (the
# way a terminal's Ctrl-C actually behaves) correctly tore down the API and
# the Postgres container below -- e2e.sh's own `trap ... EXIT` does fire, and
# does run this script -- but left the Vite server still
# listening and still answering HTTP 200 afterwards. Playwright launches that
# process fully detached (`ppid=1` from the instant it starts) in its own
# process group specifically so *it* can manage that process's lifecycle on
# its own terms; the cost is that a broadcast signal to the foreground process
# group this script's own trap fires from never reaches it at all, and
# Playwright only ever kills it as part of *its own* graceful shutdown path --
# which a process that is itself dying to the same signal cannot be trusted to
# reach. Killing whatever is bound to the port directly, independent of who
# started it or why, is what makes this guarantee hold regardless.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=./e2e-env.sh
source "$REPO_ROOT/apps/web/scripts/e2e-env.sh"

echo "== pigrocrm e2e: stopping the API =="
if [ -f "$PIGROCRM_E2E_API_PIDFILE" ]; then
  pid="$(cat "$PIGROCRM_E2E_API_PIDFILE")"
  if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
    for _ in $(seq 1 20); do
      kill -0 "$pid" >/dev/null 2>&1 || break
      sleep 0.5
    done
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi
  rm -f "$PIGROCRM_E2E_API_PIDFILE"
fi
rm -f "$PIGROCRM_E2E_API_LOG"

echo "== pigrocrm e2e: removing the Postgres container =="
docker rm -f "$PIGROCRM_E2E_CONTAINER" >/dev/null 2>&1 || true

echo "== pigrocrm e2e: stopping the frontend preview server =="
kill_port "$PIGROCRM_E2E_WEB_PORT"

echo "== pigrocrm e2e: torn down =="
