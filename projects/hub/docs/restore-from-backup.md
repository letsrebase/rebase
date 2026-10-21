# Restoring the hub's databases from the off-host backups

Two databases are backed up every night, and this page restores either: the **hub's own**
(`rebase-db-1`: the members, the companies, the matches, the contracts and their signed
copies, REB-717) and **Documenso's** (`rebase-documenso-db-1`: every uploaded and sealed
PDF, REB-715). It is for two situations, which differ only in what you do with the result: a
**drill** (prove a backup opens and its data comes back, on a scratch container, nothing
live touched) and a **recovery** (the host, its disk or a database is gone). The drill is the
proof the backup works, and it is done after any change to `db-backup/` or to the databases'
image. `db-backup/tests/backup-restore.sh` is the same sequence on throwaway servers, run in
CI; this page is the one with the real bucket.

What is backed up, and how: two services of `docker-compose.documenso.yml` (production's
overlay), `hub-backup` and `documenso-backup`, run the same script (`db-backup/scripts/backup.sh`)
against their database every night, and once after each start:

| | `hub-backup` | `documenso-backup` |
|---|---|---|
| Database | `rebase-db-1`, database `rebase` | `rebase-documenso-db-1`, database `documenso` |
| Runs at (UTC) | 03:15 | 03:45 |
| Object in the bucket | `hub/hub-<UTC stamp>.tar.cms` | `documenso/documenso-<UTC stamp>.tar.cms` |
| Archive inside | `hub.dump` | `documenso.dump` |
| Schema version in the `MANIFEST` | the Alembic revision (`alembic_version`) | the newest Prisma migration |
| Monitor (healthchecks.io) | «Hub database backup» | «Documenso backup» |

