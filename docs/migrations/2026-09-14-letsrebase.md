# 2026-09-14: joinorbiters.com becomes letsrebase.com

On 2026-09-14 Ivan decided that Orbiters becomes **Rebase** and that every public name
of the platform moves from `joinorbiters.com` to `letsrebase.com` (ORB-193). This page
is the record of the migration and the runbook for whoever has to read it back at 2am.
The brand in copy, logos, the deck and the guide is follow-up work with cards of its
own; this page is about names, mail and the host.

**The old names were retired on 2026-10-20 (REB-670)**: see the last section. Everything
below says what was true from 2026-09-14 until then, and is kept as the record.

## The names

| Old | New | What answers |
|---|---|---|
| `joinorbiters.com` | `letsrebase.com` | the website (`projects/website`, port 8082) and the hub at `/hub/` (8084, 8085) |
| `www.joinorbiters.com` | `www.letsrebase.com` | a 301 to the apex, in nginx |
| `pigro.joinorbiters.com` | `pigro.letsrebase.com` | PigroCRM (8080) |
| `preview.joinorbiters.com` | `preview.letsrebase.com` | the website and hub previews (8083, 8086, 8087) |
| `preview.pigro.joinorbiters.com` | `preview.pigro.letsrebase.com` | the CRM preview (8081) |

Nothing changed on the containers, the ports or the databases: the host is the same
Hetzner machine (`204.168.255.175`), the stacks are the same, and the old and the new
names are two doors into the same rooms.

## DNS: the `letsrebase.com` zone

The zone is on Cloudflare, in Lorenzo's account, with the registrar already delegated to
`rachel.ns.cloudflare.com` and `ray.ns.cloudflare.com`. It was empty; it now mirrors
`joinorbiters.com` record for record, all **DNS only** (grey cloud), because the origin
terminates TLS itself with Let's Encrypt and a proxied record would put Cloudflare's
certificate in front of it.

| Type | Name | Content | Why |
|---|---|---|---|
| A | `letsrebase.com` | `204.168.255.175` | the origin |
| A | `pigro.letsrebase.com` | `204.168.255.175` | |
| A | `preview.letsrebase.com` | `204.168.255.175` | |
| A | `preview.pigro.letsrebase.com` | `204.168.255.175` | |
| CNAME | `www.letsrebase.com` | `letsrebase.com` | nginx sends `www` to the apex |
| TXT | `_dmarc.letsrebase.com` | `v=DMARC1; p=quarantine; adkim=r; aspf=r;` | as on the old zone, minus GoDaddy's `rua` |
| TXT | `resend._domainkey.letsrebase.com` | `p=MIGf…` | DKIM, the value Resend gave |
| MX | `send.letsrebase.com` | `10 feedback-smtp.eu-west-1.amazonses.com` | Resend's bounce address |
| TXT | `send.letsrebase.com` | `v=spf1 include:amazonses.com ~all` | SPF for the `send` subdomain |
| CNAME | `rsend.letsrebase.com` | `send.forge.rmta.net` | Resend asked for it; the old zone predates this record |

Two records of the old zone were **not** copied on purpose: `google-site-verification`
(the token is per property; Search Console has to be verified again for the new
domain) and `_domainconnect` (a GoDaddy artefact).

## Mail: Resend

`letsrebase.com` was added to the Resend account with the API (`POST /domains`, region
`eu-west-1`, id `87c95e21-0ff8-4338-97a6-b97685241bde`) and verified within a minute of
the records above being created. The API key did not change: it is the same one in
`/opt/hub/.env` and `/opt/pigrocrm/.env`, and it is not in this repository. The senders
are now `Rebase <ciao@letsrebase.com>` (hub) and `PigroCRM <ciao@letsrebase.com>` (CRM).
`joinorbiters.com` stays verified in Resend, so a container that has not been recreated
yet can still send; it can be removed from Resend once nothing names it.

## The host

Everything below was done by hand over ssh, because the host's nginx is not part of any
deploy (`docs/adding-a-project.md`). Backups: `/root/backups/nginx-2026-09-14-rebase/`
(the four vhosts as they were) and `/root/backups/env-2026-09-14-rebase/` (the four
`.env` files as they were).

1. **Vhosts.** Each `*.joinorbiters.conf` in `/etc/nginx/sites-available` was copied to
   the matching `*.letsrebase.conf` with the domain replaced and certbot's lines
   stripped, and enabled. The committed sources moved with them:
   `projects/website/deploy/letsrebase.conf`, `preview.letsrebase.conf`,
   `projects/pigrocrm/deploy/nginx/pigro.letsrebase.conf`, `preview.pigro.letsrebase.conf`.
2. **Certificates.** Two, mirroring the old pair, both obtained with `certbot --nginx
   --redirect`, which rewrote the vhosts in place the way it did the old ones:
   `letsrebase.com` (+ `www`, `pigro`) and `preview.letsrebase.com` (+ `preview.pigro`).
   Renewal is certbot's timer, as before.
