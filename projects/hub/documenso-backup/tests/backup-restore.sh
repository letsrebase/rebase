#!/usr/bin/env bash
# The backup and restore drill of Documenso's database (REB-715), on throwaway servers,
# with the real compose file and the real scripts. Needs Docker, openssl, python3 and
# curl; touches nothing outside a temporary directory and a compose project called
# `docdrill-<random>`, so two runs on one machine (two worktrees, preflight and a hand
# run) never share containers. Documenso itself is not started: its image is a gigabyte
# and the backup reads its Postgres, not the application.
#
#   1. start the real `documenso-db` and put in it what Documenso puts there: a Prisma
#      migrations table and a contract stored as bytea;
#   2. run the `documenso-backup` service's script against an S3 gateway, and prove the
#      object in the bucket is an encrypted envelope for each certificate it was given,
#      that holds none of the plain text and that no other key opens; then run the
#      service's loop against a stand-in monitor, and prove a backup that finished pings
#      the check and one that failed pings `<check>/fail` without ending the loop;
#   3. download the object, restore.sh it with a private key, load the archive into a
#      scratch Postgres with pg_restore, from the unpacked file and straight from the
#      envelope with no file at all, and read the contract back byte for byte.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
backup_dir=$(dirname "$here")
hub_dir=$(dirname "$backup_dir")
postgres_image=$(sed -n 's/^ *image: \(postgres:[^ ]*\)$/\1/p' "$hub_dir/docker-compose.documenso.yml" | head -n 1)
[ -n "$postgres_image" ] || { echo "FAIL: no postgres image in docker-compose.documenso.yml" >&2; exit 1; }
tmp=$(mktemp -d)
suffix=$(date +%s)-$$
export COMPOSE_PROJECT_NAME="docdrill-$suffix"
scratch_name="docdrill-scratch-$suffix"
compose() {
  docker compose --project-directory "$hub_dir" -f "$hub_dir/docker-compose.documenso.yml" -f "$here/s3.yml" "$@"
}
cleanup() {
  compose down -v --remove-orphans --rmi local >/dev/null 2>&1 || true
  docker rm -f "$scratch_name" >/dev/null 2>&1 || true
  # Postgres writes as its own user, so its files are not ours to remove from here.
  docker run --rm -v "$tmp:/t" --entrypoint rm "$postgres_image" -rf /t/pgdata >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
step() { printf '\n== %s\n' "$*"; }

trap cleanup EXIT

# Two recipients and a stranger. The certificates go to the service, the keys stay here.
for who in lorenzo second stranger; do
  openssl req -x509 -newkey rsa:3072 -nodes -days 3 -subj "/CN=docdrill-$who" \
    -keyout "$tmp/$who.key" -out "$tmp/$who.crt" 2>/dev/null
done

mkdir "$tmp/pgdata"
export REBASE_DOCUMENSO_DATA_DIR="$tmp/pgdata"
export DOCUMENSO_DB_PASSWORD=drill-not-a-password
export DOCUMENSO_NEXTAUTH_SECRET=x DOCUMENSO_ENCRYPTION_KEY=x DOCUMENSO_ENCRYPTION_SECONDARY_KEY=x
export DOCUMENSO_SIGNING_PASSPHRASE=x DOCUMENSO_SIGNING_CERT_BASE64=x REBASE_RESEND_API_KEY=x
DOCUMENSO_BACKUP_CERT_B64=$(cat "$tmp/lorenzo.crt" "$tmp/second.crt" | base64 -w0)
export DOCUMENSO_BACKUP_CERT_B64
export DOCUMENSO_BACKUP_R2_ACCOUNT_ID=drill
export DOCUMENSO_BACKUP_BUCKET=drill-bucket
export DOCUMENSO_BACKUP_ACCESS_KEY_ID=drillkey
export DOCUMENSO_BACKUP_SECRET_ACCESS_KEY=drillsecret
export DOCUMENSO_BACKUP_PING_URL=http://ping:8080/drill-check

marker="drill-contract-$RANDOM-$RANDOM"

step "0. the compose file: three services, the backup one publishes nothing and is built from the database's own image"
services=$(docker compose --project-directory "$hub_dir" -f "$hub_dir/docker-compose.documenso.yml" config --services | sort | tr '\n' ' ')
[ "$services" = 'documenso documenso-backup documenso-db ' ] || { echo "FAIL: services are '$services'" >&2; exit 1; }
compose config --format json | python3 -c '
import json, sys
backup = json.load(sys.stdin)["services"]["documenso-backup"]
assert "ports" not in backup, "the backup service publishes a port"
assert backup["depends_on"]["documenso-db"]["condition"] == "service_healthy", "it does not wait for a healthy database"
'
from=$(sed -n 's/^FROM //p' "$backup_dir/Dockerfile")
case "$from" in
  "$postgres_image"@sha256:*) ;;
  *) echo "FAIL: the backup image is built FROM $from, the database runs $postgres_image (and the base is pinned by digest)" >&2; exit 1 ;;
