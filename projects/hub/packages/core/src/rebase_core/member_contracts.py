"""A freelancer's own contracts, for the member area's «Contratti» (REB-392, REB-1107).

Read-only, and only ever the caller's: the card comes from the session's user
(`MemberService.require_card`), never from the URL, and a document that is not theirs is
the same 404 as one that does not exist, never a 403. The documents themselves are the
CRM's since the relationship with a freelancer moved there (collaborators design § 5): they
are read through the CRM's door by the hub's freelancer id (`FreelancerDoor.contracts`), and
the signed PDF is served the same way. What reaches the person is the CRM's decision: every
document that went out for signature, and a letter that waits for its framework agreement
once its engagement was sent. The signing link is shown only while the document waits for the
signature: its path is the signer's token. A cancelled document's page still opens on
Documenso and fails only at the click, so the state shown here is what tells the person not
to sign it. `previous_frameworks` keeps every earlier framework agreement that was signed
visible under the current one: a notice, or a newer framework replacing it, moves it there
rather than off the page. A freelancer may hold an ordinary and an occasional framework
agreement (REB-687), and each shows on top in its own place.

**The CRM's own vocabulary stops here.** `framework_agreement` and `engagement_letter` become
the hub's `framework` and `letter`, `ordinary` becomes `vat_registered`, so the page and its
types stay as they were. The renewal and the last day for a notice are the CRM's too.

**When the CRM cannot be asked**, `PigroUnavailable` reaches the route, which answers 502 and
503: the page shows its error line, since a list built from nothing would tell a person with a
signed contract that they have none. Nothing is read from the hub's own contract tables.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from rebase_core.contract_schemas import ContractPdf, MemberContract, MemberContracts
from rebase_core.door_schemas import CrmDocument
from rebase_core.errors import NotFound
from rebase_core.members import MemberService
from rebase_core.pigro_door import NOT_THE_SHAPE, FreelancerDoor, Gone, PigroUnavailable
from rebase_core.regimes import OCCASIONAL, VAT_REGISTERED, as_regime, document_label

FRAMEWORK = "framework"
LETTER = "letter"
_KINDS = {"framework_agreement": FRAMEWORK, "engagement_letter": LETTER}
_REGIMES = {"ordinary": VAT_REGISTERED, "occasional": OCCASIONAL}


def _read(document: CrmDocument) -> MemberContract:
    """One of the CRM's documents as the page reads it. A kind or a type this hub does not
    know is a CRM that answered something other than what was asked: `PigroUnavailable`, never
    a page that silently drops a document."""
    kind = _KINDS.get(document.kind)
    regime = _REGIMES.get(document.letter_type)
    if kind is None or regime is None:
        raise PigroUnavailable(NOT_THE_SHAPE)
    return MemberContract(
        id=document.id,
        kind=kind,
        regime=as_regime(regime),
        label=document_label(kind, regime),
        number=document.number,
        status=document.state,
        customer=document.customer,
        start=document.start,
        end=document.end,
        sent_at=document.sent_at,
        signed_at=document.signed_at,
        # Whatever the CRM sends, a link is shown only while the document waits for it.
        signing_url=document.signing_url if document.state == "sent" else None,
        has_pdf_signed=document.has_signed_copy,
        active=document.active,
        renewal=document.renewal,
        last_notice_day=document.last_notice_day,
        pigro_url=document.pigro_url,
    )


def _current(frameworks: list[MemberContract], regime: str) -> MemberContract | None:
    """The framework agreement of `regime` the page shows on top (REB-687): the active
    one, else the newest that reached the person."""
    mine = [framework for framework in frameworks if framework.regime == regime]
    return next((framework for framework in mine if framework.active), None) or (
        mine[0] if mine else None
    )


class MemberContractService:
    def __init__(self, session: Session, door: FreelancerDoor) -> None:
        self.session = session
        self.door = door

    def for_user(self, user_id: UUID) -> MemberContracts:
        freelancer = MemberService(self.session).require_card(user_id)
        documents = [_read(document) for document in self.door.contracts(freelancer.id).documents]
        frameworks = [document for document in documents if document.kind == FRAMEWORK]
        framework = _current(frameworks, VAT_REGISTERED)
        occasional_framework = _current(frameworks, OCCASIONAL)
        # Every other framework agreement that was signed (REB-392): a notice, or a
        # newer one taking over on top, must not make its own signed copy disappear
        # from the page, only move it down here. Newest first, same as `frameworks`.
        on_top = {shown.id for shown in (framework, occasional_framework) if shown is not None}
        previous_frameworks = [q for q in frameworks if q.has_pdf_signed and q.id not in on_top]
        letters = [document for document in documents if document.kind == LETTER]
        return MemberContracts(
            framework=framework,
            occasional_framework=occasional_framework,
            previous_frameworks=previous_frameworks,
            letters=letters,
        )

    def signed_pdf(self, user_id: UUID, document_id: UUID) -> ContractPdf:
        """The signed copy of one of the caller's own documents. The CRM is asked for the
        caller's list first, so a document that is not theirs is refused here and, since the
        CRM refuses it too, there."""
        freelancer = MemberService(self.session).require_card(user_id)
        mine = {document.id: document for document in self.door.contracts(freelancer.id).documents}
        document = mine.get(document_id)
        if document is None or not document.has_signed_copy:
            raise NotFound("document", document_id)
        try:
            content = self.door.signed_pdf(freelancer.id, document_id)
        except Gone:
            raise NotFound("document", document_id) from None
        return ContractPdf(filename=document.filename or f"{document_id}.pdf", content=content)
