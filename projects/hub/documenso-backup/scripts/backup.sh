#!/usr/bin/env bash
# One backup of Documenso's Postgres (REB-715), run inside the `documenso-backup`
# service of docker-compose.documenso.yml, in an image made from the database's own
# (../Dockerfile), so it needs nothing that image and `apk add curl openssl` do not carry.
#
# What it saves: the whole `documenso` database, as a `pg_dump` custom-format archive.
# Documenso keeps every uploaded and every sealed PDF in that database
# (NEXT_PUBLIC_UPLOAD_TRANSPORT=database), so the dump is the contracts, their audit
# trail and the schema. `pg_dump` reads one snapshot, so it is consistent on a database
# that Documenso keeps writing to. Not in it, and not anywhere off the host: Documenso's
# encryption keys, the seal certificate and its passphrase (docs/restore-documenso.md,
# "What the dump does not hold").
#
# The bundle is a tar of the archive and a MANIFEST, encrypted as a CMS envelope to the
# public certificate(s) in DOCUMENSO_BACKUP_CERT_B64: one base64 line holding one PEM
# certificate, or several concatenated, in which case each one's private key opens the
# backup on its own. Only certificates are on this host: whoever reads the bucket, or this
# container, cannot open a backup. restore.sh is what uses a private key, on another
# machine.
#
# Where it goes: an S3 endpoint (Cloudflare R2) with DOCUMENSO_BACKUP_S3_ENDPOINT,
# _BUCKET, _ACCESS_KEY_ID and _SECRET_ACCESS_KEY, under the prefix `documenso/`, or, for
# a drill without a bucket, a directory in DOCUMENSO_BACKUP_LOCAL_DIR. Exactly one of the
# two. The database is read through the standard PGHOST, PGUSER, PGDATABASE and PGPASSWORD.
set -euo pipefail

region=${DOCUMENSO_BACKUP_REGION:-auto}
ok_file=${DOCUMENSO_BACKUP_OK_FILE:-/tmp/last-backup-ok}
export PGCONNECT_TIMEOUT=${PGCONNECT_TIMEOUT:-15}

die() { echo "backup: $*" >&2; exit 1; }

[ -n "${DOCUMENSO_BACKUP_CERT_B64:-}" ] || die "DOCUMENSO_BACKUP_CERT_B64 is empty: nothing to encrypt to"
if [ -n "${DOCUMENSO_BACKUP_LOCAL_DIR:-}" ]; then
  [ -z "${DOCUMENSO_BACKUP_S3_ENDPOINT:-}" ] || die "set the S3 endpoint or the local directory, not both"
  destination=local
else
  for v in DOCUMENSO_BACKUP_S3_ENDPOINT DOCUMENSO_BACKUP_BUCKET DOCUMENSO_BACKUP_ACCESS_KEY_ID DOCUMENSO_BACKUP_SECRET_ACCESS_KEY; do
    [ -n "${!v:-}" ] || die "$v is empty"
  done
  destination=s3
fi
for v in PGHOST PGUSER PGDATABASE PGPASSWORD; do
  [ -n "${!v:-}" ] || die "$v is empty: the database is not named"
done

# Nothing here is for anyone but this user: the archive is the contracts in clear until
# the envelope is made, and it is removed the moment it is in the bundle.
umask 077
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

printf '%s' "$DOCUMENSO_BACKUP_CERT_B64" | base64 -d > "$work/recipients.pem" || die "DOCUMENSO_BACKUP_CERT_B64 is not base64"
awk -v dir="$work" '/-----BEGIN CERTIFICATE-----/ { n++ } n { print > (dir "/recipient-" n ".crt") }' "$work/recipients.pem"
shopt -s nullglob
recipients=("$work"/recipient-*.crt)
shopt -u nullglob
[ "${#recipients[@]}" -gt 0 ] || die "DOCUMENSO_BACKUP_CERT_B64 holds no PEM certificate"
for crt in "${recipients[@]}"; do
  openssl x509 -in "$crt" -noout 2>/dev/null || die "$(basename "$crt") of DOCUMENSO_BACKUP_CERT_B64 is not a PEM certificate"
