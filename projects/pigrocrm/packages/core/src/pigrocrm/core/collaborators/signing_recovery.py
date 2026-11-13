"""Signing's recovery: what a signature leaves to do once it is committed, and the sweep that redoes
what a lost webhook or a restart left behind (REB-1019; the hub's `signing_recovery.py`, REB-391,
REB-431, REB-892).

`finish` runs after `SigningService.apply`'s commit, in the webhook's background task or under a
refresh: it first confirms a still-`sent` document with Documenso itself, over the installation's
own API token, and only moves it to `signed` (or, for a rejection or a cancellation the webhook
never delivered, `cancelled`) once Documenso says so (a forged completion then needs the token
too); then the sealed copy is downloaded and stored as the document's next version, the two mails
(the freelancer's and the issuer's) are sent, each recorded on its own once accepted
(`signed_copy_to_freelancer_at`, `signed_copy_to_issuer_at`), then, for a framework agreement, the
letters that waited for it are typeset with its signature date and sent. Each step checks under the
document's own row lock whether it is still to do, so running `finish` twice (two overlapping
deliveries' own background tasks among them) does everything once; a lost task or a restart between
steps leaves whichever of the two mail columns is unset, which the next `finish` reads as still to
do for that recipient alone.

`sweep` is the recovery the daily `pigrocrm automations` pass runs for every space, after its rules
(and the admin's «refresh» is the same thing for one document): `finish` again for every document
a webhook never reached. **It always recovers signatures.** The hub's round returned early when it
had no engagement service; this one has no such condition, and without a Documenso it only reports
what it found waiting.

The lock order is the global one: the engagement, then the document. Everything here acts as the
system (`Actor.system()`): nobody is waiting on it.
"""

import logging
from typing import TYPE_CHECKING, NamedTuple
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import aliased

from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators import frameworks
from pigrocrm.core.collaborators.forecast import EngagementForecastService
from pigrocrm.core.collaborators.letters import ENGAGEMENT_LETTER, FRAMEWORK_AGREEMENT
from pigrocrm.core.collaborators.models import Engagement
from pigrocrm.core.collaborators.outbox import NoticeOutbox
from pigrocrm.core.collaborators.signing_words import cancel_reason
from pigrocrm.core.documenso.client import COMPLETED, outcome_from_envelope
from pigrocrm.core.documents.models import Document
from pigrocrm.core.errors import (
    Conflict,
    DocumensoFailed,
    NotFound,
    SigningUnavailable,
    ValidationFailed,
)
from pigrocrm.core.mail import Attachment, signed_copy_mail

if TYPE_CHECKING:
    from pigrocrm.core.collaborators.signing import SigningService

_log = logging.getLogger(__name__)
ENTITY = "document"
PDF = "application/pdf"


class SweepResult(NamedTuple):
    """`SigningRecovery.sweep`'s own count: `touched` is how many documents actually moved;
    `unconfirmed` is how many stayed `sent` because Documenso itself could not be asked (a refusal:
    an expired, revoked or wrong token, an envelope answering 404; or an unreachable instance),
    never a document merely not completed yet, and never a routine no-Documenso installation."""

    touched: int
    unconfirmed: int


class _FinishOutcome(NamedTuple):
    """What one `finish` call did to one document, before `sweep` sums it up."""

    moved: bool
    unconfirmed: bool


