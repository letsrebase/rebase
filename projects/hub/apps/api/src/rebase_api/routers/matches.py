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
the seam's sentence when the CRM does not answer. Without the token the match reads
say so too (`_reading`): the card's sentence is the 503's, and there is no «Riprova».
"""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from rebase_api.deps import (
    AdminDep,
    EngagementsDep,
    FreelancerDoorDep,
    RendererDep,
    SessionDep,
    SettingsDep,
    SigningDep,
)
from rebase_api.downloads import pdf_response
from rebase_api.params import LIST_LIMIT_DEFAULT, Limit, Offset, SearchQ
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
from rebase_core.engagement_handoff import EngagementHandoff, HandoffFailed, HandoffRead
from rebase_core.errors import NotFound
from rebase_core.fiscal import FiscalService
from rebase_core.match_reads import MatchReads
from rebase_core.match_words import PIGRO_NOT_CONFIGURED
from rebase_core.matches import MatchService, require_live_document, require_live_match
from rebase_core.pigro_door import PigroUnavailable

router = APIRouter(prefix="/api/hub", tags=["hub-admin"])

NO_SIGNED_COPY = "Questo documento non ha ancora una copia firmata."

Status = Annotated[str | None, Query(max_length=20)]


def _require_pigro(settings: Settings) -> None:
    """503 with the sentence the match card shows when this environment has no token for
    the CRM's door: the service alone would leave the match waiting without a word."""
    if not settings.pigro_engagements_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, PIGRO_NOT_CONFIGURED)


def _reading(session: Session, settings: Settings) -> MatchReads:
    """The service as the routes that answer a match's words need it: whether this
    environment has the CRM's token, so an active match's card says
    «Consuntivo non configurato su questo ambiente.» and offers no «Riprova» without it
    (spec § 3.2)."""
    return MatchReads(session, pigro_configured=bool(settings.pigro_engagements_token))


def _writing(session: Session, settings: Settings, renderer: Renderer) -> MatchService:
    """The service as the two routes that typeset need it: the renderer, and who signs
    for rebase. The reads take neither."""
    return MatchService(
        session,
        renderer,
        signer_data(settings.signer_json),
        pigro_configured=bool(settings.pigro_engagements_token),
    )


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


class HandoffBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: UUID


@router.post("/freelancers/{freelancer_id}/engagement-handoff", response_model=HandoffRead)
def hand_off_engagement(
    _: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    door: FreelancerDoorDep,
    freelancer_id: UUID,
    body: HandoffBody,
) -> HandoffRead:
    """«Crea match» (REB-1105): the CRM finds or writes the freelancer's organization, the hub
    keeps a `handoff` row under a freshly minted engagement id with the company's budget
    snapshot, and the answer carries the CRM's engagement form to open, filled in. A request not
    in the CRM yet answers `no_deal` with its sentence and writes nothing. 503 without the CRM's
    token, 409 with the CRM's sentence when it refuses the card, 502 when it does not answer."""
    if not settings.pigro_engagements_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, PIGRO_NOT_CONFIGURED)
    try:
        return EngagementHandoff(session, door).open(freelancer_id, body.company_id)
    except HandoffFailed as exc:
        code = status.HTTP_409_CONFLICT if exc.rejected else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, exc.sentence) from exc


@router.get("/freelancers/{freelancer_id}/matches", response_model=FreelancerContracts)
def list_freelancer_matches(
    _: AdminDep, session: SessionDep, settings: SettingsDep, freelancer_id: UUID
) -> FreelancerContracts:
    return _reading(session, settings).for_freelancer(freelancer_id)


@router.get("/freelancers/{freelancer_id}/matches/prefill", response_model=MatchPrefill)
def prefill_match(
    _: AdminDep, session: SessionDep, freelancer_id: UUID, company_id: UUID
) -> MatchPrefill:
    return MatchService(session).drafts.prefill(freelancer_id, company_id)


@router.post("/freelancers/{freelancer_id}/matches/preview")
def preview_match_document(
    _: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    renderer: RendererDep,
    freelancer_id: UUID,
    payload: MatchCreate,
    document: Literal["letter", "framework"] = "letter",
) -> Response:
    """The previews of «Controlla e invia», step 3 of «Crea match»: one document,
    typeset now, saved nowhere, numbered never."""
    pdf = _writing(session, settings, renderer).drafts.preview(freelancer_id, payload, document)
    return pdf_response(pdf.filename, pdf.content)


@router.post("/freelancers/{freelancer_id}/matches/check", response_model=MatchCheck)
def check_match(
    _: AdminDep, session: SessionDep, freelancer_id: UUID, payload: MatchCreate
) -> MatchCheck:
    """«Controlla e invia», step 3 of «Crea match» (REB-476): what saving would do, in
    sentences, with nothing written and no number taken. 422 naming the field as
    `create` does, `company_id` for a closed request; missing tax data are reported
    (`missing_tax_data`), not refused."""
    return MatchService(session).drafts.check(freelancer_id, payload)


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
    the framework agreement. 422 naming `tax` without tax data, `company_id` for a
    closed request. `payload.id`, when given, makes a retry idempotent (REB-406): the
    match already written under it comes back instead of a second one, and the same id
    already used by another freelancer's match is a 409."""
    return _writing(session, settings, renderer).create(freelancer_id, payload, admin.id)


