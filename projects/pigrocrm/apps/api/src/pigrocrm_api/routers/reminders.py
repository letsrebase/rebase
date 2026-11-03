"""Reminders: what the CRM raises on its own, and what a person does about it (spec
2026-10-26, REB-732). The settings, in two layers, and acknowledge and snooze, which are
a person's own."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, status

from pigrocrm.core.reminders.preferences import ReminderPreferenceService
from pigrocrm.core.reminders.schemas import (
    AcknowledgeRequest,
    ReminderPreferenceRead,
    ReminderPreferencesRead,
    ReminderPreferenceWrite,
    ReminderRead,
    ReminderStateRead,
    ReminderTypeRead,
    SnoozeRequest,
)
from pigrocrm.core.reminders.service import ReminderService
from pigrocrm.core.reminders.state import ReminderStateService
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import PAGE_LIMIT_MAX

router = APIRouter(
    prefix="/api/reminders",
    tags=["reminders"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)

ScopePath = Annotated[
    Literal["space", "me"],
    Path(description="`space`: the space's default values (admins); `me`: yours"),
]
TypePath = Annotated[
    str,
    Path(
        max_length=40,
        description=("A reminder type, or `general` for quiet hours and the digest time"),
    ),
]


@router.get("", response_model=list[ReminderRead])
def list_reminders(
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=PAGE_LIMIT_MAX)] = PAGE_LIMIT_MAX,
) -> list[ReminderRead]:
    """The caller's reminders, recomputed now from the data: most severe first.
    Those already closed or snoozed are there, with their `status`; only what
    is resolved disappears. `limit` cuts the tail, that is, the least severe: the service evaluates
    them all, but the response does not grow with them."""
    return ReminderService(session, settings).list(actor)[:limit]


@router.get("/types", response_model=list[ReminderTypeRead])
def list_types(session: SessionDep) -> list[ReminderTypeRead]:
    """The reminder types of this space, with the default thresholds."""
    return ReminderPreferenceService(session).catalogue()


@router.get("/preferences", response_model=ReminderPreferencesRead)
def read_preferences(session: SessionDep, actor: ActorDep) -> ReminderPreferencesRead:
    """The space's values, yours, and the result for you: your choice, then
    the space's value, then the default."""
    return ReminderPreferenceService(session).read(actor)


@router.patch("/preferences/{scope}/{type_key}", response_model=ReminderPreferenceRead | None)
def write_preference(
    scope: ScopePath,
    type_key: TypePath,
    data: ReminderPreferenceWrite,
    session: SessionDep,
    actor: ActorDep,
) -> ReminderPreferenceRead | None:
    """Sets the given fields; a `null` field goes back to the value it inherits. Answers
    `null` when nothing is left to override and the row is gone."""
    return ReminderPreferenceService(session).write(actor, scope, type_key, data)


@router.delete("/preferences/{scope}/{type_key}", status_code=status.HTTP_204_NO_CONTENT)
def reset_preference(
    scope: ScopePath, type_key: TypePath, session: SessionDep, actor: ActorDep
) -> None:
    """The way back to the default value: deletes your choice (or the space's),
    so that a later change of the default still reaches you."""
    ReminderPreferenceService(session).reset(actor, scope, type_key)


@router.get("/state", response_model=list[ReminderStateRead])
def my_state(session: SessionDep, actor: ActorDep) -> list[ReminderStateRead]:
    """What you have closed or snoozed."""
    return ReminderStateService(session).mine(actor)


@router.post("/acknowledge", response_model=ReminderStateRead)
def acknowledge(
    data: AcknowledgeRequest, session: SessionDep, actor: ActorDep
) -> ReminderStateRead:
    """Marks a reminder as seen, at the severity you saw. It applies to you and does not hide it
    from others; it shows up again if its severity rises."""
    return ReminderStateService(session).acknowledge(actor, data.key, data.severity)


@router.post("/snooze", response_model=ReminderStateRead)
def snooze(data: SnoozeRequest, session: SessionDep, actor: ActorDep) -> ReminderStateRead:
    """Snoozes a reminder until tomorrow, next Monday, in a week or in a
    month. It applies to you; it shows up again if its severity rises."""
    return ReminderStateService(session).snooze(actor, data.key, data.severity, data.until)