esac
compose build documenso-backup
echo "services, no port, a healthy database first, and pg_dump from the same image as the server"

step "1. the real documenso-db, with a migration row and a contract in it"
compose up -d documenso-db s3 ping
# The image's first server, the one that runs initdb, listens on its socket only and then
# restarts: ready on the socket is not ready, ready on TCP is.
db=$(compose ps -q documenso-db)
for _ in $(seq 90); do
  docker exec "$db" pg_isready -h 127.0.0.1 -U documenso -d documenso >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$db" pg_isready -h 127.0.0.1 -U documenso -d documenso >/dev/null || { echo "FAIL: documenso-db never answered on TCP" >&2; exit 1; }
psql_db() { docker exec -i "$db" psql -v ON_ERROR_STOP=1 -U documenso -d documenso -At "$@"; }
psql_db <<SQL >/dev/null
CREATE TABLE _prisma_migrations (id text PRIMARY KEY, migration_name text NOT NULL, finished_at timestamptz);
INSERT INTO _prisma_migrations VALUES ('1', '20260901000000_before', now() - interval '1 day'), ('2', '20260922002212_add_auth_handoff', now());
CREATE TABLE "DocumentData" (id text PRIMARY KEY, type text NOT NULL, data bytea NOT NULL);
INSERT INTO "DocumentData" VALUES ('doc-1', 'BYTES_64', convert_to('$marker', 'UTF8') || convert_to((SELECT string_agg(md5(i::text), '') FROM generate_series(1, 60000) AS i), 'UTF8'));
SQL
want_sha=$(psql_db -c "SELECT encode(sha256(data), 'hex') FROM \"DocumentData\" WHERE id = 'doc-1'")
[ -n "$want_sha" ] || { echo "FAIL: the seed left no contract" >&2; exit 1; }
echo "contract doc-1 seeded, sha256 $want_sha"

step "2. the backup service's script, into an S3 bucket"
s3_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(compose ps -q s3)")
s3() {
  curl --fail --silent --show-error --user drillkey:drillsecret --aws-sigv4 'aws:amz:us-east-1:s3' \
    -H 'x-amz-content-sha256: UNSIGNED-PAYLOAD' "$@"
}
for _ in $(seq 30); do s3 -X PUT "http://$s3_ip:7070/drill-bucket" 2>/dev/null && break; sleep 1; done
# A schedule `date` cannot read ends the container instead of spinning.
if compose run --rm --no-deps -T -e DOCUMENSO_BACKUP_AT=25:00 documenso-backup 2>/dev/null; then
  echo "FAIL: the backup loop accepted DOCUMENSO_BACKUP_AT=25:00" >&2; exit 1
fi
echo "DOCUMENSO_BACKUP_AT=25:00 is refused"
# A certificate line that is not one is refused before anything is dumped.
if compose run --rm --no-deps -T --entrypoint /bin/bash -e DOCUMENSO_BACKUP_CERT_B64="$(printf 'not a certificate' | base64 -w0)" documenso-backup /scripts/backup.sh 2>"$tmp/badcert.err"; then
  echo "FAIL: the backup accepted a certificate line with no certificate in it" >&2; exit 1
