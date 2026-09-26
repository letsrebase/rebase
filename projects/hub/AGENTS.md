# AGENTS.md: working on the rebase hub

The root [`AGENTS.md`](../../AGENTS.md) covers the monorepo. This file is only about
this project.

## What it is

rebase, the freelance community, as a product of its own: the signup list the
community site collects, the freelancer profiles and the company requests the hub's
wizards will collect, and the admin area that reads them. Its design record is
`docs/superpowers/specs/`, English, one document per step; read the 2026-09-09 spec
before changing the shape of anything. Since 2026-09-10 a freelancer can get back in
with a magic link by mail (`/hub/login`, `/hub/me`): spec
`docs/superpowers/specs/2026-09-10-member-area-design.md`.

## The one rule

**Nothing here imports PigroCRM, and PigroCRM imports nothing from here.** The hub was
split out of the CRM on 2026-09-09 precisely so the two can change independently: its
own settings (`REBASE_*`), its own Postgres, its own Alembic history, its own API and
MCP server. `ruff.toml` bans the three `pigrocrm*` module roots in every package. Two
products that need to agree on something agree through `shared/`.

## Layout

```
packages/core/   rebase_core: models, migrations, services, the ad conversion, the perk files, the contracts and their texts
apps/api/        rebase_api: FastAPI, one process, its own database
apps/mcp/        rebase_mcp: the same services over stdio or Streamable HTTP, for an admin with a token
apps/web/        pnpm package `hub`: the SPA at letsrebase.com/hub/ (wizards, the member area, admin)
content/         the prose a perk is made of, reviewed as prose, and the contracts' example data
tools/           the scripts that typeset that prose into PDFs
```

`packages/core` may import neither adapter, and neither adapter may import the other:
each directory's `ruff.toml` says so.

**The UI comes from `@rebase/ui`, and this application writes no primitive at all.**
The seven hand-written ones it used to carry were deleted when the package took over
(REB-300), so `apps/web/src/components/` holds only what is genuinely the hub's
(`BrandMark`, `Shell`) and everything else is imported from the package.
`src/styles/tokens.css` is the entry file that fixes the import order, plus the one
thing that is really local: the `.site` scope, where the chooser, the two wizards and
the thanks page keep the landing's own 2px line, 8px step and 7% grid, the step by
repointing `--shadow-app-*` and the line and the grid in that same block. There is no `components.json` here: a new primitive is generated in
`shared/ui` (`pnpm --filter @rebase/ui exec shadcn add <name>`).

## The MCP server is an admin's, by token

Since REB-213 every transport resolves a personal token (`rebase_core.admin_tokens`,
minted from «Agenti» or with `rebase createtoken`) to the admin behind it before a tool
runs, and `build_server` takes a callable answering who that is: the admin signs what the
tools write. Over HTTP the `mcp` compose service serves `rebase_mcp.http:app` on 8088
(preview 8089, `REBASE_MCP_PORT`), and the host vhost proxies `/api/hub/mcp` there; it
is a process of its own because `apps/api` may not import `apps/mcp`. Over stdio the token
is `REBASE_MCP_TOKEN`. Design record:
`docs/superpowers/specs/2026-09-15-mcp-for-admins-design.md`.

Every match and contract action is a tool too (REB-478): each runs the guard the admin
API runs, then the same core service, with the calling admin as the actor, and answers
`pdf_url`, never the PDF bytes, the tax identifiers or the client's budget. The guards
are the core's `require_live_match` and `require_live_document`, and `SigningService`
comes from the core's `signing_from_settings`, the one builder the API's `SigningDep`,
the MCP transports and `rebase contracts-sweep` share; the tests hand `FakeRenderer` and
a `SigningService` over `FakeDocumenso`.

