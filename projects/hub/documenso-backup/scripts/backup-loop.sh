#!/usr/bin/env bash
# The `documenso-backup` service's process: one backup shortly after the container
# starts, then one a day at DOCUMENSO_BACKUP_AT (UTC, HH:MM), forever. A backup that fails
# is logged and tried again at the next slot; it never ends the loop, because the deploy
# fails on a container that is not `running`, and the healthcheck is what says the last
# success is too old. Read it with `docker compose -p rebase logs documenso-backup`.
#
# Every run also tells the monitor, as Vaultwarden's does (REB-671):
# DOCUMENSO_BACKUP_PING_URL is a healthchecks.io check, pinged after a backup that
# finished and at `<url>/fail` after one that did not. The check mails a person when no
# success arrives within its period plus grace, so a backup that stops quietly (a revoked
# R2 key, a full disk, a container that is gone) is noticed without anyone reading this
# log. A ping that cannot get through is logged and changes nothing else: the monitor
# going quiet is its own alarm.
#
# The image is Alpine's, so `date` is busybox's and cannot read "tomorrow 03:45": the
# next slot is worked out from the seconds since midnight instead.
set -uo pipefail

at=${DOCUMENSO_BACKUP_AT:-03:45}
ping_url=${DOCUMENSO_BACKUP_PING_URL:-}
here=$(dirname "$0")
# The URL is a secret of sorts (anyone holding it can report a success), so it is never
# printed and never on a command line, where `ps` would show it: it goes to curl on stdin,
# as the R2 key does in backup.sh. It is the check's ping URL as healthchecks.io shows it:
# no trailing slash and no query, since `/fail` is appended to it, and no quote or
# backslash, which curl's config syntax would read as an escape. Anything else ends the
# container like a bad time does, rather than sending the failure ping to a path nobody
# watches.
url_pattern='^https?://[^[:space:]"\\?#]*[^[:space:]"\\/?#]$'
if ! [[ $ping_url =~ $url_pattern ]]; then
  echo "backup: DOCUMENSO_BACKUP_PING_URL must be the monitor's http(s) ping URL, with no trailing slash, query, quote or backslash" >&2
  exit 1
fi

notify() {
  local what=${1:-} outcome=finished
  [ -z "$what" ] || outcome=failed
  if ! printf 'url = "%s%s"\n' "$ping_url" "$what" \
    | curl --config - --fail --silent --show-error --max-time 10 --retry 3 --output /dev/null; then
    echo "backup: could not tell the monitor that the backup ${outcome}" >&2
  fi
}

# A time that is not one would make the sleep below fail at once and the loop run a full
# backup, an upload included, as fast as it can, in a container that still reads
# `running`. So a bad value ends the container instead, which the deploy reports.
if ! [[ $at =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
  echo "backup: DOCUMENSO_BACKUP_AT must be HH:MM, 24 hours, UTC; got '$at'" >&2
  exit 1
fi
slot=$((10#${at%:*} * 3600 + 10#${at#*:} * 60))

# The database is healthy before this container starts (`depends_on`), but a minute is
# cheap and the first backup is the one that proves the configuration.
sleep "${DOCUMENSO_BACKUP_FIRST_DELAY:-60}"

while :; do
  if bash "$here/backup.sh"; then
    notify ''
  else
    echo "backup: FAILED at $(date -u +%FT%TZ), next try at ${at} UTC" >&2
    notify /fail
  fi
  # Seconds to the next slot. A slot less than a minute away is the one this run just
  # served, so it counts as passed: the loop never runs twice in a minute.
  wait=$((slot - $(date -u +%s) % 86400))
  [ "$wait" -gt 60 ] || wait=$((wait + 86400))
  sleep "$wait"
done
