# PigroCRM

> The CRM that works in your place.

**AI-first** CRM for Italian freelancers, consultants and small startups. Every operation available
in the web interface is also accessible via REST API and via **MCP** (Model Context Protocol),
so an agent like Claude can do everything you do.

Covers the whole cycle without changing application:

```
Contatto → Cliente → Deal → Offerta → Lavoro → Time Tracking → Fattura → Analisi economica
```

## Principles

- **Lean by default** — every feature is usable without configuration; customization is optional.
- **API first** — the UI uses exclusively the public APIs. No logic exists only in the frontend.
- **MCP first** — the MCP server is not an adapter bolted on afterwards: it uses the same services as the UI, in-process.
- **No duplication** — every piece of data is stored exactly once.
- **Single-tenant inside, several spaces outside** — no service knows about tenants. Whoever signs up
  from the login gets a *space*, that is a Postgres database of its own with the same schema, served
  on `/<name>/app` and `/<name>/api`; the root installation stays as it is. See
  `docs/superpowers/specs/2026-09-08-spazi-un-database-per-tenant-design.md`.

## Stack

| | |
|---|---|
| Backend | Python · FastAPI · SQLAlchemy · Alembic |
| Database | PostgreSQL |
| Frontend | Vite · React · TypeScript · TanStack Router · shadcn/ui (Tailwind + Radix) |
| Documents | Pandoc + Typst |
| Deploy | Docker Compose · nginx · GitHub Actions |

## Deploy

A first deploy done out of order produces a stack that starts up but where **nobody can
log in**, without a single error in any log to explain why. The steps below have to be followed
in this order.

### Prerequisites on the server

- Docker Engine, the Docker Compose plugin and `certbot` (with the nginx plugin) installed.
- A DNS record pointing the chosen domain to this server.
- The user running the deploy has to belong to the `docker` group **and** has to be `root` (or
  have sudo equivalent): `deploy/setup-server.sh` writes to `/etc/nginx/sites-available/`,
  creates the symlink in `sites-enabled/`, runs `nginx -t` and `systemctl reload nginx`, all
  operations that require root privileges. No workflow runs this script: it is a one-time
  manual step over SSH, and the deploy (`deploy-pigrocrm.yml`, through
  `_deploy-compose.yml`) only rsyncs the tree and rebuilds the stack as the environment's
  `DEPLOY_USER`. This repository does not try to bypass the root requirement with
  non-interactive `sudo`, because that would only work assuming that every target server
  already has a passwordless sudoers rule preconfigured for exactly these commands, an
  assumption that cannot be verified from here and is equivalent to the requirement above
  under a different name.
- **`pg_trgm`.** The migrations run `CREATE EXTENSION IF NOT EXISTS pg_trgm`
  when the API starts up. On the compose's `postgres:17-alpine` image the user
  `pigrocrm` is superuser and it works without intervention. On a managed PostgreSQL it takes
  the provider having `pg_trgm` in its allowlist and the user being able to create extensions:
  without that, **the deploy fails at start-up** — which is the intended behavior, because
  the alternative is an application that starts up and scans sequentially in silence.
  Exact symptom in the logs: `permission denied to create extension "pg_trgm"`.

### 1. `.env`

Copy `.env.example` into the repository root **on the server** (not only locally) as
`.env` and fill it in:

- `POSTGRES_PASSWORD` — required: `docker compose` refuses to start without it.
- `PIGROCRM_JWT_SECRET` — generate a real secret (`openssl rand -hex 32`), different from
  any value used in local development.
- Leave `PIGROCRM_COOKIE_SECURE` as it is: `docker-compose.yml` never forwards it to the
  `api` container, on purpose (see the comment there) — it only concerns the local
  development flow further up in the same file.

### 2. Start the stack

```
cd projects/pigrocrm
docker compose --env-file ../../.env up -d --build
```

The compose file lives in the project's folder, but the `.env` stays in the
repository root (step 1): without `--env-file` compose would look for it next to itself, not
find `POSTGRES_PASSWORD`, and refuse to start.

Brings up `db` (Postgres), `api` (migrates itself at start-up) and `web` (nginx with the compiled SPA,
listening only on `127.0.0.1:8080` — not yet reachable from the internet).

### 3. Configure nginx on the host

```
sudo PIGROCRM_DOMAIN=tuodominio.it bash deploy/setup-server.sh
```

Writes a vhost that acts as a reverse proxy to `127.0.0.1:8080`, for now only over HTTP.
`PIGROCRM_DOMAIN` is required: the script refuses to run without it, because a public
repository's setup script defaulting to somebody's domain is worse than asking.

**One-time step: the automatic deploy does not run it.** Re-running it on every push
would rewrite the reverse proxy on every commit, and the guard that's meant to protect the
TLS configuration looks for a file named after the domain (`sites-available/yourdomain.it`).
If certbot has left the vhost under a different name, the guard doesn't find it and the script
adds a second vhost with the same `server_name`. On this host that's exactly the case:
the file is named `pigro.letsrebase.conf`.

