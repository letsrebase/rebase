"""The hub's reads of a freelancer through the root door (REB-1107, collaborators design § 5,
«What the hub reads from the CRM through the door»): availability, the member's contracts and
their signed PDF, the fiscal identifiers the anonymous CV redacts, and the merge check.

Every read names the freelancer by the hub's own id, never by an email or a name, and answers from
the one space `PIGROCRM_LEADS_SPACE` names, which the caller opens as `Actor.rebase()`: the
caller is the hub, holding the engagements token, and no permission of a person or an agent
applies. A freelancer the space holds no organization for is an empty answer and never a missing
page; the one refusal is a document that is not theirs, the same «not found» as one that does not
exist, so the route says nothing about whose it is.

The hub's `member_contracts.py` arranges these documents into its «Contratti» page; what the CRM
adds is what only it can say: the state, the signing link while the document waits, the renewal
and the last day for a notice (the twelve months of article 9.1, `frameworks.py`).
"""

import datetime as dt
from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.collaborators import frameworks, letters
from pigrocrm.core.collaborators.door_schemas import (
    DoorAvailability,
    DoorContracts,
    DoorDocument,
    DoorFiscalIdentifiers,
    DoorMergeCheck,
)
from pigrocrm.core.collaborators.letter_service import LetterService
from pigrocrm.core.collaborators.letters import ENGAGEMENT_LETTER, FRAMEWORK_AGREEMENT
from pigrocrm.core.collaborators.models import Engagement
from pigrocrm.core.collaborators.repository import EngagementRepository
from pigrocrm.core.collaborators.service import FreelancerService
from pigrocrm.core.config import Settings
from pigrocrm.core.db import today_local
from pigrocrm.core.documents.models import Document
from pigrocrm.core.engagements.models import RebaseEngagement
from pigrocrm.core.engagements.service import deal_url
from pigrocrm.core.errors import NotFound
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.tenants.models import Tenant

ENTITY = "document"
LETTER_KINDS = (FRAMEWORK_AGREEMENT, ENGAGEMENT_LETTER)
# The states in which a framework agreement still binds the freelancer: out for signature, or
# signed (a signed one counts only while no notice was served, which is a state of its own here).
LIVE_FRAMEWORK_STATES = ("sent", "pending", "signed")
# The engagements that tie a freelancer to a client right now.
LIVE_CLIENT_STATES = ("awaiting_signature", "active")


