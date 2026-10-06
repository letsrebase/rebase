#!/usr/bin/env bash
# The backup and restore drill (REB-648), on throwaway servers, with the real compose
# file and the real scripts. Needs Docker, node and openssl; touches nothing outside a
# temporary directory and a compose project called `vwdrill-<random>`, so two runs
# on one machine (two worktrees, preflight and a hand run) never share containers.
#
#   1. start the pinned server with signups open for a moment, register an account and
#      put one item in it (encrypted client-side, as the apps do);
#   2. restart it with only the real compose file, and prove: it answers on 127.0.0.1
#      alone, a signup without an invitation is refused, the admin page is off, and
#      opens with a hash (written as README says) and closes again when it is removed;
#   3. run the `backup` service's script against an S3 gateway, and prove the object in
#      the bucket is an encrypted envelope that holds none of the plain text;
#   4. download it, restore.sh it with the private key into a new directory, start a
#      scratch server on that directory and read the item back.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
project_dir=$(dirname "$here")
image=$(sed -n 's/^ *image: &vaultwarden-image //p' "$project_dir/docker-compose.yml")
tmp=$(mktemp -d)
suffix=$(date +%s)-$$
export COMPOSE_PROJECT_NAME="vwdrill-$suffix"
scratch_name="vwdrill-scratch-$suffix"
compose() {
  docker compose --project-directory "$project_dir" -f "$project_dir/docker-compose.yml" "$@"
}
cleanup() {
  compose -f "$here/s3.yml" -f "$here/seed.yml" down -v --remove-orphans >/dev/null 2>&1 || true
  docker rm -f "$scratch_name" >/dev/null 2>&1 || true
  # The server runs as root, so its files are not ours to remove from here.
  docker run --rm -v "$tmp:/t" --entrypoint rm "$image" -rf /t/data /t/fakedata /t/restored /t/restored-new >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
step() { printf '\n== %s\n' "$*"; }

trap cleanup EXIT

# The backup key pair: the certificate goes to the server, the key stays here.
openssl req -x509 -newkey rsa:3072 -nodes -days 3 -subj '/CN=vwdrill' \
  -keyout "$tmp/backup.key" -out "$tmp/backup.crt" 2>/dev/null

mkdir "$tmp/data"
export VAULTWARDEN_DATA_DIR="$tmp/data"
VAULTWARDEN_PORT=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
export VAULTWARDEN_PORT
export VAULTWARDEN_DOMAIN=vault.example.test
export VAULTWARDEN_SMTP_PASSWORD=drill-not-a-key
export VAULTWARDEN_ORG_CREATORS=drill@example.test
VAULTWARDEN_BACKUP_CERT_B64=$(base64 -w0 < "$tmp/backup.crt")
export VAULTWARDEN_BACKUP_CERT_B64
export VAULTWARDEN_BACKUP_R2_ACCOUNT_ID=drill
export VAULTWARDEN_BACKUP_BUCKET=drill-bucket
export VAULTWARDEN_BACKUP_ACCESS_KEY_ID=drillkey
export VAULTWARDEN_BACKUP_SECRET_ACCESS_KEY=drillsecret

email=drill@example.test
password='correct horse battery staple, drill'
item="drill-item-$RANDOM"

wait_alive() {
  for _ in $(seq 60); do
    curl -fsS "$1/alive" >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "no answer from $1/alive" >&2
  return 1
}
base_of() { echo "http://$(compose port vaultwarden 80)"; }

step "1. a server with signups open for one moment, an account, an item"
compose -f "$here/s3.yml" -f "$here/seed.yml" up -d vaultwarden s3
base=$(base_of)
wait_alive "$base"
node "$here/drill.mjs" register "$base" "$email" "$password"
node "$here/drill.mjs" create "$base" "$email" "$password" "$item"

step "2. the real compose file alone: loopback only, no signup, no admin page"
compose -f "$here/s3.yml" up -d --force-recreate vaultwarden
base=$(base_of)
wait_alive "$base"
published=$(compose port vaultwarden 80)
echo "published on $published"
case "$published" in 127.0.0.1:*) ;; *) echo "FAIL: published on $published, not on 127.0.0.1" >&2; exit 1 ;; esac
node "$here/drill.mjs" refuse "$base" stranger@example.test
admin=$(curl -s -o "$tmp/admin.out" -w '%{http_code}' "$base/admin")
echo "GET /admin: HTTP $admin: $(head -c 200 "$tmp/admin.out" | tr '\n' ' ')"
grep -qi 'admin panel is disabled' "$tmp/admin.out" || { echo "FAIL: the admin page is not disabled" >&2; exit 1; }
[ "$(node "$here/drill.mjs" read "$base" "$email" "$password")" = "$item" ] || { echo "FAIL: item missing before backup" >&2; exit 1; }

