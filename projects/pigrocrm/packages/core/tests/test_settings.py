"""What `Settings` refuses at construction, what it keeps out of a repr, and what the API
refuses to boot with."""

import pytest
from pydantic import ValidationError

from pigrocrm.core.config import (
    DEFAULT_JWT_SECRET,
    KNOWN_PLACEHOLDER_JWT_SECRETS,
    MIN_SERVICE_TOKEN_LENGTH,
    Settings,
    require_production_secrets,
)

REAL_SECRET = "a-real-secret-that-nobody-published-0123456789"


def _settings(**overrides: object) -> Settings:
    # `_env_file=None` so a developer's own `.env` cannot make this suite pass or fail.
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("jwt_secret", "jwt-secret-that-must-stay-out-of-logs-0123456789"),
        ("database_url", "postgresql+psycopg://app:hunter2-app@db:5432/pigrocrm"),
        ("admin_database_url", "postgresql+psycopg://owner:hunter2-owner@db:5432/pigrocrm"),
        ("tenants_database_url", "postgresql+psycopg://reg:hunter2-registry@db:5432/tenants"),
        ("gdrive_service_account_json", '{"private_key": "gdrive-private-key-material"}'),
        ("posthog_key", "phc_posthog-project-key-0123456789"),
        ("registry_token", "registry-bearer-token-0123456789-0123456789"),
        ("engagements_token", "engagements-bearer-token-0123456789-0123456789"),
        ("google_client_secret", "google-client-secret-value"),
        ("google_token_key", "google-token-key-value"),
        ("slack_client_secret", "slack-client-secret-value"),
        ("slack_token_key", "slack-token-key-value"),
        ("resend_api_key", "re_resend-api-key-value"),
        ("vapid_private_key", "vapid-private-key-value"),
    ],
)
def test_a_credential_never_reaches_a_repr(field: str, value: str) -> None:
    """A `Settings` object ends up in logs, tracebacks and debugger frames."""
    settings = _settings(**{field: value})
    assert value not in repr(settings)
    assert value not in str(settings)


def test_a_public_value_stays_visible_in_the_repr() -> None:
    assert "https://letsrebase.test" in repr(_settings(public_url="https://letsrebase.test"))


@pytest.mark.parametrize("placeholder", sorted(KNOWN_PLACEHOLDER_JWT_SECRETS))
def test_the_api_refuses_to_boot_on_a_published_signing_key(placeholder: str) -> None:
    with pytest.raises(ValueError, match="PIGROCRM_JWT_SECRET") as caught:
        require_production_secrets(_settings(jwt_secret=placeholder))
    assert placeholder not in str(caught.value)


def test_a_bare_settings_carries_the_placeholder_and_is_refused() -> None:
    """The default is what a CLI command or a test builds, so it must stay constructible;
    the boot of the API is where it is turned down."""
    assert _settings().jwt_secret == DEFAULT_JWT_SECRET
    with pytest.raises(ValueError, match="PIGROCRM_JWT_SECRET"):
        require_production_secrets(_settings())


def test_a_real_signing_key_boots() -> None:
    require_production_secrets(_settings(jwt_secret=REAL_SECRET))


def test_plain_http_development_may_keep_the_placeholder() -> None:
    """`cookie_secure=false` is the documented switch for a development checkout."""
    require_production_secrets(_settings(cookie_secure=False))


@pytest.mark.parametrize("field", ["registry_token", "engagements_token"])
def test_a_bearer_token_is_empty_or_long_enough(field: str) -> None:
    assert getattr(_settings(), field) == ""
    assert getattr(_settings(**{field: "t" * MIN_SERVICE_TOKEN_LENGTH}), field)
    with pytest.raises(ValidationError, match=field):
        _settings(**{field: "t" * (MIN_SERVICE_TOKEN_LENGTH - 1)})


@pytest.mark.parametrize(
    ("field", "lowest", "highest"),
    [
        ("access_token_minutes", 1, 1440),
        ("refresh_token_days", 1, 3650),
        ("identity_token_days", 1, 3650),
        ("login_requests_per_minute", 1, 10_000),
        ("magic_link_minutes", 1, 120),
        ("max_spaces", 1, None),
        ("gmail_sync_address_batch_size", 1, 100),
        ("gmail_backfill_days", 1, 3650),
        ("gmail_watermark_overlap_hours", 0, 720),
        ("gmail_body_max_bytes", 1, 10_485_760),
        ("gmail_attachment_max_bytes", 1, 52_428_800),
        ("gmail_send_grace_minutes", 1, 1440),
    ],
)
def test_an_integer_setting_refuses_a_value_outside_its_range(
    field: str, lowest: int, highest: int | None
) -> None:
    assert getattr(_settings(**{field: lowest}), field) == lowest
    with pytest.raises(ValidationError, match=field):
        _settings(**{field: lowest - 1})
    if highest is not None:
        assert getattr(_settings(**{field: highest}), field) == highest
        with pytest.raises(ValidationError, match=field):
            _settings(**{field: highest + 1})


def test_the_cors_origins_are_split_stripped_and_never_empty() -> None:
    assert _settings().cors_origin_list == []
    assert _settings(cors_origins=" https://a.test , https://b.test,, ").cors_origin_list == [
        "https://a.test",
        "https://b.test",
    ]
