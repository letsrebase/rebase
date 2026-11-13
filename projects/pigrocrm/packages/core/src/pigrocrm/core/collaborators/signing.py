"""Signing: an engagement's documents go out through Documenso and come back signed (REB-1019,
collaborators design § 2; the hub's `signing.py`, `signing_admin.py`, REB-387).

`send_engagement` is «send for signature». At most one document leaves at a time: the framework
agreement when the freelancer has none active (the letter waits for it, `pending`), else the
letter. A document is typeset again as it leaves (`LetterService.render_for_signing`): the parties
as they are today, the day it leaves in the issuer's blank, the labels under the signing blanks
left undrawn. Documenso gets the PDF with the freelancer as its one signer, and PigroCRM mails the
link itself, one mail per document. **Only the freelancer signs**: the issuer's blank prints who
issued the document and when, and nothing asks the issuer for a signature. A text that is still a
draft never leaves, whatever the environment. If Documenso refuses or does not answer, the
transaction rolls back and nothing is marked sent; if only the mail fails, the document is `sent`
and the report says so, for «resend the mail».

Without a Documenso (`documenso_url` and `documenso_api_token` unset) nothing here calls one: a
send answers 503 `signing_unavailable` and a person downloads the stored letter, has it signed by
hand and uploads the signed PDF (`upload_signed_copy`). **The hub keeps signing until the cutover**;
this path is off unless its own settings are present.

**The envelope's `externalId` is `<tenant id>:<document id>`** (`documenso.client.external_id`), so
the root webhook finds the space. Every write locks in the order engagement, then document: two
admins, the webhook and a refresh can reach the same engagement's documents at once. The Documenso
calls of `dispatch` run inside the send's transaction, under those locks: a deliberate trade-off
(the send is one admin's click, and a lock released before the call could not roll the document back
when Documenso refuses).

The webhook's side is `apply` and `SigningRecovery.finish` after it (`signing_recovery.py`): `apply`
locks and commits quickly, so Documenso gets its answer long before its ten seconds, and the slow
part (the sealed copy's download, the mails, the letters a framework agreement releases) runs
after the response. A rejection or a cancellation is moved and recorded there and then; a
completion is not, since the webhook's secret travels in clear: `finish` confirms it with Documenso
itself, over the installation's own API token, before the document counts as signed.
"""

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators import frameworks, letters
from pigrocrm.core.collaborators.forecast import EngagementForecastService
from pigrocrm.core.collaborators.letter_service import LetterService
from pigrocrm.core.collaborators.letters import ENGAGEMENT_LETTER, FRAMEWORK_AGREEMENT
from pigrocrm.core.collaborators.models import LIVE_STATES, Engagement
from pigrocrm.core.collaborators.outbox import NoticeOutbox
from pigrocrm.core.collaborators.repository import EngagementRepository
from pigrocrm.core.collaborators.schemas import SendReport, SignatureRead
from pigrocrm.core.collaborators.service import EngagementService
from pigrocrm.core.collaborators.signing_words import (
    CANCEL_REFUSED,
    CANCELLED_BY_US,
    CANCELLED_WITH_ENGAGEMENT,
    HAND_SIGNED,
    NO_DOCUMENSO,
    NO_SENDER,
    NO_SIGNER_EMAIL,
    cancel_reason,
)
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db import today_local
from pigrocrm.core.documenso.client import (
    UNREACHABLE,
    DocumensoClient,
    Outcome,
    client_from_settings,
    external_id,
    fields_from_blanks,
    outcome_from_envelope,
)
from pigrocrm.core.documents.models import Document
from pigrocrm.core.errors import (
    Conflict,
    DocumensoFailed,
    NotFound,
    SigningUnavailable,
    ValidationFailed,
)
from pigrocrm.core.mail import (
    EmailSender,
    document_name,
    sender_from_settings,
    signing_cancelled_mail,
    signing_request_mail,
)
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.organizations.schemas import OrganizationRead
from pigrocrm.core.people.models import Person
from pigrocrm.core.storage.base import DocumentStorage