3. **Environment files.** In `/opt/hub/.env`: `ORBITERS_SIGNUP_URL`, `ORBITERS_MAIL_FROM`,
   and, new, `ORBITERS_HUB_URL` and `ORBITERS_PIGRO_API_URL` (they had been the code's
   defaults). In `/opt/hub-preview/.env`: `ORBITERS_SIGNUP_URL`, `ORBITERS_HUB_URL`. In
   `/opt/pigrocrm/.env`: `PIGROCRM_PUBLIC_URL`, `PIGROCRM_ORBITERS_SIGNUP_URL`, and, new,
   `PIGROCRM_MAIL_FROM` and `PIGROCRM_HUB_URL`. In `/opt/pigrocrm-preview/.env`:
   `PIGROCRM_PUBLIC_URL`, `PIGROCRM_MAIL_FROM`, `PIGROCRM_HUB_URL`. A `.env` change is
   picked up by the next deploy, which recreates the containers.
4. **The old names redirect.** Once the releases below were live, the four
   `*.joinorbiters.conf` vhosts lost their `location` blocks and answer one line each:
   `return 301 https://<new name>$request_uri;` for the website names, `return 308` for
   the two CRM names, so that a `POST` to the API or to the MCP server keeps its method
   and body. The old zone's DNS keeps pointing at the origin: that is what makes the
   redirect reachable. It is not a Cloudflare rule because the token available for the
   old zone carries DNS permissions only; if one day it gets *Zone → Dynamic Redirect*,
   the same rule can move to the edge.

## The repository

Every `joinorbiters.com` that is configuration, a link or a test expectation became
`letsrebase.com` (80 files), including the compose defaults, the config defaults of both
Python packages, the deploy workflows' environment URLs, the analytics host lists, the
site's own pages and the pitch's QR code. Left as they were: the specs and plans under
`docs/superpowers/` (they describe a moment), the rows of `DECISIONS.md` before today,
the GitHub org (`joinorbiters/orbiters`, a rename of its own), the Linear workspace slug,
the LinkedIn page slug, and the guide PDF (a binary, rebuilt by
`projects/hub/tools/build_guide_pdf.py` in a follow-up). The org rename landed the next
day: the repository is `letsrebase/rebase` since 2026-09-15 (ORB-204).

## What is not done by this migration

- **Google OAuth.** PigroCRM's Gmail and Drive connections send Google a `redirect_uri`
  built from `PIGROCRM_PUBLIC_URL`; Google accepts only URIs registered on the OAuth
  client. Until `https://pigro.letsrebase.com/api/gmail/oauth/callback` and
  `https://pigro.letsrebase.com/api/drive/oauth/callback` (and every space's
  `/<slug>/api/...` variant) are added in the Google Cloud console, a *new* connection
  fails at Google's consent screen. Connections already made keep working: a refresh
  token does not depend on the redirect URI.
- **Sessions.** A session cookie is host-only. Whoever was logged in on a
  `joinorbiters.com` name logs in again on the `letsrebase.com` one.
- **Agents on the MCP server.** A client configured with
  `https://pigro.joinorbiters.com/<slug>/mcp` gets a 308; most do not follow it with the
  bearer token, so the URL in the client has to change.
- **Search Console, PostHog, LinkedIn, the brand in the copy**: cards of their own,
  linked from ORB-193. The GitHub org was one of them and is done: `letsrebase/rebase`
  since 2026-09-15 (ORB-204).

## Rolling back

*Superseded on 2026-10-20 (REB-670): the old vhosts and their certificates are gone, see the last
section. What follows is the rollback as it was until then.*

Put the four backed-up vhosts back in `/etc/nginx/sites-available`, `nginx -t &&
systemctl reload nginx`, restore the four `.env` files from the backup and redeploy the
last tag of each project. The `letsrebase.com` zone and certificates can stay: unused,
they cost nothing.

## Retired on 2026-10-20 (REB-670)

Lorenzo approved it on 2026-10-20 («si puoi togliere tutto»), after 14 days of the origin's
access log showed 7 hits on `/orbiters` (all Googlebot, all answered 301), none on
`/api/orbiters/signups`, and on the old CRM host scanners and link previews but no person
and no MCP client. What went:

- **The four redirect vhosts** (`joinorbiters.conf`, `preview.joinorbiters.conf`,
  `pigro.joinorbiters.conf`, `preview.pigro.joinorbiters.conf`): out of the repository
  (`projects/website/deploy/redirects/`, `projects/pigrocrm/deploy/nginx/redirects/`) and
  off the origin, with their two certbot certificates. The backup of the four files is
  `/root/retired-joinorbiters-2026-10-20/` on the origin.
- **The legacy paths**: `/orbiters` on the website (`deploy/nginx.conf`,
  `src/path-map-plugin.ts`, the NixOS module in `flake.nix`) and on both CRM names
  (`pigro.letsrebase.conf`, `preview.pigro.letsrebase.conf`), and `/api/orbiters/signups`
  in the two website host vhosts. They answer a plain 404 (the website) once the
  `website-v*` tag that carries this change ships; the CRM's `/orbiters` falls to the
  space-name rule like any unknown name.
- **The reserved slug**: `orbiters` is no longer in the CRM's `RESERVED_SLUGS`
  (`schemas.py`, `tenant.ts`). No space by that name existed in production or preview.
- **The zone's Terraform** (`infra/cloudflare/orbiters.tf`, `orbiters-imports.tf`, the
  second provider and its token variable).

Left on purpose: the domain registration (GoDaddy, to 2027-09-07), the hub's `orbiters_user`
cookie and `orbiters.*` storage-key fallbacks (REB-697, due 2026-11-18), and the domain's
verification in Resend, which nothing names any more and which is Ivan's to remove.

There is no rollback of the retirement by copying files back: the two certificates were
deleted, so putting the old names back means issuing new ones and a zone that still
points at the origin.
