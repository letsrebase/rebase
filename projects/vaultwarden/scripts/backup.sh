#!/usr/bin/env bash
# One backup of the Vaultwarden data directory (REB-648), run inside the `backup`
# service of docker-compose.yml with the server's own image, so it needs nothing the
# image does not carry: `/vaultwarden backup`, tar, openssl, curl.
#
# What it saves, and why those and not the whole directory:
#   db.sqlite3     a consistent snapshot made by `vaultwarden backup` (SQLite's
#                  VACUUM INTO), never a copy of the live file and its -wal
#   attachments/   files attached to items
#   sends/         the files of Sends
#   config.json    what the admin page saved, which overrides the environment
#   rsa_key*       the key that signs every login token; without it every session and
#                  every device is logged out on the restored server
# Not saved: icon_cache/ (refetched) and tmp/.
#
# The bundle is a tar.gz encrypted, as a CMS envelope, to the public certificate in
# VAULTWARDEN_BACKUP_CERT_B64. Only the certificate is on this host: whoever reads the
# bucket, or this container, cannot open a backup. The private key is the owner's
# (README, "Backup"), and restore.sh is what uses it.
#
# Where it goes: an S3 endpoint (Cloudflare R2) with VAULTWARDEN_BACKUP_S3_ENDPOINT,
# _BUCKET, _ACCESS_KEY_ID and _SECRET_ACCESS_KEY, or, for a drill without a bucket, a
# directory in VAULTWARDEN_BACKUP_LOCAL_DIR. Exactly one of the two.
set -euo pipefail

data_dir=${VAULTWARDEN_DATA_DIR:-/data}
vw_bin=${VAULTWARDEN_BIN:-/vaultwarden}
region=${VAULTWARDEN_BACKUP_REGION:-auto}
ok_file=${VAULTWARDEN_BACKUP_OK_FILE:-/tmp/last-backup-ok}

die() { echo "backup: $*" >&2; exit 1; }

[ -n "${VAULTWARDEN_BACKUP_CERT_B64:-}" ] || die "VAULTWARDEN_BACKUP_CERT_B64 is empty: nothing to encrypt to"
if [ -n "${VAULTWARDEN_BACKUP_LOCAL_DIR:-}" ]; then
  [ -z "${VAULTWARDEN_BACKUP_S3_ENDPOINT:-}" ] || die "set the S3 endpoint or the local directory, not both"
  destination=local
else
  for v in VAULTWARDEN_BACKUP_S3_ENDPOINT VAULTWARDEN_BACKUP_BUCKET VAULTWARDEN_BACKUP_ACCESS_KEY_ID VAULTWARDEN_BACKUP_SECRET_ACCESS_KEY; do
    [ -n "${!v:-}" ] || die "$v is empty"
  done
  destination=s3
fi
[ -f "$data_dir/db.sqlite3" ] || die "no database at $data_dir/db.sqlite3 (a server that has not started yet?)"

work=$(mktemp -d)
snapshot=''
cleanup() {
  rm -rf "$work"
  [ -z "$snapshot" ] || rm -f "$snapshot"
}
trap cleanup EXIT

printf '%s' "$VAULTWARDEN_BACKUP_CERT_B64" | base64 -d > "$work/backup.crt" || die "VAULTWARDEN_BACKUP_CERT_B64 is not base64"
openssl x509 -in "$work/backup.crt" -noout || die "VAULTWARDEN_BACKUP_CERT_B64 is not a PEM certificate"

# One run at a time, across containers: the loop and a `docker compose run` drill share
# the data directory, and each deletes and picks up snapshots there.
exec 9> "$data_dir/.backup.lock"
flock -n 9 || die "another backup is running on $data_dir"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
stage="$work/stage"
mkdir -m 700 "$stage"

# The files, then the database, then the new files once more. The database says which
# attachments and sends exist, and the server keeps writing while this runs. A file
# deleted after the first copy is an orphan in the bundle, which harms nothing; a file
# added between the first copy and the snapshot is caught by the second pass, which
# adds what the first did not see and replaces nothing. What is left is a file created
# and deleted again inside those few seconds, which the database no longer names.
for item in attachments sends config.json; do
  [ ! -e "$data_dir/$item" ] || cp -a "$data_dir/$item" "$stage/$item"
done
for key in "$data_dir"/rsa_key*; do
  [ ! -e "$key" ] || cp -a "$key" "$stage/"
done

# The snapshot lands beside the database as db_<timestamp>.sqlite3. This script is the
# only thing that makes one, and it removes its own on exit; one found now is what a
# run that was killed left behind, a complete plain copy of the database, so it goes.
find "$data_dir" -maxdepth 1 -name 'db_*.sqlite3' -delete
"$vw_bin" backup
snapshot=$(find "$data_dir" -maxdepth 1 -name 'db_*.sqlite3' | sort | tail -n 1)
if [ -z "$snapshot" ] || [ ! -s "$snapshot" ]; then die "\`$vw_bin backup\` left no snapshot in $data_dir"; fi
cp "$snapshot" "$stage/db.sqlite3"
for item in attachments sends; do
  if [ -d "$data_dir/$item" ]; then
    # The first attachment of a fresh instance may arrive after the first pass saw no
    # directory at all.
    mkdir -p "$stage/$item"
    cp -a --update=none "$data_dir/$item/." "$stage/$item/"
  fi
done
{
  echo "taken_at=$stamp"
  echo "server=$("$vw_bin" --version | head -n 1)"
} > "$stage/MANIFEST"

bundle="$work/bundle.tar.gz"
tar -C "$stage" -czf "$bundle" .
object="vaultwarden-$stamp.tar.gz.cms"
encrypted="$work/$object"
openssl cms -encrypt -binary -aes-256-cbc -in "$bundle" -out "$encrypted" -outform DER -recip "$work/backup.crt"
size=$(stat -c %s "$encrypted")

case "$destination" in
  local)
    mkdir -p "$VAULTWARDEN_BACKUP_LOCAL_DIR"
    cp "$encrypted" "$VAULTWARDEN_BACKUP_LOCAL_DIR/$object"
    ;;
  s3)
    url="${VAULTWARDEN_BACKUP_S3_ENDPOINT%/}/$VAULTWARDEN_BACKUP_BUCKET/vaultwarden/$object"
    sha=$(sha256sum "$encrypted" | cut -d' ' -f1)
    # The first argument is the SHA-256 of the request body, which Signature V4 signs:
    # the file's for the upload, the empty string's for a request with no body. The
    # credentials go to curl on stdin, never on its command line, where a process
    # listing would show them.
    s3() {
      local payload_sha=$1
      shift
      printf 'user = "%s:%s"\n' "$VAULTWARDEN_BACKUP_ACCESS_KEY_ID" "$VAULTWARDEN_BACKUP_SECRET_ACCESS_KEY" \
        | curl --config - --fail --silent --show-error --retry 3 --max-time 300 \
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
echo "backup: $object, $size bytes, to $destination"