class SigningRecovery:
    def __init__(self, service: "SigningService") -> None:
        self.service = service
        self.session = service.session
        self.system = Actor.system()

    def finish(self, document_id: UUID) -> bool:
        """What a signature leaves to do once it is committed, each step idempotent: the outcome
        confirmed with Documenso itself and the document moved to `signed`, or to `cancelled` for a
        rejection or a cancellation the webhook never delivered; the sealed copy downloaded and
        stored; the signed-copy mails sent, once; for an active framework agreement, the letters
        that waited for it released. A step that fails is logged and left for the next call (the
        next refresh, or `sweep`); the others still run. Returns whether this call actually moved
        anything, so `sweep` counts only documents it truly advanced, not one still waiting on
        Documenso or a mail provider that keeps refusing."""
        return self.finish_outcome(document_id).moved

    def finish_outcome(self, document_id: UUID) -> _FinishOutcome:
        """As `finish`, but also says whether the confirmation step itself could not reach
        Documenso for an answer: a refusal or an unreachable instance, not the routine «not
        completed yet» or «no Documenso configured here». `sweep` reads this so a real failure is
        visible instead of silently retried forever."""
        confirmed = self._confirm_completion(document_id)
        moved = confirmed is True
        try:
            moved = self._store_signed_copy(document_id) or moved
        except (DocumensoFailed, SigningUnavailable, NotFound):
            self.session.rollback()
            _log.warning(
                "the signed copy of document %s is not stored yet", document_id, exc_info=True
            )
        try:
            moved = self._mail_signed_copy_once(document_id) or moved
        except NotFound:
            self.session.rollback()
        document = self.session.get(Document, document_id, populate_existing=True)
        if document is not None and frameworks.is_active(document):
            moved = self._release_letters(document) or moved
        return _FinishOutcome(moved=moved, unconfirmed=confirmed is None)

    def _confirm_completion(self, document_id: UUID) -> bool | None:
        """Before a document counts as signed: Documenso's own word on the envelope, read with no
        row lock held (a network call), so a forged webhook alone can no longer move a document to
        `signed`: a forged event then needs the installation's own token too. Nothing to confirm
        for a document not `sent`, or with no envelope. The document's engagement (when it has one)
        and the document itself are then locked in that order and the document re-read: only one
        still `sent` moves. A `COMPLETED` envelope signs it, with the signer's own date, turning
        its engagement active; a `REJECTED` or `CANCELLED` one cancels it. Still `PENDING` (or
        `DRAFT`) leaves the row `sent`, tried again next time. Documenso not configured here at all
        is a routine state and is logged and left for the next call, returning `False`; Documenso
        itself refusing or not answering is also logged and left, but returns `None` rather than
        `False`, so `sweep` can tell a real failure apart from a benign one and count it. Returns
        `True` only when this call actually moved the document."""
        document = self.service.document(document_id)
        if document.signature_state != "sent" or document.envelope_id is None:
            return False
        envelope_id = document.envelope_id
        engagement_id = document.engagement_id
        try:
            envelope = self.service.require_documenso().get(envelope_id)
        except SigningUnavailable:
            _log.info("no Documenso configured here: document %s stays sent", document_id)
            self.session.rollback()
            return False
        except DocumensoFailed:
            _log.warning(
                "could not confirm envelope %s of document %s with Documenso",
                envelope_id,
                document_id,
                exc_info=True,
            )
            self.session.rollback()
            return None
        outcome = outcome_from_envelope(envelope)
        engagement = self.service.lock_engagement(engagement_id) if engagement_id else None
        document = self.service.lock_document(document_id)
        if document.signature_state != "sent":
            self.session.rollback()
            return False
        if outcome is None:
            self.session.rollback()
            _log.info("envelope %s of document %s is not completed yet", envelope_id, document_id)
            return False
        if outcome.kind != COMPLETED:
            document.signature_state = "cancelled"
            document.cancel_reason = cancel_reason(outcome)
            self.service.activities.record(
                ENTITY, document.id, "signing_cancelled", self.system, {"by": "documenso"}
            )
            self.session.commit()
            return True
        document.signature_state = "signed"
        # The signer's own date; the moment this space heard of it only if Documenso said nothing,
        # which a completed envelope never does.
        document.signed_at = outcome.signed_at or self.service.now()
        self.service.activities.record(ENTITY, document.id, "signing_signed", self.system, None)
        if engagement is not None and engagement.state == "awaiting_signature":
            engagement.state = "active"
            self.session.flush()
            NoticeOutbox(self.session).emit(engagement, "signed")
            EngagementForecastService(self.session).sync(engagement)
            self.service.activities.record(
                "engagement",
                engagement.id,
                "state_changed",
                self.system,
                {"from": "awaiting_signature", "to": "active"},
            )
        self.session.commit()
        return True

    def sweep(self) -> SweepResult:
        """Redoes what a lost background task or a restart left behind, for every document `finish`
        still has something to do for. Each document runs in its own transaction, so the steps stay
        exactly as idempotent as the webhook's own recovery; a failure is logged and the next
        document is still tried, counted in neither number. `touched` is how many `finish`
        actually moved, not how many it merely looked at: a document still waiting on Documenso,
        tried again next time, does not count. `unconfirmed` is how many stayed `sent` because
        Documenso itself refused the confirmation or could not be reached: visible, so a stuck
        token or a wrong URL is not read as «nothing to do» forever."""
        touched = 0
        unconfirmed = 0
        for document_id in self.to_finish():
            try:
                outcome = self.finish_outcome(document_id)
            except Exception:
                self.session.rollback()
                _log.warning(
                    "signing sweep: document %s could not be finished", document_id, exc_info=True
                )
                continue
            if outcome.moved:
                touched += 1
            if outcome.unconfirmed:
                unconfirmed += 1
        return SweepResult(touched=touched, unconfirmed=unconfirmed)

    def to_finish(self) -> list[UUID]:
        """Every document a sweep must run `finish` on: a document `sent` with an envelope, whose
        outcome (a completion, a rejection or a cancellation) a lost webhook delivery never
        confirmed; a signature whose sealed copy is missing or not yet mailed to either recipient;
        and every active framework agreement that still has a letter of its own regime `pending`
        on an engagement `awaiting_signature` (a release `finish` itself missed, or never ran for).
        `finish` is idempotent either way, so the three sets are simply run together. The first
        costs one GET to Documenso per waiting document per sweep."""
        live = Document.deleted_at.is_(None)
        awaiting_confirmation = select(Document.id).where(
            live, Document.signature_state == "sent", Document.envelope_id.is_not(None)
        )
        unfinished_signatures = select(Document.id).where(
            live,
            Document.signature_state.in_(("signed", "notice_served")),
            Document.envelope_id.is_not(None),
            or_(
                Document.signed_version.is_(None),
                Document.signed_copy_to_freelancer_at.is_(None),
                Document.signed_copy_to_issuer_at.is_(None),
            ),
        )
        waiting_letter = aliased(Document)
        frameworks_with_waiting_letters = select(Document.id).where(
            live,
            Document.type == FRAMEWORK_AGREEMENT,
            Document.signature_state == "signed",
            Document.notice_at.is_(None),
            exists(
                select(1)
                .select_from(waiting_letter)
                .join(Engagement, Engagement.id == waiting_letter.engagement_id)
                .where(
                    waiting_letter.type == ENGAGEMENT_LETTER,
                    waiting_letter.signature_state == "pending",
                    waiting_letter.deleted_at.is_(None),
                    waiting_letter.customer_id == Document.customer_id,
                    waiting_letter.letter_type == Document.letter_type,
                    Engagement.state == "awaiting_signature",
                )
            ),
        )
        ids = set(self.session.scalars(awaiting_confirmation).all())
        ids.update(self.session.scalars(unfinished_signatures).all())
        ids.update(self.session.scalars(frameworks_with_waiting_letters).all())
        return sorted(ids, key=str)

    def _store_signed_copy(self, document_id: UUID) -> bool:
        """The download happens under the row's lock, so two callers download once: the second
        waits, then finds the copy stored. The sealed PDF is the document's next version
        (`signed_version`). Whether this call stored it."""
        document = self.service.lock_document(document_id)
        if (
            document.signature_state not in ("signed", "notice_served")
            or document.signed_version is not None
            or document.envelope_item_id is None
        ):
            self.session.rollback()
            return False
        pdf = self.service.require_documenso().download_signed(document.envelope_item_id)
        version = self.service.letters.uploads.add_version_row(
            document, pdf, PDF, self.system, markdown_source=None
        )
        document.signed_version = version.number
        self.session.commit()
        return True

    def _mail_signed_copy_once(self, document_id: UUID) -> bool:
        """The signed-copy mails, the freelancer's and the issuer's, each sent at most once and
        recorded on its own: one recipient's provider refusing forever must not keep the other
        from ever getting theirs. Whether either mail was accepted this call."""
        freelancer_mailed = self._mail_signed_copy_to(document_id, to_freelancer=True)
        issuer_mailed = self._mail_signed_copy_to(document_id, to_freelancer=False)
        return freelancer_mailed or issuer_mailed

    def _mail_signed_copy_to(self, document_id: UUID, *, to_freelancer: bool) -> bool:
        """One recipient's own signed-copy mail, locked under the document's own row, same as
        `_store_signed_copy`, so two concurrent `finish` calls mail this recipient once. Nothing to
        mail yet (no stored copy), no address, or this recipient already mailed leaves the row
        untouched; their own column is set, and committed, only once their mail is accepted, still
        under this lock, so a refusal is retried on the next `finish` without repeating the other
        recipient's mail."""
        service = self.service
        document = service.lock_document(document_id)
        stamped = (
            document.signed_copy_to_freelancer_at
            if to_freelancer
            else document.signed_copy_to_issuer_at
        )
        if document.signed_version is None or stamped is not None or document.customer_id is None:
            self.session.rollback()
            return False
        if service.sender is None:
            self.session.rollback()
            _log.warning(
                "no mail sender: the signed copy of document %s was not mailed", document.id
            )
            return False
        freelancer = service.letters.freelancer(document.customer_id)
        freelancer_email, first_name = service.freelancer_contact(freelancer)
        issuer = service.issuer_of(document, self.system)
        to = freelancer_email if to_freelancer else issuer.email
        if to is None:
            self.session.rollback()
            return False
        content, _content_type, filename = service.letters.uploads.download(
            document.id, document.signed_version
        )
        mail = signed_copy_mail(
            to,
            kind=document.type,
            number=service.number_of(document),
            attachment=Attachment(filename=filename, content=content),
            first_name=first_name,
            freelancer_name=freelancer.legal_name,
            for_issuer=not to_freelancer,
            regime=document.letter_type or "ordinary",
            issuer=issuer.name,
        )
        if not service.sender.send(mail):
            _log.warning(
                "the signed copy of document %s was refused by the provider (%s)",
                document.id,
                "freelancer" if to_freelancer else "issuer",
            )
            self.session.rollback()
            return False
        if to_freelancer:
            document.signed_copy_to_freelancer_at = service.now()
        else:
            document.signed_copy_to_issuer_at = service.now()
        self.session.commit()
        return True

    def _release_letters(self, framework: Document) -> bool:
        """The letters of this framework agreement's regime that waited for it, each in a
        transaction of its own and mailed after its commit: a Documenso refusal leaves that letter
        waiting for the next `finish` and lets the others go. Only engagements an admin sent
        (`awaiting_signature`); a draft's letter leaves when its engagement is sent. Without a
        Documenso or a mail sender nothing leaves (nobody could be told about it), and the letters
        stay waiting. Whether at least one letter was released this call."""
        service = self.service
        if service.documenso is None or service.sender is None:
            _log.info(
                "no Documenso or no mail sender: the letters waiting on framework agreement %s "
                "stay waiting",
                framework.id,
            )
            return False
        waiting = list(
            self.session.execute(
                select(Document.id, Document.engagement_id)
                .join(Engagement, Engagement.id == Document.engagement_id)
                .where(
                    Document.type == ENGAGEMENT_LETTER,
                    Document.customer_id == framework.customer_id,
                    Document.letter_type == framework.letter_type,
                    Document.signature_state == "pending",
                    Document.deleted_at.is_(None),
                    Engagement.state == "awaiting_signature",
                )
                .order_by(Document.created_at, Document.id)
            )
        )
        framework_id = framework.id
        self.session.rollback()
        released = False
        for letter_id, engagement_id in waiting:
            if engagement_id is None:
                continue
            try:
                letter = self._send_waiting(letter_id, engagement_id, framework_id)
            except (
                Conflict,
                DocumensoFailed,
                NotFound,
                SigningUnavailable,
                ValidationFailed,
            ):
                self.session.rollback()
                _log.warning(
                    "letter %s keeps waiting: its release failed", letter_id, exc_info=True
                )
                continue
            if letter is not None:
                released = True
                service.mail_signing_request(letter, self.system)
        return released

    def _send_waiting(
        self, letter_id: UUID, engagement_id: UUID, framework_id: UUID
    ) -> Document | None:
        """The same lock order as `apply`: the letter's engagement, then the letter itself; the
        framework agreement is only read, never locked, since nothing here writes it."""
        service = self.service
        engagement = service.lock_engagement(engagement_id)
        letter = service.lock_document(letter_id)
        framework = self.session.get(Document, framework_id, populate_existing=True)
        if (
            letter.signature_state != "pending"
            or engagement.state != "awaiting_signature"
            or framework is None
            or not frameworks.is_active(framework)
            or framework.letter_type != letter.letter_type
            or letter.customer_id is None
        ):
            self.session.rollback()
            return None
        regime = letter.letter_type or "ordinary"
        service.refuse_drafts((ENGAGEMENT_LETTER,), regime)
        # The same rule the send applies: a tax code or VAT number removed while the letter waited
        # refuses it in words, not as a blank field.
        service.letters.require_party_data(service.letters.freelancer(letter.customer_id), regime)
        service.dispatch(letter, framework, self.system)
        # Attributed to whoever sent the framework agreement that just freed it.
        letter.sent_by = framework.sent_by
        service.activities.record(
            ENTITY, letter.id, "signing_sent", self.system, {"kind": letter.type, "released": True}
        )
        NoticeOutbox(self.session).emit(engagement, "sent")
        self.session.commit()
        return letter
