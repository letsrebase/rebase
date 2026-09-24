# AGENTS.md — working on the rebase hub

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
`packages/core/src/rebase_core/contracts/texts/`, the template beside it, and the palette
and the typeface read from `shared/brand/` at the paths the image mirrors. So
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
32`): the route sits public behind `/api/hub/` with no rate limit, and the secret is the
only thing standing between it and a forged signature event. Production's webhook points
at
`http://api:8000/api/hub/documenso/webhook` inside the compose network, preview's at
`https://preview.letsrebase.com/api/hub/documenso/webhook`. Documenso retries a failed
delivery only at once, so an event lost while the API restarts stays lost: «Aggiorna
stato» on «Match e contratti» reads the envelope and applies it, and an admin presses it
on a document that has waited for its signature longer than expected. Without
`REBASE_DOCUMENSO_URL` and `REBASE_DOCUMENSO_API_TOKEN` signing answers 503, and a text
whose front matter says `status: draft` never leaves unless `REBASE_CONTRACTS_ALLOW_DRAFT`
is true, which only the preview's `.env` sets. Documenso reaches `api` only if
`NEXT_PRIVATE_WEBHOOK_SSRF_BYPASS_HOSTS` lists it (probe § 5); the secret travels in
clear, so the webhook URL stays on the compose network or is HTTPS.

The webhook's own follow-up (the sealed copy's download and mails, a framework
agreement's waiting letters) runs in the background, after the response: a restart
between the webhook's commit and that background task leaves it undone. `rebase
contracts-sweep` redoes anything a restart, or a mail the provider refused, left behind,
and production runs it every ten minutes (the schedule itself is to be scheduled with the
Documenso rollout).

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
8084, web 8085, Postgres 55435; preview 8086, 8087, 55436. The public paths are `/hub/`
(web) and `/api/hub/` + `/api/orbiters/signups` (api): `projects/website/deploy/letsrebase.conf`
proxies them to production on the host vhost, and `projects/website/deploy/preview.letsrebase.conf`
proxies the same paths to the preview stack (127.0.0.1:8086 for its api), which is why
the preview's Documenso webhook reaches it too. The member area's mail needs
`REBASE_RESEND_API_KEY` and `REBASE_MAIL_FROM` in the host `.env`; without the key
`/hub/login` answers 503 with a sentence. A preview stack that gets a key must also set
`REBASE_HUB_URL` to its own address, or every link it mints points at production.

«Istanze Pigro» in the admin area (ORB-142) reads PigroCRM's registry of spaces through
the CRM's API, never its database: `REBASE_PIGRO_API_URL` (the CRM's public origin,
also where each space is linked) and `REBASE_PIGRO_REGISTRY_TOKEN`, which must equal
the `PIGROCRM_REGISTRY_TOKEN` in the CRM's own host `.env`. One value, set by hand in
both files, generated once; without it the page answers 503 with a sentence and the CRM
side does not even have the route. The call goes through `rebase_core.http`, the seam
the mail uses, so the tests hand a fake and never reach a CRM.