fi
grep -q 'holds no PEM certificate' "$tmp/badcert.err" || { echo "FAIL: the bad certificate was refused for another reason: $(cat "$tmp/badcert.err")" >&2; exit 1; }
echo "a certificate line with no certificate in it is refused"
run_log=$(compose run --rm --no-deps -T --entrypoint /bin/bash documenso-backup /scripts/backup.sh)
echo "$run_log"
grep -q 'last migration 20260922002212_add_auth_handoff' <<<"$run_log" || { echo "FAIL: the run did not read the last migration" >&2; exit 1; }
key=$(s3 "http://$s3_ip:7070/drill-bucket?list-type=2&prefix=documenso/" | grep -o 'documenso/documenso-[0-9TZ]*\.tar\.cms' | head -n 1)
[ -n "$key" ] || { echo "FAIL: no object under documenso/ in the bucket" >&2; exit 1; }
s3 -o "$tmp/object.cms" "http://$s3_ip:7070/drill-bucket/$key"
echo "object $key, $(stat -c %s "$tmp/object.cms") bytes"
openssl cms -cmsout -inform DER -in "$tmp/object.cms" -print > "$tmp/object.txt"
grep -q 'pkcs7-envelopedData' "$tmp/object.txt" || { echo "FAIL: the object is not a CMS envelope" >&2; exit 1; }
[ "$(grep -c 'd.ktri' "$tmp/object.txt")" -eq 2 ] || { echo "FAIL: the envelope does not hold exactly two recipients" >&2; exit 1; }
if tar -tf "$tmp/object.cms" >/dev/null 2>&1; then echo "FAIL: the object is a readable archive" >&2; exit 1; fi
if grep -aq "$marker" "$tmp/object.cms"; then echo "FAIL: the contract's text is in the object in clear" >&2; exit 1; fi
if openssl cms -decrypt -binary -inform DER -in "$tmp/object.cms" -inkey "$tmp/stranger.key" -out /dev/null 2>/dev/null; then
  echo "FAIL: a key that is not one of the certificates' opened the object" >&2; exit 1
fi
# With its certificate, each recipient's key opens the envelope; a key with another
# recipient's certificate, or a stranger's own, does not.
for who in lorenzo second; do
  openssl cms -decrypt -binary -inform DER -in "$tmp/object.cms" -inkey "$tmp/$who.key" -recip "$tmp/$who.crt" | tar -tf - | grep -x documenso.dump >/dev/null \
    || { echo "FAIL: the $who key did not open the object" >&2; exit 1; }
done
if openssl cms -decrypt -binary -inform DER -in "$tmp/object.cms" -inkey "$tmp/lorenzo.key" -recip "$tmp/second.crt" -out /dev/null 2>/dev/null; then
  echo "FAIL: a key opened the object with another recipient's certificate" >&2; exit 1
fi
if openssl cms -decrypt -binary -inform DER -in "$tmp/object.cms" -inkey "$tmp/stranger.key" -recip "$tmp/stranger.crt" -out /dev/null 2>/dev/null; then
  echo "FAIL: a stranger's key and certificate opened the object" >&2; exit 1
fi
echo "the object is a CMS envelope for two recipients, each key opens it with its certificate, no other does, and the text is not in it"

step "2b. the monitor hears a backup that finished, and one that failed"
# A ping URL that is not one ends the container instead of running with no alarm, and so
# does one with a trailing slash, which would send `/fail` to a path nobody watches.
for bad in not-a-url http://ping:8080/drill-check/; do
  if refusal=$(compose run --rm --no-deps -T -e DOCUMENSO_BACKUP_PING_URL="$bad" documenso-backup 2>&1); then
    echo "FAIL: the backup loop accepted the ping URL '$bad'" >&2; exit 1
  fi
  grep -q "must be the monitor's http(s) ping URL" <<<"$refusal" \
    || { echo "FAIL: '$bad' was refused for another reason: $refusal" >&2; exit 1; }