done

stamp=$(date -u +%Y%m%dT%H%M%SZ)
stage="$work/stage"
mkdir "$stage"

pg_dump --format=custom --no-owner --no-acl --file "$stage/documenso.dump"
[ -s "$stage/documenso.dump" ] || die "pg_dump left an empty archive"
# An archive `pg_restore` cannot read is not a backup: say so now, not on the day it is needed.
pg_restore --list "$stage/documenso.dump" > /dev/null || die "pg_restore cannot read the archive pg_dump wrote"
dump_bytes=$(stat -c %s "$stage/documenso.dump")
dump_sha=$(sha256sum "$stage/documenso.dump" | cut -d' ' -f1)

# Where the schema stood: Documenso applies its migrations at every start, and the
# newest one says which version wrote this (a backup taken before an upgrade is the way
# back from it). A database that is not Prisma's says `unknown`, and the backup goes on.
psql_value() { psql --no-psqlrc --tuples-only --no-align --command "$1" 2>/dev/null || true; }
last_migration=$(psql_value 'SELECT migration_name FROM _prisma_migrations ORDER BY finished_at DESC NULLS LAST, migration_name DESC LIMIT 1')
server_version=$(psql_value 'SHOW server_version')
{
  echo "taken_at=$stamp"
  echo "database=$PGDATABASE"
  echo "server_version=${server_version:-unknown}"
  echo "pg_dump_version=$(pg_dump --version)"
  echo "last_migration=${last_migration:-unknown}"
  echo "dump_bytes=$dump_bytes"
  echo "dump_sha256=$dump_sha"
} > "$stage/MANIFEST"

bundle="$work/bundle.tar"
tar -C "$stage" -cf "$bundle" documenso.dump MANIFEST
rm -rf "$stage"
object="documenso-$stamp.tar.cms"
encrypted="$work/$object"
openssl cms -encrypt -binary -aes-256-cbc -in "$bundle" -out "$encrypted" -outform DER "${recipients[@]}"
rm -f "$bundle"
size=$(stat -c %s "$encrypted")

case "$destination" in
  local)
    mkdir -p "$DOCUMENSO_BACKUP_LOCAL_DIR"
    cp "$encrypted" "$DOCUMENSO_BACKUP_LOCAL_DIR/$object"
    ;;
  s3)
    url="${DOCUMENSO_BACKUP_S3_ENDPOINT%/}/$DOCUMENSO_BACKUP_BUCKET/documenso/$object"
    sha=$(sha256sum "$encrypted" | cut -d' ' -f1)
    # The first argument is the SHA-256 of the request body, which Signature V4 signs:
    # the file's for the upload, the empty string's for a request with no body. The
    # credentials go to curl on stdin, never on its command line, where a process
    # listing would show them.
    s3() {
      local payload_sha=$1
      shift
      printf 'user = "%s:%s"\n' "$DOCUMENSO_BACKUP_ACCESS_KEY_ID" "$DOCUMENSO_BACKUP_SECRET_ACCESS_KEY" \
        | curl --config - --fail --silent --show-error --retry 3 --max-time 600 \
            --aws-sigv4 "aws:amz:$region:s3" -H "x-amz-content-sha256: $payload_sha" "$@"
    }
    s3 "$sha" -T "$encrypted" "$url"
    # A PUT that answered 200 is not yet proof: ask the bucket how big it says the
    # object is.
    remote_size=$(s3 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 -I "$url" | tr -d '\r' | awk 'tolower($1) == "content-length:" {print $2}')
    [ "$remote_size" = "$size" ] || die "the bucket reports $remote_size bytes for $object, expected $size"
    ;;
esac

touch "$ok_file"
echo "backup: $object, $size bytes (dump $dump_bytes, last migration ${last_migration:-unknown}), to $destination"