class HubDoorService:
    """`session` is the leads space's, scoped as `Actor.rebase()`, and `settings` the space's own
    effective ones (its timezone, its storage). `root` is the installation's settings: a space's
    own `public_url` already carries its slug, so a link into the freelancer's space is built on
    the root's. `storage` is needed only to serve a signed PDF, and `registry` (a session on the
    registry database) only to link a letter to its deal in the freelancer's own space: without
    either, that part answers nothing rather than failing."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        root: Settings,
        *,
        storage: DocumentStorage | None = None,
        registry: Session | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.root = root
        self.storage = storage
        self.registry = registry
        self.repo = EngagementRepository(session)

    # ---- availability -------------------------------------------------------------------------

    def availability(
        self, hub_freelancer_id: UUID, date_from: dt.date | None, date_to: dt.date | None
    ) -> DoorAvailability:
        """The committed days a week, from the freelancer's live engagements (draft, out for
        signature and active), `unknown` («sconosciuta») for a week touched by one with no
        `days_per_week`. A freelancer the CRM holds nothing about is free: nothing is committed
        that this installation knows of."""
        freelancer = self._freelancer(hub_freelancer_id)
        start, end, weeks = FreelancerService(self.session).weeks_of(
            freelancer.id if freelancer is not None else None, date_from, date_to
        )
        return DoorAvailability(
            freelancer_id=freelancer.id if freelancer is not None else None,
            hub_freelancer_id=hub_freelancer_id,
            date_from=start,
            date_to=end,
            weeks=weeks,
        )

    # ---- the member's contracts ---------------------------------------------------------------

    def contracts(self, hub_freelancer_id: UUID) -> DoorContracts:
        freelancer = self._freelancer(hub_freelancer_id)
        if freelancer is None:
            return DoorContracts(documents=[])
        rows = self.session.execute(
            select(Document, Engagement)
            .outerjoin(Engagement, Engagement.id == Document.engagement_id)
            .where(
                Document.customer_id == freelancer.id,
                Document.type.in_(LETTER_KINDS),
                Document.deleted_at.is_(None),
            )
            .order_by(Document.created_at.desc(), Document.id.desc())
        ).all()
        visible = [
            (document, engagement)
            for document, engagement in rows
            if _visible(document, engagement)
        ]
        clients = self.repo.organization_names(
            {
                e.client_organization_id
                for _, e in visible
                if e is not None and e.client_organization_id
            }
        )
        links = self._deal_links([e for _, e in visible if e is not None])
        today = today_local(self.settings)
        return DoorContracts(
            documents=[
                self._read(document, engagement, clients, links, today)
                for document, engagement in visible
            ]
        )

    def signed_pdf(self, hub_freelancer_id: UUID, document_id: UUID) -> tuple[bytes, str]:
        """The signed copy of one of this freelancer's own documents, and the name to save it
        under. Someone else's document, one with no signed copy and one that does not exist are
        the same `NotFound`."""
        freelancer = self._freelancer(hub_freelancer_id)
        document = self.session.get(Document, document_id)
        if (
            freelancer is None
            or document is None
            or document.deleted_at is not None
            or document.type not in LETTER_KINDS
            or document.customer_id != freelancer.id
            or document.signed_version is None
        ):
            raise NotFound(ENTITY, document_id)
        if self.storage is None:
            raise RuntimeError("serving a signed copy needs the space's storage")
        content, _content_type, _name = LetterService(
            self.session, self.storage, self.settings
        ).uploads.download(document.id, document.signed_version)
        engagement = (
            self.repo.get(document.engagement_id) if document.engagement_id is not None else None
        )
        return content, self._filename(document, engagement)

    # ---- the anonymous CV's redaction list ----------------------------------------------------

    def fiscal_identifiers(self, hub_freelancer_id: UUID) -> DoorFiscalIdentifiers:
        """Tax code, VAT number and certified email of the freelancer's organization, archived
        or not: a redaction list that shrank with an archive would let a CV carry them."""
        found = self.session.execute(
            select(Organization).where(Organization.hub_freelancer_id == hub_freelancer_id)
        ).scalar_one_or_none()
        return DoorFiscalIdentifiers(
            tax_code=found.tax_code if found is not None else None,
            vat_number=found.vat_number if found is not None else None,
            certified_email=found.certified_email if found is not None else None,
        )

    # ---- the merge check ----------------------------------------------------------------------

    def merge_check(self, hub_freelancer_id: UUID, other_hub_freelancer_id: UUID) -> DoorMergeCheck:
        """What the CRM holds that stops these two cards becoming one: a live framework
        agreement of the same type on both sides, an engagement with the same client on both
        sides, or two different tax codes. It also says which of the two hub ids the CRM holds an
        organization for."""
        first = self._freelancer(hub_freelancer_id)
        second = self._freelancer(other_hub_freelancer_id)
        if first is None or second is None:
            return DoorMergeCheck(
                framework_regimes=[],
                shared_clients=0,
                tax_code_conflict=False,
                organization=first is not None,
                other_organization=second is not None,
            )
        regimes = self._framework_types(first.id) & self._framework_types(second.id)
        shared = self._live_clients(first.id) & self._live_clients(second.id)
        codes = [code.strip().upper() for code in (first.tax_code, second.tax_code) if code]
        return DoorMergeCheck(
            framework_regimes=sorted(regimes),
            shared_clients=len(shared),
            tax_code_conflict=len(codes) == 2 and codes[0] != codes[1],
            organization=True,
            other_organization=True,
        )

    def _framework_types(self, freelancer_id: UUID) -> set[str]:
        rows = self.session.execute(
            select(Document.letter_type, Document.signature_state, Document.notice_at).where(
                Document.customer_id == freelancer_id,
                Document.type == FRAMEWORK_AGREEMENT,
                Document.deleted_at.is_(None),
                Document.signature_state.in_(LIVE_FRAMEWORK_STATES),
            )
        ).all()
        return {
            letter_type or "ordinary"
            for letter_type, state, notice in rows
            if state != "signed" or notice is None
        }

    def _live_clients(self, freelancer_id: UUID) -> set[UUID]:
        return {
            engagement.client_organization_id
            for engagement in self.repo.of_freelancer(freelancer_id, LIVE_CLIENT_STATES)
            if engagement.client_organization_id is not None
        }

    # ---- helpers ------------------------------------------------------------------------------

    def _freelancer(self, hub_freelancer_id: UUID) -> Organization | None:
        found = self.repo.organization_by_hub_id(hub_freelancer_id)
        return found if found is not None and "freelancer" in found.roles else None

    def _read(
        self,
        document: Document,
        engagement: Engagement | None,
        clients: dict[UUID, str],
        links: dict[UUID, str],
        today: dt.date,
    ) -> DoorDocument:
        signed_on = frameworks.local_day(document.signed_at, self.settings.timezone)
        active = frameworks.is_active(document)
        renewal = (
            frameworks.next_renewal(signed_on, today) if active and signed_on is not None else None
        )
        # The engagement a letter prints: a framework agreement hangs on none.
        letter = engagement if document.type == ENGAGEMENT_LETTER else None
        return DoorDocument(
            id=document.id,
            kind=document.type,
            letter_type=document.letter_type or "ordinary",
            number=engagement.letter_number if engagement is not None else None,
            state=document.signature_state or "generated",  # type: ignore[arg-type]
            customer=(
                clients.get(letter.client_organization_id)
                if letter is not None and letter.client_organization_id is not None
                else None
            ),
            start=letters.italian_date(letter.starts_on) if letter is not None else None,
            end=(
                letters.italian_date(letter.ends_on)
                if letter is not None and letter.ends_on is not None
                else None
            ),
            sent_at=document.sent_at,
            signed_at=document.signed_at,
            # The link is the signer's token: shown only while the document waits for it.
            signing_url=document.signing_url if document.signature_state == "sent" else None,
            has_signed_copy=document.signed_version is not None,
            filename=(
                self._filename(document, engagement)
                if document.signed_version is not None
                else None
            ),
            active=active,
            renewal=renewal,
            last_notice_day=frameworks.last_notice_day(renewal) if renewal is not None else None,
            pigro_url=links.get(letter.id) if letter is not None else None,
        )

    @staticmethod
    def _filename(document: Document, engagement: Engagement | None) -> str:
        return letters.document_filename(
            document.type,
            document.letter_type,
            document.text_version or "1",
            engagement.letter_number if engagement is not None else None,
        )

    def _deal_links(self, engagements: Collection[Engagement]) -> dict[UUID, str]:
        """Each engagement's deal in the freelancer's own space, once the door wrote it (the
        registry row keyed by the engagement's id, or by the hub match it came from). Empty
        without a registry session or a public URL to build the link on."""
        if self.registry is None or not self.root.public_url.strip() or not engagements:
            return {}
        by_key: dict[UUID, UUID] = {}
        for engagement in engagements:
            by_key[engagement.id] = engagement.id
            if engagement.hub_match_id is not None:
                by_key[engagement.hub_match_id] = engagement.id
        rows = self.registry.execute(
            select(RebaseEngagement.match_id, RebaseEngagement.deal_id, Tenant.slug)
            .join(Tenant, Tenant.id == RebaseEngagement.tenant_id)
            .where(RebaseEngagement.match_id.in_(by_key), RebaseEngagement.deal_id.is_not(None))
        ).all()
        return {
            by_key[match_id]: deal_url(self.root, slug, deal_id)
            for match_id, deal_id, slug in rows
            if deal_id is not None
        }


def _visible(document: Document, engagement: Engagement | None) -> bool:
    """A document has reached the person once it has gone out for signature, or was signed by
    hand (a copy recorded without ever leaving through the signing site, which only the person
    could have signed); a letter that has not left, but whose engagement is already
    `awaiting_signature`, is the one waiting for its framework agreement's own signature."""
    if document.sent_at is not None or document.signature_state in ("signed", "notice_served"):
        return True
    return (
        document.type == ENGAGEMENT_LETTER
        and document.signature_state == "pending"
        and engagement is not None
        and engagement.state == "awaiting_signature"
    )
