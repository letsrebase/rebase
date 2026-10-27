"""The reminders surface on the default MCP tools (spec 2026-10-26, REB-732).

The tools are named for the feature the way the product names it, `promemoria`, as
`create_attivita` is for attività: a name with `reminder` in it would also trip
`test_gmail_tools.py::test_no_tool_prepares_a_payment_reminder`, the guard on the one
thing that is not this feature, the sollecito.

Everything here is the person's own bookkeeping or the space's settings, and none of it
consumes a number, touches the fiscal register or rewrites a rate, so none of it is on
the forbidden list of slice 3 §11. An agent may read what the space raises, set a
person's preferences and acknowledge or snooze what that person has seen, exactly as the
person can; changing the space's defaults stays an admin's by the service's own check.
"""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from pigrocrm.core.config import Settings
from pigrocrm.core.reminders.jobs import ScheduledJobService
from pigrocrm.core.reminders.preferences import ReminderPreferenceService
from pigrocrm.core.reminders.schemas import (
    AcknowledgeRequest,
    ReminderPreferenceWrite,
    SnoozeRequest,
)
from pigrocrm.core.reminders.service import ReminderService
from pigrocrm.core.reminders.state import ReminderStateService
from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext


def _preferences(context: McpContext) -> ReminderPreferenceService:
    return ReminderPreferenceService(context.session)


def _state(context: McpContext) -> ReminderStateService:
    return ReminderStateService(context.session)


def register(
    mcp: MCPServer, context: McpContext, guard: Callable[..., Any], settings: Settings
) -> None:
    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_promemoria() -> list[dict[str, Any]]:
        """I promemoria di chi chiede, ricalcolati adesso, i più gravi per primi. Ogni voce ha
        `status` (`open`, `acknowledged`, `snoozed`), `entity`, `entity_id` e `key`, che serve a
        `acknowledge_promemoria` e `snooze_promemoria`. Sparisce da solo quando la causa si
        risolve.
        """
        reminders = ReminderService(context.session, settings).list(context.actor)
        return [item.model_dump(mode="json") for item in reminders]

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_promemoria_types() -> list[dict[str, Any]]:
        """I tipi di promemoria che il CRM alza da solo, con le soglie predefinite. Ogni tipo ha
        una scala di gravità (`warning`, `serious`, `critical`) che sale con giorni o ore.
        """
        return [item.model_dump(mode="json") for item in _preferences(context).catalogue()]

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_promemoria_preferences() -> dict[str, Any]:
        """Le preferenze sui promemoria: valori dello spazio, della persona che chiede e
        `effective` (sua scelta, poi spazio, poi predefinito).
        """
        return _preferences(context).read(context.actor).model_dump(mode="json")

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def set_promemoria_preference(scope: str, type: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Imposta le preferenze di un tipo di promemoria, o con `type` = `general` ore di silenzio
        e ora del riepilogo.

        `scope`: `me` per la persona, `space` per i predefiniti dello spazio (solo amministratore).
        In `changes` solo ciò che cambia: `enabled`, `channels` (oltre all'app: `push`, `slack`,
        `email`), `ladder` (soglie in ordine di gravità, es. `{"serious": 1, "critical": 14}`,
        unità secondo il tipo), `urgent_from`, `email_mode`; per `general`, `quiet_hours` e
        `summary_hour`. Un campo `null` torna al valore ereditato.
        """
        row = _preferences(context).write(
            context.actor, _scope(scope), type, ReminderPreferenceWrite(**changes)
        )
        return {"preference": row.model_dump(mode="json") if row is not None else None}

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def reset_promemoria_preference(scope: str, type: str) -> dict[str, str]:
        """Torna al valore predefinito di un tipo (o di `general`): cancella la scelta, così le
        modifiche future del predefinito arrivano ancora.
        """
        _preferences(context).reset(context.actor, _scope(scope), type)
        return {"status": "ripristinato", "scope": scope, "type": type}

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def acknowledge_promemoria(key: str, severity: str) -> dict[str, Any]:
        """Segna un promemoria come visto per chi chiede, alla gravità vista (`warning`, `serious`,
        `critical`). Torna a comparire se sale di gravità. `key` è `tipo:identificativo`.
        """
        data = AcknowledgeRequest(key=key, severity=severity)  # type: ignore[arg-type]
        return (
            _state(context)
            .acknowledge(context.actor, data.key, data.severity)
            .model_dump(mode="json")
        )

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def snooze_promemoria(key: str, severity: str, until: str) -> dict[str, Any]:
        """Rimanda un promemoria per chi chiede. `until` è `tomorrow`, `next_monday`, `in_a_week` o
        `in_a_month`, mai una data libera. Torna a comparire quel giorno, o prima se sale di
        gravità.
        """
        data = SnoozeRequest(key=key, severity=severity, until=until)  # type: ignore[arg-type]
        return (
            _state(context)
            .snooze(context.actor, data.key, data.severity, data.until)
            .model_dump(mode="json")
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_my_promemoria_state() -> list[dict[str, Any]]:
        """I promemoria che chi chiede ha già chiuso o rimandato."""
        return [item.model_dump(mode="json") for item in _state(context).mine(context.actor)]

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def describe_scheduled_jobs() -> dict[str, Any]:
        """Come sono andate le operazioni automatiche del server per questo spazio:
        sincronizzazione Gmail, promemoria, automazioni giornaliere, riepilogo settimanale. Per
        ciascuna: `state` (`ok`, `failed`, `late`, `never_run`, `not_configured`),
        `last_run_at`, `last_failure_at` e `failure` se l'ultimo tentativo è fallito. Solo per
        gli amministratori.
        """
        return ScheduledJobService(context.session).describe(context.actor).model_dump(mode="json")


def _scope(value: str) -> Any:
    """`space` or `me`, refused by the schema's own literal when it is neither."""
    from pigrocrm.core.errors import ValidationFailed

    if value not in ("space", "me"):
        raise ValidationFailed(
            "reminder_preference", "scope", "scope sconosciuto", expected="`space` oppure `me`"
        )
    return value
