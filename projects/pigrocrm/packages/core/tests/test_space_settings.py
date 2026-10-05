"""Settings a database decides for itself, laid over the environment's."""

import base64

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import PermissionDenied
from pigrocrm.core.space_settings import SpaceSettingsService, SpaceSettingsUpdate, apply_overrides
from pigrocrm.core.tenants import space_base_settings

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


def _borrowing() -> Settings:
    """A space called `studio` while the root lends its Google client (REB-394)."""
    root = Settings(
        _env_file=None,  # type: ignore[call-arg]
        public_url="https://pigro.example",
        google_client_id="root.apps",
        google_client_secret="root-secret",
        google_token_key=base64.b64encode(b"r" * 32).decode(),
        google_shared_client=True,
    )
    return space_base_settings(root, "studio")


def test_a_borrowing_space_reads_that_it_borrows_and_nothing_to_set(
    db_session: Session,
) -> None:
    read = SpaceSettingsService(db_session, _borrowing()).read(ADMIN, spazio="studio")
    assert read.gmail_configurato is True
    assert read.google_client_condiviso is True
    # The space's own addresses, for a client of its own: the borrowed one needs none.
    assert read.redirect_uri_gmail == "https://pigro.example/studio/api/gmail/oauth/callback"
    assert read.public_url == "https://pigro.example/studio"
    assert read.sovrascritte == []
    assert "root-secret" not in read.model_dump_json()


def test_a_borrowing_space_that_sets_its_own_client_gets_its_own_key_and_callback(
    db_session: Session,
) -> None:
    """The derived key belongs to the root's client. A client of the space's own gets a
    key made the way it always was, and its own callback back."""
    base = _borrowing()
    service = SpaceSettingsService(db_session, base)
    read = service.update(
        SpaceSettingsUpdate(google_client_id="own.apps", google_client_secret="own"),
        ADMIN,
        spazio="studio",
    )
    assert read.google_client_condiviso is False
    assert read.gmail_configurato is True
    assert read.redirect_uri_gmail == "https://pigro.example/studio/api/gmail/oauth/callback"
    own_key = service.overrides()["google_token_key"]
    assert own_key != base.google_token_key
    assert service.effective().google_token_key == own_key


# ---- the version check (REB-622, spec 2026-10-03 §11) ----------------------------------


def test_a_read_and_the_version_see_a_row_changed_behind_the_session(db_session: Session) -> None:
    """The request's dependency loads the rows before any service runs, so the session
    already holds them when `update` compares versions under the lock: a row another
    admin committed in between must be read as it is now, not as the identity map
    remembers it. Simulated by an UPDATE outside the ORM on the same connection."""
    from datetime import timedelta

    from sqlalchemy import text

    service = SpaceSettingsService(db_session, BASE)
    first = service.update(SpaceSettingsUpdate(gmail_backfill_days=10), ADMIN, spazio="studio")
    assert first.updated_at is not None
    assert service.read(ADMIN, spazio="studio").gmail_backfill_days == 10  # rows now in the session
    later = first.updated_at + timedelta(minutes=1)
    db_session.execute(
        text(
            "UPDATE space_settings SET value = '20', updated_at = :at "
            "WHERE key = 'gmail_backfill_days'"
        ),
        {"at": later},
    )
    db_session.execute(
        text("UPDATE space_settings SET updated_at = :at WHERE key = '_version'"), {"at": later}
    )
    again = service.read(ADMIN, spazio="studio")
    assert again.gmail_backfill_days == 20
    assert again.updated_at == later
    assert service.version() == later
    from pigrocrm.core.errors import StaleRow

    with pytest.raises(StaleRow):
        service.update(
            SpaceSettingsUpdate(gmail_backfill_days=30, updated_at=first.updated_at),
            ADMIN,
            spazio="studio",
        )


def test_the_version_is_the_newest_override_row_and_a_stale_save_is_refused(
    db_session: Session,
) -> None:
    from pigrocrm.core.errors import StaleRow

    service = SpaceSettingsService(db_session, BASE)
    assert service.version() is None
    assert service.read(ADMIN, spazio="studio").updated_at is None

    # A draft built on the empty state says so with an explicit `null`, and is accepted
    # while nothing has been written.
    first = service.update(
        SpaceSettingsUpdate(mcp_full_access=True, updated_at=None), ADMIN, spazio="studio"
    )
    assert first.updated_at is not None
    assert first.mcp_full_access is True

    # The same `null` once a row exists is a draft built on a state that is gone.
    with pytest.raises(StaleRow) as excinfo:
        service.update(
            SpaceSettingsUpdate(gmail_backfill_days=10, updated_at=None), ADMIN, spazio="studio"
        )
    assert excinfo.value.code == "stale_row"
    assert excinfo.value.details == {"entity": "space_settings", "updated_at": first.updated_at}
    assert service.effective().gmail_backfill_days == BASE.gmail_backfill_days

    # The current version is accepted, moves the version, and the previous one is stale.
    second = service.update(
        SpaceSettingsUpdate(gmail_backfill_days=10, updated_at=first.updated_at),
        ADMIN,
        spazio="studio",
    )
    assert second.gmail_backfill_days == 10
    assert second.updated_at is not None and second.updated_at > first.updated_at
    with pytest.raises(StaleRow):
        service.update(
            SpaceSettingsUpdate(gmail_backfill_days=20, updated_at=first.updated_at),
            ADMIN,
            spazio="studio",
        )

    # A body that leaves the key out is an older client: nothing is checked, and the
    # version row never reaches `Settings` or the page.
    third = service.update(SpaceSettingsUpdate(gmail_backfill_days=20), ADMIN, spazio="studio")
    assert third.gmail_backfill_days == 20
    assert "_version" not in service.overrides()
    assert "_version" not in third.sovrascritte

    # Clearing an override moves the version even when a newer row stays on top: the
    # client id was written first, the backfill after it, and the clear touches no
    # override row's `updated_at`; the reserved row is what moves.
    with_client = service.update(
        SpaceSettingsUpdate(google_client_id="abc.apps", updated_at=third.updated_at),
        ADMIN,
        spazio="studio",
    )
    newer = service.update(
        SpaceSettingsUpdate(gmail_backfill_days=25, updated_at=with_client.updated_at),
        ADMIN,
        spazio="studio",
    )
    cleared = service.update(
        SpaceSettingsUpdate(google_client_id="", updated_at=newer.updated_at),
        ADMIN,
        spazio="studio",
    )
    assert "google_client_id" not in cleared.sovrascritte
    assert cleared.updated_at is not None and cleared.updated_at > newer.updated_at  # type: ignore[operator]
    # A save that changes nothing leaves the version where it was.
    same = service.update(
        SpaceSettingsUpdate(updated_at=cleared.updated_at), ADMIN, spazio="studio"
    )
    assert same.updated_at == cleared.updated_at
