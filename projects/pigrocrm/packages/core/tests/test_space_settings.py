"""Settings a database decides for itself, laid over the environment's."""

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import PermissionDenied
from pigrocrm.core.space_settings import SpaceSettingsService, SpaceSettingsUpdate, apply_overrides

ADMIN = Actor(id=None, type="mcp", role="admin")
BASE = Settings(
    _env_file=None,  # type: ignore[call-arg]
    public_url="https://pigro.example/studio",
)


def test_apply_overrides_gives_values_their_types_back_and_touches_nothing_else() -> None:
    effective = apply_overrides(
        BASE,
        {
            "google_client_id": "abc.apps",
            "mcp_full_access": "true",
            "solleciti_grace_days": "12",
            "storage_backend": "gdrive",
            "concentrazione_soglia_preferita": "0.45",
            "database_url": "postgresql://evil",  # not overridable: ignored
        },
    )
    assert effective.google_client_id == "abc.apps"
    assert effective.mcp_full_access is True
    assert effective.solleciti_grace_days == 12
    assert effective.storage_backend == "gdrive"
    assert effective.concentrazione_soglia_preferita == 0.45
    assert effective.database_url == BASE.database_url
    assert apply_overrides(BASE, {}) is BASE


def test_a_value_the_environment_would_refuse_is_refused_from_the_database_too() -> None:
    with pytest.raises(ValueError):
        apply_overrides(BASE, {"solleciti_max_reminders": "9"})
    with pytest.raises(ValueError):
        apply_overrides(BASE, {"concentrazione_soglia_preferita": "1.5"})


def test_update_writes_rows_makes_a_token_key_once_and_never_shows_secrets(
    db_session: Session,
) -> None:
    service = SpaceSettingsService(db_session, BASE)
    read = service.update(
        SpaceSettingsUpdate(
            google_client_id="abc.apps", google_client_secret="shh", mcp_full_access=True
        ),
        ADMIN,
        spazio="studio",
    )
    assert read.google_client_id == "abc.apps"
    assert read.google_client_secret_impostato is True
    assert read.google_token_key_impostata is True
    assert read.gmail_configurato is True
    assert read.redirect_uri_gmail == "https://pigro.example/studio/api/gmail/oauth/callback"
    assert read.mcp_full_access is True
    assert "shh" not in read.model_dump_json()
    assert set(read.sovrascritte) == {
        "google_client_id",
        "mcp_full_access",
        "google_client_secret",
        "google_token_key",
    }
    key_before = service.overrides()["google_token_key"]

    # A second save keeps the same key: rotating it would orphan every stored token.
    service.update(SpaceSettingsUpdate(solleciti_grace_days=10), ADMIN, spazio="studio")
    assert service.overrides()["google_token_key"] == key_before
    assert service.effective().solleciti_grace_days == 10
    assert service.effective().concentrazione_soglia_preferita == 0.30


def test_an_empty_string_clears_an_override(db_session: Session) -> None:
    service = SpaceSettingsService(db_session, BASE)
    service.update(SpaceSettingsUpdate(google_client_id="abc"), ADMIN, spazio=None)
    read = service.update(SpaceSettingsUpdate(google_client_id=""), ADMIN, spazio=None)
    assert read.google_client_id == ""
    assert "google_client_id" not in read.sovrascritte


def test_only_an_admin_reads_or_writes(db_session: Session) -> None:
    service = SpaceSettingsService(db_session, BASE)
    with pytest.raises(PermissionDenied):
        service.read(Actor(id=None, type="mcp", role="collaboratore"), spazio=None)
    with pytest.raises(PermissionDenied):
        service.update(
            SpaceSettingsUpdate(mcp_full_access=True),
            Actor(id=None, type="mcp", role="collaboratore"),
            spazio=None,
        )
