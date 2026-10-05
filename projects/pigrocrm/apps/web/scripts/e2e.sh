#!/usr/bin/env bash
# The one documented command: brings up a disposable backend, runs the whole
# Playwright suite against it, and tears the backend back down again -- on a
# passing run, a failing run, or a Ctrl-C. Run from anywhere:
#
#   apps/web/scripts/e2e.sh
#
# or, from apps/web/: `pnpm test:e2e`. Anything passed here is passed on to
# `playwright test`, so one file at a time works the same way:
#
#   apps/web/scripts/e2e.sh e2e/time-tracking.spec.ts
#
# Nothing else needs to be running first: no manually-started API, no manually
# started frontend. The SPA is built here (`vite build`) and Playwright's own
# `webServer` block (apps/web/playwright.config.ts) serves that build with `vite
# preview` before the run and stops it after a normal pass or failure. A production
# build rather than the dev server because the dev server's hundreds of module requests
# per page get aborted by Chromium on a loaded box (REB-659) --
# but not reliably after a signal (fix round 1: confirmed live that Playwright
# launches it fully detached, in its own process group, so a real Ctrl-C to this
# whole process group never reaches it at all). e2e-teardown.sh's own trap below
# is what makes "tears down regardless of outcome" actually true rather than true
# only on the happy path: it kills whatever is bound to the frontend's port
# directly, on top of -- not instead of -- the API and database this config
# can't own at all.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

# Sourced here too, not only inside e2e-setup.sh's own process: e2e/resilience.spec.ts
# deliberately kills the API mid-suite and relaunches it itself, which means the
# Playwright *test* process -- a child of the `pnpm exec playwright test` this script
# launches below -- needs PIGROCRM_DATABASE_URL/PIGROCRM_JWT_SECRET in its own
# environment to do that. Exporting them in this shell, before that process ever
# starts, is what makes them inherited rather than merely local to e2e-setup.sh's own
# short-lived subprocess.
# shellcheck source=./e2e-env.sh
source "$REPO_ROOT/apps/web/scripts/e2e-env.sh"

cleanup() {
  "$REPO_ROOT/apps/web/scripts/e2e-teardown.sh"
}
trap cleanup EXIT

# Before the database comes up, so a build that fails costs nothing to tear down.
echo "== pigrocrm e2e: building the SPA =="
(cd "$REPO_ROOT/apps/web" && pnpm exec vite build)

"$REPO_ROOT/apps/web/scripts/e2e-setup.sh"

set +e
(cd "$REPO_ROOT/apps/web" && pnpm exec playwright test "$@")
status=$?
set -e

exit "$status"
