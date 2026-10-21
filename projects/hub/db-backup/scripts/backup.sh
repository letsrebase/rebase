#!/usr/bin/env bash
# One backup of one Postgres database, run inside a backup service of
# docker-compose.documenso.yml (`documenso-backup`, REB-715, and `hub-backup`, REB-717),
# in an image made from the database's own (../Dockerfile), so it needs nothing that image
# and `apk add curl openssl` do not carry. The two services run this same script and differ
# in settings only: BACKUP_NAME (what the backup is called: the prefix in the bucket, the
# stem of every file) and BACKUP_HEAD_SQL (the query that says which schema version the
# database is at).
#
# What it saves: the whole database, as a `pg_dump` custom-format archive. For Documenso
# that is every uploaded and sealed PDF (NEXT_PUBLIC_UPLOAD_TRANSPORT=database), their audit
# trail and the schema; for the hub it is the members, the companies, the matches, the
# contracts and their signed copies. `pg_dump` reads one snapshot, so it is consistent on a
# database that is being written to. Not in it, and not anywhere off the host: the
# application's secrets and keys (docs/restore-from-backup.md, "What the dump does not hold").
#
# The bundle is a tar of the archive and a MANIFEST, encrypted as a CMS envelope to the
# public certificate(s) in BACKUP_CERT_B64: one base64 line holding one PEM certificate, or
# several concatenated, in which case each one's private key opens the backup on its own.
# Only certificates are on this host: whoever reads the bucket, or this container, cannot
# open a backup. restore.sh is what uses a private key, on another machine.
#
# Where it goes: an S3 endpoint (Cloudflare R2) with BACKUP_S3_ENDPOINT, BACKUP_BUCKET,
# BACKUP_ACCESS_KEY_ID and BACKUP_SECRET_ACCESS_KEY, as `<BACKUP_NAME>/<BACKUP_NAME>-<UTC
# stamp>.tar.cms`, or, for a drill without a bucket, a directory in BACKUP_LOCAL_DIR.
# Exactly one of the two. The database is read through the standard PGHOST, PGUSER,
# PGDATABASE and PGPASSWORD.
set -euo pipefail

region=${BACKUP_REGION:-auto}
ok_file=${BACKUP_OK_FILE:-/tmp/last-backup-ok}
export PGCONNECT_TIMEOUT=${PGCONNECT_TIMEOUT:-15}

die() { echo "backup: $*" >&2; exit 1; }

[ -n "${BACKUP_CERT_B64:-}" ] || die "BACKUP_CERT_B64 is empty: nothing to encrypt to"
if [ -n "${BACKUP_LOCAL_DIR:-}" ]; then
  [ -z "${BACKUP_S3_ENDPOINT:-}" ] || die "set the S3 endpoint or the local directory, not both"
  destination=local
else
  for v in BACKUP_S3_ENDPOINT BACKUP_BUCKET BACKUP_ACCESS_KEY_ID BACKUP_SECRET_ACCESS_KEY; do
    [ -n "${!v:-}" ] || die "$v is empty"
  done
  destination=s3
fi
for v in PGHOST PGUSER PGDATABASE PGPASSWORD BACKUP_HEAD_SQL; do
  [ -n "${!v:-}" ] || die "$v is empty: the database is not named"
done
# The name is a path segment of the bucket and the stem of every file, so it is one word.
name=${BACKUP_NAME:-}
[[ $name =~ ^[a-z][a-z0-9-]*$ ]] || die "BACKUP_NAME must be a lower-case word (letters, digits, hyphens); got '$name'"
dump_file="$name.dump"

# Nothing here is for anyone but this user: the archive is the whole database in clear until
# the envelope is made, and it is removed the moment it is in the bundle.
umask 077
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

printf '%s' "$BACKUP_CERT_B64" | base64 -d > "$work/recipients.pem" || die "BACKUP_CERT_B64 is not base64"
awk -v dir="$work" '/-----BEGIN CERTIFICATE-----/ { n++ } n { print > (dir "/recipient-" n ".crt") }' "$work/recipients.pem"
shopt -s nullglob
recipients=("$work"/recipient-*.crt)
shopt -u nullglob
[ "${#recipients[@]}" -gt 0 ] || die "BACKUP_CERT_B64 holds no PEM certificate"
for crt in "${recipients[@]}"; do
  openssl x509 -in "$crt" -noout 2>/dev/null || die "$(basename "$crt") of BACKUP_CERT_B64 is not a PEM certificate"
done

stamp=$(date -u +%Y%m%dT%H%M%SZ)
stage="$work/stage"
mkdir "$stage"

pg_dump --format=custom --no-owner --no-acl --file "$stage/$dump_file"
[ -s "$stage/$dump_file" ] || die "pg_dump left an empty archive"
# An archive `pg_restore` cannot read is not a backup: say so now, not on the day it is needed.
pg_restore --list "$stage/$dump_file" > /dev/null || die "pg_restore cannot read the archive pg_dump wrote"
dump_bytes=$(stat -c %s "$stage/$dump_file")
dump_sha=$(sha256sum "$stage/$dump_file" | cut -d' ' -f1)

# Where the schema stood, by the query the service is configured with (a Prisma migration
# name for Documenso, an Alembic revision for the hub): a backup taken before an upgrade
# is the way back from it. A query that fails says `unknown`, and the backup goes on.
psql_value() { psql --no-psqlrc --tuples-only --no-align --command "$1" 2>/dev/null || true; }
last_migration=$(psql_value "$BACKUP_HEAD_SQL")
server_version=$(psql_value 'SHOW server_version')
{
  echo "taken_at=$stamp"
  echo "name=$name"
  echo "database=$PGDATABASE"
  echo "server_version=${server_version:-unknown}"
  echo "pg_dump_version=$(pg_dump --version)"
  echo "last_migration=${last_migration:-unknown}"
  echo "dump_bytes=$dump_bytes"
  echo "dump_sha256=$dump_sha"
} > "$stage/MANIFEST"

bundle="$work/bundle.tar"
tar -C "$stage" -cf "$bundle" "$dump_file" MANIFEST
rm -rf "$stage"
object="$name-$stamp.tar.cms"
encrypted="$work/$object"
openssl cms -encrypt -binary -aes-256-cbc -in "$bundle" -out "$encrypted" -outform DER "${recipients[@]}"
rm -f "$bundle"
size=$(stat -c %s "$encrypted")

case "$destination" in
  local)
    mkdir -p "$BACKUP_LOCAL_DIR"
    cp "$encrypted" "$BACKUP_LOCAL_DIR/$object"
    ;;
  s3)
    url="${BACKUP_S3_ENDPOINT%/}/$BACKUP_BUCKET/$name/$object"
    sha=$(sha256sum "$encrypted" | cut -d' ' -f1)
    # The first argument is the SHA-256 of the request body, which Signature V4 signs:
    # the file's for the upload, the empty string's for a request with no body. The
    # credentials go to curl on stdin, never on its command line, where a process
    # listing would show them.
    s3() {
      local payload_sha=$1
      shift
      printf 'user = "%s:%s"\n' "$BACKUP_ACCESS_KEY_ID" "$BACKUP_SECRET_ACCESS_KEY" \
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
