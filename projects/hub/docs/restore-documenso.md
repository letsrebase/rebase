# Restoring Documenso from the off-host backup

For two situations, which differ only in what you do with the result: a **drill** (prove a
backup opens and a contract comes back, on a scratch container, nothing live touched) and a
**recovery** (the host, its disk or Documenso's database is gone). The drill is the proof
the backup works, and it is done after any change to `documenso-backup/` or to the
database's image. `documenso-backup/tests/backup-restore.sh` is the same sequence on
throwaway servers, run in CI; this page is the one with the real bucket.

What is backed up, and how (REB-715): the `documenso-backup` service of
`docker-compose.documenso.yml` runs `pg_dump` against `documenso-db` every night at 03:45
UTC and once after each start. Documenso keeps every uploaded and sealed PDF in that
database (`NEXT_PUBLIC_UPLOAD_TRANSPORT=database`), so the archive is the contracts, their
audit trail and the schema. It is bundled with a `MANIFEST` (when it was taken, which
Postgres wrote it, the newest Prisma migration, the archive's SHA-256), encrypted as a CMS
envelope to the public certificate in `DOCUMENSO_BACKUP_CERT_B64`, and put in the private
R2 bucket as `documenso/documenso-<UTC stamp>.tar.cms`. R2 deletes objects under
`documenso/` after ninety days (`infra/cloudflare/r2.tf`). The hub's own database is **not**
backed up (REB-475, postponed): everything below is Documenso's.

You need: the private key that belongs to a certificate in `DOCUMENSO_BACKUP_CERT_B64`, an
R2 access key pair with Object Read on the bucket (the backup's own will do), the Cloudflare
account id, and a machine with Docker, `openssl` and the `aws` CLI. **The certificate is Vaultwarden's**
(decision recorded in `docs/design/DECISIONS.md`, 2026-10-20): the same line as
`VAULTWARDEN_BACKUP_CERT_B64`, so the key is `vaultwarden-backup.key`, kept as
`projects/vaultwarden/README.md` § Provisioning says, and nothing new has to be guarded.
Run every step on a machine that is not the host: the host holds certificates, never a key.

## 1. Find and fetch the newest object

```
export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  AWS_DEFAULT_REGION=auto
endpoint=https://<account-id>.r2.cloudflarestorage.com
aws --endpoint-url "$endpoint" s3 ls s3://<bucket>/documenso/
aws --endpoint-url "$endpoint" s3 cp s3://<bucket>/documenso/documenso-<stamp>.tar.cms .
```

`<bucket>` is `DOCUMENSO_BACKUP_BUCKET` in `/opt/hub/.env` on the host. The names sort by
time, so the last line is the newest. If the newest is older than a day, the backup has been
failing: the healthchecks.io check «Documenso backup» should have mailed, and `cd
/opt/hub/projects/hub && docker compose -p rebase --env-file /opt/hub/.env logs
documenso-backup` says why (`docker compose ps` shows it `unhealthy` after 26 hours).

## 2. Open it, into a directory that does not exist yet

```
projects/hub/documenso-backup/scripts/restore.sh documenso-<stamp>.tar.cms vaultwarden-backup.key /dev/shm/documenso-restore
```

It prints the `MANIFEST`, checks the archive against the SHA-256 recorded in it, and refuses a
target that is not empty. The directory then holds `documenso.dump` and `MANIFEST`: every
contract, in clear. Put it on an encrypted disk or tmpfs and remove it when you are done.
When the backup was made for several certificates (a `DOCUMENSO_BACKUP_CERT_B64` with more
than one), give the certificate that belongs to your key as a fourth argument; openssl
cannot find your recipient from the key alone.

## 3. Drill: a scratch Postgres on it

Use the Postgres image the backup was made with, the base of `documenso-backup/Dockerfile`
(`pg_restore` must not be older than the server that wrote the archive):

```
image=$(sed -n 's/^FROM //p' projects/hub/documenso-backup/Dockerfile)
docker run -d --name documenso-scratch -e POSTGRES_USER=documenso -e POSTGRES_PASSWORD=scratch \
  -e POSTGRES_DB=documenso "$image"
until docker exec documenso-scratch pg_isready -h 127.0.0.1 -U documenso -d documenso; do sleep 1; done
docker exec -i documenso-scratch pg_restore --exit-on-error --no-owner --no-acl \
  -U documenso -d documenso < /dev/shm/documenso-restore/documenso.dump
```

Pass condition: **the contracts came back**. Compare with what production says today (the
host: `docker exec rebase-documenso-db-1 psql -U documenso -d documenso -Atc '...'`):

```
docker exec documenso-scratch psql -U documenso -d documenso -Atc 'SELECT count(*) FROM "Envelope"'
docker exec documenso-scratch psql -U documenso -d documenso -Atc 'SELECT count(*) FROM "DocumentData"'
docker exec documenso-scratch psql -U documenso -d documenso -Atc 'SELECT migration_name FROM _prisma_migrations ORDER BY finished_at DESC NULLS LAST, migration_name DESC LIMIT 1'
```

A backup is a day behind production, so the counts match the day it was taken, and the last
migration is the one the `MANIFEST` names. Then `docker rm -f documenso-scratch`, and remove
`/dev/shm/documenso-restore` and the key you used.

**Without any file at all**, so the contracts never touch a disk on the machine you are
sitting at (the way to prove a production object: the scratch container is on the host,
the key on the machine you are at; start the container and wait for it as above, on the
host):

```
openssl cms -decrypt -binary -inform DER -in documenso-<stamp>.tar.cms \
    -inkey vaultwarden-backup.key -recip vaultwarden-backup.crt \
  | tar -xOf - documenso.dump \
  | ssh <host> 'docker exec -i documenso-scratch pg_restore --exit-on-error --no-owner --no-acl -U documenso -d documenso'
```

(`-recip` is needed only when the envelope has several recipients; `restore.sh` is the same
command with the MANIFEST check.)

## 4. Recovery: the real database

Only when the live database is gone or ruined. Documenso's own container is the only writer,
so the order is:

1. Stop it: `docker stop rebase-documenso-1`. Never delete the old data directory
   (`REBASE_DOCUMENSO_DATA_DIR`, `/srv/rebase-data/documenso-postgres`) before a person has
   read the restored site; move it aside.
2. On a host where the database is empty (the same data directory emptied, or a new host
   after the steps below), load the archive from the machine that holds the key, as the
   stream above does but into `rebase-documenso-db-1` and with `--clean --if-exists` when
   tables already exist. The dump is made `--no-owner`, so it loads as the `documenso` role.
3. Start Documenso: `docker start rebase-documenso-1`, or, on a new host, the production
   tag's deploy, which brings up the whole project. Documenso applies any migration newer
   than the archive's at its first start, which is the same thing a deploy of a newer image
   does to an older database.
4. Open `https://firma.letsrebase.com/signin`, sign in as a Documenso user, open one sealed
   envelope and download its PDF. Ask the hub for the same match's signed copy
   (`signed_pdf`): the hub keeps its own copy of every contract, which is the second check.
   What was created or signed on Documenso after the backup is lost, up to a day. The hub
   still holds its own record of each match, so list the matches sent for signature since
   the stamp in the `MANIFEST` and send those again.

**A new host** also needs, before the tag: the `hub-production` Environment secrets
(`DEPLOY_*` and the three below), `/opt/hub/.env` as `projects/hub/.env.example` describes,
and `firma.letsrebase.com` pointed at it (`infra/cloudflare/rebase.tf`).

## What the dump does not hold

Documenso's own settings are in `/opt/hub/.env` on the host, not in its database:
`DOCUMENSO_ENCRYPTION_KEY` and `DOCUMENSO_ENCRYPTION_SECONDARY_KEY` (the rollout's plan
records that they can never change once Documenso has stored anything with them),
`DOCUMENSO_NEXTAUTH_SECRET`, and the seal: `DOCUMENSO_SIGNING_CERT_BASE64` and its
passphrase `DOCUMENSO_SIGNING_PASSPHRASE`. **They are in no backup**: the only other
copies are the dated `hub-env-*` files under `/root/backups` on the host, on the same disk
as the data. A recovery on a new host without them has a database it cannot fully read and
no certificate to seal the next contract with. They are secrets, so they do not go into
this archive unasked; the person who holds the backup key keeps them in the Rebase
organisation's `Infrastructure` collection of the password manager (a step for the owner,
tracked on REB-715).

## Provisioning, once

1. **The certificate**: `DOCUMENSO_BACKUP_CERT_B64` in `/opt/hub/.env` is the line of
   `VAULTWARDEN_BACKUP_CERT_B64` (a public certificate, one line of base64). To let a second
   person restore without the password manager's key, concatenate their certificate before
   the base64 (`cat a.crt b.crt | base64 -w0`).
2. **The bucket**: `DOCUMENSO_BACKUP_R2_ACCOUNT_ID` and `DOCUMENSO_BACKUP_BUCKET` in
   `/opt/hub/.env`; the `documenso/` expiry rule is `infra/cloudflare/r2.tf`, applied with
   `terraform apply` as `infra/cloudflare/README.md` says.
3. **The R2 key pair and the monitor's URL**: `DOCUMENSO_BACKUP_ACCESS_KEY_ID`,
   `DOCUMENSO_BACKUP_SECRET_ACCESS_KEY` and `DOCUMENSO_BACKUP_PING_URL` are secrets of the
   `hub-production` GitHub Environment; the deploy writes them into `/opt/hub/.env`
   (`env-secrets` in `deploy-hub.yml`), and the compose file refuses to start without them.
   The check is on healthchecks.io under `ciao@letsrebase.com`: «Documenso backup», period
   one day, grace two hours, mailing `ciao@letsrebase.com` and the owner; its ping URL, as
   healthchecks.io shows it (no trailing slash), is the secret.
4. **Release**: the next `hub-v*` tag starts the service; its first backup is a minute after
   the container starts.