done
echo "a ping URL that is not one, or ends in a slash, is refused"
pings() { compose logs --no-log-prefix ping 2>/dev/null | grep -c "\"GET $1 HTTP" || true; }
wait_ping() {
  for _ in $(seq 90); do
    [ "$(pings "$1")" -ge 1 ] && return 0
    sleep 1
  done
  echo "FAIL: the monitor never heard GET $1" >&2
  exit 1
}
loop=$(compose run -d --no-deps -e DOCUMENSO_BACKUP_FIRST_DELAY=0 documenso-backup)
wait_ping /drill-check
# The healthcheck, as the container has it (compose has already turned `$$` into `$`), is
# true right after a backup and false for a backup two days old.
hc=$(docker inspect -f '{{json .Config.Healthcheck.Test}}' "$loop" | python3 -c 'import json, sys; print(json.load(sys.stdin)[1])')
docker exec "$loop" sh -c "$hc" || { echo "FAIL: the healthcheck is false right after a backup" >&2; exit 1; }
docker exec "$loop" sh -c 'touch -d "$(date -u -d "@$(($(date +%s) - 172800))" "+%Y-%m-%d %H:%M:%S")" /tmp/last-backup-ok'
if docker exec "$loop" sh -c "$hc"; then echo "FAIL: the healthcheck is true for a backup two days old" >&2; exit 1; fi
docker exec "$loop" touch /tmp/last-backup-ok
# Then it sleeps until tomorrow's slot instead of backing up again: one `sleep` of more
# than a minute and at most a day and a minute.
sleep 3
secs=$(docker exec "$loop" ps -o args | sed -n 's/^sleep \([0-9][0-9]*\)$/\1/p' | head -n 1)
{ [ -n "$secs" ] && [ "$secs" -gt 60 ] && [ "$secs" -le 86460 ]; } || { echo "FAIL: the loop is not sleeping to the next slot (sleep '$secs')" >&2; exit 1; }
[ "$(pings /drill-check)" -eq 1 ] || { echo "FAIL: the loop backed up again at once" >&2; exit 1; }
loop_logs=$(docker logs "$loop" 2>&1)
docker rm -f "$loop" >/dev/null
[ "$(pings /drill-check/fail)" -eq 0 ] || { echo "FAIL: a backup that finished was reported as failed" >&2; exit 1; }
echo "a backup that finished pings the check, the healthcheck is true, and the loop sleeps $secs seconds to the next slot"
# A revoked R2 key: the upload is refused, the loop survives and says so to the monitor.
loop=$(compose run -d --no-deps -e DOCUMENSO_BACKUP_FIRST_DELAY=0 -e DOCUMENSO_BACKUP_SECRET_ACCESS_KEY=revoked documenso-backup)
wait_ping /drill-check/fail
[ "$(docker inspect -f '{{.State.Running}}' "$loop")" = true ] || { echo "FAIL: a failed backup ended the loop" >&2; exit 1; }
loop_logs+=$(docker logs "$loop" 2>&1)
docker rm -f "$loop" >/dev/null
[ "$(pings /drill-check)" -eq 1 ] || { echo "FAIL: a backup that failed was reported as a success" >&2; exit 1; }
echo "a backup that failed pings <check>/fail and nothing else"
# Whoever holds the URL can report a success, so neither run may print it.
if grep -q drill-check <<<"$loop_logs"; then echo "FAIL: the loop printed the ping URL" >&2; exit 1; fi
echo "neither run printed the ping URL"

step "3. restore: unpack with a private key, load into a scratch Postgres, read the contract back"
mkdir -m 700 "$tmp/restored"
"$backup_dir/scripts/restore.sh" "$tmp/object.cms" "$tmp/second.key" "$tmp/restored" "$tmp/second.crt"
[ "$(stat -c %a "$tmp/restored")" = 700 ] || { echo "FAIL: restore.sh changed the mode of the target directory" >&2; exit 1; }
# A target the script creates is 0700 as well, whatever the caller's umask.
(umask 022 && "$backup_dir/scripts/restore.sh" "$tmp/object.cms" "$tmp/lorenzo.key" "$tmp/restored-new" "$tmp/lorenzo.crt" >/dev/null)
[ "$(stat -c %a "$tmp/restored-new")" = 700 ] || { echo "FAIL: restore.sh created its target with the caller's umask" >&2; exit 1; }
if "$backup_dir/scripts/restore.sh" "$tmp/object.cms" "$tmp/lorenzo.key" "$tmp/restored" "$tmp/lorenzo.crt" 2>/dev/null; then
  echo "FAIL: restore.sh wrote into a directory that was not empty" >&2; exit 1
