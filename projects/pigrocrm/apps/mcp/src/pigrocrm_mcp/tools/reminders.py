"""The reminders surface on the default MCP tools (spec 2026-10-26, REB-732).

Everything here is the person's own bookkeeping or the space's settings, and none of it
consumes a number, touches the fiscal register or rewrites a rate, so none of it is on
the forbidden list of slice 3 §11. An agent may read what the space raises, set a
person's preferences and acknowledge or snooze what that person has seen, exactly as the
person can; changing the space's defaults stays an admin's by the service's own check.
"""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from pigrocrm.core.reminders.preferences import ReminderPreferenceService
from pigrocrm.core.reminders.schemas import (
    AcknowledgeRequest,
    ReminderPreferenceWrite,
    SnoozeRequest,
)
from pigrocrm.core.reminders.state import ReminderStateService
from pigrocrm_mcp.context import McpContext


def _preferences(context: McpContext) -> ReminderPreferenceService:
    return ReminderPreferenceService(context.session)


def _state(context: McpContext) -> ReminderStateService:
    return ReminderStateService(context.session)


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    @mcp.tool()
    @guard
    def list_reminder_types() -> list[dict[str, Any]]:
        """I tipi di promemoria che il CRM alza da solo, con le soglie predefinite.

        Un promemoria non è salvato: si ricalcola a ogni passaggio dai dati, e sparisce
        quando la causa è risolta. Ogni tipo ha una scala di gravità (`warning`,
        `serious`, `critical`) che sale con i giorni o le ore."""
        return [item.model_dump(mode="json") for item in _preferences(context).catalogue()]

    @mcp.tool()
    @guard
    def get_reminder_preferences() -> dict[str, Any]:
        """Le preferenze sui promemoria: i valori dello spazio, quelli della persona che
        chiede e quello che ne risulta per lei (`effective`: la sua scelta, poi il
        valore dello spazio, poi il predefinito)."""
        return _preferences(context).read(context.actor).model_dump(mode="json")

    @mcp.tool()
    @guard
    def set_reminder_preference(scope: str, type: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Imposta le preferenze di un tipo di promemoria, o con `type` = `general` le
        ore di silenzio e l'ora del riepilogo.

        `scope` è `me` per le preferenze della persona, `space` per i valori predefiniti
        dello spazio (solo un amministratore). In `changes` va solo quello che cambia:
        `enabled`, `channels` (oltre all'app, che è sempre attiva: `push`, `slack`,
        `email`), `ladder` (le soglie, per esempio `{"serious": 1, "critical": 14}`, in
        giorni o ore secondo il tipo, in ordine di gravità), `urgent_from`, `email_mode`;
        per `general`, `quiet_hours` e `summary_hour`. Un campo `null` torna al valore che
        eredita. Risponde `null` quando non resta niente da sovrascrivere."""
        row = _preferences(context).write(
            context.actor, _scope(scope), type, ReminderPreferenceWrite(**changes)
        )
        return {"preference": row.model_dump(mode="json") if row is not None else None}

    @mcp.tool()
    @guard
    def reset_reminder_preference(scope: str, type: str) -> dict[str, str]:
        """Torna al valore predefinito di un tipo (o di `general`): cancella la scelta,
        così una modifica successiva del predefinito arriva ancora."""
        _preferences(context).reset(context.actor, _scope(scope), type)
        return {"status": "ripristinato", "scope": scope, "type": type}

    @mcp.tool()
    @guard
    def acknowledge_reminder(key: str, severity: str) -> dict[str, Any]:
        """Segna un promemoria come visto per la persona che chiede, alla gravità che ha
        visto (`warning`, `serious`, `critical`). Non lo nasconde agli altri e torna a
        comparire se sale di gravità. `key` è `tipo:identificativo`."""
        data = AcknowledgeRequest(key=key, severity=severity)  # type: ignore[arg-type]
        return (
            _state(context)
            .acknowledge(context.actor, data.key, data.severity)
            .model_dump(mode="json")
        )

    @mcp.tool()
    @guard
    def snooze_reminder(key: str, severity: str, until: str) -> dict[str, Any]:
        """Rimanda un promemoria per la persona che chiede. `until` è `tomorrow`,
        `next_monday`, `in_a_week` o `in_a_month`, mai una data libera. Torna a comparire
        quel giorno, o prima se sale di gravità."""
        data = SnoozeRequest(key=key, severity=severity, until=until)  # type: ignore[arg-type]
        return (
            _state(context)
            .snooze(context.actor, data.key, data.severity, data.until)
            .model_dump(mode="json")
        )

    @mcp.tool()
    @guard
    def list_my_reminder_state() -> list[dict[str, Any]]:
        """I promemoria che la persona che chiede ha già chiuso o rimandato."""
        return [item.model_dump(mode="json") for item in _state(context).mine(context.actor)]


def _scope(value: str) -> Any:
    """`space` or `me`, refused by the schema's own literal when it is neither."""
    from pigrocrm.core.errors import ValidationFailed

    if value not in ("space", "me"):
        raise ValidationFailed(
            "reminder_preference", "scope", "scope sconosciuto", expected="`space` oppure `me`"
        )
    return value
