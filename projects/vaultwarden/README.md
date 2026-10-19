# Vaultwarden

The password manager of the rebase members: one organisation, `Rebase`, with a collection
per set of credentials, assigned to named members with a permission each. Why Vaultwarden
and not something else is `docs/design/DECISIONS.md`, 2026-10-05 (REB-644); this directory
is how it runs (REB-648). The members' own page is `docs/guida-per-i-membri.md`, in Italian.

```
docker-compose.yml          the server and its backup service, image pinned by tag and digest
.env.example                every host variable, and which ones the deploy writes for you
deploy/vault.letsrebase.conf  the host's nginx vhost
branding/                   the look of the web vault, generated from shared/brand (see "Look")
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
- The collections, named in English like the apps (REB-691, `docs/design/DECISIONS.md`
  2026-10-14): `Infrastructure` and `Administration` for rebase's own credentials, assigned to
  no User today; `Clients/<client>` for what every project of a client shares, and
  `Clients/<client>/<project>` for one match, assigned to the freelancers on it. Nesting is a
  name and nothing else: a collection inherits no item, access or permission from its parent,
  so assigning `Clients/<client>/<project>` does not show `Clients/<client>`. A freelancer gets
  «View items»; «manage» stays with the owners. «View items, hidden passwords» is not a
  protection: it hides the eye and the copy button, and the decrypted password is still in
  the page (checked on a test member, 2026-10-14).

## Look

The web vault is upstream's, unmodified, wearing the rebase brand through three
mechanisms that survive an image bump because none of them edits the image (REB-688):

- **A stylesheet.** The server compiles `branding/templates/scss/user.vaultwarden.scss.hbs`
  (`TEMPLATES_FOLDER=/templates`, mounted read-only) and appends it to `/css/vaultwarden.css`,
  which every page loads. It sets the web vault's own colour properties (both the older RGB
  triplets and the newer hex ones, light and dark theme) to palette tokens, and replaces the
  inline Vaultwarden wordmark of the pre-login header and of the sidebar with `lockup.svg`
  without its graft (the dotted line under the word, cropped out of the picture here: in a
  sidebar it read as a stray rule, REB-692), with «password manager» under the word as text in
  the page's own face (`lockup-paper.svg` on a dark ground: the vault's sidebar and the dark theme; the Admin
  Console's sidebar is light in the light theme and takes the ink one; a collapsed sidebar
  takes the mark alone, without the label). The boot splash's logo takes the cropped lockup and no label.
- **Favicons**, mounted file by file over the image's own at the paths `index.html` and the
  manifest read (checked against the served page of 1.37.4).
- **`branding/branding.env`**, a hash of the files above, loaded as the server's `env_file`.
  Compose recreates a service when its configuration changes and not when a bind-mounted
  file's content does, and the server reads its templates once and caches the stylesheet, so
  without the hash a deploy of a new look would go green and change nothing.
- **Nothing in the data directory**, so the backup carries none of it and a restore onto a
  fresh host gets the look back from the repository.

Nothing here restates a colour or a drawing: `branding/build.mjs` reads `shared/brand/palette.css`
and the two lockups and writes the files, and `tests/branding.mjs` (run by the `vaultwarden`
job and by preflight) fails when the stylesheet or the pinned-tab icon is not what the brand
produces, when `branding.env` is not the hash of the files as committed, when an image is
missing or the wrong size, or when a colour pair is below AA (4.5:1) in either theme,
measured against the web vault's own grounds. It cannot tell a favicon drawn in last
month's palette from this month's: redraw them whenever the mark changes.
Change a colour in `palette.css`, then:

```
pnpm install --frozen-lockfile --filter @rebase/brand
pnpm --filter @rebase/brand exec node ../../projects/vaultwarden/branding/build.mjs
```

**The mails** (REB-690, REB-692) use the same mechanism: `branding/templates/email/` overrides upstream's
header and footer partials and the organisation invitation (in English, like every other mail
and the apps).
Upstream's partials carry the Vaultwarden wordmark and a GitHub icon as inline images, and the
server attaches both to every mail whatever the template uses. Ours draw the mark from table
cells and attach nothing (`SMTP_EMBED_IMAGES=false` in the compose file). Every mail says
«password manager» where a reader looks first: the sender's display name (`SMTP_FROM_NAME`,
`Rebase Password Manager`), a label under the wordmark, the footer, and in the invitation its
subject and first sentence. The other mails keep upstream's body
inside our frame, so the frame's footer says nothing about ignoring the message: some of those
bodies are security alerts. Render one after a bump with the admin page's SMTP test.

What is deliberately not themed: the browser extension and the mobile apps (their look is
Bitwarden's and is not served from here), the body of every mail but the invitation, the page title, the browser chrome
colours (`theme-color`, the manifest's colours and the pinned tab's tint, all still
Bitwarden's blue), the one-time welcome dialog and the illustrations other than the
pre-login pictures, and the attribution line in the footer, which says what this server is
and is left alone.

**After bumping the image**, check it by eye as well as by the drill: the stylesheet selects
the web vault's own elements (`bit-landing-header header a bit-svg`, `bit-nav-logo a bit-svg`,
`app-organization-layout`, a link's `!tw-h-[55px]` class when the sidebar is collapsed) and
sets its own custom properties, none of which upstream promises. The drill proves the
stylesheet, the lockup rules and every mounted image are served; a renamed element would
show only as the Vaultwarden wordmark coming back, so look at the login page, the vault and
the Admin Console, expanded and collapsed, after a bump.

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
finished in the last 26 hours; `docker compose -p vaultwarden logs backup` says why.

**The alarm is a healthchecks.io check** (REB-671), so nothing extra runs on the host. After
every run the loop pings `VAULTWARDEN_BACKUP_PING_URL`, the check's URL, or `<url>/fail`
when the run failed; the check mails on a failure, and when its period plus grace passes
with no success, which is what a revoked R2 key, a full disk or a container that is gone
look like. The URL is a secret, since whoever holds it can report a success: it lives in
the `vaultwarden-production` Environment, the deploy writes it into the host `.env`
(`env-secrets`), and the loop refuses to start without one. A ping that cannot get through
is logged and nothing more: the check going quiet is its own alarm. **The restore is the
proof of a backup**:
`docs/restore.md`, to be done once on a scratch container before anything real is stored,
and again whenever the scripts or the image change.

## The drill

`tests/backup-restore.sh` runs the real compose file and the real scripts on throwaway
servers (an S3 gateway stands in for R2, a small listener for healthchecks.io): it registers
an account and puts an item in it, then proves the server answers on `127.0.0.1` only,
refuses a signup without an invitation and has the admin page off; takes a backup into the
bucket, checks the object is an encrypted envelope holding none of the plain text; runs the
loop once with the right key and once with a revoked one and checks the monitor heard a
success and then a `/fail`; restores the backup into a new directory
with `scripts/restore.sh` and reads the item back from a scratch server. CI runs it on every
change to this directory and on every `vaultwarden-v*` tag. Locally, from the repository
root: `projects/vaultwarden/tests/backup-restore.sh` (Docker, node 22 and openssl).
