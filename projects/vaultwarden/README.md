# Vaultwarden

The password manager of the rebase members: one organisation, `Rebase`, with a collection
per set of credentials, assigned to named members with a permission each. Why Vaultwarden
and not something else is `docs/design/DECISIONS.md`, 2026-10-05 (REB-644); this directory
is how it runs (REB-648). The members' own page is `docs/guida-per-i-membri.md`, in Italian.

```
docker-compose.yml          the server and its backup service, image pinned by tag and digest
.env.example                every host variable, and which ones the deploy writes for you
deploy/vault.letsrebase.conf  the host's nginx vhost
scripts/backup.sh           one encrypted backup, to R2    scripts/backup-loop.sh  its schedule
scripts/restore.sh          open one backup into an empty directory
docs/restore.md             the restore runbook
docs/guida-per-i-membri.md  the member guide (Italian)
tests/                      the drill: config, backup and restore, on throwaway servers
```

## What was decided, and where it lives

| Question | Answer |
|---|---|
| Host | The production host that runs the site, the hub, the CRM and Documenso, in a compose project of its own (`vaultwarden`), the Documenso pattern (DECISIONS.md, 2026-10-06). It is one secret, `DEPLOY_HOST` of the `vaultwarden-production` Environment, plus the DNS record. |
| Name | `vault.letsrebase.com`, a certificate of its own (`certbot --nginx -d vault.letsrebase.com`, never `--expand`). |
| Database | SQLite, in `VAULTWARDEN_DATA_DIR` on the host, outside the repository. Vaultwarden's default, a dozen members, and a file the backup can snapshot with the server's own command. Postgres would add a second container to back up for no member-visible gain. |
| Mail | Resend's SMTP endpoint (`smtp.resend.com`, 587 with STARTTLS, username `resend`, the password is an API key), sender `vault@letsrebase.com`. The hub mails its magic links through Resend's HTTP API, which Vaultwarden cannot speak, so this is a Resend API key of its own, restricted to sending, as the Environment secret `VAULTWARDEN_SMTP_PASSWORD`. |
| Backup | Daily, encrypted to a public certificate on the host, into a private R2 bucket (`rebase-vaultwarden-backup`, `infra/cloudflare/r2.tf`). The private key is not on any server. |
| Release | Tag `vaultwarden-v<semver>`, through `.github/workflows/deploy-vaultwarden.yml` and `_deploy-compose.yml`. No preview. Never `docker compose` by hand. |
| Image | `vaultwarden/server:1.37.4@sha256:efb3cde9...`, 2026-10-05, the release that fixes the advisories of that week. Upgrade within days of any release that carries a security fix: change the tag and the digest together, read the notes, tag. |

## Provisioning, once (a person with the keys does this; nothing here is automated away)

1. **DNS and the bucket**: done on 2026-10-06 (`terraform apply` in `infra/cloudflare`, README
   there): `vault.letsrebase.com` and the R2 bucket with its ninety-day expiry exist, and
   `rebase-imports.tf` and `r2-imports.tf` carry their import blocks, so a checkout without
   that state rebuilds it (a `plan` shows both as imported and nothing as changed). The
   expiry rule, `cloudflare_r2_bucket_lifecycle`, cannot be imported in provider 5.25.0:
   such a checkout plans to create it, which sets the same rule on the bucket again.
