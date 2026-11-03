"""What the reminder surfaces send and receive (spec 2026-10-26 §5, §7, §8.2).

Field descriptions and every message a person may read are Italian: the OpenAPI
descriptions are rendered on the public docs page and an assistant reads them out to
an Italian freelancer.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pigrocrm.core.reminders.catalogue import ChannelName, EmailMode, Severity

Scope = Literal["space", "me"]
# The path segment for the settings that belong to no type (quiet hours, summary hour).
GENERAL = "general"

SnoozeOptionName = Literal["tomorrow", "next_monday", "in_a_week", "in_a_month"]

_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class QuietHours(BaseModel):
    """The window that holds push, Slack and email, urgent email included, and never the
    in-app list (spec §6.3). The default is 20:00 to 08:00 and the whole weekend, in the
    installation's zone."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    start: str = Field(default="20:00", description="Start, HH:MM")
    end: str = Field(default="08:00", description="Fine, HH:MM")
    weekend: bool = Field(default=True, description="Sabato e domenica interi")

    @field_validator("start", "end")
    @classmethod
    def _clock(cls, value: str) -> str:
        if not _CLOCK.fullmatch(value):
            raise ValueError("l'ora va scritta HH:MM, per esempio 20:00")
        return value


DEFAULT_QUIET_HOURS = QuietHours()
DEFAULT_SUMMARY_HOUR = 8


class ReminderTypeRead(BaseModel):
    key: str
    label: str
    description: str
    subject: str
    audience: Literal["subject", "admins"]
    direction: Literal["since", "until"]
    unit: Literal["days", "hours", "minutes", "percent"]
    ladder: dict[Severity, int] = Field(description="The type's default thresholds")
    daily: bool


class ReminderPreferenceWrite(BaseModel):
    """The settings of one layer, as a partial update: a field left out is not touched
    and a field sent as `null` goes back to inheriting (spec §7, the way back to the
    default). Per-type fields and the two that belong to no type cannot be mixed."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = Field(default=None, description="The type is active")
    channels: list[ChannelName] | None = Field(
        default=None, description="The channels besides the app, which is always on"
    )
    ladder: dict[Severity, int] | None = Field(
        default=None,
        description="The thresholds, in days, hours, minutes or percentage depending on the type",
    )
    urgent_from: Severity | None = Field(
        default=None, description="From which severity it is urgent"
    )
    email_mode: EmailMode | None = Field(
        default=None, description="`immediate` or `summary`; omitted, urgency decides"
    )
    quiet_hours: QuietHours | None = None
    summary_hour: int | None = Field(default=None, ge=0, le=23)


class ReminderPreferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scope: Scope
    type: str | None
    enabled: bool | None
    channels: list[str] | None
    ladder: dict[str, int] | None
    urgent_from: str | None
    email_mode: str | None
    quiet_hours: QuietHours | None
    summary_hour: int | None
    updated_at: datetime


class EffectiveReminderSettings(BaseModel):
    """What applies to the person asking, after the override, the space's default and the
    built-in default, in that order. `email_mode` is `null` while the default rule stands:
    urgent at once, the rest in the daily summary."""

    type: str
    enabled: bool
    channels: list[ChannelName]
    ladder: dict[Severity, int]
    urgent_from: Severity
    email_mode: EmailMode | None


class EffectiveGeneralSettings(BaseModel):
    quiet_hours: QuietHours
    summary_hour: int


class ReminderPreferencesRead(BaseModel):
    types: list[ReminderTypeRead]
    space: list[ReminderPreferenceRead]
    mine: list[ReminderPreferenceRead]
    effective: list[EffectiveReminderSettings]
    general: EffectiveGeneralSettings


class ReminderRead(BaseModel):
    """One reminder as one person sees it (spec §4: severity is resolved per recipient).

    `status` says what this person has done about it: `open`, `acknowledged` (at this
    severity or above) or `snoozed` (until `snoozed_until`); the client decides what to
    show of each. `delivered_at` is when the pass last put it in this person's list, and
    `null` for a reminder the pass has not seen yet.
    """

    key: str
    type: str
    label: str
    severity: Severity
    measure: float
    title: str
    detail: str
    entity: str = Field(
        description=(
            "What opens: `attivita`, `deal`, `timer`, `invoice`, `proforma`, `contract`, "
            "`proposal`, `fiscal`, or a space item without a page"
        )
    )
    entity_id: UUID
    customer_id: UUID | None
    status: Literal["open", "acknowledged", "snoozed"]
    snoozed_until: date | None
    delivered_at: datetime | None


class AcknowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="`type:identifier`, as the list returns it")
    severity: Severity = Field(description="The severity you saw")


class SnoozeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    severity: Severity
    until: SnoozeOptionName = Field(
        description="`tomorrow`, `next_monday`, `in_a_week` or `in_a_month`; never a free-form date"
    )


class ReminderStateRead(BaseModel):
    """What one person has done about one reminder."""

    key: str
    acknowledged_severity: Severity | None
    snoozed_severity: Severity | None
    snoozed_until: date | None


__all__ = [
    "DEFAULT_QUIET_HOURS",
    "DEFAULT_SUMMARY_HOUR",
    "GENERAL",
    "AcknowledgeRequest",
    "EffectiveGeneralSettings",
    "EffectiveReminderSettings",
    "QuietHours",
    "ReminderPreferenceRead",
    "ReminderPreferenceWrite",
    "ReminderRead",
    "ReminderPreferencesRead",
    "ReminderStateRead",
    "ReminderTypeRead",
    "Scope",
    "SnoozeRequest",
]
