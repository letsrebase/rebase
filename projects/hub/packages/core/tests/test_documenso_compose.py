"""Documenso as production runs it (REB-393): the pinned image, by tag and digest;
a loopback port and an unpublished database; nobody can open an account; the webhook may
reach the API by its compose name; and a compose file of its own, so the preview's deploy
and CI's image build never need Documenso's secrets. The `sweep` service (also REB-393)
runs `rebase contracts-sweep` on both stacks, from the hub's own compose file, since the
preview never loads docker-compose.documenso.yml. The files are read as text, since what
matters is what they say; compose itself runs in the rollout."""

import re
from pathlib import Path

HUB = Path(__file__).resolve().parents[3]
COMPOSE = HUB / "docker-compose.yml"
DOCUMENSO = HUB / "docker-compose.documenso.yml"
EXAMPLE = HUB / ".env.example"
BACKUP_DOCKERFILE = HUB / "documenso-backup" / "Dockerfile"
DEPLOY = HUB.parents[1] / ".github" / "workflows" / "deploy-hub.yml"
# Phase 1 probed v2.18.0; v2.20.0 replaced it after its release notes, which list no
# breaking change, migration or new variable for v2.19 or v2.20 (REB-712).
PINNED = (
    "documenso/documenso:v2.20.0"
    "@sha256:05d2ede9fb87bfd8eedc11d81b8bdee43c3e775ea46c4fb64973153ca5ef8eb8"
)


def _documenso() -> str:
    return DOCUMENSO.read_text(encoding="utf-8")


def _compose() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def test_documenso_runs_the_pinned_image_by_digest() -> None:
    assert f"image: {PINNED}" in _documenso()


def test_the_hub_compose_file_starts_no_documenso() -> None:
    """Compose interpolates every service of every file it loads, profiles or not: a
    Documenso service here would make the preview's deploy and CI's build need its
    secrets. Only production's `.env` loads the second file."""
    text = _compose()
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


def test_the_signup_window_is_gated_to_letsrebase_com_on_the_server_side() -> None:
    """REB-393: `DOCUMENSO_DISABLE_SIGNUP` alone leaves a window, while it is `false`,
    where a name already in certificate transparency logs could sign up; the image's own
    `NEXT_PRIVATE_ALLOWED_SIGNUP_DOMAINS` (`isEmailDomainAllowedForSignup`, read from the
    image, read-only) closes it, since both planned users are `@letsrebase.com`."""
    text = _documenso()
    assert (
        "NEXT_PRIVATE_ALLOWED_SIGNUP_DOMAINS: ${DOCUMENSO_SIGNUP_DOMAINS:-letsrebase.com}" in text
    )


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
    managed = _managed_secrets()
    # The hub's own Resend key is already there, uncommented: Documenso reuses it.
    for name in required - {"REBASE_RESEND_API_KEY"}:
        if name in managed:
            # Written by the deploy, never by hand: the example names it and says so.
            assert f"#   {name} " in example, name
        else:
            assert f"# {name}=" in example, name
    # Commented: a `.env` copied from the example loads no Documenso until someone means it.
    assert "# COMPOSE_FILE=docker-compose.yml:docker-compose.documenso.yml" in example
    assert "\nCOMPOSE_FILE=" not in example


def test_the_hub_compose_file_runs_the_sweep_on_a_loop_with_no_port() -> None:
    """`rebase contracts-sweep` (REB-391) scheduled by this repository, on both stacks
    (REB-393): a loop, since `_deploy-compose.yml` fails a deploy on any container not
    `running`, and no port, since nothing ever calls the sweep directly."""
    services = _compose().split("\nservices:", 1)[1]
    sweep = services.split("\n  sweep:", 1)[1].split("\n  cards:", 1)[0]
    assert "while :; do sleep 600; uv run --no-sync rebase contracts-sweep; done" in sweep
    assert "init: true" in sweep
    assert "environment: *api-environment" in sweep
    assert "ports:" not in sweep


def test_the_hub_compose_file_runs_the_cards_backlog_hourly_with_no_port() -> None:
    """`rebase cards-refresh --limit 50` (REB-552): a loop like the sweep's, once an
    hour, so a card a provider outage retired comes back within the hour, without a
    manual run. It sleeps first, like `sweep`, so the first pass waits for the api's
    migrations, and it is gated by `REBASE_CARDS_LOOP`."""
    services = _compose().split("\nservices:", 1)[1]
    cards = services.split("\n  cards:", 1)[1].split("\n  web:", 1)[0]
    assert (
        'while :; do sleep 3600; if [ "${REBASE_CARDS_LOOP:-off}" = "on" ]; then uv run '
        '--no-sync rebase cards-refresh --limit 50; else echo "cards: loop off '
        '(REBASE_CARDS_LOOP)"; fi; done'
    ) in cards
    assert "init: true" in cards
    assert "environment: *api-environment" in cards
    assert "ports:" not in cards


