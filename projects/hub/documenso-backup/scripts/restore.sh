#!/usr/bin/env bash
# Open one encrypted Documenso backup (REB-715): decrypt it with a private key and unpack
# it into an empty directory, which then holds `documenso.dump` and its `MANIFEST`
# (docs/restore-documenso.md is the whole procedure, including loading the dump into a
# Postgres).
#
#   restore.sh <backup.tar.cms> <private-key.pem> <target-dir> [<certificate.pem>]
#
# Needs only tar, openssl and sha256sum, on any machine. It refuses a target that is not
# empty, so it can never be pointed at something live by a slip of the hand, and it checks
# the archive against the SHA-256 the backup recorded. The target holds every contract in
# clear: a directory on a disk that is encrypted or on tmpfs, deleted when the restore is
# done. The same envelope can be read straight into `pg_restore` without ever touching a
# disk, which the runbook shows.
#
# The certificate is the public half of the key, and it is only needed when the backup was
# made for several certificates (DOCUMENSO_BACKUP_CERT_B64 holding more than one): given
# only a key, openssl uses it on one recipient of the envelope, whichever its DER order
# puts there, takes what decrypts there for the content key and fails when the key was not
# that recipient's (measured with OpenSSL 3.5.7: one of two keys opens a two-recipient
# envelope alone, the other needs `-recip`). So a key says which recipient it is.
set -euo pipefail

if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
  echo "usage: $0 <backup.tar.cms> <private-key.pem> <target-dir> [<certificate.pem>]" >&2
  exit 2
fi
backup=$1 key=$2 target=$3 cert=${4:-}

[ -f "$backup" ] || { echo "restore: no such backup: $backup" >&2; exit 1; }
[ -f "$key" ] || { echo "restore: no such key: $key" >&2; exit 1; }
[ -z "$cert" ] || [ -f "$cert" ] || { echo "restore: no such certificate: $cert" >&2; exit 1; }
if [ -d "$target" ] && [ -n "$(ls -A "$target")" ]; then
  echo "restore: $target is not empty; restore into a new directory" >&2
  exit 1
fi
# umask 077, so a target this creates is 0700 and so is everything under it. The bundle
# has no `./` entry, so unpacking never changes the mode of a target that already exists.
umask 077
mkdir -p "$target"

decrypt=(openssl cms -decrypt -binary -inform DER -in "$backup" -inkey "$key")
[ -z "$cert" ] || decrypt+=(-recip "$cert")
set +e
"${decrypt[@]}" | tar -C "$target" -xf -
status=("${PIPESTATUS[@]}")
set -e
if [ "${status[0]}" -ne 0 ] || [ "${status[1]}" -ne 0 ]; then
  # The target was empty when this started, so whatever is in it now is this run's.
  find "$target" -mindepth 1 -delete
  if [ "${status[0]}" -ne 0 ]; then
    echo "restore: could not open $backup with that key; if it was made for several certificates, pass the one that belongs to the key as a fourth argument" >&2
  else
    echo "restore: the decrypted bundle of $backup is not a tar archive" >&2
  fi
  exit 1
fi
[ -s "$target/documenso.dump" ] || { echo "restore: the bundle holds no archive" >&2; exit 1; }
recorded=$(sed -n 's/^dump_sha256=//p' "$target/MANIFEST")
actual=$(sha256sum "$target/documenso.dump" | cut -d' ' -f1)
if [ -z "$recorded" ] || [ "$recorded" != "$actual" ]; then
  echo "restore: the archive does not match the SHA-256 in its MANIFEST" >&2
  exit 1
fi
cat "$target/MANIFEST"
echo "restore: unpacked into $target"
