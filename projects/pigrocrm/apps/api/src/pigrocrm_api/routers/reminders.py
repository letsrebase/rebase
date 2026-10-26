"""Reminders: what the CRM raises on its own, and what a person does about it (spec
2026-10-26, REB-732). The settings, in two layers, and acknowledge and snooze, which are
a person's own."""

from typing import Annotated, Literal

from fastapi import APIRouter, Path, status

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
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/reminders", tags=["reminders"], responses=PROBLEM_RESPONSES)

ScopePath = Annotated[
    Literal["space", "me"],
    Path(description="`space`: i valori predefiniti dello spazio (amministratori); `me`: i tuoi"),
]
TypePath = Annotated[
    str,
    Path(
        max_length=40,
        description=(
            "Un tipo di promemoria, o `general` per le ore di silenzio e l'ora del riepilogo"
        ),
    ),
]


@router.get("", response_model=list[ReminderRead])
def list_reminders(
    session: SessionDep, actor: ActorDep, settings: SettingsDep
) -> list[ReminderRead]:
    """I promemoria di chi chiede, ricalcolati adesso dai dati: i più gravi per primi.
    Quelli già chiusi o rimandati ci sono, con il loro `status`; sparisce solo ciò di cui
    la causa è risolta."""
    return ReminderService(session, settings).list(actor)


@router.get("/types", response_model=list[ReminderTypeRead])
def list_types(session: SessionDep, actor: ActorDep) -> list[ReminderTypeRead]:
    """I tipi di promemoria di questo spazio, con le soglie predefinite."""
    return ReminderPreferenceService(session).catalogue()


@router.get("/preferences", response_model=ReminderPreferencesRead)
def read_preferences(session: SessionDep, actor: ActorDep) -> ReminderPreferencesRead:
    """I valori dello spazio, i tuoi, e quello che ne risulta per te: la tua scelta, poi
    il valore dello spazio, poi quello predefinito."""
    return ReminderPreferenceService(session).read(actor)


@router.patch("/preferences/{scope}/{type_key}", response_model=ReminderPreferenceRead | None)
def write_preference(
    scope: ScopePath,
    type_key: TypePath,
    data: ReminderPreferenceWrite,
    session: SessionDep,
    actor: ActorDep,
) -> ReminderPreferenceRead | None:
    """Imposta i campi indicati; un campo `null` torna al valore che eredita. Risponde
    `null` quando non resta niente da sovrascrivere e la riga è sparita."""
    return ReminderPreferenceService(session).write(actor, scope, type_key, data)


@router.delete("/preferences/{scope}/{type_key}", status_code=status.HTTP_204_NO_CONTENT)
def reset_preference(
    scope: ScopePath, type_key: TypePath, session: SessionDep, actor: ActorDep
) -> None:
    """La strada di ritorno al valore predefinito: cancella la tua scelta (o quella dello
    spazio), così una modifica successiva del predefinito ti raggiunge ancora."""
    ReminderPreferenceService(session).reset(actor, scope, type_key)


@router.get("/state", response_model=list[ReminderStateRead])
def my_state(session: SessionDep, actor: ActorDep) -> list[ReminderStateRead]:
    """Quello che hai chiuso o rimandato."""
    return ReminderStateService(session).mine(actor)


@router.post("/acknowledge", response_model=ReminderStateRead)
def acknowledge(
    data: AcknowledgeRequest, session: SessionDep, actor: ActorDep
) -> ReminderStateRead:
    """Segna un promemoria come visto, alla gravità che hai visto. Vale per te e non lo
    nasconde agli altri; torna a comparire se sale di gravità."""
    return ReminderStateService(session).acknowledge(actor, data.key, data.severity)


@router.post("/snooze", response_model=ReminderStateRead)
def snooze(data: SnoozeRequest, session: SessionDep, actor: ActorDep) -> ReminderStateRead:
    """Rimanda un promemoria a domani, al prossimo lunedì, fra una settimana o fra un
    mese. Vale per te; torna a comparire se sale di gravità."""
    return ReminderStateService(session).snooze(actor, data.key, data.severity, data.until)
