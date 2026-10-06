#!/usr/bin/env bash
# Open one encrypted backup (REB-648): decrypt it with the private key and unpack it
# into an empty directory, which is then a Vaultwarden data directory (docs/restore.md
# is the whole procedure, including starting a server on it).
#
#   restore.sh <backup.tar.gz.cms> <private-key.pem> <target-dir>
#
# Needs only tar and openssl, on any machine. It refuses a target that is not empty, so
# it can never be pointed at a live data directory by a slip of the hand.
set -euo pipefail

[ "$#" -eq 3 ] || { echo "usage: $0 <backup.tar.gz.cms> <private-key.pem> <target-dir>" >&2; exit 2; }
backup=$1 key=$2 target=$3

[ -f "$backup" ] || { echo "restore: no such backup: $backup" >&2; exit 1; }
[ -f "$key" ] || { echo "restore: no such key: $key" >&2; exit 1; }
if [ -d "$target" ] && [ -n "$(ls -A "$target")" ]; then
  echo "restore: $target is not empty; restore into a new directory" >&2
  exit 1
fi
# umask 077, so a target this creates is 0700 and so is everything under it; a target
# that already exists keeps the mode its owner gave it (see --no-overwrite-dir below).
umask 077
mkdir -p "$target"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
openssl cms -decrypt -binary -inform DER -in "$backup" -inkey "$key" -out "$work/bundle.tar.gz"
# `--no-overwrite-dir`: the bundle's `./` entry must not reset the mode of a target
# directory that was made 0700 on purpose.
tar --no-overwrite-dir -C "$target" -xzf "$work/bundle.tar.gz"
[ -s "$target/db.sqlite3" ] || { echo "restore: the bundle holds no database" >&2; exit 1; }
cat "$target/MANIFEST"
echo "restore: unpacked into $target"
