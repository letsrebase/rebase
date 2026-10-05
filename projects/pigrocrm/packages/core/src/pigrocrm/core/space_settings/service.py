import base64
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import GOOGLE_TOKEN_KEY_BYTES, Settings, gmail_configured
from pigrocrm.core.space_settings.models import SpaceSetting
from pigrocrm.core.space_settings.schemas import (
    OVERRIDABLE_KEYS,
    SECRET_KEYS,
    SpaceSettingsRead,
    SpaceSettingsUpdate,
)
from pigrocrm.core.versioning import require_unchanged

ENTITY = "space_settings"
# The timeline wants an entity id and there is exactly one settings object per
# database, so it gets one fixed id.
SETTINGS_ID = UUID("00000000-0000-0000-0000-00000000c0f6")
# The row that carries the version a save is checked against (REB-622). Not in
# `OVERRIDABLE_KEYS`, so `overrides()` never hands it to `Settings` and the page never
# lists it; written on every write that changed something, so clearing an override under
# a newer one moves the version too, which `max(updated_at)` over the overrides alone
# would not. Its value is the stamp as text, only so that each write is an UPDATE the
# ORM emits (an unchanged value emits none, and `onupdate` would not fire).
VERSION_KEY = "_version"
# The advisory lock a versioned save takes for the comparison, since these settings have
# no single row to lock: the same for every writer of one database, and transaction-scoped.
VERSION_LOCK = 0xC0F6

_BOOLS = {"google_app_unverified", "mcp_full_access"}
_INTS = {
    "solleciti_grace_days",
    "solleciti_min_interval_days",
    "solleciti_max_reminders",
    "gmail_backfill_days",
}
_FLOATS = {"concentrazione_soglia_preferita"}
# What `space_base_settings` lends a space from the root's Google, blanked again when the
# space configures a client of its own (`apply_overrides`).
_BORROWED_GOOGLE: dict[str, object] = {
    "google_client_secret": "",
    "google_token_key": "",
    "google_app_unverified": False,
    "google_callback_base_url": "",
    "google_oauth_state_prefix": "",
}
# The rows that only mean something next to a client id of the space's own.
_GOOGLE_ROWS = frozenset({"google_client_secret", "google_token_key", "google_app_unverified"})


def _coerce(key: str, raw: str) -> Any:
    if key in _BOOLS:
        return raw.strip().lower() in {"1", "true", "yes", "si", "sì"}
    if key in _INTS:
        return int(raw)
    if key in _FLOATS:
        return float(raw)
    return raw


def apply_overrides(base: Settings, overrides: dict[str, str]) -> Settings:
    """`base` with the rows laid over it, re-validated by `Settings` itself so a value
    that would be refused from the environment is refused from the database too. Init
    kwargs outrank every other pydantic-settings source, which is what makes the dump
    round-trip exact: nothing from the process environment leaks back in."""
    if not overrides:
        return base
    merged = base.model_dump()
    ignored: frozenset[str] = frozenset()
    if base.google_oauth_state_prefix:
        # The root lends this space its client (REB-394), and the Google rows go all
        # together or not at all, decided by the client id. With a client id of its
        # own, nothing borrowed may travel with it: the root's secret would
        # authenticate somebody else's client, and the root's callback is not an
        # address their client registered; their own rows then land on top, exactly
        # as before the root lent anything. Without one, a secret, key or Testing row
        # left over from a client the space once had would sit on top of the root's
        # client and fail every exchange, so those rows are not read.
        if "google_client_id" in overrides:
            merged.update(_BORROWED_GOOGLE)
        else:
            ignored = _GOOGLE_ROWS
    for key, raw in overrides.items():
        if key in OVERRIDABLE_KEYS and key not in ignored:
            merged[key] = _coerce(key, raw)
    return Settings(_env_file=None, **merged)  # type: ignore[call-arg]


def new_token_key() -> str:
    """32 random bytes, base64: what `decode_google_token_key` expects."""
    return base64.b64encode(secrets.token_bytes(GOOGLE_TOKEN_KEY_BYTES)).decode("ascii")


