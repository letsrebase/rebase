"""The last executions of the automations, as the settings page shows them.

A read of `activities` by `kind`, never a table of its own (§9.4). A function and not a
method of `AutomationConfigService`, whose public methods are the audited surface the
architecture test counts: the same list is served by `describe_automations` and by the
API's `GET /api/automation-runs`, which takes its own `limit`.
"""

from sqlalchemy.orm import Session

from pigrocrm.core.activities.repository import ActivityRepository
from pigrocrm.core.automations.schemas import AUTOMATION_KINDS, AutomationRun


def recent_runs(session: Session, limit: int) -> list[AutomationRun]:
    return [
        AutomationRun(
            kind=activity.kind,
            occurred_at=activity.occurred_at,
            # `None` unless the activity really is about a deal. A bare `entity_id` here
            # would give the `automation_config` row's own id for a configuration change,
            # which renders as a link to a deal that does not exist.
            deal_id=activity.entity_id if activity.entity_type == "deal" else None,
            rule=activity.payload.get("rule"),
            reason=activity.payload.get("reason"),
            payload=activity.payload,
        )
        for activity in ActivityRepository(session).by_kind(AUTOMATION_KINDS, limit)
    ]
