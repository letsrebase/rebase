from typing import Any
from uuid import UUID

from pigrocrm.core.people.schemas import PersonCreate, PersonListQuery, PersonUpdate
from pigrocrm.core.people.service import PersonService
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import notes
from pigrocrm_mcp.tools.changes import refuse_empty, report


def create(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    return (
        PersonService(context.session)
        .create(PersonCreate(**data), context.actor)
        .model_dump(mode="json")
    )


def update(context: McpContext, person_id: str, data: dict[str, Any]) -> dict[str, Any]:
    service = PersonService(context.session)
    identifier = UUID(person_id)
    notes.refuse_note_in_changes(data, "add_note")
    changes = PersonUpdate(**data)
    refuse_empty(data)
    before = service.get(identifier, context.actor)
    return report(before, service.update(identifier, changes, context.actor))


def get(context: McpContext, person_id: str) -> dict[str, Any]:
    identifier = UUID(person_id)
    person = PersonService(context.session).get(identifier, context.actor)
    return {**person.model_dump(mode="json"), **notes.thread(context, "person", identifier)}


def search(context: McpContext, query: PersonListQuery) -> dict[str, Any]:
    page = PersonService(context.session).list(query, context.actor)
    return {
        "items": [item.model_dump(mode="json") for item in page.items],
        # Already a string since slice 6 (an opaque `(sort value, id)` cursor), so no
        # `str()`: keeping one here would suggest the value is still a UUID being
        # rendered, which is exactly the assumption this change removes.
        "next_cursor": page.next_cursor,
    }


def archive(context: McpContext, person_id: str) -> dict[str, str]:
    PersonService(context.session).soft_delete(UUID(person_id), context.actor)
    return {"status": "archiviato", "person_id": person_id}


def restore(context: McpContext, person_id: str) -> dict[str, Any]:
    return (
        PersonService(context.session)
        .restore(UUID(person_id), context.actor)
        .model_dump(mode="json")
    )
