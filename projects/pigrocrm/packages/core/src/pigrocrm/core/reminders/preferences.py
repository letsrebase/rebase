"""Two levels of preference, and the one function that resolves them (spec 2026-10-26 §5, §7).

**Space defaults** are set by admins; **user overrides** by each person, for themselves.
A setting's effective value is, in order, the person's override, the space's default and
the built-in default of the catalogue and of §6. The resolution is
`UserSettings`, called by the detectors, the dispatcher, the API and the MCP reads alike,
so no surface computes it a second way.

**Where the layers do not mirror each other.** `enabled` is the exception to override
symmetry: a type an admin turns off is not raised for anybody, and a person can switch a
type off for themselves but not back on one the space has off. A **channel** an admin
turns off by default only changes the default: the person who turned it back on for
themselves still receives it (spec §7).

**The way back is a delete.** An override is a row whose null columns inherit, so going
back to the default deletes the row, or clears one column, and a later change to the
default still reaches a person who never overrode it. A row left with nothing set is
deleted rather than kept as a copy of nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import time
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, ValidationFailed
from pigrocrm.core.reminders.catalogue import (
    CHANNELS,
    DEFAULT_URGENT_FROM,
    REMINDER_TYPES,
    SEVERITY_RANK,
    SWITCHABLE_CHANNELS,
    Channel,
    EmailMode,
    Ladder,
    ReminderType,
    Severity,
    get_type,
    validate_ladder,
)
from pigrocrm.core.reminders.models import ReminderPreference
from pigrocrm.core.reminders.schemas import (
    DEFAULT_QUIET_HOURS,
    DEFAULT_SUMMARY_HOUR,
    GENERAL,
    EffectiveGeneralSettings,
    EffectiveReminderSettings,
    QuietHours,
    ReminderPreferenceRead,
    ReminderPreferencesRead,
    ReminderPreferenceWrite,
    ReminderTypeRead,
    Scope,
)
from pigrocrm.core.schemas import supplied_changes

ENTITY = "reminder_preference"

_TYPE_COLUMNS = ("enabled", "channels", "ladder", "urgent_from", "email_mode")
_GENERAL_COLUMNS = ("quiet_hours", "summary_hour")
_ALL_COLUMNS = _TYPE_COLUMNS + _GENERAL_COLUMNS


@dataclass(frozen=True)
class EffectiveSettings:
    """One type's settings for one person."""

    type: str
    enabled: bool
    channels: tuple[Channel, ...]
    ladder: Ladder
    urgent_from: Severity
    email_mode: EmailMode | None

    def is_urgent(self, severity: Severity) -> bool:
        return SEVERITY_RANK[severity] >= SEVERITY_RANK[self.urgent_from]

    def email_mode_for(self, severity: Severity) -> EmailMode:
        """`immediate` for an urgent reminder and `summary` for the rest, unless the
        person or the space fixed one (spec §5)."""
        if self.email_mode is not None:
            return self.email_mode
        return "immediate" if self.is_urgent(severity) else "summary"


@dataclass(frozen=True)
class EffectiveGeneral:
    quiet_hours: QuietHours
    summary_hour: int

    def quiet_at(self, local_day: int, local_time: time) -> bool:
        """Whether `local_time` on a weekday (`0` Monday .. `6` Sunday), in the
        installation's zone, is inside the quiet window (spec §6.3). The window crosses
        midnight when it starts later than it ends, and a weekend day is quiet from
        midnight to midnight whatever the hours say."""
        quiet = self.quiet_hours
        if not quiet.enabled:
            return False
        if quiet.weekend and local_day >= 5:
            return True
        start, end = time.fromisoformat(quiet.start), time.fromisoformat(quiet.end)
        if start == end:
            return False
        if start < end:
            return start <= local_time < end
        return local_time >= start or local_time < end