def test_documenso_compose_file_carries_no_sweep_of_its_own() -> None:
    assert "sweep" not in _documenso()


def test_the_hub_compose_file_runs_the_campaigns_loop_every_minute_with_no_port() -> None:
    """`rebase campaigns-tick` (P-REB-41): a loop like the sweep's, every minute, since
    «Invia adesso» promises the mail within one."""
    services = _compose().split("\nservices:", 1)[1]
    loop = services.split("\n  campaigns:", 1)[1].split("\n  sweep:", 1)[0]
    assert "while :; do sleep 60; uv run --no-sync rebase campaigns-tick; done" in loop
    assert "init: true" in loop
    assert "environment: *api-environment" in loop
    assert "ports:" not in loop


def _backup_service() -> str:
    return _documenso().split("\n  documenso-backup:", 1)[1]


def _managed_secrets() -> set[str]:
    """The names `deploy-hub.yml`'s production job writes into the host `.env` from the
    `hub-production` Environment (`env-secrets`)."""
    deploy = DEPLOY.read_text(encoding="utf-8")
    assert deploy.count("env-secrets:") == 1, "only production templates secrets into .env"
    assert deploy.index("env-secrets:") > deploy.index("\n  production:")
    block = re.search(r"env-secrets: \|\n((?:[ ]+[A-Z0-9_]+\n)+)", deploy)
    assert block is not None
    return set(block.group(1).split())


def test_the_nightly_backup_is_a_loop_with_no_port_in_productions_file_only() -> None:
    """REB-715: a loop and not a one-shot, since `_deploy-compose.yml` fails a deploy on
    any container that is not `running`; no port, since nothing ever calls it; and not in
    the hub's own compose file, which the preview loads and which must never need the
    backup's secrets."""
    service = _backup_service()
    assert "entrypoint: ['/bin/bash', '/scripts/backup-loop.sh']" in service
    assert "restart: unless-stopped" in service
    assert "init: true" in service
    assert "ports:" not in service
    assert "condition: service_healthy" in service
    assert "documenso-backup" not in _compose()


def test_the_backup_dumps_with_the_same_postgres_the_database_runs_pinned_by_digest() -> None:
    """A `pg_dump` older than its server refuses to run; a newer one writes an archive an
    older `pg_restore` cannot read. The backup image is built FROM the database's tag, and
    by digest as well: `build --pull` on the deploy would otherwise move the host's tag and
    recreate every database container that names it."""
    database = re.search(r"\n  documenso-db:\n    image: (postgres:\S+)\n", _documenso())
    assert database is not None
    dockerfile = BACKUP_DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(
        rf"^FROM {re.escape(database.group(1))}@sha256:[0-9a-f]{{64}}$", dockerfile, re.MULTILINE
    )


def test_the_backup_is_encrypted_to_a_certificate_and_its_secrets_are_the_deploys() -> None:
    """The certificate is a hand-written line of `.env` (it is public); the R2 key pair and
    the monitor's URL are Environment secrets the production deploy templates in, and the
    compose file requires every one of them, so a deploy that lost one refuses to start
    rather than running a backup that cannot upload or cannot be heard."""
    required = set(re.findall(r"\$\{(DOCUMENSO_BACKUP_[A-Z0-9_]+):\?", _backup_service()))
    assert required == {
        "DOCUMENSO_BACKUP_CERT_B64",
        "DOCUMENSO_BACKUP_R2_ACCOUNT_ID",
        "DOCUMENSO_BACKUP_BUCKET",
        "DOCUMENSO_BACKUP_ACCESS_KEY_ID",
        "DOCUMENSO_BACKUP_SECRET_ACCESS_KEY",
        "DOCUMENSO_BACKUP_PING_URL",
    }
    assert _managed_secrets() == {
        "DOCUMENSO_BACKUP_ACCESS_KEY_ID",
        "DOCUMENSO_BACKUP_SECRET_ACCESS_KEY",
        "DOCUMENSO_BACKUP_PING_URL",
    }