The bundle is the `pg_dump` archive and a `MANIFEST` (when it was taken, which Postgres wrote
it, the schema version, the archive's SHA-256), encrypted as a CMS envelope to the public
certificate in `HUB_BACKUP_CERT_B64` and put in the private R2 bucket `rebase-hub-backup`.
R2 deletes objects under either prefix after ninety days (`infra/cloudflare/r2.tf`).

You need: the private key that belongs to a certificate in `HUB_BACKUP_CERT_B64`, an R2
access key pair with Object Read on the bucket (the backups' own will do), the Cloudflare
account id, and a machine with Docker, `openssl` and the `aws` CLI. **The certificate is
Vaultwarden's** (decision recorded in `docs/design/DECISIONS.md`, 2026-10-20): the same line
as `VAULTWARDEN_BACKUP_CERT_B64`, so the key is `vaultwarden-backup.key`, kept as
`projects/vaultwarden/README.md` § Provisioning says, and nothing new has to be guarded. Run
every step on a machine that is not the host: the host holds certificates, never a key.

Below, `<name>` is `hub` or `documenso`, and `<db>` is `rebase` or `documenso` (the database
and its role have the same name in both).

## 1. Find and fetch the newest object

```
export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  AWS_DEFAULT_REGION=auto
endpoint=https://<account-id>.r2.cloudflarestorage.com
aws --endpoint-url "$endpoint" s3 ls s3://rebase-hub-backup/<name>/
aws --endpoint-url "$endpoint" s3 cp s3://rebase-hub-backup/<name>/<name>-<stamp>.tar.cms .
```

The bucket is `HUB_BACKUP_BUCKET` in `/opt/hub/.env` on the host. The names sort by time, so
the last line is the newest. If the newest is older than a day, the backup has been failing:
the healthchecks.io check should have mailed, and `cd /opt/hub/projects/hub && docker compose
-p rebase --env-file /opt/hub/.env logs <name>-backup` says why (`docker compose ps` shows
the service `unhealthy` after 26 hours).

## 2. Open it, into a directory that does not exist yet

```
projects/hub/db-backup/scripts/restore.sh <name>-<stamp>.tar.cms vaultwarden-backup.key /dev/shm/<name>-restore
```

It prints the `MANIFEST`, checks the archive against the SHA-256 recorded in it, and refuses a
target that is not empty. The directory then holds `<name>.dump` and `MANIFEST`: the whole
database, in clear. Put it on an encrypted disk or tmpfs and remove it when you are done.
When the backup was made for several certificates (a `HUB_BACKUP_CERT_B64` with more than
one), give the certificate that belongs to your key as a fourth argument; openssl cannot find
your recipient from the key alone.

## 3. Drill: a scratch Postgres on it

Use the Postgres image the backup was made with, the base of `db-backup/Dockerfile`
(`pg_restore` must not be older than the server that wrote the archive):

```
image=$(sed -n 's/^FROM //p' projects/hub/db-backup/Dockerfile)
docker run -d --name scratch -e POSTGRES_USER=<db> -e POSTGRES_PASSWORD=scratch \
  -e POSTGRES_DB=<db> "$image"
until docker exec scratch pg_isready -h 127.0.0.1 -U <db> -d <db>; do sleep 1; done
docker exec -i scratch pg_restore --exit-on-error --no-owner --no-acl \
  -U <db> -d <db> < /dev/shm/<name>-restore/<name>.dump
```

Pass condition: **the data came back**, and it matches what production says today, up to
the day since the backup. `db-backup/scripts/row-counts.sql` prints one `table|rows` line
per table, so no table is forgotten and a table a migration added is compared without
editing anything. Run it on both sides and diff:

```
# the restore
docker exec -i scratch psql -U <db> -d <db> -At -f - < projects/hub/db-backup/scripts/row-counts.sql > restored.txt
# production, on the host: container `rebase-db-1` for the hub, `rebase-documenso-db-1` for Documenso
ssh <host> 'docker exec -i <container> psql -U <db> -d <db> -At -f -' < projects/hub/db-backup/scripts/row-counts.sql > live.txt
diff restored.txt live.txt
```

A backup is up to a day behind production, so counts that moved since 03:15 (03:45) differ
by the rows written since; everything else is equal, and the schema version is the one the
`MANIFEST` names:

```
# the hub: the Alembic revision
docker exec scratch psql -U rebase -d rebase -Atc 'SELECT version_num FROM alembic_version'
# Documenso: the newest Prisma migration
docker exec scratch psql -U documenso -d documenso -Atc 'SELECT migration_name FROM _prisma_migrations ORDER BY finished_at DESC NULLS LAST, migration_name DESC LIMIT 1'
```

Then `docker rm -f scratch`, and remove `/dev/shm/<name>-restore` and the key you used.

**Without any file at all**, so the data never touches a disk on the machine you are sitting
at (the way to prove a production object: the scratch container is on the host, the key on the
machine you are at; start the container and wait for it as above, on the host):

```
openssl cms -decrypt -binary -inform DER -in <name>-<stamp>.tar.cms \
    -inkey vaultwarden-backup.key -recip vaultwarden-backup.crt \
  | tar -xOf - <name>.dump \
  | ssh <host> 'docker exec -i scratch pg_restore --exit-on-error --no-owner --no-acl -U <db> -d <db>'
```

(`-recip` is needed only when the envelope has several recipients; `restore.sh` is the same
command with the MANIFEST check.)

## 4. Recovery: the real database

Only when the live database is gone or ruined. Never delete the old data directory before a
person has read the restored site; move it aside.

**The hub** (`/srv/rebase-data/postgres`, container `rebase-db-1`). Five containers write to
it, so stop them first: `docker stop rebase-api-1 rebase-mcp-1 rebase-campaigns-1
rebase-sweep-1 rebase-cards-1`. On a host where the database is empty (the data directory
emptied, or a new host after the steps below), stream the archive from the machine that holds
the key, as above but into `rebase-db-1` (`-U rebase -d rebase`, and `--clean --if-exists`
when tables already exist). Then start the five again: the api runs `alembic upgrade head` at
its start, which applies any migration newer than the archive's, the same thing a deploy of a
newer image does to an older database. Open `https://letsrebase.com/hub/` and sign in to the
admin area, open one match and one company, and check `/health` on both ports. What was
written after the backup is lost, up to a day: new sign-ups, edits, campaign sends and their
state. Resend's own log says which mails went out since the stamp in the `MANIFEST`, and
Documenso's own records say which contracts were signed.

**Documenso** (`/srv/rebase-data/documenso-postgres`, container `rebase-documenso-db-1`). Its
own container is the only writer: stop it with `docker stop rebase-documenso-1`, load the
archive into `rebase-documenso-db-1` the same way (`-U documenso -d documenso`), then
`docker start rebase-documenso-1`, or, on a new host, the production tag's deploy, which
brings up the whole project. Documenso applies any migration newer than the archive's at its
first start. Open `https://firma.letsrebase.com/signin`, sign in as a Documenso user, open one
sealed envelope and download its PDF. Ask the hub for the same match's signed copy
(`signed_pdf`): the hub keeps its own copy of every contract, which is the second check. What
was created or signed on Documenso after the backup is lost, up to a day. The hub still holds
its own record of each match, so list the matches sent for signature since the stamp in the
`MANIFEST` and send those again.

**A new host** also needs, before the tag: the `hub-production` Environment secrets (`DEPLOY_*`
and the four below), `/opt/hub/.env` as `projects/hub/.env.example` describes, and
`letsrebase.com` and `firma.letsrebase.com` pointed at it (`infra/cloudflare/rebase.tf`).

## What the dumps do not hold

The databases hold data; the settings are in `/opt/hub/.env` on the host. For Documenso that
is `DOCUMENSO_ENCRYPTION_KEY` and `DOCUMENSO_ENCRYPTION_SECONDARY_KEY` (the rollout's plan
records that they can never change once Documenso has stored anything with them),
`DOCUMENSO_NEXTAUTH_SECRET`, and the seal: `DOCUMENSO_SIGNING_CERT_BASE64` and its passphrase
`DOCUMENSO_SIGNING_PASSPHRASE`. For the hub it is the database password, the Resend, Anthropic
and Documenso API keys, and the webhook secrets. **They are in neither dump**: the only other
copies are the dated `hub-env-*` files under `/root/backups` on the same disk as the data, and, for Documenso's, one item of
the password manager: «Documenso production secrets (firma.letsrebase.com)» in the
`Infrastructure` collection of the Rebase organisation, with the five `.env` values as
hidden fields and `DOCUMENSO_SIGNING_CERT_BASE64` in the note (REB-716). Without them a
recovery on a new host has a Documenso database it cannot fully read and no certificate to
seal the next contract with. The hub's own settings have no such copy: a recovery of the hub
on a new host needs its integrations (Resend, Anthropic, Documenso, the webhooks)
reissued. They are secrets, so they do not go into these archives unasked; the person who
holds the backup key is the one who keeps the password manager item current.

## Provisioning, once

1. **The certificate**: `HUB_BACKUP_CERT_B64` in `/opt/hub/.env` is the line of
   `VAULTWARDEN_BACKUP_CERT_B64` (a public certificate, one line of base64). To let a second
   person restore without the password manager's key, concatenate their certificate before
   the base64 (`cat a.crt b.crt | base64 -w0`).
2. **The bucket**: `HUB_BACKUP_R2_ACCOUNT_ID` and `HUB_BACKUP_BUCKET` in `/opt/hub/.env`;
   the bucket and the two `hub/` and `documenso/` ninety-day expiry rules are
   `infra/cloudflare/r2.tf`, applied with `terraform apply` as `infra/cloudflare/README.md`
   says.
3. **The R2 key pair and the monitors' URLs**: `HUB_BACKUP_ACCESS_KEY_ID` and
   `HUB_BACKUP_SECRET_ACCESS_KEY` (Object Read and Write on `rebase-hub-backup` only), and
   `HUB_BACKUP_PING_URL` and `DOCUMENSO_BACKUP_PING_URL` are secrets of the `hub-production`
   GitHub Environment; the deploy writes them into `/opt/hub/.env` (`env-secrets` in
   `deploy-hub.yml`), and the compose file refuses to start without them. The checks are on
   healthchecks.io under `ciao@letsrebase.com`: «Hub database backup» and «Documenso
   backup», each with a period of one day and a grace of two hours, mailing
   `ciao@letsrebase.com` and the owner; a check's ping URL, as healthchecks.io shows it (no
   trailing slash), is its secret.
4. **Release**: the next `hub-v*` tag starts the services; each one's first backup is a
   minute after its container starts.
