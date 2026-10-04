"""The aziende of a space, and each one's fiscal profile (REB-616, spec 2026-10-03 §3).

These routes replace `GET/PUT /api/emitter` and `GET/PUT /api/fiscal-profile`, which
addressed one row with no id. There is no `POST /api/aziende` here on purpose: creating
a second azienda opens in milestone 5 (§9), after the register, the customer chain, the
rendering and the per-azienda taxes can serve it, and a test asserts the route is
absent until then. Everything an admin could do to the one profile before, they do to
`/api/aziende/{id}` now; the SPA reads the id from `GET /api/aziende`.

Admin-only writes are enforced by the services (`actor.require_admin`), not by a router
dependency: there is no role dependency in this codebase, and adding one here would
put the same rule in two places.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from pigrocrm.core.emitter.schemas import NAZIONE_MAX_LENGTH, AziendaRead, AziendaUpsert
from pigrocrm.core.emitter.service import AziendaService
from pigrocrm.core.fiscal.schemas import FiscalProfileRead, FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/aziende", tags=["aziende"], responses=PROBLEM_RESPONSES)


@router.get("", response_model=list[AziendaRead])
def list_aziende(
    session: SessionDep, actor: ActorDep, include_inactive: bool = False
) -> list[AziendaRead]:
    """The default first. Deactivated aziende are left out unless asked for: a
    selector never offers one, Impostazioni may still show it."""
    return AziendaService(session).list(actor, only_active=not include_inactive)


# Before `/{azienda_id}`: a literal segment declared after the parameterised route would
# be read as an id and answer 422.
@router.get("/proposta", response_model=AziendaRead)
def propose_azienda(
    session: SessionDep,
    actor: ActorDep,
    nazione: Annotated[
        SafeStr | None,
        Query(
            max_length=NAZIONE_MAX_LENGTH,
            description="Nazione del cliente, ISO 3166-1 alpha-2; omessa, IT",
        ),
    ] = None,
) -> AziendaRead:
    """The azienda a new customer of `nazione` would be billed by when nobody picks one
    (REB-624, spec 2026-10-03 §1.6). The form and an agent both ask here, so the rule
    lives in `AziendaService.propose` once; `POST /api/customers` applies the same one
    when `azienda_id` is left out."""
    return AziendaRead.model_validate(AziendaService(session).propose(nazione))


@router.get("/{azienda_id}", response_model=AziendaRead)
def get_azienda(azienda_id: UUID, session: SessionDep, actor: ActorDep) -> AziendaRead:
    return AziendaService(session).get(actor, azienda_id)


@router.put("/{azienda_id}", response_model=AziendaRead)
def update_azienda(
    azienda_id: UUID, data: AziendaUpsert, session: SessionDep, actor: ActorDep
) -> AziendaRead:
    """Whole-row replacement, as the single profile always was: a key left out goes
    back to its default."""
    return AziendaService(session).update(azienda_id, data, actor)


@router.post("/{azienda_id}/predefinita", response_model=AziendaRead)
def set_default_azienda(azienda_id: UUID, session: SessionDep, actor: ActorDep) -> AziendaRead:
    return AziendaService(session).set_default(azienda_id, actor)


@router.delete("/{azienda_id}", response_model=AziendaRead)
def deactivate_azienda(azienda_id: UUID, session: SessionDep, actor: ActorDep) -> AziendaRead:
    """Deactivation, never a row delete: an azienda that issued an invoice stays
    readable forever. Refused on the default; move the default first."""
    return AziendaService(session).deactivate(azienda_id, actor)


@router.get("/{azienda_id}/fiscal-profile", response_model=FiscalProfileRead)
def get_fiscal_profile(azienda_id: UUID, session: SessionDep, actor: ActorDep) -> FiscalProfileRead:
    """404 until it is saved once: an empty profile and an unsaved one are different
    facts, and nothing can be issued without it."""
    return FiscalProfileService(session).get(actor, azienda_id)


@router.put(
    "/{azienda_id}/fiscal-profile",
    response_model=FiscalProfileRead,
    status_code=status.HTTP_200_OK,
)
def upsert_fiscal_profile(
    azienda_id: UUID, data: FiscalProfileUpsert, session: SessionDep, actor: ActorDep
) -> FiscalProfileRead:
    return FiscalProfileService(session).upsert(data, actor, azienda_id)
