"""The team builder's public routes (REB-512, spec § 3.2): a visitor describes a project
and gets an anonymous team, then files «Assumi team» with a company name, an email and a
phone. No login, like the wizards, and behind the same speed bump (`spend_one`): the
proposal because each one costs a call to Claude, the request because each one mails
rebase.

A proposal also waits for a slot of the process (`ProposalSlotsDep`, spec § 5): Claude
holds a worker thread for seconds to tens of seconds, on the pool the member area and
the webhooks share, so the one past `REBASE_TEAM_BUILDER_CONCURRENCY` answers 503 at
once; and for room in the day (`team_caps.require_daily_room`). Both answer the same
«Troppe richieste» sentence. What the public read carries is C4's (`TeamBuilder`): no
freelancer id and no place of a card.
"""

import logging

from fastapi import APIRouter, Request, status

from rebase_api.deps import (
    ProposalSlotsDep,
    SenderDep,
    SessionDep,
    SettingsDep,
    TeamBuilderDep,
    TrackerDep,
)
from rebase_api.ratelimit import spend_one
from rebase_core.errors import TeamBuilderBusy, TeamBuilderOff
from rebase_core.team_builder import OFF_SENTENCE
from rebase_core.team_caps import BUSY_SENTENCE, require_daily_room
from rebase_core.team_requests import TeamRequestService
from rebase_core.team_schemas import (
    TeamProposalCreate,
    TeamProposalRead,
    TeamRequestCreate,
    TeamRequestCreated,
)

router = APIRouter(prefix="/api/hub/team", tags=["hub"])

_log = logging.getLogger(__name__)


@router.post("/proposals", response_model=TeamProposalRead)
def propose_team(
    data: TeamProposalCreate,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    builder: TeamBuilderDep,
    slots: ProposalSlotsDep,
) -> TeamProposalRead:
    """«Proponi il team» and «Rigenera» (`previous_id`, `nota`). 503 «Il team builder è
    spento.» without a key or with the switch off, 503 «Troppe richieste…» with every
    slot taken or the day's proposals spent, 502 when Claude does not answer."""
    spend_one(request)
    # Before the caps: an environment with the builder off says so, not «Troppe
    # richieste», whatever the day's count.
    if not settings.team_builder_enabled or builder.llm is None:
        raise TeamBuilderOff(OFF_SENTENCE)
    if not slots.acquire(blocking=False):
        _log.info("team builder: every proposal slot is taken")
        raise TeamBuilderBusy(BUSY_SENTENCE)
    try:
        require_daily_room(session, settings)
        return builder.propose(data, origine="pubblico", user_id=None)
    finally:
        slots.release()


@router.post("/requests", response_model=TeamRequestCreated, status_code=status.HTTP_201_CREATED)
def request_team(
    data: TeamRequestCreate,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    sender: SenderDep,
    tracker: TrackerDep,
) -> TeamRequestCreated:
    """«Invia la richiesta»: 201 with the request's id; 409 on a proposal already
    requested, a second click included; 422 on a proposal older than a day, not a
    public one, or with nobody in it."""
    spend_one(request)
    read = TeamRequestService(session, settings=settings, sender=sender, tracker=tracker).create(
        data, origine="pubblico", user_id=None, company_id=None
    )
    return TeamRequestCreated(id=read.id)
