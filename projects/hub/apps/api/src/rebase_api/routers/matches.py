"""Matches and the contracts they write, behind the admin cookie (REB-387, phase 2).

An admin pairs a freelancer card with a company request; the hub writes the letter of
engagement and, when the freelancer has no active framework agreement, the framework
agreement too, and both PDFs are downloadable from here. «Invia per la firma» sends a
match's documents through Documenso (`rebase_core.signing`, phase 3). The routes sit
under `/api/hub/` beside the rest of the admin area. A contract that cannot be typeset
is a 503 with a sentence (`main.domain_error_handler`).

An active match is linked to its deal on Pigro (REB-499, `rebase_core.engagements`):
«Riprova su Pigro» links it now, «Consuntivo» reads its hours. Both answer the split
`routers/pigro.py` makes: 503 when this environment has no token for the CRM, 502 with
the seam's sentence when the CRM does not answer.
"""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from rebase_api.deps import (
    AdminDep,
    EngagementsDep,
    RendererDep,
    SessionDep,
    SettingsDep,
    SigningDep,
)
from rebase_api.downloads import pdf_response
from rebase_core.config import Settings
from rebase_core.contract_schemas import (
    ContractDocumentRead,
    FiscalData,
    FiscalRead,
    FreelancerContracts,
    MatchCheck,
    MatchCreate,
    MatchList,
    MatchPrefill,
    MatchRead,
    MatchReport,
    SendReport,
)
from rebase_core.contracts.fields import signer_data
from rebase_core.contracts.render import Renderer
from rebase_core.engagements import PIGRO_NOT_CONFIGURED
from rebase_core.errors import NotFound
from rebase_core.fiscal import FiscalService
from rebase_core.matches import (
    LIST_LIMIT_DEFAULT,
    LIST_LIMIT_MAX,
    MatchService,
    require_live_document,
    require_live_match,
)
from rebase_core.pigro import PigroUnavailable
from rebase_core.search import SEARCH_MAX_LENGTH

router = APIRouter(prefix="/api/hub", tags=["hub-admin"])

NO_SIGNED_COPY = "Questo documento non ha ancora una copia firmata."

Limit = Annotated[int, Query(ge=1, le=LIST_LIMIT_MAX)]
Offset = Annotated[int, Query(ge=0)]
SearchQ = Annotated[str | None, Query(max_length=SEARCH_MAX_LENGTH)]
Stato = Annotated[str | None, Query(max_length=20)]


def _require_pigro(settings: Settings) -> None:
    """503 with the sentence the match card shows when this environment has no token for
    the CRM's door: the service alone would leave the match waiting without a word."""
    if not settings.pigro_engagements_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, PIGRO_NOT_CONFIGURED)


def _writing(session: Session, settings: Settings, renderer: Renderer) -> MatchService:
    """The service as the two routes that typeset need it: the renderer, and who signs
    for rebase. The reads take neither."""
    return MatchService(session, renderer, signer_data(settings.signer_json))


@router.get("/freelancers/{freelancer_id}/fiscal", response_model=FiscalRead | None)
def get_fiscal(_: AdminDep, session: SessionDep, freelancer_id: UUID) -> FiscalRead | None:
    return FiscalService(session).get(freelancer_id)


@router.put("/freelancers/{freelancer_id}/fiscal", response_model=FiscalRead)
def save_fiscal(
    admin: AdminDep, session: SessionDep, freelancer_id: UUID, payload: FiscalData
) -> FiscalRead:
    """«Chi e per chi», step 1 of «Crea match», and the form on «Match e contratti»:
    saved for next time."""
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
    """The previews of «Controlla e invia», step 3 of «Crea match»: one document,
    typeset now, saved nowhere, numbered never."""
    pdf = _writing(session, settings, renderer).preview(freelancer_id, payload, documento)
    return pdf_response(pdf.filename, pdf.content)


@router.post("/freelancers/{freelancer_id}/matches/check", response_model=MatchCheck)
def check_match(
    _: AdminDep, session: SessionDep, freelancer_id: UUID, payload: MatchCreate
) -> MatchCheck:
    """«Controlla e invia», step 3 of «Crea match» (REB-476): what saving would do, in
    sentences, with nothing written and no number taken. 422 naming the field as
    `create` does, `company_id` for a closed request; missing tax data are reported
    (`dati_fiscali_mancanti`), not refused."""
    return MatchService(session).check(freelancer_id, payload)


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
    """«Salva senza inviare»: the draft match with its numbered letter and, when needed,
    the framework agreement. 422 naming `fiscale` without tax data, `company_id` for a
    closed request. `payload.id`, when given, makes a retry idempotent (REB-406): the
    match already written under it comes back instead of a second one, and the same id
    already used by another freelancer's match is a 409."""
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
    require_live_match(session, match_id)
    return MatchService(session).get(match_id)