class SpaceSettingsService:
    """On a session of the database whose settings these are; `base` is what the
    environment (and, for a space, `deps.get_request_settings`) already decided."""

    def __init__(self, session: Session, base: Settings) -> None:
        self.session = session
        self.base = base
        self.activities = ActivityService(session)

    def overrides(self) -> dict[str, str]:
        rows = self.session.scalars(select(SpaceSetting)).all()
        return {row.key: row.value for row in rows if row.key in OVERRIDABLE_KEYS}

    def effective(self) -> Settings:
        return apply_overrides(self.base, self.overrides())

    def version(self) -> datetime | None:
        """What `SpaceSettingsRead.updated_at` says and what a save compares with
        (REB-622): the newest row's `updated_at`, the reserved `VERSION_KEY` row's after
        the first write under this code and the newest override's on a database written
        before it, `None` with no row at all. One value for the whole settings object,
        since the page saves it as one, over a table that keeps one row per key."""
        return self.session.scalar(select(func.max(SpaceSetting.updated_at)))

    def read(self, actor: Actor, *, spazio: str | None) -> SpaceSettingsRead:
        actor.require_admin("read_space_settings")
        return self._read(spazio=spazio)

    def update(
        self, data: SpaceSettingsUpdate, actor: Actor, *, spazio: str | None
    ) -> SpaceSettingsRead:
        actor.require_unscoped_admin("update_space_settings")
        # Sent, not merely non-null (REB-622): the version is `None` on a database with
        # no row yet, and a draft built on that state has to be refused once a row
        # exists, so an explicit `null` is a real check and only an absent key is none.
        # Under the advisory lock, so two saves carrying one version cannot both pass.
        if "updated_at" in data.model_fields_set:
            self.session.execute(select(func.pg_advisory_xact_lock(VERSION_LOCK)))
            require_unchanged(ENTITY, sent=data.updated_at, current=self.version())
        changed: list[str] = []
        for key in data.model_fields_set - {"updated_at"}:
            value = getattr(data, key)
            if value is None:
                continue
            if isinstance(value, str) and value == "":
                if self._delete(key):
                    changed.append(key)
                continue
            self._set(key, str(value).lower() if isinstance(value, bool) else str(value))
            changed.append(key)

        # A Google client without a key to encrypt its refresh tokens would connect an
        # account and then be unable to keep it: the key is made here, once, the moment
        # a client id first appears, and never shown.
        effective_before_key = self.effective()
        if effective_before_key.google_client_id and not effective_before_key.google_token_key:
            self._set("google_token_key", new_token_key())
            changed.append("google_token_key")

        if changed:
            # Keys only, never values: two of them are secrets, and the timeline is read
            # by more people, for longer, than this table.
            self.activities.record(
                ENTITY, SETTINGS_ID, "updated", actor, {"chiavi": sorted(set(changed))}
            )
            self._set(VERSION_KEY, datetime.now(UTC).isoformat())
        self.session.commit()
        return self._read(spazio=spazio)

    def _set(self, key: str, value: str) -> None:
        row = self.session.get(SpaceSetting, key)
        if row is None:
            self.session.add(SpaceSetting(key=key, value=value))
        else:
            row.value = value

    def _delete(self, key: str) -> bool:
        row = self.session.get(SpaceSetting, key)
        if row is None:
            return False
        self.session.delete(row)
        return True

    def _read(self, *, spazio: str | None) -> SpaceSettingsRead:
        overrides = self.overrides()
        settings = apply_overrides(self.base, overrides)
        public_url = settings.public_url.rstrip("/")
        return SpaceSettingsRead(
            spazio=spazio,
            public_url=public_url,
            google_client_id=settings.google_client_id,
            google_client_secret_impostato=bool(settings.google_client_secret),
            google_token_key_impostata=bool(settings.google_token_key),
            google_app_unverified=settings.google_app_unverified,
            gmail_configurato=gmail_configured(settings),
            google_client_condiviso=bool(settings.google_oauth_state_prefix),
            # This space's own addresses, the ones a client of its own registers, even
            # while it borrows the root's client (REB-394), whose consent comes back
            # through the root's callback and needs nothing registered per space.
            redirect_uri_gmail=f"{public_url}/api/gmail/oauth/callback" if public_url else "",
            redirect_uri_drive=f"{public_url}/api/drive/oauth/callback" if public_url else "",
            storage_backend=settings.storage_backend,
            mcp_full_access=settings.mcp_full_access,
            solleciti_grace_days=settings.solleciti_grace_days,
            solleciti_min_interval_days=settings.solleciti_min_interval_days,
            solleciti_max_reminders=settings.solleciti_max_reminders,
            gmail_backfill_days=settings.gmail_backfill_days,
            concentrazione_soglia_preferita=settings.concentrazione_soglia_preferita,
            sovrascritte=sorted(key for key in overrides if key not in SECRET_KEYS)
            + sorted(key for key in overrides if key in SECRET_KEYS),
            updated_at=self.version(),
        )