- `preview_match`: step 3's sentences for the hub's proposal with the given fields; writes nothing.
- `create_match`: the draft match, «Salva senza inviare»; `match_id` makes a retry idempotent.
- `send_match_for_signature`: «Invia per la firma»; `messaggio` is the page's sentence.
- `resend_signing_mail`: «Reinvia email», for a document still waiting.
- `refresh_contract`: «Aggiorna stato», Documenso's word applied as the webhook would.
- `cancel_contract`: «Annulla» on a framework agreement not signed yet.
- `record_notice`: «Registra disdetta» on an active framework agreement.
- `cancel_match`: «Annulla» on a match in draft or in signature.
- `close_match`: «Chiudi match», the engagement ends.
- `set_freelancer_tax_data`: saves the tax data; answers that they are saved, never the values.

## The guide is a generated file, committed, and easy to leave stale

`content/guida-primi-passi-freelance.md` is typeset by `tools/build_guide_pdf.py`, with
pandoc and Typst, into `packages/core/src/rebase_core/perks/`, and the result is
**committed**. It is the one build output in git here, and the script's docstring says
why: the alternative puts those two binaries plus fontTools inside `Dockerfile.api` for
one document.

It is served by `GET /api/hub/me/guida`, which depends on `MeDep` and nothing else, so
the perk of being signed in is that the route answers at all (`MemberDep` until
REB-278 unified member and admin sign-in). There is no public URL for the file, and
the website links to the wizard instead (ORB-70).

What that costs is a file that can fall behind its sources, so after editing the
Markdown, the template, the palette or the typeface run
`uv run python projects/hub/tools/build_guide_pdf.py` and commit both the PDF and
`tools/guide-pdf.lock.json`. Forgetting is a failing test rather than a stale download:
`packages/core/tests/test_guide_pdf.py` compares every source's hash with that lock,
`apps/web/src/lib/perks.test.ts` compares the page count and size the member area shows,
and the `guide-pdf` preflight check rebuilds the bytes. Comparing bytes is meaningful
only because the build is reproducible on purpose: `--creation-timestamp 0` for Typst,
`recalcTimestamp=False` for the font instances.

## The contracts are typeset at request time

Since REB-387 the API writes the framework agreement and the letter of engagement itself,
with `rebase_core.contracts`: pandoc and Typst over the Markdown in
`packages/core/src/rebase_core/contracts/texts/`, the template beside it, and the
palette, the typeface and the echo logo read from `shared/brand/` at the paths the image
mirrors. So
`Dockerfile.api` carries PigroCRM's pandoc and Typst (`test_api_image.py` holds the two
images to one pair) and fontTools is a dependency of `rebase_core`. `rebase
contracts-check` typesets both texts from fiction and says whether a machine can; the
`hub-image` preflight check and CI's image job run it inside the built image. Who signs
for rebase comes from `REBASE_SIGNER_JSON` in the host `.env`, never from the repository.

## Contracts are signed on Documenso

Since REB-387 phase 3 «Invia per la firma» sends a match's documents through Documenso
(`rebase_core.signing`, `rebase_core.documenso`), and the hub mails the signing link
itself: Documenso sends no mail of its own. Documenso calls back
`POST /api/hub/documenso/webhook` with `X-Documenso-Secret` equal to
`REBASE_DOCUMENSO_WEBHOOK_SECRET`, which must be long and random (`openssl rand -hex
32`): the route sits public behind `/api/hub/` with no rate limit, and for a rejection
or a cancellation the secret is the only guard. A completion is not moved on the secret
alone (REB-431): the webhook only marks which document to confirm, and `finish` reads
the envelope back with the hub's own API token before the document counts as signed, so
a secret leaked to somebody who never held the token still cannot forge a signature.
Production's webhook points at
`http://api:8000/api/hub/documenso/webhook` inside the compose network, preview's at
`https://preview.letsrebase.com/api/hub/documenso/webhook`. Documenso retries a failed
delivery only at once, so an event lost while the API restarts is not lost for good: the
sweep (below) reads the envelope and applies it on its own, every ten minutes, and
«Aggiorna stato» on «Match e contratti» does the same thing at once, for an admin who
does not want to wait for the next sweep. Without
`REBASE_DOCUMENSO_URL` and `REBASE_DOCUMENSO_API_TOKEN` signing answers 503, and a text
whose front matter says `status: draft` never leaves unless `REBASE_CONTRACTS_ALLOW_DRAFT`
is true; only the preview's `.env` sets it, and even there it stays false while both
texts are `status: final`, ready again for the next draft. Documenso reaches `api` only if
`NEXT_PRIVATE_WEBHOOK_SSRF_BYPASS_HOSTS` lists it (probe § 5); the secret travels in
clear, so the webhook URL stays on the compose network or is HTTPS.

