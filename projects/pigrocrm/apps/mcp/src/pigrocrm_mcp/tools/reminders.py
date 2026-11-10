"""The reminders surface on the default MCP tools (spec 2026-10-26, REB-732).

The tools are named for the feature the way the product names it, `promemoria`, as
`create_task` is for attività: a name with `reminder` in it would also trip
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
from pigrocrm.core.reminders.catalogue import Severity
from pigrocrm.core.reminders.detectors import off_types
from pigrocrm.core.reminders.jobs import ScheduledJobService
from pigrocrm.core.reminders.preferences import ReminderPreferenceService
from pigrocrm.core.reminders.schemas import (
    AcknowledgeRequest,
    ReminderPreferenceWrite,
    SnoozeOptionName,
    SnoozeRequest,
)
from pigrocrm.core.reminders.service import ReminderService
from pigrocrm.core.reminders.state import ReminderStateService
from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools.params import literal_arg


def _preferences(context: McpContext) -> ReminderPreferenceService:
    return ReminderPreferenceService(context.session, off_types(context.modules))


def _state(context: McpContext) -> ReminderStateService:
    return ReminderStateService(context.session)


def register(
    mcp: MCPServer, context: McpContext, guard: Callable[..., Any], settings: Settings
) -> None:
    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_reminders() -> list[dict[str, Any]]:
        """The reminders of whoever is asking, recomputed now, most serious first. Each entry has
        `status` (`open`, `acknowledged`, `snoozed`), `entity`, `entity_id` and `key`, which
        `acknowledge_reminder` and `snooze_reminder` need. It disappears by itself when the
        cause is resolved.
        """
        reminders = ReminderService(context.session, settings).list(context.actor)
        return [item.model_dump(mode="json") for item in reminders]

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_reminder_types() -> list[dict[str, Any]]:
        """The reminder types the CRM raises by itself, with the default thresholds. Each type has
        a severity scale (`warning`, `serious`, `critical`) that rises with days or hours.
        """
        return [item.model_dump(mode="json") for item in _preferences(context).catalogue()]

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_reminder_preferences() -> dict[str, Any]:
        """The reminder preferences: the space's values, those of the person asking and
        `effective` (their choice, then the space, then the default).
        """
        return _preferences(context).read(context.actor).model_dump(mode="json")

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def set_reminder_preference(scope: str, type: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Sets the preferences of a reminder type, or with `type` = `general` the quiet hours
        and the summary time.

        `scope`: `me` for the person, `space` for the space defaults (administrator only).
        In `changes` only what changes: `enabled`, `channels` (besides the app: `push`, `slack`,
        `email`), `ladder` (thresholds in order of severity, e.g. `{"serious": 1, "critical": 14}`,
        unit depends on the type), `urgent_from`, `email_mode`; for `general`, `quiet_hours` and
        `summary_hour`. A `null` field goes back to the inherited value.
        """
        row = _preferences(context).write(
            context.actor, _scope(scope), type, ReminderPreferenceWrite(**changes)
        )
        return {"preference": row.model_dump(mode="json") if row is not None else None}

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def reset_reminder_preference(scope: str, type: str) -> dict[str, str]:
        """Goes back to the default value of a type (or of `general`): deletes the choice, so
        future changes to the default still apply.
        """
        _preferences(context).reset(context.actor, _scope(scope), type)
        return {"status": "reset", "scope": scope, "type": type}

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def acknowledge_reminder(key: str, severity: str) -> dict[str, Any]:
        """Marks a reminder as seen for whoever is asking, at the severity seen (`warning`,
        `serious`, `critical`). It reappears if it rises in severity. `key` is `type:identifier`.
        """
        data = AcknowledgeRequest(key=key, severity=literal_arg("severity", severity, Severity))
        return (
            _state(context)
            .acknowledge(context.actor, data.key, data.severity)
            .model_dump(mode="json")
        )

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def snooze_reminder(key: str, severity: str, until: str) -> dict[str, Any]:
        """Snoozes a reminder for whoever is asking. `until` is `tomorrow`, `next_monday`,
        `in_a_week` or `in_a_month`, never a free date. It reappears that day, or earlier if it
        rises in severity.
        """
        data = SnoozeRequest(
            key=key,
            severity=literal_arg("severity", severity, Severity),
            until=literal_arg("until", until, SnoozeOptionName),
        )
        return (
            _state(context)
            .snooze(context.actor, data.key, data.severity, data.until)
            .model_dump(mode="json")
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_my_reminder_state() -> list[dict[str, Any]]:
        """The reminders that whoever is asking has already closed or snoozed."""
        return [item.model_dump(mode="json") for item in _state(context).mine(context.actor)]

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def describe_scheduled_jobs() -> dict[str, Any]:
        """How the server's automatic operations went for this space:
        Gmail sync, reminders, daily automations, weekly summary. For
        each: `state` (`ok`, `failed`, `late`, `never_run`, `not_configured`),
        `last_run_at`, `last_failure_at` and `failure` if the last attempt failed. Only for
        administrators.
        """
        return ScheduledJobService(context.session).describe(context.actor).model_dump(mode="json")


def _scope(value: str) -> Any:
    """`space` or `me`, refused by the schema's own literal when it is neither."""
    from pigrocrm.core.errors import ValidationFailed

    if value not in ("space", "me"):
        raise ValidationFailed(
            "reminder_preference", "scope", "unknown scope", expected="`space` or `me`"
        )
    return value
