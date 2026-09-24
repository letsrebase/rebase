"""Matches and the contracts they write, behind the admin cookie (REB-387, phase 2).

An admin pairs a freelancer card with a company request; the hub writes the letter of
engagement and, when the freelancer has no active framework agreement, the framework
agreement too, and both PDFs are downloadable from here. Nothing leaves the hub yet:
sending for signature arrives with Documenso (phase 3). The routes sit under `/api/hub/`
beside the rest of the admin area. A contract that cannot be typeset is a 503 with a
sentence (`main.domain_error_handler`).
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy.orm import Session

from rebase_api.deps import AdminDep, RendererDep, SessionDep, SettingsDep
from rebase_api.downloads import pdf_response
from rebase_core.config import Settings
from rebase_core.contract_schemas import (
    FiscalData,
    FiscalRead,
    FreelancerContracts,
    MatchCreate,
    MatchPrefill,
    MatchRead,
)
from rebase_core.contracts.fields import signer_data
from rebase_core.contracts.render import Renderer
from rebase_core.errors import NotFound
from rebase_core.fiscal import FiscalService
from rebase_core.matches import ENTITY, MatchService
from rebase_core.models import ContractDocument, Freelancer, Match

router = APIRouter(prefix="/api/hub", tags=["hub-admin"])

DOCUMENT_ENTITY = "documento"
NO_SIGNED_COPY = "Questo documento non ha ancora una copia firmata."


def _writing(session: Session, settings: Settings, renderer: Renderer) -> MatchService:
    """The service as the two routes that typeset need it: the renderer, and who signs
    for rebase. The reads take neither."""
    return MatchService(session, renderer, signer_data(settings.signer_json))


def _require_live_freelancer(
    session: Session, freelancer_id: UUID, entity: str, identifier: UUID
) -> None:
    """A match or a document of a soft-deleted freelancer is gone from the admin area,
    the same way `for_freelancer` already treats it in the core: `MatchService.get` and
    `document_pdf` read no further than the row itself, so the route enforces it here."""
    freelancer = session.get(Freelancer, freelancer_id)
    if freelancer is None or freelancer.deleted_at is not None:
        raise NotFound(entity, identifier)


@router.get("/freelancers/{freelancer_id}/fiscal", response_model=FiscalRead | None)
def get_fiscal(_: AdminDep, session: SessionDep, freelancer_id: UUID) -> FiscalRead | None:
    return FiscalService(session).get(freelancer_id)


@router.put("/freelancers/{freelancer_id}/fiscal", response_model=FiscalRead)
def save_fiscal(
    admin: AdminDep, session: SessionDep, freelancer_id: UUID, payload: FiscalData
) -> FiscalRead:
    """Step 2 of «Crea match» and the form on «Match e contratti»: saved for next time."""
    return FiscalService(session).save(freelancer_id, payload, admin.id)


@router.get("/freelancers/{freelancer_id}/matches", response_model=FreelancerContracts)
def list_freelancer_matches(
    _: AdminDep, session: SessionDep, freelancer_id: UUID
) -> FreelancerContracts:
    return MatchService(session).for_freelancer(freelancer_id)


@router.get("/freelancers/{freelancer_id}/matches/prefill", response_model=MatchPrefill)
def prefill_match(
    _: AdminDep, session: SessionDep, freelancer_id: UUID, company_id: UUID
) -> MatchPrefill:
    return MatchService(session).prefill(freelancer_id, company_id)


@router.post("/freelancers/{freelancer_id}/matches/preview")
def preview_match_document(
    _: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    renderer: RendererDep,
    freelancer_id: UUID,
    payload: MatchCreate,
    documento: Literal["lettera", "quadro"] = "lettera",
) -> Response:
    """Step 5's preview: one document, typeset now, saved nowhere, numbered never."""
    pdf = _writing(session, settings, renderer).preview(freelancer_id, payload, documento)
    return pdf_response(pdf.filename, pdf.content)


@router.post(
    "/freelancers/{freelancer_id}/matches",
    response_model=MatchRead,
    status_code=status.HTTP_201_CREATED,
)
def create_match(
    admin: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    renderer: RendererDep,
    freelancer_id: UUID,
    payload: MatchCreate,
) -> MatchRead:
    """«Salva come bozza»: the draft match with its numbered letter and, when needed,
    the framework agreement. 422 naming `fiscale` without tax data, `company_id` for a
    closed request."""
    return _writing(session, settings, renderer).create(freelancer_id, payload, admin.id)


@router.get("/matches/{match_id}", response_model=MatchRead)
def get_match(_: AdminDep, session: SessionDep, match_id: UUID) -> MatchRead:
    match = MatchService(session).get(match_id)
    _require_live_freelancer(session, match.freelancer_id, ENTITY, match_id)
    return match


@router.post("/matches/{match_id}/cancel", response_model=MatchRead)
def cancel_match(admin: AdminDep, session: SessionDep, match_id: UUID) -> MatchRead:
    match = session.get(Match, match_id)
    if match is None:
        raise NotFound(ENTITY, match_id)
    _require_live_freelancer(session, match.freelancer_id, ENTITY, match_id)
    return MatchService(session).cancel(match_id, admin.id)


@router.post("/matches/{match_id}/close", response_model=MatchRead)
def close_match(admin: AdminDep, session: SessionDep, match_id: UUID) -> MatchRead:
    match = session.get(Match, match_id)
    if match is None:
        raise NotFound(ENTITY, match_id)
    _require_live_freelancer(session, match.freelancer_id, ENTITY, match_id)
    return MatchService(session).close(match_id, admin.id)


@router.get("/contract-documents/{document_id}/pdf")
def download_contract(
    _: AdminDep, session: SessionDep, document_id: UUID, firmato: bool = False
) -> Response:
    """404 when the document itself is gone, its freelancer is soft-deleted, or, with
    `firmato=true`, there is no signed copy yet -- in plain Italian, since the core's
    own sentence for that last case ("documento firmato ... non trovato") reads oddly
    to an admin."""
    document = session.get(ContractDocument, document_id)
    if document is None:
        raise NotFound(DOCUMENT_ENTITY, document_id)
    _require_live_freelancer(session, document.freelancer_id, DOCUMENT_ENTITY, document_id)
    try:
        pdf = MatchService(session).document_pdf(document_id, signed=firmato)
    except NotFound as exc:
        if firmato:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SIGNED_COPY) from exc
        raise
    return pdf_response(pdf.filename, pdf.content)