The webhook's own follow-up starts with that confirmation, then the sealed copy's
download and mails, a framework agreement's waiting letters, all in the background,
after the response: a restart caught mid-confirmation leaves a signed document plain
`inviato`, not even `firmato` yet, until the next sweep tries it again; caught later, it
leaves whichever step ran undone. `rebase contracts-sweep` redoes anything a restart, or
a mail the provider refused, left behind, and runs every ten minutes on production and
the preview alike, from the `sweep` service in `docker-compose.yml` (REB-393). Read what
it did with `docker logs rebase-sweep-1` (production) or `docker logs
rebase-preview-sweep-1` (preview): each run prints «N documenti ripresi», and, when
Documenso itself refused a confirmation or could not be reached (an expired, revoked or
wrong token among them, REB-431), «, M non confermati» on the same line.

## Campaigns

Since P-REB-41 «Invia una campagna» the `campaigns` service in `docker-compose.yml` runs
`rebase campaigns-tick` on a loop, every minute, on both stacks. Each pass is one call
of `run_tick` (`rebase_core.campaigns.tick`), inside a session advisory lock so two
passes never overlap: it takes every campaign whose `programmata_per` has come, and
every campaign an earlier pass left `in_invio`, and sends their queued recipients
through `campaign_sender_from_settings` (`rebase_core.campaigns.sender`), one mail at a
time and one mail a second, leaving Resend's other request a second to the magic link.
A loop, never a one-shot, for the same reason as `sweep`: `_deploy-compose.yml` fails a
deploy on any container that is not `running`. Without `REBASE_RESEND_API_KEY` the
command prints why and exits 0, so the loop keeps running and sends nothing -- the
preview carries no key. Read what it did with `docker logs rebase-campaigns-1`
(production) or `docker logs rebase-preview-campaigns-1` (preview): each run prints one
line, «N campagne, M inviate, S saltate, F fallite». `campagne` is how many due
campaigns this pass touched, and `inviate` and `saltate` are recipients this pass
actually sent to or skipped. `fallite` are rows this pass marked `fallita`: a mail
Resend refused outright (a 422, say), a row whose third attempt failed too (a timeout,
a 429 or a 5xx leaves the row `in_coda` for the next pass, with the same
`Idempotency-Key`), and a row whose checks or render raised, marked rather than let it
wedge every campaign after it (controller ruling R14). A 401 or 403 (a revoked or
restricted key, a domain Resend no longer sends for) marks nothing: the pass stops that
campaign with its rows still `in_coda` and logs «campaign <id> stopped this tick:
Resend 401», and the send resumes on the first pass after the key is fixed. No
address and no key ever appears in these lines or anywhere else in the logs.

**Scripts written for one campaign wave never live under `/opt/hub`.** The deploy syncs
the whole repository there with `rsync -az --delete` (`.github/workflows/
_deploy-compose.yml`), so anything dropped into the checkout by hand that is not in the
repository is gone on the next deploy: a one-off `outreach-r2` directory of scripts for
the September waves, placed under `/opt/hub` rather than committed or kept outside the
checkout, was wiped this way on 24/09. A wave's own scripts belong in the repository (if
they are worth keeping) or outside `/opt/hub` entirely, the same rule `REBASE_DATA_DIR`
follows for Postgres's own files.

