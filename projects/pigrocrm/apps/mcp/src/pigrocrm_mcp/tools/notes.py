from typing import Any
from uuid import UUID

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.notes.schemas import NoteCreate, NoteUpdate
from pigrocrm.core.notes.service import NoteService
from pigrocrm_mcp.context import McpContext

# How many notes of a deal, a customer or a person `get_deal` and its two siblings carry:
# the latest ones, newest first. The whole thread is in the `deal://`, `customer://` and
# `person://` resources. The cap is stated in each tool's description, like `get_contract`'s
# `note_clauses`.
THREAD_LIMIT = 50


def add(
    context: McpContext,
    text: str,
    deal_id: str | None,
    customer_id: str | None,
    person_id: str | None,
) -> dict[str, Any]:
    """The note on the one record named: exactly one of the three ids, as `create_task`
    takes its single reference."""
    named = {
        entity_type: entity_id
        for entity_type, entity_id in (
            ("deal", deal_id),
            ("customer", customer_id),
            ("person", person_id),
        )
        if entity_id
    }
    if len(named) != 1:
        raise ValidationFailed(
            "input",
            "deal_id, customer_id, person_id",
            "serve uno solo fra `deal_id`, `customer_id` e `person_id`"
            if not named
            else "`deal_id`, `customer_id` e `person_id` sono alternativi: passane uno solo",
            expected="l'id del deal, del cliente o della persona a cui va la nota",
        )
    ((entity_type, entity_id),) = named.items()
    return (
        NoteService(context.session)
        .add(entity_type, UUID(entity_id), NoteCreate(text=text), context.actor)
        .model_dump(mode="json")
    )


def update(context: McpContext, note_id: str, text: str) -> dict[str, Any]:
    return (
        NoteService(context.session)
        .update(UUID(note_id), NoteUpdate(text=text), context.actor)
        .model_dump(mode="json")
    )


def delete(context: McpContext, note_id: str) -> dict[str, str]:
    NoteService(context.session).delete(UUID(note_id), context.actor)
    return {"status": "eliminata", "note_id": note_id}


def thread(context: McpContext, entity_type: str, entity_id: UUID) -> dict[str, Any]:
    """The `note` and `note_total` keys a `get_*` answer adds to the record: the latest
    `THREAD_LIMIT` notes, newest first, and how many the record has."""
    service = NoteService(context.session)
    notes = service.list_for_entity(entity_type, entity_id, context.actor, limit=THREAD_LIMIT)
    return {
        "note": [note.model_dump(mode="json") for note in notes],
        "note_total": service.count_for_entity(entity_type, entity_id, context.actor),
    }


def refuse_note_in_changes(data: dict[str, Any], add_tool: str) -> None:
    """A `note` in the `changes` of an `update_deal`, `update_customer` or `update_person`:
    the record has a thread now, and the schema's own «campo non riconosciuto» would not
    say where the note goes."""
    if "note" in data:
        raise ValidationFailed(
            "input",
            "changes.note",
            "le note non si cambiano da qui",
            expected=(
                f"`{add_tool}` per aggiungere una nota; `update_note` e `delete_note` per "
                "correggerne o toglierne una"
            ),
        )
