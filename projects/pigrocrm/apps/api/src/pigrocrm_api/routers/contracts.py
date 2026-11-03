from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.clause_note_service import ClauseNoteService
from pigrocrm.core.contracts.projection_service import ContractProjectionService
from pigrocrm.core.contracts.rate_card_service import RateCardService
from pigrocrm.core.contracts.renewal_assumption_service import RenewalAssumptionService
from pigrocrm.core.contracts.schemas import (
    ClauseNoteCreate,
    ClauseNoteRead,
    ClauseNoteUpdate,
    ContractConcentrationCap,
    ContractCreate,
    ContractDetail,
    ContractListQuery,
    ContractPage,
    ContractProjectionQuery,
    ContractProjectionRead,
    ContractRead,
    ContractRenew,
    ContractStateChange,
    ContractUpdate,
    RateCardCreate,
    RateCardRead,
    RateCardUpdate,
    RenewalAssumptionRead,
    RenewalAssumptionUpsert,
)
from pigrocrm.core.contracts.service import ContractService
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import LEGAL_ENTITY_QUERY_DESCRIPTION, SortedParams, sort_field
from pigrocrm_api.timeline import timeline_router

router = APIRouter(
    prefix="/api/contracts",
    tags=["contracts"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.post("", response_model=ContractRead, status_code=status.HTTP_201_CREATED)
def create(data: ContractCreate, session: SessionDep, actor: ActorDep) -> ContractRead:
    return ContractService(session).create(data, actor)


class ContractListParams(SortedParams):
    customer_id: UUID | None = None
    status: str | None = None
    legal_entity_id: UUID | None = Field(default=None, description=LEGAL_ENTITY_QUERY_DESCRIPTION)
    sort: SafeStr | None = sort_field("created_at", "updated_at", "title")


@router.get("", response_model=ContractPage)
def list_contracts(
    session: SessionDep, actor: ActorDep, params: Annotated[ContractListParams, Query()]
) -> ContractPage:
    return ContractService(session).list(params.to_query(ContractListQuery), actor)


@router.get("/{contract_id}", response_model=ContractDetail)
def get(contract_id: UUID, session: SessionDep, actor: ActorDep) -> ContractDetail:
    """The contract with its clause notes (REB-762) and, if it has been renewed,
    the id of its renewal (REB-763)."""
    return ContractService(session).detail(contract_id, actor)


@router.patch("/{contract_id}", response_model=ContractRead)
def update(
    contract_id: UUID, data: ContractUpdate, session: SessionDep, actor: ActorDep
) -> ContractRead:
    return ContractService(session).update(contract_id, data, actor)


@router.post("/{contract_id}/state", response_model=ContractRead)
def set_state(
    contract_id: UUID, data: ContractStateChange, session: SessionDep, actor: ActorDep
) -> ContractRead:
    return ContractService(session).set_state(contract_id, data, actor)


@router.post(
    "/{contract_id}/renew", response_model=ContractRead, status_code=status.HTTP_201_CREATED
)
def renew(
    contract_id: UUID, data: ContractRenew, session: SessionDep, actor: ActorDep
) -> ContractRead:
    """Renews the contract (REB-763): the successor has the same terms, starts the
    day after this one ends and lasts as long as it did, except for `start`, `end` or `duration`.
    The rate cards that reach the new term move with it; its own stay
    where they are. A contract is renewed once (409) and only if it has an end (422)."""
    return ContractService(session).renew(contract_id, data, actor)


def _contract_exists(session: Session, contract_id: UUID, actor: Actor) -> None:
    ContractService(session).get(contract_id, actor)


router.include_router(timeline_router("contract", id_param="contract_id", guard=_contract_exists))


@router.get("/{contract_id}/concentration", response_model=ContractConcentrationCap)
def concentration_cap(
    contract_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    as_of: Annotated[date | None, Query()] = None,
    threshold: Annotated[float | None, Query(ge=0, le=1)] = None,
) -> ContractConcentrationCap:
    return ContractService(session).concentration_cap(contract_id, actor, as_of, threshold)


@router.post(
    "/{contract_id}/rate-cards",
    response_model=RateCardRead,
    status_code=status.HTTP_201_CREATED,
)
def create_rate_card(
    contract_id: UUID,
    data: RateCardCreate,
    session: SessionDep,
    actor: ActorDep,
    close_previous: Annotated[
        bool,
        Query(
            description="Closes, the day before, the day or hour rate card in force at this one's "
            "start date, instead of refusing the overlap; refused on a fee card"
        ),
    ] = False,
) -> RateCardRead:
    return RateCardService(session).create(contract_id, data, actor, close_previous=close_previous)


@router.patch("/{contract_id}/rate-cards/{rate_card_id}", response_model=RateCardRead)
def update_rate_card(
    contract_id: UUID,
    rate_card_id: UUID,
    data: RateCardUpdate,
    session: SessionDep,
    actor: ActorDep,
) -> RateCardRead:
    return RateCardService(session).update(contract_id, rate_card_id, data, actor)


@router.get("/{contract_id}/rate-cards", response_model=list[RateCardRead])
def list_rate_cards(contract_id: UUID, session: SessionDep, actor: ActorDep) -> list[RateCardRead]:
    return RateCardService(session).list_for_contract(contract_id, actor)


@router.post(
    "/{contract_id}/clause-notes",
    response_model=ClauseNoteRead,
    status_code=status.HTTP_201_CREATED,
)
def create_clause_note(
    contract_id: UUID, data: ClauseNoteCreate, session: SessionDep, actor: ActorDep
) -> ClauseNoteRead:
    """A reading of a clause: the reference, the literal text and the adopted
    interpretation (REB-762)."""
    return ClauseNoteService(session).create(contract_id, data, actor)


@router.patch("/{contract_id}/clause-notes/{note_id}", response_model=ClauseNoteRead)
def update_clause_note(
    contract_id: UUID,
    note_id: UUID,
    data: ClauseNoteUpdate,
    session: SessionDep,
    actor: ActorDep,
) -> ClauseNoteRead:
    return ClauseNoteService(session).update(contract_id, note_id, data, actor)


@router.get("/{contract_id}/clause-notes", response_model=list[ClauseNoteRead])
def list_clause_notes(
    contract_id: UUID, session: SessionDep, actor: ActorDep
) -> list[ClauseNoteRead]:
    return ClauseNoteService(session).list_for_contract(contract_id, actor)


@router.put("/{contract_id}/renewal-assumption", response_model=RenewalAssumptionRead)
def upsert_renewal_assumption(
    contract_id: UUID, data: RenewalAssumptionUpsert, session: SessionDep, actor: ActorDep
) -> RenewalAssumptionRead:
    """Admin or collaborator (the write role), enforced by the service (`actor.require_write`), not
    here -- there is no role dependency in this codebase, and adding one here would
    put the same rule in two places."""
    return RenewalAssumptionService(session).upsert(contract_id, data, actor)


@router.get("/{contract_id}/renewal-assumption", response_model=RenewalAssumptionRead)
def get_renewal_assumption(
    contract_id: UUID, session: SessionDep, actor: ActorDep
) -> RenewalAssumptionRead:
    return RenewalAssumptionService(session).get(contract_id, actor)


@router.get("/{contract_id}/projection", response_model=ContractProjectionRead)
def project_revenue(
    contract_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    date_from: Annotated[
        date, Query(alias="from", description="Start of the window, YYYY-MM-DD, inclusive")
    ],
    date_to: Annotated[
        date, Query(alias="to", description="End of the window, YYYY-MM-DD, exclusive")
    ],
    as_of: Annotated[
        date | None,
        Query(description="Reference date for the irrevocability window; default today"),
    ] = None,
) -> ContractProjectionRead:
    """The «scheduled» revenue beyond the irrevocability window, plus the contribution
    of the renewal assumption, if any -- a figure distinct from the `projected` of
    `GET /api/analytics/overview`, never combined with it."""
    query = ContractProjectionQuery(date_from=date_from, date_to=date_to, as_of=as_of)
    return ContractProjectionService(session).project(contract_id, query, actor)