**Four rules keep an address out of a campaign, and only one of them is an opt-out.**
`exclusions()` (`rebase_core.campaigns.audience`) runs when the list is shown, when it
is frozen and again right before each mail, and leaves out, with the reason on screen:
an admin, by `User.role == "admin"` (`REASON_ADMIN`, no row needed); an address with a
row in `campaign_optouts` -- `fonte='link'` for the recipient's own unsubscribe,
`'reclamo'` for a spam complaint Resend reports, `'admin'` for «Non scrivere mai»,
which is how the team goes in rather than relying on the role check; an address that
hard-bounced on any earlier campaign (`campaign_recipients.rimbalzata_at`, no optout
row); and an address another campaign reached in the last `REBASE_CAMPAIGN_GAP_DAYS`
(3), the gap rule that also turns several campaigns scheduled for the same minute into
one mail per person. Before each mail the tick also checks whether the action is
already done (`done_at`) and, for a list built from a state or from filters, whether
the person is still on it. That list is rebuilt once per pass, since it is a scan of
every card; an opt-out or a bounce recorded mid-pass keeps a queued mail from leaving.
Optouts are campaigns-only: the magic link, the welcome mail and the contracts flow
keep reaching an opted-out address, since none of those is a campaign. The unsubscribe
route (`POST /api/hub/campagne/disiscrizione`) has no rate limit on purpose: Gmail and
Yahoo send RFC 8058's one-click POST from a few server addresses, the token is 256
random bits and the write is idempotent.

`POST /api/hub/webhooks/resend` is that webhook: Resend signs every call with Svix, and
the route verifies the raw body against `REBASE_RESEND_WEBHOOK_SECRET` before parsing
it as JSON, then turns `email.delivered`, `email.bounced`, `email.clicked` and
`email.complained` into a row on `campaign_recipients`
(`rebase_core.campaigns.webhook`). Ivan sets it up once per environment:

1. In Resend, go to Webhooks, then «Add endpoint».
   - Production: `https://letsrebase.com/api/hub/webhooks/resend`.
   - Preview: `https://preview.letsrebase.com/api/hub/webhooks/resend`, only once the
     preview has a Resend key.
2. Select the events `email.delivered`, `email.bounced`, `email.clicked` and
   `email.complained`.
3. Copy the `whsec_…` value into that environment's `${DEPLOY_PATH}/.env` as
   `REBASE_RESEND_WEBHOOK_SECRET`.
4. Recreate the api container with the next deploy, or with
   `docker compose -p rebase --env-file ... up -d api` from `projects/hub`.
5. Verify the webhook: send yourself a test from «Nuova campagna», then open the
   endpoint in Resend's Webhooks page. Its delivery log must show `200` for the
   `email.delivered` of that test. A `401` means the secret in `.env` is not this
   endpoint's.

A key without the secret is refused on purpose: with `REBASE_RESEND_API_KEY` set and
`REBASE_RESEND_WEBHOOK_SECRET` empty, «Mandami una prova», «Invia» and «Programma»
answer 503 «Manca il webhook di Resend: configuralo prima di inviare, vedi AGENTS.md.»
before they touch the campaign, since a campaign sent that way would never learn about
its deliveries, bounces or complaints.

Resend's webhook covers every mail the team sends, magic links included. Those arrive
untagged and are acknowledged without effect. A tagged event whose recipient row is not
in this database answers 503, so that Resend retries it (for about a day) while the tick
commits the row. **So the preview must never get a key on production's Resend team**:
each environment would then 503 the other's tagged events, and Resend would retry every
one of them for a day. Give the preview its own Resend team, or no key at all.

## The team builder

