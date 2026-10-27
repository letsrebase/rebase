"""«Valutazione» (REB-836, plan Task 12): the admin's door to the evaluation set the card
writer is measured against. Admin only, every route. An admin reads the profiles and the
runs, corrects a profile's expected card (which sends it back to `bozza`), accepts it
and rates a run's role, skills and summary on the three-point scale. The rules (a card
to accept, 1 to 3 ratings, the audit of field names never values) live in
`CardEvalService`; this module only maps HTTP onto it."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from rebase_api.deps import AdminDep, SessionDep
from rebase_core.card_eval import CardEvalService
from rebase_core.card_eval_schemas import CardEvalRead, EvalProfileRead, EvalRunRead
from rebase_core.team_schemas import Card

router = APIRouter(prefix="/api/hub/admin/card-eval", tags=["hub-admin"])


class RatingsBody(BaseModel):
    """`freelancer_id -> {"ruolo" | "competenze" | "sintesi": 1..3}`; the service rejects
    anything else with a 422."""

    model_config = ConfigDict(extra="forbid")

    ratings: dict[UUID, dict[str, int]]


def get_service(session: SessionDep) -> CardEvalService:
    return CardEvalService(session)


ServiceDep = Annotated[CardEvalService, Depends(get_service)]


@router.get("", response_model=CardEvalRead)
def read_all(admin: AdminDep, service: ServiceDep) -> CardEvalRead:
    return service.list()


@router.put("/{freelancer_id}", response_model=EvalProfileRead)
def put_expected(
    admin: AdminDep, service: ServiceDep, freelancer_id: UUID, card: Card
) -> EvalProfileRead:
    """422 for a body that is not a `Card`; a saved correction is `bozza` again."""
    return service.set_expected(admin.id, freelancer_id, card)


@router.post("/{freelancer_id}/accept", response_model=EvalProfileRead)
def accept(admin: AdminDep, service: ServiceDep, freelancer_id: UUID) -> EvalProfileRead:
    """409 for a profile with no expected card."""
    return service.accept(admin.id, freelancer_id)


@router.post("/runs/{run_id}/ratings", response_model=EvalRunRead)
def rate(admin: AdminDep, service: ServiceDep, run_id: UUID, body: RatingsBody) -> EvalRunRead:
    return service.rate(admin.id, run_id, body.ratings)
