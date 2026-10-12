"""A freelancer's own contracts, for the member area's «Contratti» (REB-392).

Read-only, and only ever the caller's: the card comes from the session's user
(`MemberService.require_card`), never from the URL, and a document that is not theirs
is the same 404 as one that does not exist, never a 403. The person sees what has
reached them: every document that went out for signature, and a letter that waits for
its framework agreement once its match was sent. A draft match, and a document
cancelled before it left, are the admin's business. The signing link is shown only
while the document waits for the signature: its path is the signer's token. A
cancelled document's page still opens on Documenso and fails only at the click, so the
state shown here is what tells the person not to sign it. `quadri_precedenti` keeps
every earlier framework agreement that was signed visible under the current one: a
notice, or a newer framework replacing it, moves it there rather than off the page.
Since REB-687 a freelancer may hold an ordinary and an occasional framework agreement,
and each shows on top in its own place.
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
from rebase_core.regimes import OCCASIONALE, PARTITA_IVA, as_regime, document_label


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
    pigro_url = match.pigro_url if match is not None and match.pigro_stato == "collegato" else None
    return MemberContract(
        id=document.id,
        kind=document.kind,
        regime=as_regime(document.regime),
        tipo=document_label(document.kind, document.regime),
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
        pigro_url=pigro_url,
    )


def _current(quadri: list[MemberContract], regime: str) -> MemberContract | None:
    """The framework agreement of `regime` the page shows on top (REB-687): the active
    one, else the newest that reached the person."""
    mine = [quadro for quadro in quadri if quadro.regime == regime]
    return next((quadro for quadro in mine if quadro.attivo), None) or (mine[0] if mine else None)


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
        quadro = _current(quadri, PARTITA_IVA)
        quadro_occasionale = _current(quadri, OCCASIONALE)
        # Every other framework agreement that was signed (REB-392): a notice, or a
        # newer one taking over on top, must not make its own signed copy disappear
        # from the page, only move it down here. Newest first, same as `quadri`.
        on_top = {shown.id for shown in (quadro, quadro_occasionale) if shown is not None}
        quadri_precedenti = [q for q in quadri if q.ha_pdf_firmato and q.id not in on_top]
        lettere = [
            _read(document, match, today) for document, match in visible if document.kind == LETTERA
        ]
        return MemberContracts(
            quadro=quadro,
            quadro_occasionale=quadro_occasionale,
            quadri_precedenti=quadri_precedenti,
            lettere=lettere,
        )

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