@router.get("/matches", response_model=MatchList)
def list_matches(
    _: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    status: Status = None,
    q: SearchQ = None,
    limit: Limit = LIST_LIMIT_DEFAULT,
    offset: Offset = 0,
) -> MatchList:
    """«Match» (REB-413): every match, newest first. Declared before
    `GET /matches/{match_id}` -- FastAPI matches routes in the order they are
    registered, and a static path must come first or `/matches/{match_id}` would
    swallow it. 422 naming `status` for an unknown state."""
    return _reading(session, settings).list_all(status=status, q=q, limit=limit, offset=offset)


@router.get("/matches/{match_id}", response_model=MatchRead)
def get_match(_: AdminDep, session: SessionDep, settings: SettingsDep, match_id: UUID) -> MatchRead:
    require_live_match(session, match_id)
    return _reading(session, settings).get(match_id)


@router.post("/matches/{match_id}/cancel", response_model=MatchRead)
def cancel_match(
    admin: AdminDep, session: SessionDep, signing: SigningDep, match_id: UUID
) -> MatchRead:
    """A draft; or a match in signature, whose letter's envelope is cancelled on
    Documenso too (REB-407)."""
    require_live_match(session, match_id)
    return signing(session).admin.cancel_match(match_id, admin.id)


@router.post("/matches/{match_id}/close", response_model=MatchRead)
def close_match(
    admin: AdminDep, session: SessionDep, settings: SettingsDep, match_id: UUID
) -> MatchRead:
    require_live_match(session, match_id)
    return MatchService(session, pigro_configured=bool(settings.pigro_engagements_token)).close(
        match_id, admin.id
    )


@router.post("/matches/{match_id}/pigro/link", response_model=MatchRead)
def link_match_to_pigro(
    admin: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    engagements: EngagementsDep,
    match_id: UUID,
) -> MatchRead:
    """«Riprova su Pigro» (REB-499): the link to the match's deal runs now, as the
    admin, and the match comes back as it stands, `linked` or with the CRM's sentence
    (`error`, `rejected`). The browser waits for it, up to the 90 seconds the first
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
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
) -> MatchReport:
    """«Consuntivo» (REB-499): the hours on the match's deal, asked of the CRM now and
    stored nowhere, by default over the whole engagement (`from` the letter's start, `to`
    today). 409 with where the link stands for a match not `linked`, and for a deal
    the CRM says was deleted in the space, which files the match `rejected` (spec
    § 3.10); 422 naming `from` for a period that ends before it starts, 502 with the
    seam's sentence when the CRM does not answer with a report, 503 without the token."""
    require_live_match(session, match_id)
    _require_pigro(settings)
    try:
        return engagements.report(match_id, from_date, to_date)
    except PigroUnavailable as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/matches/{match_id}/send", response_model=SendReport)
def send_match(
    admin: AdminDep, session: SessionDep, signing: SigningDep, match_id: UUID
) -> SendReport:
    """«Invia per la firma»: the document that can leave now goes to Documenso and the
    freelancer gets its mail; a letter whose framework agreement is not signed yet waits
    for it. 503 when this environment cannot sign, 502 when Documenso refuses, 409 for a
    draft text or a match with nothing left to send, 422 on `regime` for an ordinary
    letter whose freelancer has no partita IVA any more."""
    require_live_match(session, match_id)
    return signing(session).send_match(match_id, admin.id)


@router.post("/contract-documents/{document_id}/refresh", response_model=ContractDocumentRead)
def refresh_contract(
    _: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Aggiorna stato»: what Documenso says about the envelope, applied as the webhook
    would, and whatever a signature still leaves to do (REB-407)."""
    require_live_document(session, document_id)
    return signing(session).admin.refresh(document_id)


@router.post("/contract-documents/{document_id}/resend", response_model=ContractDocumentRead)
def resend_contract(
    admin: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Reinvia email»: the signing mail again, for a document still waiting."""
    require_live_document(session, document_id)
    return signing(session).admin.resend_mail(document_id, admin.id)


@router.post("/contract-documents/{document_id}/cancel", response_model=ContractDocumentRead)
def cancel_contract(
    admin: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Annulla» on a framework agreement not signed yet; a letter goes with its match."""
    require_live_document(session, document_id)
    return signing(session).admin.cancel_document(document_id, admin.id)


@router.post("/contract-documents/{document_id}/notice", response_model=ContractDocumentRead)
def record_contract_notice(
    admin: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Registra disdetta» on an active framework agreement."""
    require_live_document(session, document_id)
    return signing(session).admin.record_notice(document_id, admin.id)


@router.get("/contract-documents/{document_id}/pdf")
def download_contract(
    _: AdminDep, session: SessionDep, document_id: UUID, signed: bool = False
) -> Response:
    """404 when the document itself is gone, its freelancer is soft-deleted, or, with
    `signed=true`, there is no signed copy yet -- in plain Italian, since the core's
    own sentence for that last case ("documento firmato ... non trovato") reads oddly
    to an admin."""
    require_live_document(session, document_id)
    try:
        pdf = MatchReads(session).document_pdf(document_id, signed=signed)
    except NotFound as exc:
        if signed:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SIGNED_COPY) from exc
        raise
    return pdf_response(pdf.filename, pdf.content)