Since milestone C of `docs/superpowers/specs/2026-09-25-team-builder-and-talent-cloud-design.md`
(REB-508 to REB-516) Claude reads every freelancer's CV once and writes an anonymous
card of it (`rebase_core.cards`), and `/hub/team` turns a project's description and
those cards into a team (`rebase_core.team_builder`), which a company files with
«Assumi team» and an admin works in «Richieste team». Both calls go through one seam,
`rebase_core.llm`, with inference in the European Union; the tests hand a
`RecordingCall` and never reach Anthropic.

**The privacy page goes out before the hub.** From the first card on, CVs go to
Anthropic, and it is letsrebase.com/privacy, in «La tua scheda e il team builder», that
tells the people they belong to. So on production the section is live first: the
`website-v*` tag that carries it, then the key in `.env`, then the `hub-v*` tag. The
other way round, CVs reach Anthropic while the privacy page still says nothing of it.

**The key** is `REBASE_ANTHROPIC_API_KEY`, in production's and the preview's
`${DEPLOY_PATH}/.env`, never in the repository; recreate `api` after setting it
(`docker compose -p rebase --env-file "${DEPLOY_PATH}/.env" up -d api` from
`projects/hub`, `-p rebase-preview` on the preview). Without it no card is written
and `POST /api/hub/team/proposals` answers 503 «Il team builder è spento.»; nothing
else of the hub changes. `REBASE_TEAM_BUILDER_ENABLED=false` is the switch that turns
the proposals off with the key still there: the same 503, while cards go on being
written and requests already filed stay in «Richieste team».

**The backlog is `rebase cards-refresh`.** A card is written after the wizard or a CV
upload, so the CVs already on file get theirs from `docker exec rebase-api-1 uv run
--no-sync rebase cards-refresh` (`rebase-preview-api-1` on the preview), run after the
deploy that brings the key (on production, once the talents' mail of spec § 4.4 has
gone out) and again until it prints «0 schede scritte, 0 non riuscite». Each run takes
50 CVs, the oldest first (`--limit`), and never a turned-down person's. A CV that
failed on its own account (a refusal, a scan with no text, a card that names the
person) is not tried again until it changes, so the runs end; an outage stops the
batch, counts under «non riuscite» and is the next run's, so a run that keeps printing
«0 schede scritte, 1 non riuscite» is Claude not answering, not a CV. «Rigenera
scheda» on the talent's page asks again for one.

**Two caps.** `REBASE_TEAM_BUILDER_CONCURRENCY` (4) is how many proposals run at once
in the API process: the next one answers 503 «Troppe richieste in questo momento:
riprova tra un minuto.» at once rather than wait on the threads the member area and the
webhooks share. `REBASE_TEAM_BUILDER_DAILY_CAP` (300) is how many proposals a day, the
public page and the cloud together, counted on `team_proposals` since midnight in Rome
(`rebase_core.team_caps`); once reached, the same 503 until the next day. It counts
proposals written: an admin's own and one with an empty catalogue (no call made) do
not count, and a call that failed is bounded by `spend_one` and the concurrency cap,
not by this one.

**`/api/hub/team/proposals` needs 90 seconds on the host vhost, and so does
`/api/hub/me/cloud/proposals`**, the same builder inside the talent cloud (REB-519). A
proposal holds the request while Claude writes, and the seam gives up after two
attempts of 40 seconds (`llm.py`, `_TIMEOUT_SECONDS` and `_MAX_RETRIES`), near 81
seconds; nginx's default of 60 would answer the visitor 504 while the proposal goes
on. The repository's copies of the host vhosts, `projects/website/deploy/letsrebase.conf`
and `preview.letsrebase.conf`, carry both locations beside `location ^~ /api/hub/`,
which an exact match outranks; the installed vhost in `/etc/nginx/sites-available/`
gets them by hand, as the MCP location did. Production's:

```
location = /api/hub/team/proposals {
    client_max_body_size 6M;
    proxy_pass http://127.0.0.1:8084;
    include /etc/nginx/snippets/orbiters-proxy.conf;
    proxy_read_timeout 90s;
}
location = /api/hub/me/cloud/proposals {
    client_max_body_size 6M;
    proxy_pass http://127.0.0.1:8084;
    include /etc/nginx/snippets/orbiters-proxy.conf;
    proxy_read_timeout 90s;
}
```