_log = logging.getLogger(__name__)
ENTITY = "document"
ENGAGEMENT_ENTITY = "engagement"
PDF = "application/pdf"
# What a document must print once it leaves: the issuer's, the freelancer's and the client's data,
# and the fields the service fills itself. What may stay blank is what the signing site fills (the
# signature and the date) and the engagement's optional lines. By the labels the blanks print.
MUST_PRINT_PREFIXES = ("emittente ", "professionista ", "cliente ")
MUST_PRINT = frozenset({"numero", "data contratto quadro", "luogo firma", "firma rebase"})
# The states a document can still be sent from, and the ones a cancellation reaches.
SENDABLE = ("generated", "pending")
LIVE = ("generated", "pending", "sent")


class _FromSettings(Enum):
    """The default of a collaborator not handed in: built from the settings, while `None` handed in
    means that one is off."""

    TOKEN = "from settings"


FROM_SETTINGS: Final = _FromSettings.TOKEN


@dataclass(frozen=True)
class Issuer:
    """Who issued a document, as the mails and the signing site name it."""

    name: str
    email: str | None


def _refuse_blanks(blank: list[str]) -> None:
    """A document that would leave with a party's data missing is refused, naming it."""
    unfilled = [
        label for label in blank if label.startswith(MUST_PRINT_PREFIXES) or label in MUST_PRINT
    ]
    if unfilled:
        raise ValidationFailed(
            "letter",
            "blank",
            f"il documento lascerebbe in bianco {', '.join(unfilled)}: completali prima di "
            "inviarlo",
            expected="i dati dell'azienda emittente, del freelance e del cliente",
        )


