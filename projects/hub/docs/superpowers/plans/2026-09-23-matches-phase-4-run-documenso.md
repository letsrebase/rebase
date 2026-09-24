# Phase 4: run Documenso for real. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Documenso runs beside the hub's production stack at `https://firma.letsrebase.com`, with its own Postgres, a self-signed signing certificate, Resend for its own account mails and public sign-up closed; production and preview each sign through a Documenso user and organisation of their own, and both hubs carry the settings phase 3 reads.

**Architecture:** the Documenso services live in `projects/hub/docker-compose.documenso.yml`, in the same `rebase` compose project, and only production's host `.env` loads that file (`COMPOSE_FILE=docker-compose.yml:docker-compose.documenso.yml`): compose interpolates the required variables of every service of every file it reads, profiles or not (checked with compose v2.35 on 2026-09-23), so a Documenso service in `docker-compose.yml` would make the preview's deploy and CI's image build fail without Documenso's secrets. Production's hub reaches Documenso as `http://documenso:3000` and Documenso calls production's webhook as `http://api:8000`, both inside the compose network; the preview's hub reaches the same instance at its public name and receives its webhook at `https://preview.letsrebase.com`. Everything that touches the server, DNS or production is a step Ivan approves, and production moves only on a `hub-v*` tag he asks for.

**Tech Stack:** Docker Compose v2, `documenso/documenso:v2.18.0` pinned by digest, Postgres 17, nginx with certbot, Terraform (Cloudflare provider) in `infra/cloudflare`, OpenSSL, the hub's `rebase` CLI.

**Spec:** `projects/hub/docs/superpowers/specs/2026-09-23-matches-and-contract-signing-design.md` (§ 7, § 10 item 4), with the facts of `projects/hub/docs/superpowers/specs/2026-09-23-documenso-probe.md` (§ 1 the image, § 2 the environment, § 3 accounts and tokens, § 5 the webhook and its SSRF guard, § 7 the certificate, § 8 one user per environment, § 11 items 1, 4, 5, 12 and 13). Where the two differ on Documenso, the probe wins. Needs phase 3 (`2026-09-23-matches-phase-3-sign.md`) merged on `main`: the settings it reads and the webhook route.

## Global Constraints