and the preview's the same on 127.0.0.1:8086; `nginx -t`, then `systemctl reload
nginx`.

## Running it

From the repository root:

```
uv sync --frozen
uv run pytest -q projects/hub/packages/core/tests projects/hub/apps/api/tests projects/hub/apps/mcp/tests
uv run --env-file projects/hub/.env uvicorn rebase_api.main:app --port 8010
```

The tests bring a `testcontainers` Postgres to `head` with this package's migrations,
never with `create_all`: a table the model declares and the migration forgets fails
here rather than on the server.

**A migration that renames or drops a table is also a change outside this repository.**
Six of these tables are read by PostHog's warehouse as `posthog_ro`, and a `GRANT`
follows a rename while a sync does not: `member_logins` became `logins` in migration
0012 and PostHog paused that sync nine days later, in an email.
`packages/core/tests/test_warehouse_contract.py` now fails on the pull request instead,
and names what to do in PostHog; the runbook is `docs/adding-a-project.md` § 7 and the
order of operations is the `posthog-analytics` skill.

**Its `vite preview` serves under `/hub/`, not `/`.** The web app is built with
`base: '/hub/'`, so the preview's root path 404s and the wizard pages are at `/hub/`,
`/hub/freelance` and `/hub/companies`. A blank page at `/` is that, not a broken build.
Unlike the website's, this preview has no `strictPort`, so a second checkout does not
collide with the first: it takes the next free port and logs it, confirmed live as `4174`
while another agent held 4173 on 2026-09-10. Read the port off its own output rather than
assuming 4173.

## The database was inherited

`signups` was created by PigroCRM's sidecar in production and holds real rows.
Migration 0001 adopts it as it stands (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT
EXISTS`, the unique index `IF NOT EXISTS`) so the first `alembic upgrade head` on the
copied database changes nothing and records 0001. Keep every migration that may run on
that database conditional in the same way until the copy is confirmed.

## Deploying

Through CI only, as every project here (`docs/adding-a-project.md` §7): preview on a
push to `main` that touched the hub, production on a tag `hub-v<semver>`, both by
`.github/workflows/deploy-hub.yml` calling `_deploy-compose.yml`. The production compose
project is `rebase`; the preview's is `rebase-preview`. Both were `orbiters` and
`orbiters-preview` until 2026-09-15, and each moves the day its environment is
migrated: the stack stopped, `/srv/<project>-data` moved, the database and role
renamed with `ALTER`. Pass `-p` to every `docker compose` you ever run against either
by hand, or compose names a second stack after the directory.

Each environment's `.env` is `${DEPLOY_PATH}/.env`, the root of that environment's
checkout and two levels above the compose file: the deploy passes
`--env-file "${DEPLOY_PATH}/.env"`, never rsyncs a `.env`, and reads nothing beside
the compose file. It holds the `REBASE_*` and `POSTGRES_*` values and is never in the
repository. `REBASE_DATA_DIR` has no default in the compose file, so a `.env` that
forgets it fails the stack instead of mounting an empty directory.

Ports, loopback only, from the table in `docs/adding-a-project.md` §7: production api
8084, web 8085, mcp 8088, Postgres 55435, Documenso 8090; preview 8086, 8087, 8089,
55436. The public paths are `/hub/` (web) and `/api/hub/` + `/api/orbiters/signups`
(api): `projects/website/deploy/letsrebase.conf` proxies them to production on the host
vhost, and `projects/website/deploy/preview.letsrebase.conf` proxies the same paths to
the preview stack at `preview.letsrebase.com` (`location ^~ /api/hub/`,
`preview.letsrebase.conf`, 127.0.0.1:8086 for its api) -- which is why the
preview's Documenso webhook, at `https://preview.letsrebase.com/api/hub/documenso/webhook`,
depends on that vhost rather than reaching the preview api directly. The member area's
mail needs `REBASE_RESEND_API_KEY` and `REBASE_MAIL_FROM` in the host `.env`; without
the key `/hub/login` answers 503 with a sentence. A preview stack that gets a key must
also set `REBASE_HUB_URL` to its own address, or every link it mints points at
production.

