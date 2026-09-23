import base64
from pathlib import Path

import pytest

from pigrocrm.core.config import (
    GOOGLE_TOKEN_KEY_BYTES,
    Settings,
    decode_google_token_key,
    gmail_configured,
    require_gmail_configured,
)
from pigrocrm.core.errors import Conflict

VALID_KEY = base64.b64encode(b"k" * GOOGLE_TOKEN_KEY_BYTES).decode()


def _settings(**overrides: object) -> Settings:
    # _env_file=None so a developer's own .env cannot make this suite pass or fail.
    base: dict[str, object] = {"jwt_secret": "x" * 32, "_env_file": None}
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def test_gmail_is_absent_when_no_client_id_is_set() -> None:
    assert gmail_configured(_settings()) is False


def test_gmail_needs_all_four_values_not_just_the_client_id() -> None:
    whole = {
        "google_client_id": "cid",
        "google_client_secret": "secret",
        "google_token_key": VALID_KEY,
        "public_url": "https://crm.example.it",
    }
    assert gmail_configured(_settings(**whole)) is True

    # Each of the four on its own is enough to make Gmail absent. Asserting only one
    # partial combination would pass against a check that reads `google_client_id` and
    # stops, which is exactly the half-configured install this function exists to catch.
    for missing in whole:
        partial = {key: value for key, value in whole.items() if key != missing}
        assert gmail_configured(_settings(**partial)) is False, missing


def test_an_unconfigured_install_gets_a_sentence_not_a_stack_trace() -> None:
    with pytest.raises(Conflict) as caught:
        require_gmail_configured(_settings())
    assert "Gmail non è configurato su questa installazione" in caught.value.message
    assert caught.value.code == "conflict"


def test_require_is_silent_once_everything_is_set() -> None:
    require_gmail_configured(
        _settings(
            google_client_id="cid",
            google_client_secret="secret",
            google_token_key=VALID_KEY,
            public_url="https://crm.example.it",
        )
    )


def test_the_token_key_must_be_thirty_two_bytes_of_base64() -> None:
    for bad, why in [
        ("", "missing"),
        ("not-base64!!", "not base64"),
        (base64.b64encode(b"short").decode(), "wrong length"),
        # Valid base64, right shape, wrong size in the other direction: a 64-byte key
        # is the mistake someone makes running `openssl rand -base64 64`.
        (base64.b64encode(b"k" * 64).decode(), "too long"),
    ]:
        with pytest.raises(ValueError, match="PIGROCRM_GOOGLE_TOKEN_KEY") as caught:
            decode_google_token_key(_settings(google_token_key=bad))
        if not bad:
            # `"" in anything` is True, so the leak check below says nothing about the
            # empty key. There is no value to leak in that case either.
            continue
        # The message names the variable and the requirement, and never the value:
        # a key that reached a log or an exception message is a leaked key. The chained
        # cause is checked too -- binascii.Error quotes the offending input in some
        # builds, and `raise ... from exc` carries it to any handler that formats the
        # whole chain.
        rendered = str(caught.value) + repr(caught.value.__cause__)
        assert bad not in rendered, why


def test_a_valid_token_key_decodes_to_exactly_thirty_two_bytes() -> None:
    assert decode_google_token_key(_settings(google_token_key=VALID_KEY)) == (
        b"k" * GOOGLE_TOKEN_KEY_BYTES
    )


def test_the_settings_repr_never_carries_the_secrets() -> None:
    """`Settings` is passed around and lands in logs, tracebacks and debugger frames.
    The token key decrypts a third-party refresh token, and the client secret is the
    other half of the OAuth credential; neither may be readable from a repr."""
    rendered = repr(
        _settings(
            google_client_secret="il-segreto-oauth",
            google_token_key=VALID_KEY,
        )
    )
    assert "il-segreto-oauth" not in rendered
    assert VALID_KEY not in rendered


def test_the_batch_size_is_configurable_without_touching_code() -> None:
    # Spec 4.1: twenty addresses with two clauses each fit Gmail's practical `q`
    # length with margin, but the number has to be correctable from the environment.
    assert _settings().gmail_sync_address_batch_size == 20
    assert _settings(gmail_sync_address_batch_size=8).gmail_sync_address_batch_size == 8


def test_the_documented_defaults_are_the_documented_values() -> None:
    settings = _settings()
    assert settings.gmail_backfill_days == 90
    assert settings.gmail_watermark_overlap_hours == 24
    assert settings.gmail_body_max_bytes == 262_144
    assert settings.gmail_attachment_max_bytes == 20 * 1024 * 1024
    assert settings.gmail_send_grace_minutes == 15
    assert settings.google_app_unverified is False


def test_the_env_example_documents_every_gmail_variable() -> None:
    """`.env.example` is the only place an operator learns these exist. A variable added
    to `Settings` and not to the example is a feature nobody can turn on."""
    example = (Path(__file__).resolve().parents[3] / ".env.example").read_text(encoding="utf-8")
    for variable in (
        "PIGROCRM_GOOGLE_CLIENT_ID",
        "PIGROCRM_GOOGLE_CLIENT_SECRET",
        "PIGROCRM_GOOGLE_TOKEN_KEY",
        "PIGROCRM_PUBLIC_URL",
        "PIGROCRM_GOOGLE_APP_UNVERIFIED",
        "PIGROCRM_GOOGLE_SHARED_CLIENT",
    ):
        assert f"\n{variable}=" in example, variable

    # Empty on purpose: a fresh checkout must be an install *without* Gmail, so nobody
    # inherits a half-configured OAuth client from a file they copied.
    assert "\nPIGROCRM_GOOGLE_CLIENT_ID=\n" in example