### 4. Enable TLS — before trying to log in

```
sudo certbot --nginx -d tuodominio.it
```

(the same domain as the previous step). Manual, one-time step, not automated by CI.

**No login works before this step, and without a single visible error.**
`cookie_secure` (`packages/core/src/pigrocrm/core/config.py`) is `true` by default and
`docker-compose.yml` never overrides it for the `api` container, on purpose — it's the same
protection that stops whoever is intercepting an unencrypted network from replaying the session
cookie. Over plain HTTP the browser silently drops a `Secure` cookie: the login still
answers 200, but every following request comes back unauthenticated (the same
mechanism that `.env.example` describes for Safari in local development, except here it happens by
default, in production, on the first deploy, in every browser).

### 5. Create the first administrator

There is no default user or password (the direct lesson from the previous system's
hardcoded credentials):

```
# Postgres data lives on the host in PIGROCRM_DATA_DIR (see .env.example): a Docker reset
# doesn't touch it. Backup: `scripts/backup-db.sh` (dated pg_dumpall, last 30 kept).
docker compose exec api uv run --no-sync pigrocrm createadmin --email admin@tuodominio.it --nome "Nome Cognome"
```

Asks for the password on the terminal, twice (never as an argument or environment variable).
`--no-sync` isn't decorative: without it, `uv run` inside the container resyncs the environment
against the whole `pyproject.toml`, including the `dev` group (mypy, ruff, pytest, ...), downloading them
from the network inside a production container already running, on every single invocation
(see the comment in `Dockerfile.api`).

### 6. Log in

`https://tuodominio.it/`, with the credentials just created.

### 7. The cron jobs

There is no daemon and no queue: the Gmail sync is a loop that starts, does its work and
ends. The fifteen minutes are cron's, one line in the `crontab` of the user that owns the
deploy. `$DEPLOY_PATH` below is the deploy directory configured in the environment (the
root of this repository's checkout on the server):

```
*/15 * * * * cd $DEPLOY_PATH/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm gmail-sync >> /var/log/pigrocrm-gmail-sync.log 2>&1
```

Every run synchronises every connected mailbox of the root installation and of every
space in the registry (REB-404), and writes one line for each: the time, the installation
(`root` or the space's slug), the mailbox's address and the loop's counters, and nothing
else (never a subject, a message body or a correspondent's address). A space with no
mailbox prints nothing. It exits `0` when every loop went through, or found another one
already running, which is not an error, and `1` when at least one line went to `stderr`
with a sentence: no mailbox anywhere, a deactivated owner, a revoked consent, or a space
whose database or schema could not be used (`saltato`). An unforeseen failure is
`saltato (Type)` followed by the frames of its traceback, in this same log. One line
never stops the next.
`--env-file ../../.env` and `--no-sync` are here for exactly the reasons of §1 and §5.

Same shape for the weekly report, one line for `pigrocrm digest` at eight on Monday:

```
0 8 * * 1 cd $DEPLOY_PATH/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm digest >> /var/log/pigrocrm-digest.log 2>&1
```

Every run prints one line per space: `inviato a N`, `vuoto`, `già inviato`, `nessun
destinatario`, or `saltato (Type)` on `stderr`. `0 8` assumes the host's clock is already
on Europe/Rome — check with `timedatectl` before pasting the line, or the report lands at
the wrong hour. The runbook below now covers both jobs.

The runbook — the table of error sentences, what to do about each one and how they relate
to the expiry of the Google consent — is
[`docs/superpowers/notes/2026-09-09-gmail-cron-runbook.md`](docs/superpowers/notes/2026-09-09-gmail-cron-runbook.md).

## Connect an agent

Every operation in the UI is also an MCP tool. Over HTTP the server answers at
`https://<host>/mcp` for the root installation and `https://<host>/<slug>/mcp` for a
space, authenticated with a personal access token as a bearer. In the app, the
«Collega un agente» entry at the bottom of the sidebar mints a token and gives you
both snippets below with the values filled in.

```
claude mcp add --transport http pigrocrm https://<host>/mcp --header "Authorization: Bearer pgc_..."
```

```json
{"mcpServers": {"pigrocrm": {"type": "http", "url": "https://<host>/mcp",
  "headers": {"Authorization": "Bearer pgc_..."}}}}
```

The host vhosts in `deploy/nginx/` gained a `/mcp` block with a long read timeout for
this, because a tool call answers only when it has finished: an installation still
serving an older copy of its vhost caps every call at nginx's 60-second default until it
re-applies the file (`deploy/setup-server.sh`).

The token inherits the whole role of whoever created it; revoke it from Impostazioni →
Token if it leaks. Claude Code, Cursor, Codex and any client that can send a header
work; the connectors of claude.ai and Claude Desktop need OAuth, which this server does
not offer yet. The stdio transport is still there for a client that launches the server
itself (see the head of `docker-compose.yml`).

## Status

In development. Will replace the previous system.

The specs are in [`docs/superpowers/specs/`](docs/superpowers/specs/).
