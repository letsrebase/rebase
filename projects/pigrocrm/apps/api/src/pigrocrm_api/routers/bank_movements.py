"""Matching a bank movement to the due items it pays (REB-1042, cash design § 3), behind the
`cash` module.

A movement is proposed to its items, a person confirms the match (which settles them with the
movement as evidence) and a confirmed match is undone (which reopens them). Every route is an
administrator's. Confirming and undoing are a person's acts: a personal access token is refused
whatever it carries, and an agent proposes (`match-proposal`) and reads. The ranking, the sums and
the authority are `MovementMatchService`'s; a router that holds an `if` for them is one the MCP
adapter cannot reuse.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from pigrocrm.core.bank.match_schemas import (
    MatchConfirm,
    MatchItemRead,
    MatchPreview,
    MatchProposalCreate,
    MatchSelection,
    MatchUndo,
)
from pigrocrm.core.bank.matching import (
    OPEN_ITEMS_LIMIT_DEFAULT,
    OPEN_ITEMS_LIMIT_MAX,
    MovementMatchService,
)
from pigrocrm.core.bank.schemas import MovementDetail
from pigrocrm.core.modules import enabled_modules
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor, module_guard
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/bank-movements",
    tags=["bank-movements"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("cash")],
)


@router.get("/{movement_id}", response_model=MovementDetail)
def get_movement(
    movement_id: UUID, session: SessionDep, actor: ActorDep, settings: SettingsDep
) -> MovementDetail:
    """The movement with its live proposal or confirmed match, up to three ranked candidates
    while it waits (each with the reasons it was ranked by) and what was dismissed or undone."""
    return MovementMatchService(session).get_movement(
        movement_id, actor, modules=enabled_modules(settings)
    )


@router.get("/{movement_id}/open-items", response_model=list[MatchItemRead])
def list_open_items(
    movement_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    search: Annotated[
        str | None,
        Query(
            max_length=100,
            description="Keeps the items whose counterparty, description or "
            "document number contains this text",
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=OPEN_ITEMS_LIMIT_MAX)] = OPEN_ITEMS_LIMIT_DEFAULT,
) -> list[MatchItemRead]:
    """The open due items this movement could pay (same company, currency and direction), by due
    date: what a person searches when none of the candidates is the right one."""
    return MovementMatchService(session).list_open_items(
        movement_id, actor, search=search, limit=limit, modules=enabled_modules(settings)
    )


@router.post("/{movement_id}/match-preview", response_model=MatchPreview)
def preview_match(
    movement_id: UUID,
    data: MatchSelection,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
) -> MatchPreview:
    """What confirming these items would do, without doing it: the part of the movement each
    takes, `difference` (zero when they cover it exactly) and whether one would be paid in part."""
    return MovementMatchService(session).preview(
        movement_id, data, actor, modules=enabled_modules(settings)
    )


@router.post("/{movement_id}/match-proposal", response_model=MovementDetail)
def propose_match(
    movement_id: UUID,
    data: MatchProposalCreate,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
) -> MovementDetail:
    """Records that the movement pays these items, for a person to confirm. It settles nothing,
    and replaces a proposal that is waiting. The items must cover the movement exactly."""
    return MovementMatchService(session).propose_match(
        movement_id, data, actor, modules=enabled_modules(settings)
    )


@router.post("/{movement_id}/match", response_model=MovementDetail)
def confirm_match(
    movement_id: UUID,
    data: MatchConfirm,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
) -> MovementDetail:
    """A person confirms: each item is settled, dated on the movement's booking date, with the
    movement as the payment's evidence (or the movement attaches to the payment already marked
    by hand for the same amount). A personal access token is refused (403)."""
    return MovementMatchService(session).confirm(
        movement_id, data, actor, modules=enabled_modules(settings)
    )


@router.post("/{movement_id}/unmatch", response_model=MovementDetail)
def undo_match(
    movement_id: UUID,
    data: MatchUndo,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
) -> MovementDetail:
    """A person takes the confirmed match back: the payments it wrote are reversed (the rows stay,
    stamped), the items reopen and the movement waits again. A personal access token is refused
    (403)."""
    return MovementMatchService(session).undo(
        movement_id, data, actor, modules=enabled_modules(settings)
    )
