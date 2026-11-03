# Shared constants for the E2E scripts (e2e-setup.sh / e2e-teardown.sh / e2e.sh).
# Sourced, never executed directly -- every value here has to land as an exported
# variable in the *caller's* shell, not in a short-lived subshell of its own.
#
# `e2e.sh` sources this itself (in addition to calling e2e-setup.sh, which sources
# it a second time inside its own process) specifically so the top-level Playwright
# `test` process it launches inherits PIGROCRM_DATABASE_URL/PIGROCRM_JWT_SECRET too --
# `e2e/resilience.spec.ts` needs both in `process.env` to relaunch uvicorn itself
# after deliberately killing it mid-suite. Sourcing the same file from both places
# is what keeps the two copies from ever drifting apart.
#
# Lives under apps/web/ with the e2e suite it serves.

# Two roles, as the compose stack runs (REB-634): the API as `pigrocrm_app`, which the
# row-level policies bind, and the container's superuser as the owner for the migration
# and the seed. `e2e-setup.sh` creates the role through `ensure-space-defaults`, the
# same step the image's CMD runs, so a spec about a scoped member proves the real thing
# and not a superuser that Postgres keeps outside every policy.
export PIGROCRM_DATABASE_URL="postgresql+psycopg://pigrocrm_app:pigrocrm_app@localhost:55434/pigrocrm_e2e"
export PIGROCRM_ADMIN_DATABASE_URL="postgresql+psycopg://pigrocrm:pigrocrm@localhost:55434/pigrocrm_e2e"
# The API judges «data futura» in this zone, and `e2e/time-tracking.spec.ts` takes its
# «today» from the same one (and pins the browser to it). The default is already
# Europe/Rome; pinned here so a `PIGROCRM_TIMEZONE` left in the caller's shell cannot
# move the API's «today» away from the spec's.
export PIGROCRM_TIMEZONE="Europe/Rome"
# At least 32 characters: `Settings` rejects a shorter secret at import time, so the API would
# not even start.
export PIGROCRM_JWT_SECRET="e2e-secret-not-for-production-but-long-enough"
# Set false, not left to the default `true`: Chromium treats "localhost" as a secure context
# and keeps a `Secure` cookie over plain HTTP, but WebKit does not, and the failure there is
# silent (every request after login looks unauthenticated and each spec dies on a bare
# timeout). `.env.example` documents the same Safari/WebKit behaviour and the same `false`.
export PIGROCRM_COOKIE_SECURE=false
# The suite logs in once per spec, forty-one times in under three minutes at the
# preview server's speed, and the product's budget for one address is ten a minute
# (`Settings.login_requests_per_minute`, REB-270): past it `POST /api/auth/login`
# answers 429, the page stays on `/app/login` with a toast nobody reads, and the
# helper's URL assertion failed with the same sentence as a redirect that did not
# happen (REB-662: every login a full run lost at that line was a 429, not one a
# race). The budget is the suite's to declare, like `PIGROCRM_COOKIE_SECURE` above,
# and `helpers.ts::login` fails on the status itself, so a refusal reads as what it is.
export PIGROCRM_LOGIN_REQUESTS_PER_MINUTE=600

# Exported, not merely `readonly`: e2e/resilience.spec.ts (running inside the
# Playwright *test* process, a grandchild of e2e.sh via `pnpm exec playwright
# test`) reads PIGROCRM_E2E_API_PIDFILE/PIGROCRM_E2E_API_PORT from
# `process.env` to kill and relaunch the API itself, and to know where to
# write the new pid back to for e2e-teardown.sh to find afterwards.
export PIGROCRM_E2E_CONTAINER="pigrocrm-e2e"
export PIGROCRM_E2E_PG_PORT="55434"
export PIGROCRM_E2E_API_PORT="8000"
export PIGROCRM_E2E_API_PIDFILE="/tmp/pigrocrm-e2e-api.pid"
export PIGROCRM_E2E_API_LOG="/tmp/pigrocrm-e2e-api.log"
# The frontend server Playwright's own `webServer` block (playwright.config.ts)
# starts: `vite preview` over the production build e2e.sh makes first (REB-659), not
# the dev server, which serves hundreds of module requests per page and loses some of
# them on a loaded box. Matches that config's hardcoded `baseURL`/`url`; there is no
# single env var to read this back from on either side, so it is repeated as a
# literal in both places rather than invented here.
export PIGROCRM_E2E_WEB_PORT="5173"

# Frees a TCP port: SIGTERM whatever listens on it, then SIGKILL if it is still
# there a second later, tolerating "nothing there". Shared by e2e-setup.sh
# (clearing a stale leftover *before* starting, so `reuseExistingServer: !CI` can
# never silently reuse a previous, improperly-torn-down run's server, and a stale
# API cannot answer the readiness probe in place of the new one) and
# e2e-teardown.sh (Playwright launches its web server fully detached, in its own
# process group, so a Ctrl-C to this script's group never reaches it; the port is
# the only handle left on it).
#
# Listeners come from `ss -ltnp`, because `lsof` is not installed on every machine this runs on
# (REB-659) and a helper that silently found nothing left a stray server in place.
# If the port is still taken after the SIGKILL -- or `ss` is missing, or the owner
# is a process this user cannot see or signal -- it says so, naming the port, and
# returns non-zero rather than letting the next step run against someone else's
# server.
# Prints the `ss` rows for the listeners on port $1 (none when it is free). Returns
# non-zero, naming the port, when `ss` is missing or the query itself fails: an empty
# listing is only "free" when the query succeeded.
port_listeners() {
  local rows
  command -v ss >/dev/null 2>&1 || {
    echo "pigrocrm e2e: 'ss' is not installed, cannot tell what holds :$1" >&2
    return 1
  }
  # `-p` adds each row's `users:(("node",pid=123,fd=19),...)` for the processes this
  # user can see.
  rows="$(ss -H -ltnp "sport = :$1" 2>/dev/null)" || {
    echo "pigrocrm e2e: 'ss' failed while checking :$1, cannot tell whether it is free" >&2
    return 1
  }
  printf '%s' "$rows"
}

kill_port() {
  local port="$1" rows pids
  rows="$(port_listeners "$port")" || return 1
  [ -z "$rows" ] && return 0
  pids="$(printf '%s\n' "$rows" | { grep -o 'pid=[0-9]*' || true; } | cut -d= -f2 | sort -u)"
  if [ -n "$pids" ]; then
    echo "pigrocrm e2e: :$port is held by pid(s) $(echo "$pids" | tr '\n' ' '), stopping" >&2
    echo "$pids" | xargs kill -TERM 2>/dev/null || true
    sleep 1
    rows="$(port_listeners "$port")" || return 1
    [ -z "$rows" ] && return 0
    pids="$(printf '%s\n' "$rows" | { grep -o 'pid=[0-9]*' || true; } | cut -d= -f2 | sort -u)"
    if [ -n "$pids" ]; then echo "$pids" | xargs kill -9 2>/dev/null || true; fi
    sleep 1
    rows="$(port_listeners "$port")" || return 1
    [ -z "$rows" ] && return 0
  fi
  echo "pigrocrm e2e: :$port is still in use and could not be cleared (held by a process this user cannot see or signal?). Free it and run again." >&2
  return 1
}
