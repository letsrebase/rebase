"""Matches and the contracts they write, behind the admin cookie (REB-387, phase 2).

An admin pairs a freelancer card with a company request; the hub writes the letter of
engagement and, when the freelancer has no active framework agreement, the framework
agreement too, and both PDFs are downloadable from here. «Invia per la firma» sends a
match's documents through Documenso (`rebase_core.signing`, phase 3). The routes sit
under `/api/hub/` beside the rest of the admin area. A contract that cannot be typeset
is a 503 with a sentence (`main.domain_error_handler`).
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from rebase_api.deps import AdminDep, RendererDep, SessionDep, SettingsDep, SigningDep
from rebase_api.downloads import pdf_response
from rebase_core.config import Settings
from rebase_core.contract_schemas import (
    ContractDocumentRead,
    FiscalData,
    FiscalRead,
    FreelancerContracts,
    MatchCreate,
    MatchList,
    MatchPrefill,
    MatchRead,
    SendReport,
)
from rebase_core.contracts.fields import signer_data
from rebase_core.contracts.render import Renderer
from rebase_core.errors import NotFound
from rebase_core.fiscal import FiscalService
from rebase_core.matches import ENTITY, LIST_LIMIT_DEFAULT, LIST_LIMIT_MAX, MatchService
from rebase_core.models import ContractDocument, Freelancer, Match
from rebase_core.search import SEARCH_MAX_LENGTH

router = APIRouter(prefix="/api/hub", tags=["hub-admin"])

DOCUMENT_ENTITY = "documento"
NO_SIGNED_COPY = "Questo documento non ha ancora una copia firmata."

Limit = Annotated[int, Query(ge=1, le=LIST_LIMIT_MAX)]
Offset = Annotated[int, Query(ge=0)]
SearchQ = Annotated[str | None, Query(max_length=SEARCH_MAX_LENGTH)]
Stato = Annotated[str | None, Query(max_length=20)]


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


def _document_guard(session: Session, document_id: UUID) -> ContractDocument:
    """404 when the document itself is gone or its freelancer is soft-deleted, exactly as
    `download_contract` already checks it, before a signing action reaches the service."""
    document = session.get(ContractDocument, document_id)
    if document is None:
        raise NotFound(DOCUMENT_ENTITY, document_id)
    _require_live_freelancer(session, document.freelancer_id, DOCUMENT_ENTITY, document_id)
    return document


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


@router.get("/matches", response_model=MatchList)
def list_matches(
    _: AdminDep,
    session: SessionDep,
    stato: Stato = None,
    q: SearchQ = None,
    limit: Limit = LIST_LIMIT_DEFAULT,
    offset: Offset = 0,
) -> MatchList:
    """«Match» (REB-413): every match, newest first. Declared before
    `GET /matches/{match_id}` -- FastAPI matches routes in the order they are
    registered, and a static path must come first or `/matches/{match_id}` would
    swallow it. 422 naming `stato` for an unknown state."""
    return MatchService(session).list_all(stato=stato, q=q, limit=limit, offset=offset)


@router.get("/matches/{match_id}", response_model=MatchRead)
def get_match(_: AdminDep, session: SessionDep, match_id: UUID) -> MatchRead:
    match = MatchService(session).get(match_id)
    _require_live_freelancer(session, match.freelancer_id, ENTITY, match_id)
    return match


@router.post("/matches/{match_id}/cancel", response_model=MatchRead)
def cancel_match(
    admin: AdminDep, session: SessionDep, signing: SigningDep, match_id: UUID
) -> MatchRead:
    """A draft; or a match in signature, whose letter's envelope is cancelled on
    Documenso too (REB-407)."""
    match = session.get(Match, match_id)
    if match is None:
        raise NotFound(ENTITY, match_id)
    _require_live_freelancer(session, match.freelancer_id, ENTITY, match_id)
    return signing(session).cancel_match(match_id, admin.id)


@router.post("/matches/{match_id}/close", response_model=MatchRead)
def close_match(admin: AdminDep, session: SessionDep, match_id: UUID) -> MatchRead:
    match = session.get(Match, match_id)
    if match is None:
        raise NotFound(ENTITY, match_id)
    _require_live_freelancer(session, match.freelancer_id, ENTITY, match_id)
    return MatchService(session).close(match_id, admin.id)


@router.post("/matches/{match_id}/send", response_model=SendReport)
def send_match(
    admin: AdminDep, session: SessionDep, signing: SigningDep, match_id: UUID
) -> SendReport:
    """«Invia per la firma»: the document that can leave now goes to Documenso and the
    freelancer gets its mail; a letter whose framework agreement is not signed yet waits
    for it. 503 when this environment cannot sign, 502 when Documenso refuses, 409 for a
    draft text or a match with nothing left to send."""
    match = session.get(Match, match_id)
    if match is None:
        raise NotFound(ENTITY, match_id)
    _require_live_freelancer(session, match.freelancer_id, ENTITY, match_id)
    return signing(session).send_match(match_id, admin.id)


@router.post("/contract-documents/{document_id}/refresh", response_model=ContractDocumentRead)
def refresh_contract(
    _: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Aggiorna stato»: what Documenso says about the envelope, applied as the webhook
    would, and whatever a signature still leaves to do (REB-407)."""
    _document_guard(session, document_id)
    return signing(session).refresh(document_id)


@router.post("/contract-documents/{document_id}/resend", response_model=ContractDocumentRead)
def resend_contract(
    admin: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Reinvia email»: the signing mail again, for a document still waiting."""
    _document_guard(session, document_id)
    return signing(session).resend_mail(document_id, admin.id)


@router.post("/contract-documents/{document_id}/cancel", response_model=ContractDocumentRead)
def cancel_contract(
    admin: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Annulla» on a framework agreement not signed yet; a letter goes with its match."""
    _document_guard(session, document_id)
    return signing(session).cancel_document(document_id, admin.id)


@router.post("/contract-documents/{document_id}/notice", response_model=ContractDocumentRead)
def record_contract_notice(
    admin: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Registra disdetta» on an active framework agreement."""
    _document_guard(session, document_id)
    return signing(session).record_notice(document_id, admin.id)


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