- English for code, comments, docs and commit messages; Italian only for what the product says to people (the CLI's sentences included). No em dashes anywhere.
- Conventional Commits in the first person, pathspec staging, no AI co-author trailer; the body's last line is `REB-393.`
- Nothing is deployed by hand (`docs/design/DECISIONS.md`, 2026-09-09). Production moves only on an annotated `hub-v*` tag on the merge commit that Ivan asks for, bumped by what `main` carries; the preview moves on a merge to `main` or a re-run of the latest «Deploy hub» preview run. No `docker compose up` on the server by hand, ever.
- Every step marked **Ivan approves** touches the server, DNS, Documenso's live UI or production: it runs only after Ivan says yes to that step, one step at a time, and its output is shown to him before the next.
- Secrets (Documenso's database password, auth secret, the two encryption keys, the certificate and its passphrase, the API tokens, the webhook secrets) are generated on the host or in Documenso's UI and live in the host `.env` files and Ivan's password manager only: never in the repository, a card, a pull request, a log, a commit or a message.
- Every compose command names its project and its env file: `-p rebase` or `-p rebase-preview`, `--env-file "${DEPLOY_PATH}/.env"`. The containers are `rebase-*` and `rebase-preview-*`; the old `orbiters` names are stale.
- The installed vhost in `/etc/nginx/sites-available/` is edited in place (certbot rewrites it) with `nginx -t` before every reload; the repository copy is plain HTTP and the source of truth for the rules.
- DNS changes go through `infra/cloudflare`: a resource, `terraform plan` read twice, `apply`, then the import block, as its README says. Never the panel.
- Before any `hub-v*` tag: the newest `hub-v*` tag and what `main` adds since, production's `alembic_version`, and a diff of `main` for fixtures (`Studio Rossi`, `example.com`) that must not ship. A re-run of an older tag's deploy breaks production.
- Documenso's image is `documenso/documenso:v2.18.0@sha256:126976b9e3be54193e1a3be8d22130af1913aaa894c550b98870a2cc4c422650`, the one phase 1 probed; an upgrade is its own pull request.
- One Documenso user and organisation per environment, each owning its environment's team, token and webhook (probe § 8): a token reads and cancels every envelope of its user's teams.
- Ports come from `docs/adding-a-project.md` §7: Documenso takes 127.0.0.1:8090, the next free one; its Postgres publishes no port.
- Commands run from the repository root unless a step says it runs on the host (`ssh orbiters`).

## Review Focus

1. **The preview's deploy and CI's image build must never need Documenso's secrets.** A Documenso service in `docker-compose.yml`, even behind a profile, makes compose fail on its `:?` variables wherever they are unset. Task 1 `test_the_hub_compose_file_starts_no_documenso` and the preview-shaped `docker compose config` of Task 1 Step 6.
2. **Nobody but the two environments' users may open an account on `firma.letsrebase.com`.** Sign-up is off by default, is opened for the minutes the two users take, and is checked closed afterwards. Task 1 `test_nobody_opens_an_account_and_the_webhook_reaches_the_api_by_name`, Task 1 Step 7's local check of the sign-up page, Task 2 Step 9.
3. **Production's webhook must get past Documenso's SSRF guard.** `http://api:8000` resolves to a private address and fails every delivery silently unless `api` is in `NEXT_PRIVATE_WEBHOOK_SSRF_BYPASS_HOSTS` (probe § 5). Task 1's test pins the default; Task 2 Step 10 cancels a throwaway envelope and reads the delivery in both the webhook log and the API's log.
4. **One environment's token must not reach the other's envelopes.** Task 2 Step 10 reads each throwaway envelope with the other environment's token and expects 404 (probe § 8).
5. **A compose that does not read `COMPOSE_FILE` from `--env-file` would start production without Documenso and report green.** Task 2 Step 1 checks the server's compose on a throwaway env file before the tag, and Step 6 reads the deploy's service list after it.

---

## File Structure

```
projects/hub/docker-compose.documenso.yml        T1: documenso, documenso-db (production only)
projects/hub/.env.example                        T1: the Documenso block, commented
projects/hub/deploy/firma.letsrebase.conf        T1: the host vhost, plain HTTP
projects/hub/packages/core/src/rebase_core/documenso.py   T1: DocumensoClient.ping, client_from_settings(http=)
projects/hub/packages/core/src/rebase_core/cli.py         T1: `rebase documenso-check`
projects/hub/packages/core/tests/fakes_documenso.py       T1: the envelope listing
projects/hub/packages/core/tests/test_documenso.py        T1: ping and the check
projects/hub/packages/core/tests/test_documenso_compose.py  T1
infra/cloudflare/rebase.tf                       T1: firma.letsrebase.com; T2: its import block in rebase-imports.tf
docs/adding-a-project.md                         T1: the port table
projects/hub/AGENTS.md                           T1: how Documenso runs; T2: the tokens' dates
```

Task order: 1 (a pull request, merged, which deploys nothing new: the preview's `.env` does not load the Documenso file) → 2 (the rollout, step by step with Ivan).

---

### Task 1: Documenso's compose file, vhost, DNS record, check command and deploy notes (REB-393, part 1 of 2)

**Files:**
- Create: `projects/hub/docker-compose.documenso.yml`
- Modify: `projects/hub/.env.example` (a block before `# --- preview`)
- Create: `projects/hub/deploy/firma.letsrebase.conf`
- Modify: `projects/hub/packages/core/src/rebase_core/documenso.py` (`ping`, `client_from_settings`)
- Modify: `projects/hub/packages/core/src/rebase_core/cli.py` (`documenso_check`, the parser, the dispatch)
- Modify: `projects/hub/packages/core/tests/fakes_documenso.py` (the listing)
- Modify: `projects/hub/packages/core/tests/test_documenso.py` (append)
- Create: `projects/hub/packages/core/tests/test_documenso_compose.py`
- Modify: `infra/cloudflare/rebase.tf` (append one record)
- Modify: `docs/adding-a-project.md` (§7, the port table), `projects/hub/AGENTS.md` (the «Deploying» section)

**Interfaces:**
- Consumes (phase 3): `rebase_core.documenso.{DocumensoClient, client_from_settings, UNREACHABLE}`, `DocumensoClient._call`, `rebase_core.errors.DocumensoFailed`, `Settings.documenso_url`, `Settings.documenso_api_token`, `fakes_documenso.{BASE, TOKEN, FakeDocumenso}`; main (`rebase_core.cli.main`, `rebase_core.http.HttpCall`).
- Produces:
  - `DocumensoClient.ping() -> None` (`GET /api/v2/envelope`, raises `DocumensoFailed`); `client_from_settings(settings: Settings, http: HttpCall | None = None) -> DocumensoClient | None`.
  - `rebase_core.cli.documenso_check(settings: Settings, http: HttpCall | None = None) -> int` and the command `rebase documenso-check` (exit 0 and «Documenso risponde a {url} e accetta il token.», else exit 1 and a sentence on stderr).
  - Compose services `documenso` and `documenso-db` in project `rebase`; host variables `REBASE_DOCUMENSO_DATA_DIR`, `REBASE_DOCUMENSO_PORT`, `DOCUMENSO_PUBLIC_URL`, `DOCUMENSO_DB_PASSWORD`, `DOCUMENSO_NEXTAUTH_SECRET`, `DOCUMENSO_ENCRYPTION_KEY`, `DOCUMENSO_ENCRYPTION_SECONDARY_KEY`, `DOCUMENSO_SIGNING_CERT_BASE64`, `DOCUMENSO_SIGNING_PASSPHRASE`, `DOCUMENSO_MAIL_FROM`, `DOCUMENSO_DISABLE_SIGNUP`, `DOCUMENSO_WEBHOOK_BYPASS_HOSTS` (compose only: the API reads none of them, so none is in `config.py`); the resource `cloudflare_dns_record.rebase_firma_a`.

- [ ] **Step 1: Write the failing tests**

`projects/hub/packages/core/tests/test_documenso_compose.py`:

```python
"""Documenso as production runs it (REB-393): the image phase 1 probed, pinned by digest;
a loopback port and an unpublished database; nobody can open an account; the webhook may
reach the API by its compose name; and a compose file of its own, so the preview's deploy
and CI's image build never need Documenso's secrets. The files are read as text, since
what matters is what they say; compose itself runs in the rollout."""

import re
from pathlib import Path

HUB = Path(__file__).resolve().parents[3]
COMPOSE = HUB / "docker-compose.yml"
DOCUMENSO = HUB / "docker-compose.documenso.yml"
EXAMPLE = HUB / ".env.example"
PROBED = (
    "documenso/documenso:v2.18.0"
    "@sha256:126976b9e3be54193e1a3be8d22130af1913aaa894c550b98870a2cc4c422650"
)


def _documenso() -> str:
    return DOCUMENSO.read_text(encoding="utf-8")


def test_documenso_runs_the_image_phase_1_probed_pinned_by_digest() -> None:
    assert f"image: {PROBED}" in _documenso()


def test_the_hub_compose_file_starts_no_documenso() -> None:
    """Compose interpolates every service of every file it loads, profiles or not: a
    Documenso service here would make the preview's deploy and CI's build need its
    secrets. Only production's `.env` loads the second file."""
    text = COMPOSE.read_text(encoding="utf-8")
    assert "documenso/documenso" not in text
    assert "documenso-db:" not in text


def test_documenso_publishes_on_the_loopback_only_and_its_database_not_at_all() -> None:
    text = _documenso()
    assert "- '${REBASE_DOCUMENSO_PORT:-127.0.0.1:8090}:3000'" in text
    assert text.count("ports:") == 1
    assert "0.0.0.0" not in text


def test_nobody_opens_an_account_and_the_webhook_reaches_the_api_by_name() -> None:
    text = _documenso()
    assert "NEXT_PUBLIC_DISABLE_SIGNUP: ${DOCUMENSO_DISABLE_SIGNUP:-true}" in text
    assert "NEXT_PRIVATE_WEBHOOK_SSRF_BYPASS_HOSTS: ${DOCUMENSO_WEBHOOK_BYPASS_HOSTS:-api}" in text
    assert "DOCUMENSO_DISABLE_TELEMETRY: 'true'" in text
    assert "NEXT_PRIVATE_SMTP_TRANSPORT: resend" in text
    assert "NEXT_PUBLIC_UPLOAD_TRANSPORT: database" in text


def test_every_variable_documenso_requires_is_in_the_env_example_and_commented() -> None:
    required = set(re.findall(r"\$\{([A-Z0-9_]+):\?", _documenso()))
    assert required >= {
        "REBASE_DOCUMENSO_DATA_DIR",
        "DOCUMENSO_DB_PASSWORD",
        "DOCUMENSO_NEXTAUTH_SECRET",
        "DOCUMENSO_ENCRYPTION_KEY",
        "DOCUMENSO_ENCRYPTION_SECONDARY_KEY",
        "DOCUMENSO_SIGNING_CERT_BASE64",
        "DOCUMENSO_SIGNING_PASSPHRASE",
        "REBASE_RESEND_API_KEY",
    }
    example = EXAMPLE.read_text(encoding="utf-8")
    # The hub's own Resend key is already there, uncommented: Documenso reuses it.
    for name in required - {"REBASE_RESEND_API_KEY"}:
        assert f"# {name}=" in example, name
    # Commented: a `.env` copied from the example loads no Documenso until someone means it.
    assert "# COMPOSE_FILE=docker-compose.yml:docker-compose.documenso.yml" in example
    assert "\nCOMPOSE_FILE=" not in example
```

Append to `projects/hub/packages/core/tests/test_documenso.py` (add `import pytest` if absent and `from rebase_core.cli import documenso_check`):

```python
def test_the_check_says_whether_this_environment_reaches_documenso_with_its_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`rebase documenso-check`, run inside the api container after the settings change
    (REB-393): one page of the team's envelopes, read and dropped."""
    fake = FakeDocumenso()
    both = Settings(_env_file=None, documenso_url=BASE, documenso_api_token=TOKEN)  # type: ignore[call-arg]
    assert documenso_check(both, http=fake) == 0
    assert fake.calls == [("GET", "/envelope")]
    assert "accetta il token" in capsys.readouterr().out
    wrong = Settings(_env_file=None, documenso_url=BASE, documenso_api_token="api_sbagliato")  # type: ignore[call-arg]
    assert documenso_check(wrong, http=fake) == 1
    assert "Invalid session or API token" in capsys.readouterr().err
    assert documenso_check(Settings(_env_file=None), http=fake) == 1  # type: ignore[call-arg]
    assert "la firma è spenta" in capsys.readouterr().err
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_documenso_compose.py projects/hub/packages/core/tests/test_documenso.py`
Expected: FAIL: `FileNotFoundError` on `docker-compose.documenso.yml`, and `ImportError: cannot import name 'documenso_check'`.

- [ ] **Step 3: The check command**

`projects/hub/packages/core/tests/fakes_documenso.py`: in `_operation`, before the `download` branch, add

```python
        if method == "GET" and path == "/envelope":
            return "list"
```

and in `__call__`, before `if operation == "create":`, add

```python
        if operation == "list":
            return 200, json.dumps({"data": [], "count": len(self.envelopes)}).encode()
```

`projects/hub/packages/core/src/rebase_core/documenso.py`: add to `DocumensoClient`, after `cancel`:

```python
    def ping(self) -> None:
        """One page of the team's envelopes, read and dropped: whether the instance
        answers and the token opens it (`rebase documenso-check`, REB-393)."""
        self._call("GET", "/envelope")
```

and replace `client_from_settings` with:

```python
def client_from_settings(settings: Settings, http: HttpCall | None = None) -> DocumensoClient | None:
    """`None` without a URL or a token: signing is off on this environment, and says so.
    `http` is the seam, for the check command's test."""
    if not settings.documenso_url or not settings.documenso_api_token:
        return None
    return DocumensoClient(
        settings.documenso_url, settings.documenso_api_token, http or urllib_download_call
    )
```

`projects/hub/packages/core/src/rebase_core/cli.py`: add the imports `from rebase_core.documenso import client_from_settings`, `from rebase_core.errors import DocumensoFailed, DomainError` (extending the existing `DomainError` import) and `from rebase_core.http import HttpCall`, then after `contracts_check`:

```python
def documenso_check(settings: Settings, http: HttpCall | None = None) -> int:
    """`rebase documenso-check`: does this environment reach its Documenso, and does its
    token open it? Reads one page of the team's envelopes and prints none of them. Run
    inside the api container after `REBASE_DOCUMENSO_*` change (REB-393)."""
    client = client_from_settings(settings, http)
    if client is None:
        print(
            "REBASE_DOCUMENSO_URL o REBASE_DOCUMENSO_API_TOKEN mancano: la firma è spenta.",
            file=sys.stderr,
        )
        return 1
    try:
        client.ping()
    except DocumensoFailed as exc:
        print(exc.message, file=sys.stderr)
        return 1
    print(f"Documenso risponde a {settings.documenso_url} e accetta il token.")
    return 0
```

In `main`, after the `contracts-check` parser:

```python
    sub.add_parser(
        "documenso-check",
        help="Documenso risponde, e il token di questo ambiente lo apre?",
    )
```

and in the dispatch: `if args.command == "documenso-check": return documenso_check(get_settings())`.

- [ ] **Step 4: The compose file** (`projects/hub/docker-compose.documenso.yml`)

```yaml
# Documenso, the site where a freelancer signs rebase's contracts (REB-387, REB-393), in
# the `rebase` compose project beside the hub. A file of its own, which only production's
# `.env` loads:
#
#     COMPOSE_FILE=docker-compose.yml:docker-compose.documenso.yml
#
# Compose interpolates the required variables of every service of every file it reads,
# profiles or not, so these services in docker-compose.yml would fail the preview's deploy
# and CI's image build, neither of which runs Documenso. The preview signs on this same
# instance through a Documenso user of its own (probe § 8).
#
# The image is the one phase 1 probed, pinned by tag and digest
# (docs/superpowers/specs/2026-09-23-documenso-probe.md § 1). Its uploaded and sealed PDFs
# live in its own Postgres (`NEXT_PUBLIC_UPLOAD_TRANSPORT=database`), so the data to back
# up is REBASE_DOCUMENSO_DATA_DIR and nothing else. About 630 MiB of memory once warm,
# 80 MiB for its Postgres.
services:
  documenso-db:
    image: postgres:17-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: documenso
      POSTGRES_PASSWORD: ${DOCUMENSO_DB_PASSWORD:?set it in .env, openssl rand -hex 24}
      POSTGRES_DB: documenso
    # On the host, outside Docker and outside the repository, like the hub's own data. No
    # default, for the reason docker-compose.yml gives for REBASE_DATA_DIR.
    volumes:
      - ${REBASE_DOCUMENSO_DATA_DIR:?set it in .env, to its own directory outside the repository}:/var/lib/postgresql/data
    # No published port: only Documenso reads it, over the compose network.
    healthcheck:
      test: ['CMD-SHELL', 'pg_isready -U documenso']
      interval: 5s
      retries: 10

  documenso:
    image: documenso/documenso:v2.18.0@sha256:126976b9e3be54193e1a3be8d22130af1913aaa894c550b98870a2cc4c422650
    restart: unless-stopped
    depends_on:
      documenso-db:
        condition: service_healthy
    environment:
      # The names are the tag's own `.env.example`, as the probe checked them (§ 2).
      NEXTAUTH_SECRET: ${DOCUMENSO_NEXTAUTH_SECRET:?set it in .env, openssl rand -hex 32}
      NEXT_PRIVATE_ENCRYPTION_KEY: ${DOCUMENSO_ENCRYPTION_KEY:?set it in .env, openssl rand -hex 32}
      NEXT_PRIVATE_ENCRYPTION_SECONDARY_KEY: ${DOCUMENSO_ENCRYPTION_SECONDARY_KEY:?set it in .env, openssl rand -hex 32}
      NEXT_PUBLIC_WEBAPP_URL: ${DOCUMENSO_PUBLIC_URL:-https://firma.letsrebase.com}
      # The container's own address, for its background jobs (probe § 2).
      NEXT_PRIVATE_INTERNAL_WEBAPP_URL: http://localhost:3000
      NEXT_PRIVATE_DATABASE_URL: postgresql://documenso:${DOCUMENSO_DB_PASSWORD}@documenso-db:5432/documenso
      NEXT_PRIVATE_DIRECT_DATABASE_URL: postgresql://documenso:${DOCUMENSO_DB_PASSWORD}@documenso-db:5432/documenso
      NEXT_PUBLIC_UPLOAD_TRANSPORT: database
      # The seal: a self-signed PKCS#12, made on the host (AGENTS.md), as one line of
      # base64. Valid, and marked "issuer not trusted" by PDF readers until a certificate
      # on Adobe's trust list is bought.
      NEXT_PRIVATE_SIGNING_TRANSPORT: local
      NEXT_PRIVATE_SIGNING_PASSPHRASE: ${DOCUMENSO_SIGNING_PASSPHRASE:?set it in .env}
      NEXT_PRIVATE_SIGNING_LOCAL_FILE_CONTENTS: ${DOCUMENSO_SIGNING_CERT_BASE64:?set it in .env, the .p12 as base64}
      # Documenso's own account mails only (a confirmation, a password reset): every
      # signing mail is the hub's. Through Resend, with the hub's own key.
      NEXT_PRIVATE_SMTP_TRANSPORT: resend
      NEXT_PRIVATE_RESEND_API_KEY: ${REBASE_RESEND_API_KEY:?Documenso mails through the hub's Resend key}
      NEXT_PRIVATE_SMTP_FROM_NAME: rebase
      NEXT_PRIVATE_SMTP_FROM_ADDRESS: ${DOCUMENSO_MAIL_FROM:-ciao@letsrebase.com}
      # Nobody opens an account on the public instance. The rollout opens it for the
      # minutes the two environments' users take, then closes it again.
      NEXT_PUBLIC_DISABLE_SIGNUP: ${DOCUMENSO_DISABLE_SIGNUP:-true}
      # Production's webhook is `http://api:8000/...`, a name that resolves to a private
      # address: Documenso refuses to call it unless the name is listed (probe § 5).
      NEXT_PRIVATE_WEBHOOK_SSRF_BYPASS_HOSTS: ${DOCUMENSO_WEBHOOK_BYPASS_HOSTS:-api}
      DOCUMENSO_DISABLE_TELEMETRY: 'true'
    ports:
      # Loopback only: the host's nginx serves it as firma.letsrebase.com
      # (deploy/firma.letsrebase.conf).
      - '${REBASE_DOCUMENSO_PORT:-127.0.0.1:8090}:3000'
```

- [ ] **Step 5: The `.env.example` block** (before `# --- preview`)

```
# --- Documenso, the signing site (REB-393), production only -----------------------------
# Documenso runs in this compose project from a file of its own, which only production's
# `.env` loads (compose interpolates every service of every file it reads, so the
# preview's deploy and CI's build must never see it):
# COMPOSE_FILE=docker-compose.yml:docker-compose.documenso.yml
# The preview signs on production's instance through a Documenso user of its own and sets
# none of what follows. Each secret is `openssl rand -hex 32`, the database password
# `openssl rand -hex 24` (a URL carries it); the certificate is the `.p12` made on the
# host, as one line of base64 (AGENTS.md). Documenso mails through REBASE_RESEND_API_KEY.
# REBASE_DOCUMENSO_DATA_DIR=/srv/rebase-data/documenso-postgres
# REBASE_DOCUMENSO_PORT=127.0.0.1:8090
# DOCUMENSO_PUBLIC_URL=https://firma.letsrebase.com
# DOCUMENSO_DB_PASSWORD=
# DOCUMENSO_NEXTAUTH_SECRET=
# DOCUMENSO_ENCRYPTION_KEY=
# DOCUMENSO_ENCRYPTION_SECONDARY_KEY=
# DOCUMENSO_SIGNING_CERT_BASE64=
# DOCUMENSO_SIGNING_PASSPHRASE=
# DOCUMENSO_MAIL_FROM=ciao@letsrebase.com
# Open (false) only for the minutes the two environments' users take; true otherwise:
# DOCUMENSO_DISABLE_SIGNUP=true
# Production's webhook calls the API by its compose name; a local run calls the host:
# DOCUMENSO_WEBHOOK_BYPASS_HOSTS=api
```

- [ ] **Step 6: Run the tests, then compose on both shapes of `.env`**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_documenso_compose.py projects/hub/packages/core/tests/test_documenso.py`
Expected: all pass.

Run (a preview-shaped env file: no `COMPOSE_FILE`, no Documenso variable):

```bash
CHECK="$(mktemp -d)"
printf 'POSTGRES_PASSWORD=x\nREBASE_DATA_DIR=%s/data\n' "$CHECK" > "$CHECK/preview.env"
(cd projects/hub && docker compose -p rebase-check --env-file "$CHECK/preview.env" config --services)
```

Expected: `db`, `api`, `mcp`, `web` in some order, exit 0: the preview never sees Documenso.

Run (a production-shaped one):

```bash
cat > "$CHECK/production.env" <<EOF
COMPOSE_FILE=docker-compose.yml:docker-compose.documenso.yml
POSTGRES_PASSWORD=x
REBASE_DATA_DIR=$CHECK/data
REBASE_RESEND_API_KEY=re_check
REBASE_DOCUMENSO_DATA_DIR=$CHECK/documenso
DOCUMENSO_DB_PASSWORD=x
DOCUMENSO_NEXTAUTH_SECRET=x
DOCUMENSO_ENCRYPTION_KEY=x
DOCUMENSO_ENCRYPTION_SECONDARY_KEY=x
DOCUMENSO_SIGNING_CERT_BASE64=x
DOCUMENSO_SIGNING_PASSPHRASE=x
EOF
(cd projects/hub && docker compose -p rebase-check --env-file "$CHECK/production.env" config --services)
grep -v '^DOCUMENSO_SIGNING_PASSPHRASE=' "$CHECK/production.env" > "$CHECK/forgot.env"
(cd projects/hub && docker compose -p rebase-check --env-file "$CHECK/forgot.env" config --services); echo "exit $?"
rm -rf "$CHECK"
```

Expected: the four plus `documenso` and `documenso-db`; then `required variable DOCUMENSO_SIGNING_PASSPHRASE is missing a value` and `exit 1`.

- [ ] **Step 7: Start Documenso locally once, from this file alone**

A throwaway run on this machine, never the server: it proves the file starts, seals with a certificate given as base64, and keeps sign-up closed at runtime, which the probe did not run (probe § 11.13).

```bash
SMOKE="$(mktemp -d)"
openssl req -x509 -newkey rsa:2048 -days 30 -keyout "$SMOKE/key.pem" -out "$SMOKE/cert.pem" \
  -passout pass:prova -subj "/CN=rebase smoke/O=rebase/C=IT"
openssl pkcs12 -export -inkey "$SMOKE/key.pem" -passin pass:prova -in "$SMOKE/cert.pem" \
  -out "$SMOKE/cert.p12" -passout pass:prova
cat > "$SMOKE/smoke.env" <<EOF
REBASE_RESEND_API_KEY=re_smoke_not_a_key
REBASE_DOCUMENSO_DATA_DIR=$SMOKE/postgres
REBASE_DOCUMENSO_PORT=127.0.0.1:18090
DOCUMENSO_PUBLIC_URL=http://localhost:18090
DOCUMENSO_DB_PASSWORD=smoke
DOCUMENSO_NEXTAUTH_SECRET=$(openssl rand -hex 32)
DOCUMENSO_ENCRYPTION_KEY=$(openssl rand -hex 32)
DOCUMENSO_ENCRYPTION_SECONDARY_KEY=$(openssl rand -hex 32)
DOCUMENSO_SIGNING_CERT_BASE64=$(base64 < "$SMOKE/cert.p12" | tr -d '\n')
DOCUMENSO_SIGNING_PASSPHRASE=prova
EOF
(cd projects/hub && docker compose -p rebase-documenso-smoke -f docker-compose.documenso.yml --env-file "$SMOKE/smoke.env" up -d)
curl -fsS --retry 30 --retry-delay 3 --retry-all-errors http://127.0.0.1:18090/api/health; echo
curl -fsS http://127.0.0.1:18090/api/certificate-status; echo
```

Expected: `{"status":"ok","checks":{"database":{"status":"ok"},"certificate":{"status":"ok"}}}` and `{"isAvailable":true}`. Then open `http://localhost:18090/signup` in a browser (Playwright from the repository's own install, or by hand) and take a screenshot: with `DOCUMENSO_DISABLE_SIGNUP` unset (so `true`) the page offers no sign-up form. If it does offer one, stop here and report it: the rollout's plan to close sign-up by setting would not hold, and Ivan decides the fallback (a vhost `location = /signup { return 404; }`) before anything reaches the server. Then:

```bash
(cd projects/hub && docker compose -p rebase-documenso-smoke -f docker-compose.documenso.yml --env-file "$SMOKE/smoke.env" down -v)
rm -rf "$SMOKE"
```

- [ ] **Step 8: The vhost** (`projects/hub/deploy/firma.letsrebase.conf`)

```nginx
# firma.letsrebase.com -- Documenso, the site where a freelancer signs rebase's contracts
# (REB-387, REB-393).
#
# Documenso runs in the hub's production compose project from docker-compose.documenso.yml
# on 127.0.0.1:8090, and this name proxies all of it: the signing pages the hub's mails
# link to (`/sign/<token>`), the login where each environment's Documenso user makes its
# token and its webhook, and the API the preview's hub calls (`/api/v2/`). Production's hub
# calls Documenso inside the compose network (`http://documenso:3000`) and Documenso calls
# production's hub back the same way, so neither of those crosses this file.
#
# A signing URL carries its secret in the path: `Referrer-Policy: no-referrer` keeps it
# out of every Referer, and `X-Robots-Tag` keeps every page out of search. No
# Content-Security-Policy of ours: the pages are Documenso's, and the website's policy
# (projects/website/deploy/security-headers.conf) would break them.
#
# TLS is added by `certbot --nginx -d firma.letsrebase.com`, a certificate of its own:
# `--expand` would rewrite every vhost it matches (docs/adding-a-project.md §7). The copy
# here is the plain-HTTP source of truth; the installed copy in
# /etc/nginx/sites-available/ has certbot's 443 block on top and is edited in place, with
# `nginx -t` before every reload.

server {
    listen 80;
    listen [::]:80;
    server_name firma.letsrebase.com;

    # The hub uploads a contract of a few hundred kilobytes; Documenso's own upload form
    # takes more. Ten megabytes is room for both.
    client_max_body_size 10M;

    add_header X-Robots-Tag "noindex, nofollow, noarchive" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header X-Content-Type-Options "nosniff" always;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        proxy_pass http://127.0.0.1:8090;
        include /etc/nginx/snippets/orbiters-proxy.conf;
    }
}
```

- [ ] **Step 9: The DNS record** (`infra/cloudflare/rebase.tf`, append)

```hcl
# Documenso, the contracts' signing site (REB-393): the same Hetzner origin, whose nginx
# proxies it to the hub's production compose project on 127.0.0.1:8090.
resource "cloudflare_dns_record" "rebase_firma_a" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "firma.letsrebase.com"
  type     = "A"
  content  = "204.168.255.175"
  ttl      = 1
  proxied  = false
  comment  = "Documenso, the contracts' signing site (REB-393)"
}
```

Run: `terraform -chdir=infra/cloudflare fmt -check`
Expected: exit 0. (No `plan` here: it needs the zone's token and is Task 2's first DNS step. The import block follows the `apply`, as the README asks, so the resource count runs one ahead of the imports until Task 2 Step 3.)

- [ ] **Step 10: The docs**

`docs/adding-a-project.md` §7, the table: add a row after the hub's

```
| Documenso (in `rebase`, from `docker-compose.documenso.yml`) | documenso 8090 (its Postgres publishes none) | none: the preview signs on production's instance |
```

`projects/hub/AGENTS.md`, in «Deploying»: change the ports paragraph's first sentence to «Ports, loopback only, from the table in `docs/adding-a-project.md` §7: production api 8084, web 8085, Postgres 55435, Documenso 8090; preview 8086, 8087, 55436.» and add, at the end of the section:

```
### Documenso, the signing site

Since REB-393 Documenso runs beside production in the `rebase` compose project, from
`docker-compose.documenso.yml`, which only production's `.env` loads with
`COMPOSE_FILE=docker-compose.yml:docker-compose.documenso.yml`. Compose interpolates
every service of every file it reads, profiles or not, so the Documenso services in
`docker-compose.yml` would fail the preview's deploy and CI's build on their secrets.
Anything you run by hand against production passes `-p rebase --env-file
"${DEPLOY_PATH}/.env"` from `projects/hub`, or compose sees neither file nor stack.

- **Image**: `documenso/documenso:v2.18.0`, pinned by digest, the one phase 1 probed. An
  upgrade is a pull request that moves the pin, after reading the release notes.
- **Name and port**: `https://firma.letsrebase.com`, the vhost `deploy/firma.letsrebase.conf`
  (its own certificate) in front of 127.0.0.1:8090.
- **Data**: its own Postgres, `documenso-db`, on `REBASE_DOCUMENSO_DATA_DIR`
  (`/srv/rebase-data/documenso-postgres`), with every uploaded and sealed PDF in it:
  back it up with the hub's own data. About 630 MiB of memory once warm, 80 MiB for its
  Postgres.
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
  | production | `ciao+firma@letsrebase.com` | `rebase` | `http://api:8000/api/hub/documenso/webhook` |
  | preview | `ciao+firma-preview@letsrebase.com` | `rebase-preview` | `https://preview.letsrebase.com/api/hub/documenso/webhook` |

  Both webhooks send `document.completed`, `document.rejected` and `document.cancelled`,
  each with its own secret, which is that hub's `REBASE_DOCUMENSO_WEBHOOK_SECRET`.
  Production's hub reaches Documenso as `REBASE_DOCUMENSO_URL=http://documenso:3000`,
  the preview's as `https://firma.letsrebase.com`. `docker exec rebase-api-1 uv run
  --no-sync rebase documenso-check` (and `rebase-preview-api-1`) says whether each hub
  reaches Documenso with its token.
- **What still stops a real signature**: the texts' `status: draft` (spec § 1f) and,
  on production, the `rebase-*` fields of `REBASE_SIGNER_JSON`, which wait for the SRL
  (roadmap #284). Until both are there «Invia per la firma» refuses with a sentence.
```

- [ ] **Step 11: Lint, types, the hub suite**

Run: `uv run pytest -q projects/hub/packages/core/tests projects/hub/apps/api/tests projects/hub/apps/mcp/tests && uv run ruff check projects/hub && uv run ruff format --check projects/hub && uv run mypy`
Expected: green.

- [ ] **Step 12: Commit**

```bash
git add projects/hub/docker-compose.documenso.yml projects/hub/.env.example \
  projects/hub/deploy/firma.letsrebase.conf \
  projects/hub/packages/core/src/rebase_core/documenso.py \
  projects/hub/packages/core/src/rebase_core/cli.py \
  projects/hub/packages/core/tests/fakes_documenso.py \
  projects/hub/packages/core/tests/test_documenso.py \
  projects/hub/packages/core/tests/test_documenso_compose.py \
  infra/cloudflare/rebase.tf docs/adding-a-project.md projects/hub/AGENTS.md
git commit -F - <<'EOF'
feat(hub): run Documenso beside production, from a compose file of its own

I add docker-compose.documenso.yml with Documenso, pinned to the image the
probe ran, and its own Postgres on a host directory. Only production's .env
loads it through COMPOSE_FILE, because compose interpolates every service it
reads and the preview and CI must never need Documenso's secrets. Sign-up is
closed by default, the webhook may reach the API by its compose name, and
Documenso mails through the hub's Resend key. I add the firma.letsrebase.com
vhost and DNS record, port 8090 in the table, the deploy notes, and
`rebase documenso-check`, which says whether a hub reaches Documenso.

REB-393.
EOF
```

---

### Task 2: The rollout: DNS, certificate, settings, two tags, two Documenso users (REB-393, part 2 of 2)

Every step from Step 1 on is **Ivan approves**: the controller shows him the step, runs it only after his yes, shows the output, and stops at the first answer that differs from the expected one. Nothing here is a hand deploy: the stack moves only through the tags and the preview re-run Ivan asks for.

**Files:**
- Modify: `infra/cloudflare/rebase-imports.tf` (Step 3)
- Modify: `projects/hub/AGENTS.md` (Step 11)

**Interfaces:**
- Consumes: Task 1 merged on `main` and deployed to the preview (the preview's checkout then has `docker-compose.documenso.yml`, and its `.env` does not load it); phase 3's settings and webhook; `rebase documenso-check`.
- Produces: Documenso live at `https://firma.letsrebase.com`; the production and preview Documenso users, teams, tokens and webhooks; both hubs' `REBASE_DOCUMENSO_*` and `REBASE_CONTRACTS_MAIL` set.

- [ ] **Step 1: Ivan approves (read-only on the host): capacity, versions, and whether compose reads `COMPOSE_FILE` from `--env-file`**

```bash
ssh orbiters 'free -m; df -h /srv; docker compose version'
ssh orbiters 'docker exec rebase-db-1 psql -U rebase -d rebase -tAc "SELECT version_num FROM alembic_version"'
git fetch -q origin && git tag --list 'hub-v*' --sort=-v:refname | head -3
git log --oneline "$(git tag --list 'hub-v*' --sort=-v:refname | head -1)"..origin/main -- projects/hub | head -40
git diff "$(git tag --list 'hub-v*' --sort=-v:refname | head -1)" origin/main -- projects/hub | grep -n -i 'studio rossi\|example\.com' | head
```

Expected: at least 1 GiB available beyond today's use (Documenso about 630 MiB, its Postgres 80 MiB); Docker Compose v2; production's `alembic_version` the newest tag's head; the log lists phases 2, 3 and 4 and nothing unexpected; the grep finds no fixture outside tests and docs (a hit in a seed or a page stops the rollout until it is removed on `main`).

Then, on the preview's checkout, a throwaway env file that touches no stack. The preview's `DEPLOY_PATH` is the `hub-preview` environment's secret; on the host it is the path whose `.deployed` record (written by `_deploy-compose.yml` beside it) says `environment=hub-preview`, and Steps 8 and 9 use the same path:

```bash
ssh orbiters 'bash -s' <<'SH'
set -euo pipefail
PREVIEW=$( (grep -l '^environment=hub-preview$' /opt/*.deployed /srv/*.deployed 2>/dev/null || true) | head -1 | sed 's/\.deployed$//')
test -n "$PREVIEW" && echo "preview checkout: $PREVIEW"
cd "$PREVIEW/projects/hub"
cat > /tmp/documenso-check.env <<EOF
COMPOSE_FILE=docker-compose.yml:docker-compose.documenso.yml
POSTGRES_PASSWORD=x
REBASE_DATA_DIR=/tmp/documenso-check-data
REBASE_RESEND_API_KEY=x
REBASE_DOCUMENSO_DATA_DIR=/tmp/documenso-check-db
DOCUMENSO_DB_PASSWORD=x
DOCUMENSO_NEXTAUTH_SECRET=x
DOCUMENSO_ENCRYPTION_KEY=x
DOCUMENSO_ENCRYPTION_SECONDARY_KEY=x
DOCUMENSO_SIGNING_CERT_BASE64=x
DOCUMENSO_SIGNING_PASSPHRASE=x
EOF
docker compose -p documenso-check --env-file /tmp/documenso-check.env config --services
rm /tmp/documenso-check.env
SH
```

Expected: six services, `documenso` and `documenso-db` among them. Four means this compose ignores `COMPOSE_FILE` in an env file: stop, and Ivan decides between upgrading the compose plugin and another selection mechanism before anything else happens.

- [ ] **Step 2: Ivan approves (DNS): the record**

```bash
export TF_VAR_rebase_api_token=...   # letsrebase.com's token, from the password manager
terraform -chdir=infra/cloudflare init
terraform -chdir=infra/cloudflare plan
```

Expected: exactly one to add, `cloudflare_dns_record.rebase_firma_a`, and nothing to change or destroy. Read it twice, then:

```bash
terraform -chdir=infra/cloudflare apply
dig +short firma.letsrebase.com @1.1.1.1
```

Expected: `204.168.255.175`.

- [ ] **Step 3: Ivan approves (repository): the import block**

```bash
terraform -chdir=infra/cloudflare state show cloudflare_dns_record.rebase_firma_a | grep -E '^\s+id\s'
```

Append to `infra/cloudflare/rebase-imports.tf`, with the id it printed:

```hcl
import {
  to       = cloudflare_dns_record.rebase_firma_a
  id       = "904a60b42314c819a66340ae8c1a0e88/<the id state show printed>"
  provider = cloudflare.rebase
}
```

Run: `terraform -chdir=infra/cloudflare plan` (expected: no changes), then check the counts match: `grep -c '^resource "cloudflare_dns_record"' infra/cloudflare/rebase.tf` and `grep -c '^  to ' infra/cloudflare/rebase-imports.tf`. Commit on a branch and open a pull request:

```bash
git add infra/cloudflare/rebase-imports.tf
git commit -m "chore(infra): import the firma.letsrebase.com record" -m "I add the import block for the record REB-393 created, so a fresh clone rebuilds the state without touching it." -m "REB-393."
```

- [ ] **Step 4: Ivan approves (server): the vhost and its certificate**

```bash
scp projects/hub/deploy/firma.letsrebase.conf orbiters:/tmp/firma.letsrebase.conf
ssh orbiters 'cp /tmp/firma.letsrebase.conf /etc/nginx/sites-available/firma.letsrebase.conf \
  && ln -s /etc/nginx/sites-available/firma.letsrebase.conf /etc/nginx/sites-enabled/firma.letsrebase.conf \
  && nginx -t && systemctl reload nginx && rm /tmp/firma.letsrebase.conf'
ssh -t orbiters 'certbot --nginx -d firma.letsrebase.com && nginx -t && systemctl reload nginx'
curl -sI https://firma.letsrebase.com/ | head -8
```

Expected: `nginx -t` ok both times; certbot issues a certificate for `firma.letsrebase.com` alone; the `curl` answers `502 Bad Gateway` (nothing listens on 8090 yet) with `X-Robots-Tag: noindex, nofollow, noarchive` and `Referrer-Policy: no-referrer`.

- [ ] **Step 5: Ivan approves (server): the data directory, the signing certificate and production's `.env`**

```bash
ssh orbiters 'bash -s' <<'SH'
set -euo pipefail
umask 077
mkdir -p /root/backups /srv/rebase-data/documenso-postgres
cp /opt/hub/.env "/root/backups/hub-env-$(date +%F)-before-documenso"
work=$(mktemp -d)
pass=$(openssl rand -hex 24)
openssl req -x509 -newkey rsa:2048 -days 3650 -keyout "$work/key.pem" -out "$work/cert.pem" \
  -passout "pass:$pass" -subj "/CN=rebase firma elettronica/O=rebase/C=IT"
openssl pkcs12 -export -inkey "$work/key.pem" -passin "pass:$pass" -in "$work/cert.pem" \
  -out "$work/cert.p12" -passout "pass:$pass"
{
  echo
  echo '# --- Documenso, the signing site (REB-393) ---'
  echo 'COMPOSE_FILE=docker-compose.yml:docker-compose.documenso.yml'
  echo 'REBASE_DOCUMENSO_DATA_DIR=/srv/rebase-data/documenso-postgres'
  echo 'REBASE_DOCUMENSO_PORT=127.0.0.1:8090'
  echo 'DOCUMENSO_PUBLIC_URL=https://firma.letsrebase.com'
  echo "DOCUMENSO_DB_PASSWORD=$(openssl rand -hex 24)"
  echo "DOCUMENSO_NEXTAUTH_SECRET=$(openssl rand -hex 32)"
  echo "DOCUMENSO_ENCRYPTION_KEY=$(openssl rand -hex 32)"
  echo "DOCUMENSO_ENCRYPTION_SECONDARY_KEY=$(openssl rand -hex 32)"
  echo "DOCUMENSO_SIGNING_CERT_BASE64=$(base64 -w0 "$work/cert.p12")"
  echo "DOCUMENSO_SIGNING_PASSPHRASE=$pass"
  echo '# Open only until the two Documenso users exist (Step 7), then true.'
  echo 'DOCUMENSO_DISABLE_SIGNUP=false'
} >> /opt/hub/.env
cp /opt/hub/.env "/root/backups/hub-env-$(date +%F)-with-documenso"
shred -u "$work/key.pem" "$work/cert.pem" "$work/cert.p12"
rmdir "$work"
grep -c '^DOCUMENSO_' /opt/hub/.env
SH
```

Expected: `8` (the eight `DOCUMENSO_*` lines; the two `REBASE_DOCUMENSO_*` ones start differently), and nothing printed of any value. The encryption keys can never change once Documenso has stored anything with them: the second backup under `/root/backups` (the `...-with-documenso` one, taken right after they were written) is the copy of the keys; the first, `...-before-documenso`, stays the rollback point.

- [ ] **Step 6: Ivan asks for the tag: Documenso starts**

The tag is annotated, on the merge commit of Task 1's pull request, the version bumped by what `main` carries since the newest `hub-v*` (phases 2 to 4: a minor):

```bash
git tag -a hub-vX.Y.0 <merge commit> -m "hub-vX.Y.0: matches, contracts and Documenso (REB-387)"
git push origin hub-vX.Y.0
```

Watch «Deploy hub», job `production`: its last step lists every service `running`, `documenso` and `documenso-db` included. Then:

```bash
ssh orbiters 'curl -fsS --retry 20 --retry-delay 3 --retry-all-errors http://127.0.0.1:8090/api/health; echo; \
  curl -fsS http://127.0.0.1:8090/api/certificate-status; echo; \
  docker stats --no-stream --format "{{.Name}} {{.MemUsage}}" rebase-documenso-1 rebase-documenso-db-1; \
  docker exec rebase-db-1 psql -U rebase -d rebase -tAc "SELECT version_num FROM alembic_version"'
curl -sI https://firma.letsrebase.com/ | head -3
```

Expected: `{"status":"ok","checks":{"database":{"status":"ok"},"certificate":{"status":"ok"}}}`, `{"isAvailable":true}`, Documenso near 630 MiB, the hub's database at `0018`, and `https://firma.letsrebase.com/` answering 200 or a redirect to its sign-in.

- [ ] **Step 7: Ivan, in Documenso's UI: one user, organisation, team, token and webhook per environment**

In a private window, `https://firma.letsrebase.com/signup`:

1. Production: user `ciao+firma@letsrebase.com`, name «rebase», a typed signature. The confirmation mail reaches `ciao@letsrebase.com` (Gmail `u/4`): that is Resend working as Documenso's transport, which the probe could not run. Confirm, sign in.
2. `/o/<organisation>/settings/teams`, «Create team»: `rebase`.
3. `/t/rebase/settings/tokens`, «Create token»: name «hub produzione», expiry 12 months. Store it in the password manager.
4. `/t/rebase/settings/webhooks`, «Create Webhook»: URL `http://api:8000/api/hub/documenso/webhook`, Enabled, triggers `document.completed`, `document.rejected`, `document.cancelled`, secret the output of `openssl rand -hex 32` run on Ivan's own machine. Store the secret.
5. Sign out, then the preview in a new private window: user `ciao+firma-preview@letsrebase.com`, team `rebase-preview`, token «hub anteprima» (12 months), webhook `https://preview.letsrebase.com/api/hub/documenso/webhook` with its own secret. Store both.

Expected: two users, each with its own organisation and team; four values in the password manager and nowhere else. Write down today's date: both tokens expire twelve months from it.

- [ ] **Step 8: Ivan approves (server): sign-up closed, and both hubs' settings**

Production (`/opt/hub/.env`), in an editor on the host, pasting from the password manager:

```
DOCUMENSO_DISABLE_SIGNUP=true
REBASE_DOCUMENSO_URL=http://documenso:3000
REBASE_DOCUMENSO_API_TOKEN=<the production token of Step 7>
REBASE_DOCUMENSO_WEBHOOK_SECRET=<the production webhook secret of Step 7>
REBASE_CONTRACTS_MAIL=ciao@letsrebase.com
```

(`DOCUMENSO_DISABLE_SIGNUP=true` replaces the `false` line of Step 5.) The preview (its `DEPLOY_PATH` of Step 1, `.env`):

```
REBASE_DOCUMENSO_URL=https://firma.letsrebase.com
REBASE_DOCUMENSO_API_TOKEN=<the preview token of Step 7>
REBASE_DOCUMENSO_WEBHOOK_SECRET=<the preview webhook secret of Step 7>
REBASE_CONTRACTS_MAIL=ciao+firma-preview@letsrebase.com
REBASE_HUB_URL=https://preview.letsrebase.com/hub
REBASE_SIGNER_JSON='{"rebase-sede": "Milano (anteprima)", "rebase-cf": "00000000000", "rebase-piva": "00000000000", "rebase-codice-destinatario": "0000000", "rebase-pec": "anteprima@pec.example", "rebase-rappresentante": "Anteprima rebase"}'
```

The preview's signer is fiction on purpose: the preview is open and nothing on it may be a secret. Keep an existing `REBASE_HUB_URL` or `REBASE_SIGNER_JSON` line if the preview already has one, rather than writing a second. Production's `REBASE_SIGNER_JSON` stays as it is: its `rebase-*` fields wait for the SRL, and until then production refuses to send, by design.

Expected: `grep -c '^REBASE_DOCUMENSO_' /opt/hub/.env` answers `5` (the data directory and the port of Step 5, and the three settings), the same `grep` on the preview's `.env` answers `3`, and `grep -c '^DOCUMENSO_DISABLE_SIGNUP=true$' /opt/hub/.env` answers `1`.

- [ ] **Step 9: Ivan asks for the patch tag and the preview re-run; then the two checks**

A patch tag on the same merge commit applies production's `.env` (`hub-vX.Y.1`, annotated, «apply the Documenso settings»); a re-run of the latest successful «Deploy hub» preview run applies the preview's. Then:

```bash
ssh orbiters 'docker exec rebase-api-1 uv run --no-sync rebase documenso-check; \
  docker exec rebase-preview-api-1 uv run --no-sync rebase documenso-check'
curl -s -o /dev/null -w '%{http_code}\n' https://firma.letsrebase.com/signup
```

Expected: `Documenso risponde a http://documenso:3000 e accetta il token.` and `Documenso risponde a https://firma.letsrebase.com e accetta il token.` Then open `https://firma.letsrebase.com/signup` in a private window: no sign-up form. If the form is still there, add `location = /signup { return 404; }` inside the installed vhost's 443 server block, `nginx -t && systemctl reload nginx`, check again, and carry the same block into `projects/hub/deploy/firma.letsrebase.conf` with a pull request.

- [ ] **Step 10: Ivan approves: each environment's webhook reaches its hub, and neither token reaches the other's envelope**

In a `bash` on Ivan's machine (zsh's `read -p` means something else), a throwaway envelope per environment, made with the public example (no real data), never signed, sent to nobody (`distributionMethod: NONE`) and cancelled at once, which fires `DOCUMENT_CANCELLED` to that environment's webhook. The hub answers it 200 and ignores it, since it never recorded the envelope.

```bash
uv run python projects/hub/tools/build_contract_pdf.py lettera-di-incarico --public \
  --data projects/hub/content/contratti/incarico.esempio.json
PDF=projects/hub/content/contratti/dist/public/lettera-di-incarico-incarico.esempio.pdf
API=https://firma.letsrebase.com/api/v2
OFF='{"recipientSigningRequest":false,"recipientRemoved":false,"recipientSigned":false,"documentPending":false,"documentCompleted":false,"documentDeleted":false,"ownerDocumentCompleted":false,"ownerRecipientExpired":false,"ownerDocumentCreated":false}'
PAYLOAD='{"type":"DOCUMENT","title":"Prova REB-393","externalId":"prova-reb-393","recipients":[{"email":"ciao@letsrebase.com","name":"Prova","role":"SIGNER","fields":[{"type":"SIGNATURE","page":1,"positionX":10,"positionY":10,"width":20,"height":3}]}],"meta":{"distributionMethod":"NONE","emailSettings":'"$OFF"'}}'
throwaway() {
  id=$(curl -fsS -H "Authorization: $1" --form-string "payload=$PAYLOAD" -F "files=@$PDF;type=application/pdf" \
    "$API/envelope/create" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  curl -fsS -H "Authorization: $1" -H 'Content-Type: application/json' \
    -d "{\"envelopeId\":\"$id\",\"meta\":{\"distributionMethod\":\"NONE\"}}" "$API/envelope/distribute" >/dev/null
  curl -fsS -H "Authorization: $1" -H 'Content-Type: application/json' \
    -d "{\"envelopeId\":\"$id\",\"reason\":\"Prova REB-393\"}" "$API/envelope/cancel" >/dev/null
  echo "$id"
}
read -rsp 'token produzione: ' PROD; echo
read -rsp 'token anteprima: ' PREVIEW; echo
PROD_ID=$(throwaway "$PROD"); PREVIEW_ID=$(throwaway "$PREVIEW")
curl -s -o /dev/null -w 'production token on the preview envelope: %{http_code}\n' -H "Authorization: $PROD" "$API/envelope/$PREVIEW_ID"
curl -s -o /dev/null -w 'preview token on the production envelope: %{http_code}\n' -H "Authorization: $PREVIEW" "$API/envelope/$PROD_ID"
unset PROD PREVIEW
ssh orbiters 'docker logs --since 10m rebase-api-1 2>&1 | grep "documenso/webhook"; \
  docker logs --since 10m rebase-preview-api-1 2>&1 | grep "documenso/webhook"'
```

Expected: `404` both times (probe § 8); one `POST /api/hub/documenso/webhook ... 200` in each hub's log; and in each team's webhook log in Documenso's UI, the `DOCUMENT_CANCELLED` delivery answered 200. A production delivery that failed with «Webhook URL resolves to a private or loopback address» means `api` is not in the bypass list the container got: stop and read `docker exec rebase-documenso-1 env | grep SSRF`.

- [ ] **Step 11: Ivan approves: the notes that only the rollout knows, and the backups**

In `projects/hub/AGENTS.md`, under the accounts table of «Documenso, the signing site», add the line «Both API tokens were created on <the date of Step 7> with a 12-month expiry: rotate both, and each hub's `REBASE_DOCUMENSO_API_TOKEN`, before <the same date a year later>.», with the two dates written out, and commit it on a branch with a pull request (`docs(hub): record when the Documenso tokens expire`, body ending `REB-393.`). Put the rotation date in Ivan's calendar as well.

On the host:

```bash
ssh orbiters 'ls -la /srv/rebase-data; crontab -l 2>/dev/null | grep -i -E "backup|rebase-data"; ls /etc/cron.d 2>/dev/null'
```

Expected: `documenso-postgres` beside the hub's own data, and a backup job that covers `/srv/rebase-data`. If no job covers it, file a card in Linear (team rebase) for a backup of Documenso's Postgres rather than improvising one here: every signed contract lives there and in the hub's `signed_pdf`, and the hub's copy is not a backup of Documenso's audit trail.

- [ ] **Step 12: One signature end to end on the preview, once the texts are final**

This is spec § 9's «one document signed end to end on the preview», and it waits for Ivan and Lorenzo to mark both texts `status: final` on `main` (spec § 1f): until then «Invia per la firma» refuses on every environment, by design. When that commit has reached the preview, on `https://preview.letsrebase.com/hub/`, with a test identity of Ivan's (an alias of his own address) as the freelancer:

1. A card, its tax data, a match with a company request, «Invia per la firma». The contract mail arrives with «Firma il documento».
2. Sign the framework agreement on `firma.letsrebase.com`. Within seconds «Match e contratti» says `Firmato`; the signed copy arrives by mail, attached, at the test address and at `ciao+firma-preview@letsrebase.com`; the letter leaves on its own and its mail arrives.
3. Sign the letter: the match becomes `Attivo`; the member area's «Contratti» shows both documents signed, with their copies.
4. `pdfsig` on the downloaded copy: `Signature is Valid.`, `Certificate issuer isn't Trusted.`, one page more than the original.
5. The production token on this preview envelope: `curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: <production token>" https://firma.letsrebase.com/api/v2/envelope/<the letter's envelope id>` answers `404`.

Expected: each point as written. Any other answer is a bug to file against REB-390, REB-391 or REB-392, not something to patch on the server.

---

## Self-Review

**Spec coverage (phase 4, § 10 item 4, § 7).** The compose services with the pinned tag and digest, the data bind with no default, a loopback port from the table: Task 1 Steps 4, 5 and 10. Documenso's environment with the probe's names, Resend as its mail sender, sign-up disabled, the SSRF bypass host: Task 1 Step 4, proven locally in Step 7 and on the server in Task 2 Steps 6, 9 and 10. The vhost and DNS for `firma.letsrebase.com`: Task 1 Steps 8 and 9, Task 2 Steps 2 to 4. The certificate: Task 2 Step 5. One Documenso user and organisation per environment (probe § 8, overriding the spec's one team): Task 2 Step 7, checked in Step 10. The hub settings on production and preview: Task 2 Steps 8 and 9. The deploy notes: Task 1 Step 10 and Task 2 Step 11. Spec § 9's end-to-end signature on the preview: Task 2 Step 12, gated on the texts leaving `status: draft`.

**Placeholder scan.** The only values the plan does not write are secrets and what exists only at rollout time (the Cloudflare record's id, the tokens, the tag's version, the dates of Step 7), each with the command or the screen that produces it.

**Type consistency.** `client_from_settings(settings, http=None)` keeps phase 3's call sites valid; `documenso_check(settings, http=None) -> int` matches its test and the CLI's dispatch; the host variable names are the same in the compose file, `.env.example`, the test and Task 2 Step 5.

**Review Focus.** Lines 1 to 3 have tests in Task 1 and checks in Task 2; lines 4 and 5 are server facts, checked in Task 2 Steps 10, 1 and 6.

## Execution

Task 1 is a subagent's pull request like any other (REB-393). Task 2 is run by the controller with Ivan, step by step, since every step needs his yes; it is not handed to an unattended subagent.
