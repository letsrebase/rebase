from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.activities.repository import ActivityRepository
from pigrocrm.core.activities.sanitize import sanitize_payload
from pigrocrm.core.activities.schemas import ActivityRead
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import ValidationFailed

# Column widths from activities/models.py. entity_type/kind are literals developers
# write in code, not user input or agent-supplied data, so plain truncation -- not the
# richer sanitize_payload treatment the payload gets -- is enough: nothing meaningful
# is hidden by cutting off a value nobody was going to type out in full anyway.
_ENTITY_TYPE_MAX_LENGTH = 30
_KIND_MAX_LENGTH = 50

# Matches every REST timeline route's own `Query(ge=1, le=200)` exactly (see
# routers/customers.py, people.py, deals.py). Enforced here, not only per-router,
# because the MCP `get_timeline` tool calls straight into this method with a bare
# `limit: int` parameter of its own -- no schema of any kind sits between the tool
# and this call, unlike search_customers/search_people/search_deals, whose `limit`
# is bounded through `*ListQuery`'s own `Field(ge=1, le=200)`. Without this check,
# `-1` reached Postgres raw as `psycopg.errors.InvalidRowCountInLimitClause` ("LIMIT
# must not be negative"), and `10**9` silently returned everything where REST would
# have refused the request outright -- a parity divergence, not only a crash risk.
TIMELINE_LIMIT_MIN = 1
TIMELINE_LIMIT_MAX = 200


class ActivityService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = ActivityRepository(session)

    def record(
        self,
        entity_type: str,
        entity_id: UUID,
        kind: str,
        actor: Actor,
        payload: dict[str, Any] | None = None,
    ) -> Activity:
        """Flushes but never commits: it joins the caller's transaction so a timeline
        entry can never survive a change that was rolled back.

        Exactly because of that, this must be the *last* thing that touches the
        session before the caller's own commit. Never follow it with a call into
        another service's method that commits on its own behalf in the same
        session -- that commit would persist this entry too, even if the caller's own
        transaction is later rolled back, which defeats the guarantee above.

        `payload` is sanitized by `sanitize_payload`, never validated: an audit entry
        must not be able to fail the very change it is recording. See that function's
        docstring for what "sanitized" means here and why rejecting is not an option.
        """
        return self.repo.add(
            Activity(
                entity_type=entity_type[:_ENTITY_TYPE_MAX_LENGTH],
                entity_id=entity_id,
                kind=kind[:_KIND_MAX_LENGTH],
                actor_id=actor.id,
                actor_type=actor.type,
                payload=sanitize_payload(payload or {}),
            )
        )

    def timeline(self, entity_type: str, entity_id: UUID, limit: int = 50) -> list[ActivityRead]:
        if not TIMELINE_LIMIT_MIN <= limit <= TIMELINE_LIMIT_MAX:
            raise ValidationFailed(
                "activity",
                "limit",
                "fuori intervallo",
                expected=f"{TIMELINE_LIMIT_MIN}-{TIMELINE_LIMIT_MAX}",
            )
        return serve_activities(self.session, self.repo.timeline(entity_type, entity_id, limit))


def serve_activities(session: Session, rows: Iterable[Activity]) -> list[ActivityRead]:
    """The rows as the API and the MCP serve them: each with the name of the member
    behind it (REB-781).

    A function and not an `ActivityService` method: the operational dashboard serves its
    «attività recenti» through it too, and a public method on the service would be a
    surface the MCP coverage sweep demands a tool or an exclusion for, for what is a
    helper to two readers.

    One query for the whole page, not one per row. `User` is read without a filter on
    `attivo`: a member who has left still did what the timeline says they did, and a
    blank where their name was would read as a system action. A row whose id no user row
    holds (a space restored from a partial dump, a token owner removed by hand) keeps
    `actor_name=None` rather than failing the read.
    """
    activities = list(rows)
    wanted: set[UUID] = set()
    for activity in activities:
        wanted.update(
            member
            for member in (activity.actor_id, _triggering_member(activity))
            if member is not None
        )
    names: dict[UUID, str] = {}
    # Imported here, not at the top: `auth/__init__` loads `pat_service`, which imports this
    # module for `ActivityService`, so a top-level import of `auth.models` is a cycle that
    # only an import order starting in `auth` survives.
    from pigrocrm.core.auth.models import User

    if wanted:
        found = session.execute(select(User.id, User.nome).where(User.id.in_(wanted)))
        names = {user_id: nome for user_id, nome in found}
    result: list[ActivityRead] = []
    for activity in activities:
        read = ActivityRead.model_validate(activity)
        if activity.actor_id is not None:
            read.actor_name = names.get(activity.actor_id)
        trigger = _triggering_member(activity)
        if trigger is not None:
            read.triggered_by_name = names.get(trigger)
        result.append(read)
    return result


def _triggering_member(activity: Activity) -> UUID | None:
    """The member an automation's entry names in `payload.attivata_da`, if it names one.

    The payload is free-form JSON written by many callers, so anything that is not the
    text of a UUID is no member at all rather than an error: a read of the timeline must
    not fail on one odd row."""
    raw = activity.payload.get("attivata_da") if activity.actor_type == "system" else None
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None
