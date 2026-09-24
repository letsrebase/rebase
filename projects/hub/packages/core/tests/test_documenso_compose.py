"""Documenso as production runs it (REB-393): the image phase 1 probed, pinned by digest;
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
PROBED = (
    "documenso/documenso:v2.18.0"
    "@sha256:126976b9e3be54193e1a3be8d22130af1913aaa894c550b98870a2cc4c422650"
)


def _documenso() -> str:
    return DOCUMENSO.read_text(encoding="utf-8")


def _compose() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def test_documenso_runs_the_image_phase_1_probed_pinned_by_digest() -> None:
    assert f"image: {PROBED}" in _documenso()


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


def test_the_hub_compose_file_runs_the_sweep_on_a_loop_with_no_port() -> None:
    """`rebase contracts-sweep` (REB-391) scheduled by this repository, on both stacks
    (REB-393): a loop, since `_deploy-compose.yml` fails a deploy on any container not
    `running`, and no port, like `db` between `db` and `api` publishes for nothing else."""
    text = _compose()
    assert "while :; do sleep 600; uv run --no-sync rebase contracts-sweep; done" in text
    assert "init: true" in text
    services = text.split("\nservices:", 1)[1]
    sweep = services.split("\n  sweep:", 1)[1].split("\n  web:", 1)[0]
    assert "environment: *api-environment" in sweep
    assert "ports:" not in sweep


def test_documenso_compose_file_carries_no_sweep_of_its_own() -> None:
    assert "sweep" not in _documenso()