«Istanze Pigro» in the admin area (ORB-142) reads PigroCRM's registry of spaces through
the CRM's API, never its database: `REBASE_PIGRO_API_URL` (the CRM's public origin,
also where each space is linked) and `REBASE_PIGRO_REGISTRY_TOKEN`, which must equal
the `PIGROCRM_REGISTRY_TOKEN` in the CRM's own host `.env`. One value, set by hand in
both files, generated once; without it the page answers 503 with a sentence and the CRM
side does not even have the route. The call goes through `rebase_core.http`, the seam
the mail uses, so the tests hand a fake and never reach a CRM.

### Documenso, the signing site

Since REB-393 Documenso runs beside production in the `rebase` compose project, from
`docker-compose.documenso.yml`, which only production's `.env` loads with
`COMPOSE_FILE=docker-compose.yml:docker-compose.documenso.yml`. Compose interpolates
every service of every file it reads, profiles or not, so the Documenso services in
`docker-compose.yml` would fail the preview's deploy and CI's build on their secrets.
Anything you run by hand against production passes `-p rebase --env-file
"${DEPLOY_PATH}/.env"` from `projects/hub`, or compose sees neither file nor stack. The
webhook, its SSRF bypass and `REBASE_DOCUMENSO_*` are described above (§ "Contracts are
signed on Documenso"); this section is the container's own.

- **Image**: `documenso/documenso:v2.18.0`, pinned by digest, the one phase 1 probed. An
  upgrade is a pull request that moves the pin, after reading the release notes.
- **Rolling back past this release**: `COMPOSE_FILE=docker-compose.yml:docker-compose.documenso.yml`
  lives in `/opt/hub/.env` on the server, not in the checkout, so redeploying an older
  tag rsyncs `docker-compose.documenso.yml` away while `.env` still names it, and compose
  refuses to come up. Remove the `COMPOSE_FILE` line from `/opt/hub/.env` before rolling
  back past this release. `documenso` and `documenso-db` are not stopped by that: they
  keep running as orphans, unmanaged by the older compose project, until stopped by hand
  (`docker stop rebase-documenso-1 rebase-documenso-db-1`).
- **Name and port**: `https://firma.letsrebase.com`, the vhost `deploy/firma.letsrebase.conf`
  (its own certificate) in front of 127.0.0.1:8090.
- **Data**: its own Postgres, `documenso-db`, on `REBASE_DOCUMENSO_DATA_DIR`
  (`/srv/rebase-data/documenso-postgres`), with every uploaded and sealed PDF in it:
  nothing backs it up yet, nor the hub's own Postgres (REB-475). About 500 to 630 MiB of
  memory once warm, 45 to 80 MiB for its Postgres; the server has 3.8 GB and 4 GB of swap
  since 2026-09-25.
- **Certificate**: self-signed, made on the host with OpenSSL, in the `.env` as the
  `.p12` on one line of base64 with its passphrase. The seal is valid and PDF readers say
  its issuer is not trusted; a certificate on Adobe's trust list is a later purchase.
- **Mail**: Documenso sends only its own account mails, through Resend with the hub's
  key; every signing mail is the hub's.
- **Accounts**: public sign-up is off (`DOCUMENSO_DISABLE_SIGNUP` defaults to true). One
  Documenso user per environment, each with its own organisation, team, API token and
  webhook, because a token reads and cancels every envelope of its user's teams
  (probe § 8):

  | Environment | Documenso user | Team | Webhook URL |
  |---|---|---|---|
  | production | `ciao+firma-is@letsrebase.com` | its Personal Team | `http://api:8000/api/hub/documenso/webhook` |
  | preview | `ciao+firma-preview@letsrebase.com` | its Personal Team | `https://preview.letsrebase.com/api/hub/documenso/webhook` |

  Made on 2026-09-25 (REB-408). Each user's own Personal Team holds its API token («prod»
  and «demo», no expiry: revoke and replace one in Documenso, then in that hub's `.env`,
  then redeploy) and its webhook. The two webhooks were written straight into Documenso's
  `Webhook` table (URL, the three events, the secret, the user's own team) rather than
  typed into its form, which is equivalent; the form is the way to edit them.

  Both webhooks send `document.completed`, `document.rejected` and `document.cancelled`,
  each with its own secret, which is that hub's `REBASE_DOCUMENSO_WEBHOOK_SECRET`.
  Production's hub reaches Documenso as `REBASE_DOCUMENSO_URL=http://documenso:3000`,
  the preview's as `https://firma.letsrebase.com`. `docker exec rebase-api-1 uv run
  --no-sync rebase documenso-check` (and `rebase-preview-api-1`) says whether each hub
  reaches Documenso with its token.

  Step 5 of the rollout opens `DOCUMENSO_DISABLE_SIGNUP` for the tens of minutes the two
  environments' users take to be made, on a name already in certificate transparency
  logs, so `NEXT_PRIVATE_ALLOWED_SIGNUP_DOMAINS` (`DOCUMENSO_SIGNUP_DOMAINS` in `.env`,
  defaulting to `letsrebase.com`) refuses any other domain on the server itself, whether
  or not the window is open, so an account made in that window cannot outlive it. Check
  nobody else got in before closing the window: `docker exec rebase-documenso-db-1 psql
  -U documenso -d documenso -tAc 'SELECT email FROM "User"'` must list exactly the two
  `@letsrebase.com` addresses above, beside Documenso's own two internal accounts,
  `serviceaccount@firma.letsrebase.com` and `deleted-account@firma.letsrebase.com`.
- **Who signs for rebase**: the `rebase-*` fields of `REBASE_SIGNER_JSON`, in the host's
  `.env`, never in the repository. With both texts `status: final`, production signs
  with whoever's data is there, which until the SRL exists (roadmap #284) is a person's,
  not the company's yet. The value is one line of compact JSON. Compose's `.env` parser
  refuses shell-style quoting (`'...'"'"'...'`), which an apostrophe in an address
  produces (the patch tag of 2026-09-25 failed on it once). Unquoted JSON works as long as
  it holds no ` #` (the rest of the line would become a comment) and no `$` (compose would
  interpolate it); otherwise wrap the value in double quotes and escape every inner `"`,
  `\` and `$` as `\"`, `\\` and `$$`. Check with `docker compose -p rebase --env-file
  /opt/hub/.env config | grep REBASE_SIGNER_JSON` before any tag.
- **The preview sends real mail**: its `.env` carries `REBASE_RESEND_API_KEY` since
  2026-09-25, because «Invia per la firma» refuses without a mail sender; its signer is
  fiction, and its contracts mail is `ciao+firma-preview@letsrebase.com`.
- **Branding** (REB-474): both organisations show rebase, set in Documenso under
  Organisation settings → Preferenze → Branding: the logo is the echo,
  `shared/brand/echo/echo-ink-watermelon-outlines.png` (Ivan, 2026-09-25: the signing
  surfaces and the documents carry the echo; the site's and the hub's headers keep the
  lockup), the brand URL `https://letsrebase.com`, and the colours
  background `#f1f2f3`, foreground `#011936`, primary `#c50d33` with `#ffffff` on it,
  border `#465362`, ring `#ed254e`, radius `0rem`; no custom CSS. With billing off the
  branding needs no plan or licence. The preview's organisation took the same settings by
  copying production's `OrganisationGlobalSettings` branding columns in SQL.
