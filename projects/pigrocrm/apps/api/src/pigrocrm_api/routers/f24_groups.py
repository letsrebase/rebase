"""F24 groups (REB-1076, taxes design § 2 and § 5), behind the `taxes` module.

The due items that fall on one date, credits included, are grouped into one F24, each line with the
code the pack gives it («codice da verificare» where the pack has none). The group's total is the
net, and one bank movement equal to it settles the group and every line: that is the bank
matching's (`/api/bank-movements`, which offers an open group as a candidate and settles it as a
whole). Every route is an administrator's, enforced by `F24GroupService`: a router with an
authorisation `if` is one the MCP adapter cannot reuse.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from pigrocrm.core.fiscal.f24 import F24GroupService
from pigrocrm.core.fiscal.f24_schemas import (
    F24GroupCreate,
    F24GroupListQuery,
    F24GroupPage,
    F24GroupRead,
    F24GroupState,
    F24ReceiptAttach,
)
from pigrocrm.core.modules import enabled_modules
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor, module_guard
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/f24-groups",
    tags=["f24-groups"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("taxes")],
)


@router.get("", response_model=F24GroupPage)
def list_f24_groups(
    session: SessionDep,
    actor: ActorDep,
    state: Annotated[
        F24GroupState | None,
        Query(description="The groups in this state; omitted, the open ones."),
    ] = None,
    legal_entity_id: Annotated[
        UUID | None, Query(description="Only the groups of this legal entity.")
    ] = None,
    due_from: Annotated[
        date | None, Query(description="Payment date from, YYYY-MM-DD, inclusive.")
    ] = None,
    due_to: Annotated[
        date | None, Query(description="Payment date to, YYYY-MM-DD, inclusive.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[UUID | None, Query(description="`next_cursor` of the last page.")] = None,
) -> F24GroupPage:
    """Groups by payment date, each with its lines, their codes and the net to pay."""
    return F24GroupService(session).list_f24_groups(
        F24GroupListQuery.model_validate(
            {
                "state": state,
                "legal_entity_id": legal_entity_id,
                "due_from": due_from,
                "due_to": due_to,
                "limit": limit,
                "cursor": cursor,
            }
        ),
        actor,
    )


@router.post("", response_model=F24GroupRead, status_code=201)
def group_f24(
    data: F24GroupCreate, session: SessionDep, actor: ActorDep, settings: SettingsDep
) -> F24GroupRead:
    """Groups the named due items into one F24, dated on their common date. They must be owed to
    the tax office, open with nothing paid on them and fall on one day, and their net (the debits
    less the credits) must be positive. A tax forecast becomes the item that is paid. Each line
    keeps the pack's code for it, or none (read «codice da verificare»)."""
    return F24GroupService(session).group_f24(data, actor, modules=enabled_modules(settings))


@router.get("/{group_id}", response_model=F24GroupRead)
def get_f24_group(group_id: UUID, session: SessionDep, actor: ActorDep) -> F24GroupRead:
    return F24GroupService(session).get_f24_group(group_id, actor)


@router.post("/{group_id}/ungroup", response_model=F24GroupRead)
def ungroup_f24(group_id: UUID, session: SessionDep, actor: ActorDep) -> F24GroupRead:
    """Releases the lines of a group nobody has paid. A group a movement paid is refused: undo
    the match first."""
    return F24GroupService(session).ungroup_f24(group_id, actor)


@router.post("/{group_id}/receipts", response_model=F24GroupRead, status_code=201)
def attach_f24_receipt(
    group_id: UUID, data: F24ReceiptAttach, session: SessionDep, actor: ActorDep
) -> F24GroupRead:
    """Points the group at an inbox item of kind `tax_document`: the receipt of the F24. The file
    is the inbox's (upload it there, set its kind); nothing is copied."""
    return F24GroupService(session).attach_f24_receipt(group_id, data.inbox_item_id, actor)


@router.delete("/{group_id}/receipts/{inbox_item_id}", response_model=F24GroupRead)
def detach_f24_receipt(
    group_id: UUID, inbox_item_id: UUID, session: SessionDep, actor: ActorDep
) -> F24GroupRead:
    """Takes a receipt off the group; the inbox item stays where it is."""
    return F24GroupService(session).detach_f24_receipt(group_id, inbox_item_id, actor)
