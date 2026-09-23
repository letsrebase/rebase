import base64
import secrets
from typing import Any
from uuid import UUID

from sqlalchemy import select
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

ENTITY = "space_settings"
# The timeline wants an entity id and there is exactly one settings object per
# database, so it gets one fixed id.
SETTINGS_ID = UUID("00000000-0000-0000-0000-00000000c0f6")

_BOOLS = {"google_app_unverified", "mcp_full_access"}
_INTS = {
    "solleciti_grace_days",
    "solleciti_min_interval_days",
    "solleciti_max_reminders",
    "gmail_backfill_days",
}
_FLOATS = {"concentrazione_soglia_preferita"}


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
    for key, raw in overrides.items():
        if key in OVERRIDABLE_KEYS:
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

    def read(self, actor: Actor, *, spazio: str | None) -> SpaceSettingsRead:
        actor.require_admin("read_space_settings")
        return self._read(spazio=spazio)

    def update(
        self, data: SpaceSettingsUpdate, actor: Actor, *, spazio: str | None
    ) -> SpaceSettingsRead:
        actor.require_admin("update_space_settings")
        changed: list[str] = []
        for key in data.model_fields_set:
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
        )
