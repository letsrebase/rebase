# Restoring Vaultwarden from the off-host backup

For two situations, which differ only in what you do with the result: a **drill** (prove a
backup opens and a test item comes back, on a scratch container, nothing live touched) and a
**recovery** (the host or its data directory is gone). The drill is the proof the backup
works, and it is done before anything real is stored in the server (REB-644's rule) and
again after any change to `scripts/` or the image. `tests/backup-restore.sh` is the same
sequence on throwaway servers, run in CI; this page is the one with the real bucket.

You need: the private key (`vaultwarden-backup.key`, README § Provisioning step 2), the R2
access key pair (any key with Object Read on `rebase-vaultwarden-backup`; the backup's own
will do), the Cloudflare account id, and a machine with Docker and `openssl`.

## 1. Find and fetch the newest object

```
export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  AWS_DEFAULT_REGION=auto
endpoint=https://<account-id>.r2.cloudflarestorage.com
aws --endpoint-url "$endpoint" s3 ls s3://rebase-vaultwarden-backup/vaultwarden/
aws --endpoint-url "$endpoint" s3 cp s3://rebase-vaultwarden-backup/vaultwarden/vaultwarden-<stamp>.tar.gz.cms .
```

Any S3 client works; the names sort by time, so the last line is the newest. If the
newest is older than a day, the backup has been failing: read the backup container's logs
before trusting this one.

## 2. Open it, into a directory that does not exist yet

```
projects/vaultwarden/scripts/restore.sh vaultwarden-<stamp>.tar.gz.cms vaultwarden-backup.key /tmp/vw-restore
```

It prints the bundle's `MANIFEST` (when it was taken, which server version wrote it) and
refuses a target directory that is not empty. What is in the directory is a data directory:
`db.sqlite3`, `rsa_key*` and `MANIFEST` always, `attachments/`, `sends/` and `config.json`
only when the live server had them (the 2026-10-15 drill's bundle had none of the three).

**On a Mac, use Homebrew's OpenSSL.** The system `/usr/bin/openssl` is LibreSSL 3.3.6, which
cannot decrypt the backup; the one from Homebrew can (tested with OpenSSL 3.6.3 at
`/opt/homebrew/bin/openssl` on 2026-10-21). Put it first on the path for the one command
(`$(brew --prefix)/bin` on an Intel Mac):

```
PATH=/opt/homebrew/bin:$PATH projects/vaultwarden/scripts/restore.sh vaultwarden-<stamp>.tar.gz.cms vaultwarden-backup.key /tmp/vw-restore
```

The script runs on macOS's own `tar` (bsdtar) as it is, and leaves the target `0700` with
either kind of `tar` (REB-718). A restore that fails part-way can leave the target
half-filled: remove it and run again into a new directory.

## 3. Drill: a scratch container on it

The web vault refuses a server over plain HTTP, `localhost` included: the login stops on
«Insecure URL not allowed. All URLs must use HTTPS.» and sends nothing. So the scratch
container serves TLS itself, with a throwaway self-signed certificate:

```
mkdir /tmp/vw-ssl
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
  -addext subjectAltName=DNS:localhost -keyout /tmp/vw-ssl/key.pem -out /tmp/vw-ssl/cert.pem
docker run -d --name vw-scratch -p 127.0.0.1:8099:80 -v /tmp/vw-restore:/data \
  -v /tmp/vw-ssl:/ssl:ro -e 'ROCKET_TLS={certs="/ssl/cert.pem",key="/ssl/key.pem"}' \
  -e SIGNUPS_ALLOWED=false -e DOMAIN=https://localhost:8099 \
  vaultwarden/server:1.37.4@sha256:efb3cde962015fcc036b2ea625242248611943b27212ebf4392841fb30fad055
```

Use the tag and digest in `docker-compose.yml` now, not this page's. Open
`https://localhost:8099`, accept the certificate warning, log in as a member whose item you
know is in the backup, and find it. That is the drill's whole pass condition: **the test
item came back**. Then `docker rm -f vw-scratch`, `rm -rf /tmp/vw-ssl` and
`rm -rf /tmp/vw-restore` (the files are root's: use
`docker run --rm -v /tmp:/t --entrypoint rm <image> -rf /t/vw-restore`), and delete the
private key you used.

Notes: the scratch server has no SMTP, so nothing is mailed from it. The unpacked directory
is the whole vault (`db.sqlite3` with every master-password hash, `rsa_key*`), so keep it
and the key off any committed path and out of any log.

## 4. Recovery: the real data directory

Only when the live one is gone or ruined. The deploy owns the stack, so the order is:

1. Stop trusting the old directory: move it aside (`mv <data-dir> <data-dir>.broken`, with
   the path from the host's `.env`), never delete it before the restored server has been
   read by a member.
2. Run step 2 **on another machine**, with the target a new directory there: the private
   key never goes onto the host, so the host never holds a way to open its own backups.
   Then copy the unpacked directory into the host's data directory, the
   `VAULTWARDEN_DATA_DIR` line of `${DEPLOY_PATH}/.env` (`/srv/rebase-data/vaultwarden` in
   `.env.example`; it is empty now: create it first, mode 0700, owned by root), for example
   `tar -C /tmp/vw-restore -cf - . | ssh <host> 'tar -C /srv/rebase-data/vaultwarden -xf -'`
   with that path written out, since the remote shell does not read the `.env`. Delete the
   unpacked copy from the other machine.
3. Start the stack the way it is always started: re-run the last deploy
   (`gh run rerun <run-id>`), not `docker compose up`. On a new host, that is the
   Environment's `DEPLOY_HOST` and `DEPLOY_KNOWN_HOSTS` first, the DNS record second, the
   steps of README § Provisioning for the host third, then the tag.
4. Members log in with what they had: the database, the organisation, the collections and
   every master password are in the backup. `rsa_key*` in it is what keeps their devices
   logged in; without it they sign in again. What was written after the backup is lost, up
   to a day.
5. Read the organisation as an owner, check one collection per permission, and only then
   remove the `.broken` directory.
