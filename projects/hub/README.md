# rebase hub

rebase, the freelance community, as a product of its own: the signup form the
community site collects, the freelancer and company wizards, and the admin area that
reads them. Served at `letsrebase.com/hub/`. Split out of PigroCRM on 2026-09-09 so
the two products change independently — its own settings (`REBASE_*`), its own
Postgres, its own Alembic history, its own API and MCP server. Nothing here imports
PigroCRM, and PigroCRM imports nothing from here.

The design record is
[`docs/superpowers/specs/2026-09-09-orbiters-hub-design.md`](docs/superpowers/specs/2026-09-09-orbiters-hub-design.md).
Read it before changing the shape of anything here; this file does not restate it.

## What it does

Three public flows and the admin area behind them:

- `/hub/` — the chooser: «Sono un freelance» / «Cerco persone per un progetto».
- `/hub/freelance` — the freelancer wizard, ending at `/hub/thanks`. The CV is a step
  of it and an optional one: a card without a PDF is stored, reads «da completare»,
  and the person adds the file from `/hub/me` whenever they have it.
- `/hub/companies` — the company wizard.
- `/hub/login` and `/hub/me`: a freelancer gets back in with a magic link by mail, to
  see or change what they sent.
- `/hub/admin/*` — the freelancer, company and signup lists — reached by the same
  magic-link session as `/hub/me` (`/hub/login`), open only when the signed-in
  person's role is `admin`. The first admin is granted with `rebase setrole` (below);
  the next ones with one click from «Amministratori» inside the area, no form, no
  password.
- Every login through the magic link is recorded in `logins` (ORB-158): the
  admin area shows who entered and when under «Accessi», and each card carries its
  count and its last login. The link request itself is not counted.
- A freelancer card can also be born from a signup (ORB-155): an admin writes what the
  public web says about the person through `POST /api/hub/signups/{id}/scheda` or the
  MCP tool `create_freelancer_from_signup`, «Talenti» links to it, and the card stays
  «da completare» until the person adds the CV, the rate and the rest from `/hub/io`.
  Research never overwrites a card the person filled. Spec:
  `docs/superpowers/specs/2026-09-11-freelancer-card-from-a-signup-design.md`.

`POST /api/orbiters/signups` is the community site's signup endpoint, moved here
unchanged on 2026-09-09: the website's form and the ChatGPT Ads conversion still post
to the same path.

## Layout

```
packages/core/   rebase_core: models, Alembic migrations, services, the ad conversion
apps/api/        rebase_api: FastAPI, one process, its own database
apps/mcp/        rebase_mcp: the same services over stdio or Streamable HTTP, for an admin with a token
apps/web/        pnpm package `hub`: the SPA at letsrebase.com/hub/
```

`packages/core` imports neither adapter, and neither adapter imports the other.

## Running it

Python, from the repository root:

```
uv sync --frozen
uv run pytest -q projects/hub/packages/core/tests projects/hub/apps/api/tests projects/hub/apps/mcp/tests
uv run --env-file projects/hub/.env uvicorn rebase_api.main:app --port 8010
uv run --env-file projects/hub/.env uvicorn rebase_mcp.http:app --port 8011   # the MCP server over HTTP
```

The tests bring a `testcontainers` Postgres to `head` with this package's migrations,
never with `create_all`.

Web, also from the root:

```
pnpm --filter hub dev      # :5180, with /api proxied to a running hub API on :8084
pnpm --filter hub build
pnpm --filter hub test
pnpm --filter hub lint
```

The full stack, its own Postgres included, from this directory:

```
cd projects/hub
docker compose -p rebase up -d --build
```

`-p rebase` is not decorative: without it compose names the stack after the
directory, and a second stack starts beside the one already running rather than
joining it.

## Ports and the environment file

Loopback only, production values (`docs/adding-a-project.md` §7 has preview's):

| Service | Port |
|---|---|
| api | 8084 |
| web | 8085 |
| mcp | 8088 |
| Postgres | 55435 |

`.env.example` lists every variable the compose file needs: `POSTGRES_*`,
`REBASE_DATABASE_URL`, the ports above, the ChatGPT Ads pair, and
`REBASE_DATA_DIR` — Postgres' data directory, outside the repository, with no
default in `docker-compose.yml`, so a `.env` that forgets it fails the stack rather
than mounting an empty one. The `.env` itself is never in the repository. Locally it
is `projects/hub/.env`, beside the compose file. On a server it is
`${DEPLOY_PATH}/.env`, the root of that environment's checkout, two levels above the
compose file: the deploy passes `--env-file` explicitly and never rsyncs one.

The first administrator, once the stack is up:

```
docker compose -p rebase exec api uv run --no-sync rebase setrole --email you@example.com --role admin --nome Nome --cognome Cognome
```

Creates the `users` row if none exists yet and sends the same magic link everyone
else gets, never a password. `--role member` demotes.

## Connect an agent

The MCP server answers admins only (REB-213). Each admin mints personal tokens from
«Agenti» in the admin area, or the operator does it for them:

```
docker compose -p rebase exec api uv run --no-sync rebase createtoken --email you@example.com --nome "Claude Code"
```

The value is printed once and only its hash is kept. A client presents it as a bearer
on `https://letsrebase.com/api/hub/mcp`, which the host proxies to the `mcp` service:

```
claude mcp add --transport http rebase-hub https://letsrebase.com/api/hub/mcp --header "Authorization: Bearer reb_…"
```

Over stdio (`python -m rebase_mcp`, for a developer's client or a shell on the host)
the same token goes in `REBASE_MCP_TOKEN`, and the process refuses to start without
one that resolves. An unknown, revoked or deactivated-owner token is one uniform 401.
The claude.ai and Claude Desktop connectors want OAuth and are not supported, as in the
CRM.

Telling the people with a card that their area is open (ORB-157), once, by hand:

```
docker compose -p rebase exec api uv run --no-sync rebase welcome --email you@example.com
docker compose -p rebase exec api uv run --no-sync rebase welcome --all
```

`--all` covers every address the hub knows, signups and cards alike, and the mail speaks
in one of three voices: a card the person filled (it is complete, enter), a card we drafted
from public sources (a recap, the ask to enter and complete it, and that an offer in line
with the profile is already there), or no card at all (the wizard first, then the address
is the way in). One line per address with the outcome and the voice; that output is the
record of the mailing. Needs `REBASE_RESEND_API_KEY` in the host `.env`, like the magic
link.

## Deploy

`.github/workflows/deploy-hub.yml`: preview on a push to `main` that touched the hub,
production on a tag `hub-v<semver>` (`hub-v0.1.0` is out already). Both call the
shared `_deploy-compose.yml`. The production compose project is `rebase`, not `hub`:
the stack went up by hand on 2026-09-09 (ORB-17) as `orbiters` and carries that name
until the tag that ships this rename deploys it, since a different name starts a second
stack beside the running one rather than moving it. The preview's is `rebase-preview`,
migrated on 2026-09-15. `GET /health` touches the database on purpose, so a
green deploy means Postgres is up and migrated, not only that uvicorn answered.

## Status

Shipped: `hub-v0.1.0` is deployed. What is still open — CV retention, the privacy
paragraph the wizard has to link before the CV step, whether the signup-listing MCP
tool moves out of PigroCRM's server — and the decisions taken with Ivan are in
[`docs/superpowers/specs/`](docs/superpowers/specs/).
