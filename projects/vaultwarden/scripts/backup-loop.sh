#!/usr/bin/env bash
# The `backup` service's process: one backup shortly after the container starts, then
# one a day at VAULTWARDEN_BACKUP_AT (UTC, HH:MM), forever. A backup that fails is
# logged and tried again at the next slot; it never ends the loop, because the deploy
# fails on a container that is not `running`, and the healthcheck is what says the last
# success is too old. Read it with `docker compose -p vaultwarden logs backup`.
set -uo pipefail

at=${VAULTWARDEN_BACKUP_AT:-03:30}
here=$(dirname "$0")

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
  if ! bash "$here/backup.sh"; then
    echo "backup: FAILED at $(date -u +%FT%TZ), next try at ${at} UTC" >&2
  fi
  now=$(date -u +%s)
  next=$(date -u -d "today $at" +%s)
  [ "$next" -gt "$now" ] || next=$(date -u -d "tomorrow $at" +%s)
  wait=$((next - now))
  [ "$wait" -ge 60 ] || wait=60
  sleep "$wait"
done