2. **The backup key pair**, on a machine that is not the host:

   ```
   openssl req -x509 -newkey rsa:4096 -nodes -days 36500 -subj '/CN=rebase vaultwarden backup' \
     -keyout vaultwarden-backup.key -out vaultwarden-backup.crt
   base64 -w0 < vaultwarden-backup.crt        # goes in the host .env as VAULTWARDEN_BACKUP_CERT_B64
   ```

   Keep `vaultwarden-backup.key` in two places that are offline or encrypted and on no
   server: not Vaultwarden, not the host, not a machine that holds the R2 key (an
   encrypted offline drive in a safe, and the owner's own encrypted store, are the shape).
   A backup you can only open with a password stored inside the thing you lost is no
   backup, and a key on a server that can also read the bucket is no encryption. The
   certificate alone cannot decrypt anything.
3. **R2 access key**: Cloudflare dashboard, R2, Manage API tokens, Object Read and Write,
   scoped to `rebase-vaultwarden-backup` only. The key pair is two Environment secrets.
4. **Resend**: a new API key, sending access only, domain `letsrebase.com`. It is an
   Environment secret.
5. **The host**: a data directory (`/srv/rebase-data/vaultwarden`, owned by root, mode
   0700), `${DEPLOY_PATH}/.env` from `.env.example` (the non-secret lines, among them
   `VAULTWARDEN_ORG_CREATORS`, the owner's address: only it may create an organisation), the vhost
   copied to `/etc/nginx/sites-available/`, enabled, `nginx -t`, reloaded, then
   `certbot --nginx --redirect -d vault.letsrebase.com`, then `Strict-Transport-Security`
   added to the installed 443 block by hand (the vhost's own header comment says why).
6. **GitHub**: the Environment `vaultwarden-production` with the five `DEPLOY_*` secrets
   (`DEPLOY_KNOWN_HOSTS` captured as `docs/adding-a-project.md` §7 says) and the three
   secrets above, then the variable `VAULTWARDEN_DEPLOY_ENABLED=true`.
7. **The release**: `git tag vaultwarden-v0.1.0 && git push origin vaultwarden-v0.1.0`,
   after the PR is merged and Lorenzo says so. The tag's CI run, the deploy run and
   `https://vault.letsrebase.com/alive` answering are the evidence.

## First run: the first account, the organisation, the admin page closed

Registration is closed, so the very first account is invited from the admin page, which
is off. This is the one time it is opened.

1. On any machine with Docker, make the hash of a long random token:
   `docker run --rm -it vaultwarden/server:1.37.4@sha256:efb3cde962015fcc036b2ea625242248611943b27212ebf4392841fb30fad055 /vaultwarden hash`
   (the image `docker-compose.yml` pins when you read this; type the token and keep it in a
   safe place, only the hash goes to the host).
2. On the host, add `VAULTWARDEN_ADMIN_TOKEN='<the hash>'` to `${DEPLOY_PATH}/.env`, in
   single quotes, and re-run the last deploy (`gh run rerun <run-id>` on the tag's deploy
   run: it syncs, finds the changed environment and recreates the container; nobody runs
   `docker compose` by hand).
3. Open `https://vault.letsrebase.com/admin`, enter the token, **Users, Invite User**,
   the owner's address. The mail arrives from `vault@letsrebase.com`; the owner opens the
   link, picks a master password and registers.
4. **Close the page**: delete the `VAULTWARDEN_ADMIN_TOKEN` line, re-run the deploy, and
   check `https://vault.letsrebase.com/admin` says "The admin panel is disabled".
5. The owner creates the organisation `Rebase`, sets the policies (below), and invites
   every other member from the organisation's Members page. An invitation to an address
   with no account works with `INVITATIONS_ALLOWED`, and the member follows the mailed
   link; nobody needs the admin page again.

**The admin page stays closed**, because a setting saved there is written to `config.json`
in the data directory and overrides the compose file for ever after, so the running
configuration would stop being the one in the repository. Anything it can do that a
member cannot (delete a user, read diagnostics) is the same procedure: open it for the
minutes it takes, close it, and the change of `.env` is the record. If something was saved
there, `config.json` holds it and the backup carries it.

### Policies and permissions (checked live, by a person, after the first release)

- Organisation, Settings, Policies: **account recovery administration** on, with automatic
  enrolment, so a member who forgets the master password can be recovered. Needs SMTP.
  A member who is not enrolled and forgets it loses their personal items for good; the
  member guide says so. Two-step login enforcement for the organisation is a decision for
  the owner.
- Each credential set is a **collection** assigned to named members, never to everyone:
  view, view with hidden passwords, edit, edit with hidden passwords, or manage.
  Organisation owners and admins can open every collection, so those roles stay with the
  few people trusted with all of it. `ORG_GROUPS_ENABLED` stays off.

## Backup

`backup` (a service of the same compose project, same image) runs `scripts/backup-loop.sh`:
one backup a minute after the container starts, then one a day at `VAULTWARDEN_BACKUP_AT`
(03:30 UTC). Each run:

1. takes a consistent snapshot with the server's own `vaultwarden backup` (SQLite's
   `VACUUM INTO`, safe on a running server), and removes it afterwards;
2. bundles it with `attachments/`, `sends/`, `config.json` and `rsa_key*`;
3. encrypts the bundle as a CMS envelope to the certificate in
   `VAULTWARDEN_BACKUP_CERT_B64`, so nothing on the host or in the bucket can open it;
4. uploads it to `vaultwarden/vaultwarden-<UTC stamp>.tar.gz.cms` in the bucket with a
   Signature V4 `curl`, and asks the bucket for the object's size before calling it done.

R2 deletes objects after ninety days (`infra/cloudflare/r2.tf`). `docker compose -p
vaultwarden ps` on the host shows the backup container `unhealthy` when no backup has
finished in the last 26 hours; `docker compose -p vaultwarden logs backup` says why. There
is no alert beyond that (a follow-up card). **The restore is the proof of a backup**:
`docs/restore.md`, to be done once on a scratch container before anything real is stored,
and again whenever the scripts or the image change.

## The drill

`tests/backup-restore.sh` runs the real compose file and the real scripts on throwaway
servers (an S3 gateway stands in for R2): it registers an account and puts an item in it,
then proves the server answers on `127.0.0.1` only, refuses a signup without an
invitation and has the admin page off; takes a backup into the bucket, checks the object is
an encrypted envelope holding none of the plain text; restores it into a new directory
with `scripts/restore.sh` and reads the item back from a scratch server. CI runs it on every
change to this directory and on every `vaultwarden-v*` tag. Locally, from the repository
root: `projects/vaultwarden/tests/backup-restore.sh` (Docker, node 22 and openssl).