step "2b. the admin page opens with a hash and closes when the line is removed"
# `vaultwarden hash` reads the token from a terminal, so it gets one.
token=drill-admin-token
hash_line=$(printf '%s\n%s\n' "$token" "$token" \
  | script -qec "docker run -it --rm $image /vaultwarden hash --preset owasp" /dev/null | tr -d '\r' | grep "^ADMIN_TOKEN=")
# The line goes into an env file exactly as the README says to write it: single quotes.
printf 'VAULTWARDEN_%s\n' "$hash_line" > "$tmp/admin.env"
compose -f "$here/s3.yml" --env-file "$tmp/admin.env" up -d --force-recreate vaultwarden
base=$(base_of)
wait_alive "$base"
admin_login() { curl -s -o /dev/null -D - --data-urlencode "token=$1" "$base/admin" | tr -d '\r'; }
admin_login "$token" | grep -qi '^set-cookie: VW_ADMIN' || { echo "FAIL: the right token did not open the admin page" >&2; exit 1; }
if admin_login 'not-the-token' | grep -qi '^set-cookie: VW_ADMIN'; then echo "FAIL: a wrong token opened the admin page" >&2; exit 1; fi
echo "the admin page opens with the hash and refuses another token"
compose -f "$here/s3.yml" up -d --force-recreate vaultwarden
base=$(base_of)
wait_alive "$base"
curl -s "$base/admin" | grep -qi 'admin panel is disabled' || { echo "FAIL: the admin page did not close again" >&2; exit 1; }
echo "and it is disabled again once the line is gone"

step "3. the backup service's script, into an S3 bucket"
s3_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(compose ps -q s3)")
s3() {
  curl --fail --silent --show-error --user drillkey:drillsecret --aws-sigv4 'aws:amz:us-east-1:s3' \
    -H 'x-amz-content-sha256: UNSIGNED-PAYLOAD' "$@"
}
s3 -X PUT "http://$s3_ip:7070/drill-bucket"
# A plain snapshot a killed run left behind must be gone after the next one.
touch "$tmp/data/db_20000101_000000.sqlite3"
# A schedule `date` cannot read ends the container instead of spinning.
if compose -f "$here/s3.yml" run --rm --no-deps -T -e VAULTWARDEN_BACKUP_AT=25:00 backup 2>/dev/null; then
  echo "FAIL: the backup loop accepted VAULTWARDEN_BACKUP_AT=25:00" >&2; exit 1
fi
echo "VAULTWARDEN_BACKUP_AT=25:00 is refused"
compose -f "$here/s3.yml" run --rm --no-deps -T --entrypoint /usr/bin/bash backup /scripts/backup.sh
key=$(s3 "http://$s3_ip:7070/drill-bucket?list-type=2&prefix=vaultwarden/" | grep -o 'vaultwarden/vaultwarden-[0-9TZ]*\.tar\.gz\.cms' | head -n 1)
[ -n "$key" ] || { echo "FAIL: no object in the bucket" >&2; exit 1; }
s3 -o "$tmp/object.cms" "http://$s3_ip:7070/drill-bucket/$key"
echo "object $key, $(stat -c %s "$tmp/object.cms") bytes"
openssl cms -cmsout -inform DER -in "$tmp/object.cms" -print | grep -q 'pkcs7-envelopedData' \
  || { echo "FAIL: the object is not a CMS envelope" >&2; exit 1; }