class ReminderSettings:
    """Every preference row of a space, read once: a pass resolves hundreds of
    reminders for a handful of people and must not ask the database for each."""

    def __init__(self, rows: Iterable[ReminderPreference]) -> None:
        self._space: dict[str | None, ReminderPreference] = {}
        self._users: dict[UUID, dict[str | None, ReminderPreference]] = {}
        for row in rows:
            if row.user_id is None:
                self._space[row.type] = row
            else:
                self._users.setdefault(row.user_id, {})[row.type] = row

    @classmethod
    def load(cls, session: Session) -> ReminderSettings:
        return cls(session.scalars(select(ReminderPreference)))

    def space_enabled(self, kind: ReminderType | str) -> bool:
        """Whether the space raises this type at all: what decides, before any detector
        runs, that a switched-off type is skipped (spec §7)."""
        row = self._space.get(kind if isinstance(kind, str) else kind.key)
        return row is None or row.enabled is None or row.enabled

    def for_user(self, user_id: UUID) -> UserSettings:
        return UserSettings(self._space, self._users.get(user_id, {}))

    def space(self) -> UserSettings:
        """The settings as the space's defaults leave them, for a caller with no person
        to resolve for."""
        return UserSettings(self._space, {})

    def candidate_bound(self, kind: ReminderType) -> int:
        """The loosest rung of any ladder of this type, the space's and every person's:
        a detector offers every subject that reaches it, and each person's own ladder
        then decides whether it is theirs. Without it a detector filtering on the default
        ladder would hide a reminder from the person who asked for an earlier one."""
        ladders: list[Mapping[Any, int]] = [kind.ladder]
        for layer in (self._space, *self._users.values()):
            row = layer.get(kind.key)
            if row is not None and row.ladder:
                ladders.append(row.ladder)
        values = [value for ladder in ladders for value in ladder.values()]
        return min(values) if kind.direction == "since" else max(values)


class UserSettings:
    def __init__(
        self,
        space: Mapping[str | None, ReminderPreference],
        user: Mapping[str | None, ReminderPreference],
    ) -> None:
        self._space = space
        self._user = user

    def _pick(self, type_key: str | None, column: str) -> Any:
        for layer in (self._user, self._space):
            row = layer.get(type_key)
            if row is not None and getattr(row, column) is not None:
                return getattr(row, column)
        return None

    def type(self, kind: ReminderType | str) -> EffectiveSettings:
        key = kind if isinstance(kind, str) else kind.key
        reminder_type = get_type(key)
        space_row = self._space.get(key)
        user_row = self._user.get(key)
        space_on = space_row is None or space_row.enabled is None or space_row.enabled
        user_on = user_row is None or user_row.enabled is None or user_row.enabled
        channels = self._pick(key, "channels")
        chosen = tuple(
            channel
            for channel in SWITCHABLE_CHANNELS
            if channel in (channels if channels is not None else SWITCHABLE_CHANNELS)
        )
        ladder = self._pick(key, "ladder")
        return EffectiveSettings(
            type=key,
            enabled=bool(space_on and user_on),
            channels=("in_app", *chosen),
            ladder=dict(ladder) if ladder is not None else dict(reminder_type.ladder),
            urgent_from=self._pick(key, "urgent_from") or DEFAULT_URGENT_FROM,
            email_mode=self._pick(key, "email_mode"),
        )

    def general(self) -> EffectiveGeneral:
        quiet = self._pick(None, "quiet_hours")
        hour = self._pick(None, "summary_hour")
        return EffectiveGeneral(
            quiet_hours=QuietHours.model_validate(quiet)
            if quiet is not None
            else DEFAULT_QUIET_HOURS,
            summary_hour=hour if hour is not None else DEFAULT_SUMMARY_HOUR,
        )


def type_read(kind: ReminderType) -> ReminderTypeRead:
    return ReminderTypeRead(
        key=kind.key,
        label=kind.label,
        description=kind.description,
        subject=kind.subject,
        audience=kind.audience,
        direction=kind.direction,
        unit=kind.unit,
        ladder=dict(kind.ladder),
        daily=kind.daily,
    )


def _row_read(row: ReminderPreference, scope: Scope) -> ReminderPreferenceRead:
    return ReminderPreferenceRead(
        scope=scope,
        type=row.type,
        enabled=row.enabled,
        channels=row.channels,
        ladder=row.ladder,
        urgent_from=row.urgent_from,
        email_mode=row.email_mode,
        quiet_hours=QuietHours.model_validate(row.quiet_hours)
        if row.quiet_hours is not None
        else None,
        summary_hour=row.summary_hour,
        updated_at=row.updated_at,
    )