@router.post("/matches/{match_id}/cancel", response_model=MatchRead)
def cancel_match(
    admin: AdminDep, session: SessionDep, signing: SigningDep, match_id: UUID
) -> MatchRead:
    """A draft; or a match in signature, whose letter's envelope is cancelled on
    Documenso too (REB-407)."""
    require_live_match(session, match_id)
    return signing(session).cancel_match(match_id, admin.id)


@router.post("/matches/{match_id}/close", response_model=MatchRead)
def close_match(admin: AdminDep, session: SessionDep, match_id: UUID) -> MatchRead:
    require_live_match(session, match_id)
    return MatchService(session).close(match_id, admin.id)


@router.post("/matches/{match_id}/pigro/link", response_model=MatchRead)
def link_match_to_pigro(
    admin: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    engagements: EngagementsDep,
    match_id: UUID,
) -> MatchRead:
    """«Riprova su Pigro» (REB-499): the link to the match's deal runs now, as the
    admin, and the match comes back as it stands, `collegato` or with the CRM's sentence
    (`errore`, `rifiutato`). The browser waits for it, up to the 90 seconds the first
    link of a freelancer takes to open their space. 409 for a match not active, 503
    without the token."""
    require_live_match(session, match_id)
    _require_pigro(settings)
    try:
        return engagements.link(match_id, admin.id)
    except PigroUnavailable as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/matches/{match_id}/report", response_model=MatchReport)
def match_report(
    _: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    engagements: EngagementsDep,
    match_id: UUID,
    da: date | None = None,
    a: date | None = None,
) -> MatchReport:
    """«Consuntivo» (REB-499): the hours on the match's deal, asked of the CRM now and
    stored nowhere, by default over the whole engagement (`da` the letter's start, `a`
    today). 409 with where the link stands for a match not `collegato`, 422 naming `da`
    for a period that ends before it starts, 502 with the seam's sentence when the CRM
    does not answer with a report, 503 without the token."""
    require_live_match(session, match_id)
    _require_pigro(settings)
    try:
        return engagements.report(match_id, da, a)
    except PigroUnavailable as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/matches/{match_id}/send", response_model=SendReport)
def send_match(
    admin: AdminDep, session: SessionDep, signing: SigningDep, match_id: UUID
) -> SendReport:
    """«Invia per la firma»: the document that can leave now goes to Documenso and the
    freelancer gets its mail; a letter whose framework agreement is not signed yet waits
    for it. 503 when this environment cannot sign, 502 when Documenso refuses, 409 for a
    draft text or a match with nothing left to send."""
    require_live_match(session, match_id)
    return signing(session).send_match(match_id, admin.id)


@router.post("/contract-documents/{document_id}/refresh", response_model=ContractDocumentRead)
def refresh_contract(
    _: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Aggiorna stato»: what Documenso says about the envelope, applied as the webhook
    would, and whatever a signature still leaves to do (REB-407)."""
    require_live_document(session, document_id)
    return signing(session).refresh(document_id)


@router.post("/contract-documents/{document_id}/resend", response_model=ContractDocumentRead)
def resend_contract(
    admin: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Reinvia email»: the signing mail again, for a document still waiting."""
    require_live_document(session, document_id)
    return signing(session).resend_mail(document_id, admin.id)


@router.post("/contract-documents/{document_id}/cancel", response_model=ContractDocumentRead)
def cancel_contract(
    admin: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Annulla» on a framework agreement not signed yet; a letter goes with its match."""
    require_live_document(session, document_id)
    return signing(session).cancel_document(document_id, admin.id)


@router.post("/contract-documents/{document_id}/notice", response_model=ContractDocumentRead)
def record_contract_notice(
    admin: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Registra disdetta» on an active framework agreement."""
    require_live_document(session, document_id)
    return signing(session).record_notice(document_id, admin.id)


@router.get("/contract-documents/{document_id}/pdf")
def download_contract(
    _: AdminDep, session: SessionDep, document_id: UUID, firmato: bool = False
) -> Response:
    """404 when the document itself is gone, its freelancer is soft-deleted, or, with
    `firmato=true`, there is no signed copy yet -- in plain Italian, since the core's
    own sentence for that last case ("documento firmato ... non trovato") reads oddly
    to an admin."""
    require_live_document(session, document_id)
    try:
        pdf = MatchService(session).document_pdf(document_id, signed=firmato)
    except NotFound as exc:
        if firmato:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SIGNED_COPY) from exc
        raise
    return pdf_response(pdf.filename, pdf.content)