fi
if "$backup_dir/scripts/restore.sh" "$tmp/object.cms" "$tmp/stranger.key" "$tmp/restored-stranger" "$tmp/stranger.crt" 2>/dev/null; then
  echo "FAIL: restore.sh opened the object with a stranger's key" >&2; exit 1
fi
# A refused restore leaves no half-unpacked directory behind.
[ -z "$(ls -A "$tmp/restored-stranger")" ] || { echo "FAIL: a refused restore left files in its target" >&2; exit 1; }
grep -qx 'last_migration=20260922002212_add_auth_handoff' "$tmp/restored/MANIFEST" || { echo "FAIL: the MANIFEST does not name the last migration" >&2; exit 1; }
# A MANIFEST that disagrees with the archive is refused.
tampered=$(mktemp -d "$tmp/tampered.XXXXXX")
openssl cms -decrypt -binary -inform DER -in "$tmp/object.cms" -inkey "$tmp/lorenzo.key" -recip "$tmp/lorenzo.crt" | tar -C "$tampered" -xf -
printf 'x' >> "$tampered/documenso.dump"
tar -C "$tampered" -cf "$tmp/tampered.tar" documenso.dump MANIFEST
openssl cms -encrypt -binary -aes-256-cbc -in "$tmp/tampered.tar" -out "$tmp/tampered.cms" -outform DER "$tmp/lorenzo.crt"
if "$backup_dir/scripts/restore.sh" "$tmp/tampered.cms" "$tmp/lorenzo.key" "$tmp/restored-tampered" "$tmp/lorenzo.crt" 2>/dev/null; then
  echo "FAIL: restore.sh accepted an archive that does not match its MANIFEST" >&2; exit 1
fi
echo "unpacked with either key, refused with a stranger's, into a used directory and for an archive that does not match its MANIFEST"

docker run -d --name "$scratch_name" -e POSTGRES_USER=documenso -e POSTGRES_PASSWORD=scratch -e POSTGRES_DB=restored "$postgres_image" >/dev/null
for _ in $(seq 90); do
  docker exec "$scratch_name" pg_isready -h 127.0.0.1 -U documenso -d restored >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$scratch_name" pg_isready -h 127.0.0.1 -U documenso -d restored >/dev/null || { echo "FAIL: the scratch Postgres never answered on TCP" >&2; exit 1; }
scratch_psql() { docker exec -i "$scratch_name" psql -v ON_ERROR_STOP=1 -U documenso -At "$@"; }
docker exec -i "$scratch_name" pg_restore --exit-on-error --no-owner --no-acl -U documenso -d restored < "$tmp/restored/documenso.dump"
got=$(scratch_psql -d restored -c "SELECT encode(sha256(data), 'hex') FROM \"DocumentData\" WHERE id = 'doc-1'")
[ "$got" = "$want_sha" ] || { echo "FAIL: the restored contract is '$got', expected '$want_sha'" >&2; exit 1; }
scratch_psql -d restored -c "SELECT migration_name FROM _prisma_migrations ORDER BY finished_at DESC NULLS LAST, migration_name DESC LIMIT 1" | grep -qx 20260922002212_add_auth_handoff \
  || { echo "FAIL: the restored database lacks the last migration" >&2; exit 1; }
echo "restored from the unpacked file: contract doc-1 byte for byte ($got)"
# The same, straight from the envelope into pg_restore: the contracts never touch a disk.
scratch_psql -d restored -c 'CREATE DATABASE restored_stream' >/dev/null
openssl cms -decrypt -binary -inform DER -in "$tmp/object.cms" -inkey "$tmp/lorenzo.key" -recip "$tmp/lorenzo.crt" | tar -xOf - documenso.dump \
  | docker exec -i "$scratch_name" pg_restore --exit-on-error --no-owner --no-acl -U documenso -d restored_stream
got=$(scratch_psql -d restored_stream -c "SELECT encode(sha256(data), 'hex') FROM \"DocumentData\" WHERE id = 'doc-1'")
[ "$got" = "$want_sha" ] || { echo "FAIL: the contract restored from the stream is '$got', expected '$want_sha'" >&2; exit 1; }
echo "restored straight from the envelope, no file: contract doc-1 byte for byte"

printf '\nOK: backup, encryption, the monitor, restore and every refusal held\n'