if tar -tzf "$tmp/object.cms" >/dev/null 2>&1; then echo "FAIL: the object is a readable archive" >&2; exit 1; fi
# A key that is not the certificate's must not open it.
openssl req -x509 -newkey rsa:3072 -nodes -days 3 -subj '/CN=stranger' -keyout "$tmp/other.key" -out "$tmp/other.crt" 2>/dev/null
if openssl cms -decrypt -binary -inform DER -in "$tmp/object.cms" -inkey "$tmp/other.key" -out /dev/null 2>/dev/null; then
  echo "FAIL: a key that is not the certificate's opened the object" >&2; exit 1
fi
echo "the object is a CMS envelope, not an archive, and only the certificate's key opens it"
[ -z "$(find "$tmp/data" -maxdepth 1 -name 'db_*.sqlite3')" ] || { echo "FAIL: a snapshot was left in the data directory" >&2; exit 1; }

step "3b. a file that first appears while the backup runs is in the bundle"
# A stand-in for the server binary that, when asked for a backup, makes the very first
# attachment of an instance that had none (so the first copy pass found no directory),
# the case the second pass in backup.sh exists for.
mkdir "$tmp/fakedata"
touch "$tmp/fakedata/db.sqlite3"
cat > "$tmp/fake-vaultwarden" <<'FAKE'
#!/usr/bin/env bash
if [ "$1" = backup ]; then
  mkdir -p /data/attachments/item-1
  echo attachment-body > /data/attachments/item-1/file-1
  echo snapshot > /data/db_20000101_000000.sqlite3
  echo "Backup ok"
else
  echo "Vaultwarden fake"
fi
FAKE
chmod +x "$tmp/fake-vaultwarden"
docker run --rm -v "$tmp/fakedata:/data" -v "$project_dir/scripts:/scripts:ro" -v "$tmp/fake-vaultwarden:/fake-vaultwarden:ro" \
  -e VAULTWARDEN_BIN=/fake-vaultwarden -e VAULTWARDEN_BACKUP_CERT_B64 -e VAULTWARDEN_BACKUP_LOCAL_DIR=/data/out \
  -e VAULTWARDEN_BACKUP_OK_FILE=/tmp/ok --entrypoint bash "$image" /scripts/backup.sh
fake_object=$(find "$tmp/fakedata/out" -name '*.cms')
openssl cms -decrypt -binary -inform DER -in "$fake_object" -inkey "$tmp/backup.key" | tar -tz | grep -qx './attachments/item-1/file-1' \
  || { echo "FAIL: the attachment made during the backup is not in the bundle" >&2; exit 1; }
echo "the attachment made during the backup is in the bundle"

step "4. restore into a new directory, a scratch server on it, the item back"
mkdir -m 700 "$tmp/restored"
"$project_dir/scripts/restore.sh" "$tmp/object.cms" "$tmp/backup.key" "$tmp/restored"
[ "$(stat -c %a "$tmp/restored")" = 700 ] || { echo "FAIL: restore.sh changed the mode of the target directory" >&2; exit 1; }
# A target the script creates is 0700 as well, whatever the caller's umask.
(umask 022 && "$project_dir/scripts/restore.sh" "$tmp/object.cms" "$tmp/backup.key" "$tmp/restored-new" >/dev/null)
[ "$(stat -c %a "$tmp/restored-new")" = 700 ] || { echo "FAIL: restore.sh created its target with the caller's umask" >&2; exit 1; }
if "$project_dir/scripts/restore.sh" "$tmp/object.cms" "$tmp/backup.key" "$tmp/restored" 2>/dev/null; then
  echo "FAIL: restore.sh wrote into a directory that was not empty" >&2; exit 1
fi
docker run -d --name "$scratch_name" -p 127.0.0.1::80 -v "$tmp/restored:/data" \
  -e SIGNUPS_ALLOWED=false -e DOMAIN=https://vault.example.test "$image" >/dev/null
scratch="http://$(docker port "$scratch_name" 80/tcp | head -n 1)"
wait_alive "$scratch"
got=$(node "$here/drill.mjs" read "$scratch" "$email" "$password")
[ "$got" = "$item" ] || { echo "FAIL: restored server returned '$got', expected '$item'" >&2; exit 1; }
echo "restored server returned the item: $got"

printf '\nOK: backup, encryption, restore and the three refusals all held\n'
