#!/usr/bin/env bash
# The `backup` service's process: one backup shortly after the container starts, then
# one a day at VAULTWARDEN_BACKUP_AT (UTC, HH:MM), forever. A backup that fails is
# logged and tried again at the next slot; it never ends the loop, because the deploy
# fails on a container that is not `running`, and the healthcheck is what says the last
# success is too old. Read it with `docker compose -p vaultwarden logs backup`.
#
# Every run also tells the monitor (REB-671): VAULTWARDEN_BACKUP_PING_URL is a
# healthchecks.io check, pinged after a backup that finished and at `<url>/fail` after
# one that did not. The check mails a person when no success arrives within its period
# plus grace, so a backup that stops quietly (a revoked R2 key, a full disk, a container
# that is gone) is noticed without anyone reading this log. A ping that cannot get
# through is logged and changes nothing else: the monitor going quiet is its own alarm.
set -uo pipefail

at=${VAULTWARDEN_BACKUP_AT:-03:30}
ping_url=${VAULTWARDEN_BACKUP_PING_URL:-}
here=$(dirname "$0")

# The URL is a secret of sorts (anyone holding it can report a success), so it is never
# printed, and a value that is not an http(s) URL ends the container like a bad time does.
if ! [[ $ping_url =~ ^https?://[^[:space:]]+$ ]]; then
  echo "backup: VAULTWARDEN_BACKUP_PING_URL must be the monitor's http(s) URL" >&2
  exit 1
fi

notify() {
  if ! curl --fail --silent --show-error --max-time 10 --retry 3 --output /dev/null "${ping_url}$1"; then
    echo "backup: could not tell the monitor (${1:-success})" >&2
  fi
}

# A time `date` cannot read would make the sleep below fail at once and the loop run a
# full backup, an upload included, as fast as it can, in a container that still reads
# `running`. So a bad value ends the container instead, which the deploy reports.
if ! [[ $at =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
  echo "backup: VAULTWARDEN_BACKUP_AT must be HH:MM, 24 hours, UTC; got '$at'" >&2
  exit 1
fi

# The server creates its database on first start; give it a moment before the first try.
sleep "${VAULTWARDEN_BACKUP_FIRST_DELAY:-60}"

while :; do
  if bash "$here/backup.sh"; then
    notify ''
  else
    echo "backup: FAILED at $(date -u +%FT%TZ), next try at ${at} UTC" >&2
    notify /fail
  fi
  now=$(date -u +%s)
  next=$(date -u -d "today $at" +%s)
  [ "$next" -gt "$now" ] || next=$(date -u -d "tomorrow $at" +%s)
  wait=$((next - now))
  [ "$wait" -ge 60 ] || wait=60
  sleep "$wait"
done
