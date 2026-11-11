"""Thin calls into `ExpenseNoteService`, like every other tool module.

An agent submits a note for the staff member its owner is and reads the notes it may see; the
service decides which (a staff member's own, or an administrator's every note with
`full_access`). Deciding and paying back are the privileged tools (`tools/privileged.py`).
"""

from typing import Any
from uuid import UUID

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.expense_notes.schemas import (
    Bucket,
    ExpenseNoteCreate,
    ExpenseNoteListQuery,
    NoteKind,
    NoteStatus,
)
from pigrocrm.core.expense_notes.service import ExpenseNoteService
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools.params import (
    literal_arg,
    optional_money,
    parse_legal_entity_id,
    required_iso_date,
)


def _uuid(name: str, value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValidationFailed("input", name, "is not an id", expected="a UUID") from exc


def submit(
    context: McpContext,
    *,
    date: str,
    description: str,
    category_id: str,
    kind: str,
    amount: float | str | None,
    inbox_item_id: str | None,
    kilometres: float | str | None,
    rate_per_km: float | str | None,
    route_from: str | None,
    route_to: str | None,
) -> dict[str, Any]:
    data = ExpenseNoteCreate.model_validate(
        {
            "date": required_iso_date(date),
            "description": description,
            "category_id": _uuid("category_id", category_id),
            "kind": literal_arg("kind", kind, NoteKind),
            "amount": optional_money(amount, "amount"),
            "inbox_item_id": _uuid("inbox_item_id", inbox_item_id),
            "kilometres": optional_money(kilometres, "kilometres"),
            "rate_per_km": optional_money(rate_per_km, "rate_per_km"),
            "route_from": route_from,
            "route_to": route_to,
        }
    )
    return (
        ExpenseNoteService(context.session)
        .submit_expense_note(data, context.actor)
        .model_dump(mode="json")
    )


def search(
    context: McpContext,
    *,
    status: str | None,
    bucket: str | None,
    staff_member_id: str | None,
    category_id: str | None,
    legal_entity_id: str | None,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    query = ExpenseNoteListQuery(
        status=literal_arg("status", status, NoteStatus),
        bucket=literal_arg("bucket", bucket, Bucket),
        staff_member_id=_uuid("staff_member_id", staff_member_id),
        category_id=_uuid("category_id", category_id),
        legal_entity_id=parse_legal_entity_id(legal_entity_id),
        limit=limit,
        cursor=_uuid("cursor", cursor),
    )
    return (
        ExpenseNoteService(context.session)
        .list_expense_notes(query, context.actor)
        .model_dump(mode="json")
    )
