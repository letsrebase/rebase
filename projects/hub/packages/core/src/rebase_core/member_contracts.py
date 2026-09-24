"""A freelancer's own contracts, for the member area's «Contratti» (REB-392).

Read-only, and only ever the caller's: the card comes from the session's user
(`MemberService.require_card`), never from the URL, and a document that is not theirs
is the same 404 as one that does not exist, never a 403. The person sees what has
reached them: every document that went out for signature, and a letter that waits for
its framework agreement once its match was sent. A draft match, and a document
cancelled before it left, are the admin's business. The signing link is shown only
while the document waits for the signature: its path is the signer's token. A
cancelled document's page still opens on Documenso and fails only at the click, so the
state shown here is what tells the person not to sign it.
"""

from collections.abc import Callable
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.contract_schemas import ContractPdf, MemberContract, MemberContracts
from rebase_core.errors import NotFound
from rebase_core.framework import framework_dates, rome_today
from rebase_core.matches import LETTERA, QUADRO, MatchService
from rebase_core.members import MemberService
from rebase_core.models import ContractDocument, Match


def _visible(document: ContractDocument, match: Match | None) -> bool:
    """A document has reached the person once it has gone out for signature; a letter
    that has not yet, but whose match is already in `in_firma`, is the one waiting for
    its framework agreement's own signature."""
    if document.sent_at is not None:
        return True
    return (
        document.kind == LETTERA
        and document.stato == "in_attesa"
        and match is not None
        and match.stato == "in_firma"
    )


def _printed(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _read(document: ContractDocument, match: Match | None, today: date) -> MemberContract:
    active, renewal, last_notice = framework_dates(document, today)
    data = document.data or {}
    return MemberContract(
        id=document.id,
        kind=document.kind,
        numero=document.numero,
        stato=document.stato,
        cliente=match.cliente_ragione_sociale if match is not None else None,
        inizio=_printed(data.get("data-inizio")),
        fine=_printed(data.get("data-fine")),
        sent_at=document.sent_at,
        signed_at=document.signed_at,
        signing_url=document.signing_url if document.stato == "inviato" else None,
        ha_pdf_firmato=document.signed_pdf is not None,
        attivo=active,
        rinnovo=renewal,
        ultimo_giorno_disdetta=last_notice,
    )


class MemberContractService:
    def __init__(self, session: Session, today: Callable[[], date] = rome_today) -> None:
        self.session = session
        self.today = today

    def for_user(self, user_id: UUID) -> MemberContracts:
        freelancer = MemberService(self.session).require_card(user_id)
        rows = self.session.execute(
            select(ContractDocument, Match)
            .outerjoin(Match, Match.id == ContractDocument.match_id)
            .where(ContractDocument.freelancer_id == freelancer.id)
            .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
        ).all()
        today = self.today()
        visible = [(document, match) for document, match in rows if _visible(document, match)]
        quadri = [
            _read(document, None, today) for document, _ in visible if document.kind == QUADRO
        ]
        quadro = next((q for q in quadri if q.attivo), None) or (quadri[0] if quadri else None)
        lettere = [
            _read(document, match, today) for document, match in visible if document.kind == LETTERA
        ]
        return MemberContracts(quadro=quadro, lettere=lettere)

    def signed_pdf(self, user_id: UUID, document_id: UUID) -> ContractPdf:
        freelancer = MemberService(self.session).require_card(user_id)
        document = self.session.get(ContractDocument, document_id)
        if (
            document is None
            or document.freelancer_id != freelancer.id
            or document.signed_pdf is None
        ):
            raise NotFound("documento", document_id)
        return MatchService(self.session).document_pdf(document_id, signed=True)