class SigningService:
    def __init__(
        self,
        session: Session,
        storage: DocumentStorage,
        settings: Settings | None = None,
        *,
        documenso: DocumensoClient | None | _FromSettings = FROM_SETTINGS,
        sender: EmailSender | None | _FromSettings = FROM_SETTINGS,
        tenant_id: UUID | None = None,
        now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        """Each collaborator is needed only by the steps that use it: a webhook that cancels a
        document needs no renderer, a draft's cancellation no Documenso. `tenant_id` is this
        space's id in the registry, `None` for the root installation: what the envelope's
        `externalId` names so the root webhook finds it again."""
        from pigrocrm.core.collaborators.signing_recovery import SigningRecovery

        self.session = session
        self.storage = storage
        self.settings = settings if settings is not None else get_settings()
        self.documenso = (
            client_from_settings(self.settings)
            if isinstance(documenso, _FromSettings)
            else documenso
        )
        self.sender = (
            sender_from_settings(self.settings) if isinstance(sender, _FromSettings) else sender
        )
        self.tenant_id = tenant_id
        self.now = now
        self.letters = LetterService(session, storage, self.settings)
        self.engagements = EngagementRepository(session)
        self.activities = ActivityService(session)
        self.recovery = SigningRecovery(self)

    # ---- the engagement service that cancels the envelopes too ---------------------------------

    def engagement_service(self) -> EngagementService:
        """An `EngagementService` whose cancellation also cancels the engagement's documents on
        Documenso and tells the freelancer: the one every adapter builds, so no door cancels an
        engagement and leaves its link alive."""
        return EngagementService(self.session, on_cancel=self.cancel_letters_of)

    # ---- «send for signature» -----------------------------------------------------------------

    def send_engagement(self, engagement_id: UUID, actor: Actor) -> SendReport:
        actor.require_admin("send_engagement_for_signature")
        self.require_documenso()
        self.require_sender()
        found = self.engagements.get(engagement_id)
        if found is None:
            raise NotFound(ENGAGEMENT_ENTITY, engagement_id)
        freelancer = self.letters.freelancer(found.freelancer_id)
        regime = found.letter_type
        # The agreement the letter stands on: written now, for a person, when the freelancer has
        # none (an agent reaches only one a person already wrote). Outside the locks below, since
        # writing a letter commits.
        self._ensure_framework(freelancer, regime, actor)
        leaving: Document | None = None
        try:
            engagement = self.engagements.get(engagement_id, lock=True)
            if engagement is None:
                raise NotFound(ENGAGEMENT_ENTITY, engagement_id)
            if engagement.state not in ("draft", "awaiting_signature"):
                raise Conflict(
                    ENGAGEMENT_ENTITY,
                    "si invia per la firma un incarico in bozza o in firma",
                    state=engagement.state,
                )
            letter = self._lock_letter(engagement.id)
            if letter is None:
                raise Conflict(
                    ENGAGEMENT_ENTITY,
                    "questo incarico non ha ancora la lettera: scrivila prima di inviarla",
                )
            if letter.signature_state not in SENDABLE:
                raise Conflict(
                    ENGAGEMENT_ENTITY,
                    "la lettera di questo incarico è già partita, firmata o annullata: non c'è "
                    "nulla da inviare",
                    state=letter.signature_state,
                )
            self.letters.require_party_data(freelancer, regime)
            active = frameworks.active_framework(self.session, freelancer.id, regime)
            leaves = (ENGAGEMENT_LETTER,) if active else (FRAMEWORK_AGREEMENT, ENGAGEMENT_LETTER)
            self.refuse_drafts(leaves, regime)
            if active is not None:
                leaving = letter
                self.dispatch(letter, active, actor)
            else:
                letter.signature_state = "pending"
                leaving = self._framework_to_send(freelancer.id, regime)
                if leaving is not None:
                    self.dispatch(leaving, None, actor)
            previous = engagement.state
            if previous == "draft":
                engagement.state = "awaiting_signature"
                self.session.flush()
                self.activities.record(
                    ENGAGEMENT_ENTITY,
                    engagement.id,
                    "state_changed",
                    actor,
                    {"from": previous, "to": "awaiting_signature"},
                )
            if leaving is not None:
                self.activities.record(
                    ENTITY,
                    leaving.id,
                    "signing_sent",
                    actor,
                    {"kind": leaving.type, "engagement_id": str(engagement.id)},
                )
            if previous == "draft" or (leaving is not None and leaving.type == ENGAGEMENT_LETTER):
                # The hub hears of the move to «awaiting signature» and of the letter leaving.
                NoticeOutbox(self.session).emit(engagement, "sent")
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        mailed = self.mail_signing_request(leaving, actor) if leaving is not None else None
        report = self.engagement_service().get(engagement_id, actor)
        documents = [
            self.read(document.id) for document in self._documents_of(engagement_id, freelancer.id)
        ]
        return SendReport(
            engagement=report,
            sent=leaving.type if leaving is not None else None,
            mail_sent=mailed,
            documents=documents,
        )

    def _ensure_framework(self, freelancer: Organization, regime: str, actor: Actor) -> None:
        if frameworks.active_framework(self.session, freelancer.id, regime) is not None:
            return
        if frameworks.pending_framework(self.session, freelancer.id, regime) is not None:
            return
        try:
            self.letters.create_framework_agreement(freelancer.id, regime, actor)
        except Conflict:
            # Two sends at once both found none: the partial unique index let one write it.
            self.session.rollback()

    def _framework_to_send(self, freelancer_id: UUID, regime: str) -> Document | None:
        """The framework agreement of `regime` a waiting letter needs, locked: none when one is out
        for signature already (the letter leaves after it), else the one generated for a draft."""
        pending = frameworks.pending_framework(self.session, freelancer_id, regime)
        if pending is None:
            raise Conflict(
                ENGAGEMENT_ENTITY,
                "il contratto quadro e' cambiato mentre lo inviavi: ricarica e riprova",
            )
        framework = self.lock_document(pending.id)
        if framework.signature_state == "sent":
            return None
        if framework.signature_state == "generated":
            return framework
        raise Conflict(
            ENGAGEMENT_ENTITY,
            "il contratto quadro e' cambiato mentre lo inviavi: ricarica e riprova",
            state=framework.signature_state,
        )

    def refuse_drafts(self, kinds: tuple[str, ...], regime: str) -> None:
        """A text that is still a draft never leaves, and none of the ones about to leave may be:
        refused before any document is typeset, so nothing is half sent."""
        for kind in kinds:
            template = self.letters.template(kind, regime)
            letters.refuse_draft(template.body_markdown, template.name)

    def dispatch(self, document: Document, framework: Document | None, actor: Actor) -> None:
        """Typeset `document` as it leaves, hand it to Documenso and record the envelope, inside
        the caller's transaction: nothing here commits, so a refusal at any step leaves the
        document as it was. The caller must already hold the document's lock. If `get` or
        `distribute` fails after `create` already left an envelope on Documenso, a best-effort
        delete follows it (`_remove_orphan`), so a retried send does not pile up drafts."""
        documenso = self.require_documenso()
        framework_signed_on = (
            frameworks.local_day(framework.signed_at, self.settings.timezone) if framework else None
        )
        copy = self.letters.render_for_signing(
            document, actor, framework_signed_on=framework_signed_on
        )
        if copy.rendered.draft:
            raise ValidationFailed(
                "letter",
                "status",
                "il testo e' ancora una bozza: non parte per la firma",
                expected="un testo con status: final",
            )
        _refuse_blanks(copy.rendered.blank)
        fields = fields_from_blanks(copy.blanks)
        freelancer = self.letters.freelancer(document.customer_id)  # type: ignore[arg-type]
        contact = OrganizationRead.model_validate(freelancer)
        if not contact.email:
            raise SigningUnavailable(NO_SIGNER_EMAIL)
        issuer = self.issuer_of(document, actor)
        number = self.number_of(document)
        title = document_name(
            document.type, number, document.letter_type or "ordinary", issuer.name
        )
        envelope_id = documenso.create(
            title=title[0].upper() + title[1:],
            external_id=external_id(self.tenant_id, document.id),
            filename=letters.document_filename(
                document.type, document.letter_type, copy.rendered.version, number
            ),
            pdf=copy.rendered.pdf,
            signer_email=contact.email,
            signer_name=freelancer.legal_name,
            fields=fields,
        )
        try:
            envelope = documenso.get(envelope_id)
            signing_url = documenso.distribute(envelope_id)
            self.letters.uploads.add_version_row(
                document,
                copy.rendered.pdf,
                PDF,
                actor,
                markdown_source=copy.rendered.markdown,
                template_id=copy.template.id,
                variables=copy.variables,
            )
        except Exception:
            self._remove_orphan(documenso, envelope_id)
            raise
        document.text_version = copy.rendered.version
        document.envelope_id = envelope_id
        document.envelope_item_id = envelope.item_id
        document.signing_url = signing_url
        document.signature_state = "sent"
        document.sent_at = self.now()
        document.sent_by = actor.id

    def _remove_orphan(self, documenso: DocumensoClient, envelope_id: str) -> None:
        """`get`, `distribute` or storing the version failed after `create` already left an
        envelope on Documenso: a best-effort delete, falling back to a cancel, so a retried send
        does not pile up drafts. Both are best effort: a failure of either is logged with the
        envelope id and never raised over the failure the admin already sees."""
        try:
            documenso.delete(envelope_id)
            return
        except Exception:
            _log.warning(
                "could not delete the orphaned envelope %s, trying to cancel it instead",
                envelope_id,
                exc_info=True,
            )
        try:
            documenso.cancel(envelope_id, "invio non completato: annullo l'envelope orfano")
        except Exception:
            _log.warning(
                "could not cancel the orphaned envelope %s either", envelope_id, exc_info=True
            )

    # ---- mails ---------------------------------------------------------------------------------

    def freelancer_contact(self, freelancer: Organization) -> tuple[str | None, str | None]:
        """The address the freelancer's mails go to and the first name they are greeted by: the
        organization's primary email, and the first name of the person linked to it whose email it
        is (or the only one)."""
        email = OrganizationRead.model_validate(freelancer).email
        people = list(
            self.session.scalars(
                select(Person).where(
                    Person.customer_id == freelancer.id, Person.deleted_at.is_(None)
                )
            )
        )
        named = [person for person in people if person.email and person.email == email]
        chosen = named[0] if named else (people[0] if len(people) == 1 else None)
        return email, chosen.first_name if chosen is not None else None

    def issuer_of(self, document: Document, actor: Actor) -> Issuer:
        entity = self.letters.ctx.legal_entity.get(actor, document.legal_entity_id)
        return Issuer(name=entity.name or entity.legal_name, email=entity.email)

    def mail_signing_request(self, document: Document, actor: Actor) -> bool:
        """After the commit: a refused mail leaves the document `sent`, and says so."""
        if document.signing_url is None or self.sender is None or document.customer_id is None:
            return False
        freelancer = self.letters.freelancer(document.customer_id)
        to, first_name = self.freelancer_contact(freelancer)
        if to is None:
            return False
        mail = signing_request_mail(
            to,
            first_name,
            document.type,
            self.number_of(document),
            document.signing_url,
            regime=document.letter_type or "ordinary",
            issuer=self.issuer_of(document, actor).name,
        )
        if not self.sender.send(mail):
            _log.warning("the signing mail of document %s was refused by the provider", document.id)
            return False
        return True

    def _mail_cancellation(self, document: Document, actor: Actor) -> None:
        """The freelancer's own notice that a document already out for signature will not be signed
        after all: their link is dead from now on. A refused or missing sender is logged, never
        raised: the cancel already happened and nothing here may undo it."""
        if self.sender is None or document.customer_id is None:
            _log.warning("no mail sender: nobody was told document %s was cancelled", document.id)
            return
        freelancer = self.letters.freelancer(document.customer_id)
        to, first_name = self.freelancer_contact(freelancer)
        if to is None:
            return
        mail = signing_cancelled_mail(
            to,
            first_name,
            document.type,
            self.number_of(document),
            regime=document.letter_type or "ordinary",
            issuer=self.issuer_of(document, actor).name,
        )
        if not self.sender.send(mail):
            _log.warning(
                "the cancellation mail of document %s was refused by the provider", document.id
            )

    # ---- the webhook ---------------------------------------------------------------------------

    def apply(self, outcome: Outcome) -> UUID | None:
        """One envelope's outcome, applied once. The document is found by its envelope with no
        lock; then its engagement (when it has one) and the document itself are locked in that
        order, and its state is re-checked once every lock is held, in case anything moved while
        this delivery waited: only a document still `sent` moves, and an unknown envelope (one this
        space never recorded) and a document already signed or cancelled are acknowledged and left
        alone. No call leaves this method.

        A rejection or a cancellation is moved and recorded here: the harm of forging one is small,
        and both only ever cancel. A completion is not moved here: the webhook's own secret
        travels in clear, so a forged `DOCUMENT_COMPLETED` must not by itself turn a document
        `signed`. This only returns the document's id, for `finish` after the commit, which
        confirms the completion with Documenso itself before it counts. Returns `None` when
        nothing needs `finish`."""
        from pigrocrm.core.documenso.client import COMPLETED

        found = self.session.execute(
            select(Document.id, Document.engagement_id).where(
                Document.envelope_id == outcome.envelope_id
            )
        ).first()
        if found is None:
            self.session.rollback()
            return None
        document_id, engagement_id = found
        if engagement_id is not None:
            self.lock_engagement(engagement_id)
        document = self.lock_document(document_id)
        if document.signature_state != "sent":
            self.session.rollback()
            return None
        signed: UUID | None = None
        if outcome.kind == COMPLETED:
            # Left `sent` on purpose: `finish` confirms it with Documenso itself before moving it.
            signed = document.id
        else:
            document.signature_state = "cancelled"
            document.cancel_reason = cancel_reason(outcome)
            self.activities.record(
                ENTITY, document.id, "signing_cancelled", Actor.system(), {"by": "documenso"}
            )
        self.session.commit()
        return signed

    # ---- reads ---------------------------------------------------------------------------------

    def document(self, document_id: UUID) -> Document:
        document = self.session.get(Document, document_id, populate_existing=True)
        if (
            document is None
            or document.deleted_at is not None
            or document.type not in (FRAMEWORK_AGREEMENT, ENGAGEMENT_LETTER)
        ):
            raise NotFound(ENTITY, document_id)
        return document

    def get(self, document_id: UUID, actor: Actor) -> SignatureRead:
        actor.require_admin("read_engagements")
        return self.read(document_id)

    def read(self, document_id: UUID) -> SignatureRead:
        document = self.document(document_id)
        state = document.signature_state or "generated"
        zone = self.settings.timezone
        signed_on = frameworks.local_day(document.signed_at, zone)
        active = frameworks.is_active(document)
        renewal = (
            frameworks.next_renewal(signed_on, today_local(self.settings))
            if active and signed_on is not None
            else None
        )
        return SignatureRead(
            document_id=document.id,
            kind=document.type,
            letter_type=document.letter_type,
            engagement_id=document.engagement_id,
            number=self.number_of(document),
            text_version=document.text_version,
            state=state,  # type: ignore[arg-type]
            sent_at=document.sent_at,
            signed_at=document.signed_at,
            notice_at=document.notice_at,
            cancel_reason=document.cancel_reason,
            has_signed_copy=document.signed_version is not None,
            signed_by_hand=state in ("signed", "notice_served") and document.envelope_id is None,
            active=active,
            renewal=renewal,
            last_notice_day=frameworks.last_notice_day(renewal) if renewal is not None else None,
        )

    # ---- admin: refresh, resend, cancel, notice ------------------------------------------------

    def refresh(self, document_id: UUID, actor: Actor) -> SignatureRead:
        """Documenso's own word on the envelope, applied the way the webhook applies it, then
        whatever a signature still leaves to do. The net for an event Documenso gave up on, a copy
        not downloaded yet, a letter whose release failed. It reads the envelope twice for a
        completion (here, and in `finish`'s own confirmation): an admin's click pays that second
        read, which keeps `finish` free of a second signature."""
        actor.require_admin("refresh_document_signature")
        document = self.document(document_id)
        if document.envelope_id is None:
            raise Conflict(
                ENTITY,
                "questo documento non è mai partito per la firma: non c'è nulla da aggiornare",
                state=document.signature_state,
            )
        envelope = self.require_documenso().get(document.envelope_id)
        self.session.rollback()
        outcome = outcome_from_envelope(envelope)
        if outcome is not None:
            self.apply(outcome)
        self.recovery.finish_outcome(document_id)
        return self.read(document_id)

    def resend_mail(self, document_id: UUID, actor: Actor) -> SignatureRead:
        """The signing mail again, the same link, for a document that still waits for the
        signature."""
        actor.require_admin("resend_signing_mail")
        self.require_sender()
        document = self.document(document_id)
        if document.signature_state != "sent" or document.signing_url is None:
            raise Conflict(
                ENTITY,
                "si reinvia la mail solo di un documento che aspetta la firma",
                state=document.signature_state,
            )
        if not self.mail_signing_request(document, actor):
            raise SigningUnavailable(
                "La mail non è partita: il provider l'ha rifiutata. Riprova tra qualche minuto."
            )
        self.activities.record(ENTITY, document.id, "signing_mail_resent", actor, None)
        self.session.commit()
        return self.read(document_id)

    def cancel_framework_agreement(self, document_id: UUID, actor: Actor) -> SignatureRead:
        """«Cancel» on a framework agreement not signed yet: its envelope cancelled on Documenso
        first, under the row's lock (only a `PENDING` one can be, probe § 4), then the row. A
        letter is cancelled with its engagement. The letters that waited for this agreement keep
        waiting: sending their engagement again writes a new one. A freelancer already told about
        this document by mail is told again, once it is gone."""
        actor.require_admin("cancel_framework_agreement")
        self.document(document_id)
        was_sent = False
        try:
            document = self.lock_document(document_id)
            if document.type != FRAMEWORK_AGREEMENT:
                raise Conflict(ENTITY, "una lettera di incarico si annulla con il suo incarico")
            if document.signature_state not in ("generated", "sent"):
                raise Conflict(
                    ENTITY,
                    "si annulla solo un contratto quadro non ancora firmato",
                    state=document.signature_state,
                )
            was_sent = document.signature_state == "sent"
            if was_sent and document.envelope_id is not None:
                self._cancel_envelope(document.envelope_id, CANCELLED_BY_US)
            document.signature_state = "cancelled"
            document.cancel_reason = CANCELLED_BY_US
            self.activities.record(ENTITY, document.id, "signing_cancelled", actor, {"by": "us"})
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        if was_sent:
            self._mail_cancellation(document, actor)
        return self.read(document_id)

    def record_framework_notice(self, document_id: UUID, actor: Actor) -> SignatureRead:
        """A notice or a withdrawal on an active framework agreement. From now the freelancer has
        none active, and their next engagement writes a new one."""
        actor.require_admin("record_framework_notice")
        found = self.document(document_id)
        try:
            # The engagements the notice tells the hub about are locked before the document, the
            # order every other writer keeps (engagement, then document).
            bound = [
                self.lock_engagement(engagement_id)
                for engagement_id in self._bound_engagements(found)
            ]
            document = self.lock_document(document_id)
            if not frameworks.is_active(document):
                raise Conflict(
                    ENTITY,
                    "si registra la disdetta solo di un contratto quadro attivo",
                    state=document.signature_state,
                )
            document.notice_at = self.now()
            document.signature_state = "notice_served"
            self.activities.record(ENTITY, document.id, "notice_recorded", actor, None)
            outbox = NoticeOutbox(self.session)
            for engagement in bound:
                outbox.emit(engagement, "notice_served")
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return self.read(document_id)

    def _bound_engagements(self, framework: Document) -> list[UUID]:
        """The ids, in order, of the live engagements of the framework agreement's freelancer
        whose letter is of its regime: the ones a notice on the agreement concerns."""
        if framework.customer_id is None:
            return []
        return list(
            self.session.scalars(
                select(Engagement.id)
                .where(
                    Engagement.freelancer_id == framework.customer_id,
                    Engagement.state.in_(LIVE_STATES),
                    Engagement.letter_type == framework.letter_type,
                )
                .order_by(Engagement.id)
            )
        )

    def cancel_letters_of(self, engagement: Engagement, actor: Actor) -> Callable[[], None]:
        """The hook of `EngagementService`'s cancellation: the engagement's letter cancelled with
        it, its envelope cancelled on Documenso first when it is out for signature so the link the
        freelancer got stops working. Runs inside the engagement's own transaction, under its lock
        (so engagement, then document); a refusal raises and leaves everything as it was. The
        framework agreement is the freelancer's, not the engagement's, and stays. Answers what is
        left to do after the commit: the freelancer's mail for each document that was out."""
        letters = list(
            self.session.scalars(
                select(Document)
                .where(
                    Document.engagement_id == engagement.id,
                    Document.type == ENGAGEMENT_LETTER,
                    Document.deleted_at.is_(None),
                    Document.signature_state.in_(LIVE),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        mailed: list[Document] = []
        for letter in letters:
            if letter.signature_state == "sent":
                if letter.envelope_id is not None:
                    self._cancel_envelope(letter.envelope_id, CANCELLED_WITH_ENGAGEMENT)
                mailed.append(letter)
            letter.signature_state = "cancelled"
            letter.cancel_reason = CANCELLED_WITH_ENGAGEMENT
            self.activities.record(
                ENTITY, letter.id, "signing_cancelled", actor, {"by": "engagement"}
            )

        def after() -> None:
            for letter in mailed:
                self._mail_cancellation(letter, actor)

        return after

    def upload_signed_copy(
        self,
        document_id: UUID,
        data: bytes,
        content_type: str,
        signed_on: dt.date | None,
        actor: Actor,
    ) -> SignatureRead:
        """When there is no Documenso: a letter downloaded, signed by hand, its signed PDF uploaded.
        The document is `signed` on `signed_on` (today by default) and the PDF is its next version.
        A person's act, since it says a signature happened that no signing site witnessed. Only a
        document that never went out through Documenso can be recorded this way: one whose link is
        live is signed there or cancelled first. A letter's engagement becomes active, as a
        signature from Documenso makes it; a framework agreement signed this way is not followed
        by any automatic release of waiting letters, which wait for their own upload."""
        actor.require_admin("upload_signed_document")
        self.document(document_id)
        zone = self.settings.timezone
        try:
            # Engagement first, then the document: the order every other writer keeps.
            if (engagement_id := self.document(document_id).engagement_id) is not None:
                self.lock_engagement(engagement_id)
            document = self.lock_document(document_id)
            if document.signature_state not in SENDABLE or document.envelope_id is not None:
                raise Conflict(
                    ENTITY,
                    "si carica il PDF firmato di un documento non ancora partito per la firma",
                    state=document.signature_state,
                )
            if content_type != PDF:
                raise ValidationFailed(
                    ENTITY, "content_type", "il documento firmato è un PDF", expected=PDF
                )
            day = signed_on or today_local(self.settings)
            version = self.letters.uploads.add_version_row(
                document, data, PDF, actor, markdown_source=None
            )
            document.signed_version = version.number
            document.signature_state = "signed"
            # Noon in the space's own zone: the same calendar day whichever side reads it.
            document.signed_at = dt.datetime.combine(day, dt.time(12, 0), tzinfo=ZoneInfo(zone))
            document.sent_by = actor.id
            document.cancel_reason = None
            self.activities.record(
                ENTITY, document.id, "signed_copy_uploaded", actor, {"note": HAND_SIGNED}
            )
            if document.engagement_id is not None:
                self._activate(document.engagement_id, actor)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return self.read(document_id)

    def _activate(self, engagement_id: UUID, actor: Actor) -> None:
        """A signed letter makes its engagement active, stepping through «awaiting signature» when
        the letter was never sent. Inside the caller's transaction."""
        engagement = self.engagements.get(engagement_id, lock=True)
        if engagement is None or engagement.state not in ("draft", "awaiting_signature"):
            return
        steps = ("awaiting_signature", "active") if engagement.state == "draft" else ("active",)
        for target in steps:
            previous = engagement.state
            engagement.state = target
            self.session.flush()
            self.activities.record(
                ENGAGEMENT_ENTITY,
                engagement.id,
                "state_changed",
                actor,
                {"from": previous, "to": target},
            )
        NoticeOutbox(self.session).emit(engagement, "signed")
        EngagementForecastService(self.session).sync(engagement)

    def _cancel_envelope(self, envelope_id: str, reason: str) -> None:
        """Documenso refuses to cancel an envelope that is no longer `PENDING` (already completed,
        rejected or cancelled there, probe § 4), typically a webhook this space missed. That
        refusal's own English sentence is not fit for an admin, so it becomes an Italian one that
        points at the way out; the row this call is inside stays untouched either way. Documenso
        not answering at all is a different failure and keeps its own sentence: it says nothing
        about the document's state."""
        try:
            self.require_documenso().cancel(envelope_id, reason)
        except DocumensoFailed as exc:
            if exc.message == UNREACHABLE:
                raise
            raise DocumensoFailed(CANCEL_REFUSED, exc.detail) from exc

    # ---- plumbing ------------------------------------------------------------------------------

    def require_documenso(self) -> DocumensoClient:
        if self.documenso is None:
            raise SigningUnavailable(NO_DOCUMENSO)
        return self.documenso

    def require_sender(self) -> EmailSender:
        if self.sender is None:
            raise SigningUnavailable(NO_SENDER)
        return self.sender

    def lock_engagement(self, engagement_id: UUID) -> Engagement:
        engagement = self.engagements.get(engagement_id, lock=True)
        if engagement is None:
            raise NotFound(ENGAGEMENT_ENTITY, engagement_id)
        return engagement

    def lock_document(self, document_id: UUID) -> Document:
        """The document, row-locked until this transaction ends, and read again from the database
        rather than from the session's memory: another transaction may have moved it while this
        one waited. The caller holds its engagement's lock first."""
        document = self.session.scalars(
            select(Document)
            .where(Document.id == document_id, Document.deleted_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if document is None:
            raise NotFound(ENTITY, document_id)
        return document

    def _lock_letter(self, engagement_id: UUID) -> Document | None:
        return self.session.scalars(
            select(Document)
            .where(
                Document.engagement_id == engagement_id,
                Document.type == ENGAGEMENT_LETTER,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.created_at.desc(), Document.id.desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()

    def _documents_of(self, engagement_id: UUID, freelancer_id: UUID) -> list[Document]:
        """The engagement's letter and the freelancer's framework agreements: what a send says."""
        return list(
            self.session.scalars(
                select(Document)
                .where(
                    Document.customer_id == freelancer_id,
                    Document.deleted_at.is_(None),
                    (Document.engagement_id == engagement_id)
                    | (Document.type == FRAMEWORK_AGREEMENT),
                )
                .order_by(Document.created_at, Document.id)
            )
        )

    def number_of(self, document: Document) -> str | None:
        if document.engagement_id is None:
            return None
        engagement = self.engagements.get(document.engagement_id)
        return engagement.letter_number if engagement is not None else None