class ReminderPreferenceService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def catalogue(self) -> list[ReminderTypeRead]:
        return [type_read(kind) for kind in REMINDER_TYPES.values()]

    def read(self, actor: Actor) -> ReminderPreferencesRead:
        """Both layers and what they resolve to for the person asking. Anyone may read
        them: the space's defaults are what explains why a reminder arrives."""
        rows = list(self.session.scalars(select(ReminderPreference)))
        settings = ReminderSettings(rows)
        resolved = settings.for_user(actor.id) if actor.id is not None else settings.space()
        general = resolved.general()
        return ReminderPreferencesRead(
            types=self.catalogue(),
            space=[_row_read(row, "space") for row in rows if row.user_id is None],
            mine=[
                _row_read(row, "me")
                for row in rows
                if actor.id is not None and row.user_id == actor.id
            ],
            effective=[
                EffectiveReminderSettings(
                    type=kind.key,
                    enabled=effective.enabled,
                    channels=list(effective.channels),
                    ladder=effective.ladder,
                    urgent_from=effective.urgent_from,
                    email_mode=effective.email_mode,
                )
                for kind in REMINDER_TYPES.values()
                for effective in [resolved.type(kind)]
            ],
            general=EffectiveGeneralSettings(
                quiet_hours=general.quiet_hours, summary_hour=general.summary_hour
            ),
        )

    def write(
        self, actor: Actor, scope: Scope, type_key: str, data: ReminderPreferenceWrite
    ) -> ReminderPreferenceRead | None:
        """Sets the fields supplied, clears the ones sent as `null`, and returns the row,
        or `None` when nothing is left to override and the row went."""
        owner = self._owner(actor, scope)
        row_type = self._row_type(type_key)
        changes = supplied_changes(data)
        allowed = _GENERAL_COLUMNS if row_type is None else _TYPE_COLUMNS
        stray = sorted(set(changes) - set(allowed))
        if stray:
            raise ValidationFailed(
                ENTITY,
                stray[0],
                "questa impostazione vale per tutti i tipi insieme, non per uno"
                if stray[0] in _GENERAL_COLUMNS
                else "questa impostazione vale per un tipo, non per tutti insieme",
                expected=", ".join(allowed),
            )
        if not changes:
            raise ValidationFailed(
                ENTITY, "body", "niente da cambiare", expected=", ".join(allowed)
            )
        row = self._row(owner, row_type)
        created = row is None
        if row is None:
            row = ReminderPreference(user_id=owner, type=row_type)
            self.session.add(row)
        for column, value in changes.items():
            setattr(row, column, self._clean(row_type, column, value))
        if all(getattr(row, column) is None for column in _ALL_COLUMNS):
            if not created:
                self.session.delete(row)
            else:
                self.session.expunge(row)
            self.session.commit()
            return None
        self.session.commit()
        return _row_read(row, scope)

    def reset(self, actor: Actor, scope: Scope, type_key: str) -> None:
        """The way back to the default: deletes the layer's row for this type, which is a
        no-op when it never had one."""
        owner = self._owner(actor, scope)
        row = self._row(owner, self._row_type(type_key))
        if row is not None:
            self.session.delete(row)
            self.session.commit()

    # -- internals ---------------------------------------------------------------

    @staticmethod
    def _owner(actor: Actor, scope: Scope) -> UUID | None:
        if scope == "space":
            actor.require_unscoped_admin("set_reminder_defaults")
            return None
        if actor.id is None:
            raise Conflict(ENTITY, "le preferenze personali sono di una persona")
        return actor.id

    @staticmethod
    def _row_type(type_key: str) -> str | None:
        if type_key == GENERAL:
            return None
        return get_type(type_key).key

    def _row(self, owner: UUID | None, row_type: str | None) -> ReminderPreference | None:
        statement = select(ReminderPreference).where(
            ReminderPreference.user_id.is_(None)
            if owner is None
            else ReminderPreference.user_id == owner,
            ReminderPreference.type.is_(None)
            if row_type is None
            else ReminderPreference.type == row_type,
        )
        return self.session.scalars(statement).one_or_none()

    @staticmethod
    def _clean(row_type: str | None, column: str, value: Any) -> Any:
        """The value as the column stores it, refused in Italian when it is not one."""
        if value is None:
            return None
        if column == "ladder":
            return validate_ladder(get_type(row_type or ""), value)
        if column == "channels":
            unknown = [channel for channel in value if channel not in CHANNELS]
            if unknown:
                raise ValidationFailed(
                    ENTITY,
                    "channels",
                    f"canale sconosciuto: {unknown[0]}",
                    expected=", ".join(CHANNELS),
                )
            # In-app is always on and is not a setting, so a list that names it is not
            # stored with it.
            return [channel for channel in SWITCHABLE_CHANNELS if channel in value]
        if column == "quiet_hours":
            return QuietHours.model_validate(value).model_dump()
        return value
