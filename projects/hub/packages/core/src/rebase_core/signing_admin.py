"""Signing's admin actions: «Aggiorna stato», «Reinvia email», «Annulla» on a framework
agreement or on a match, and «Registra disdetta» (REB-387, REB-407, REB-892).

`refresh` is the recovery action for the event Documenso gave up on; `cancel_document` and
`cancel_match` both cancel the envelope on Documenso before the row, so the link the
freelancer got stops working, and tell them by mail; `record_notice` ends an active
framework agreement. Every write locks in the global order (`match_rows`): the freelancer's
row, the match, the document. `SigningAdmin` is built by `SigningService` and reached as
`signing.admin`.
"""

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from rebase_core.audit import AdminActionService
from rebase_core.contract_schemas import ContractDocumentRead, MatchRead
from rebase_core.documenso import UNREACHABLE, outcome_from_envelope
from rebase_core.errors import DocumensoFailed, InvalidState, SigningUnavailable
from rebase_core.framework import is_active
from rebase_core.mail_contracts import signing_cancelled_mail
from rebase_core.match_rows import ENTITY, FRAMEWORK, lock_freelancer, lock_match, match_freelancer
from rebase_core.models import ContractDocument
from rebase_core.signing_words import (
    CANCEL_REFUSED,
    CANCELLED_BY_REBASE,
    CANCELLED_WITH_MATCH,
)

if TYPE_CHECKING:
    from rebase_core.signing import SigningService
    from rebase_core.signing_recovery import SigningRecovery

_log = logging.getLogger(__name__)
FREELANCER = "freelancer"


class SigningAdmin:
    def __init__(self, service: "SigningService", recovery: "SigningRecovery") -> None:
        self.service = service
        self.recovery = recovery
        self.session = service.session

    def refresh(self, document_id: UUID) -> ContractDocumentRead:
        """«Aggiorna stato»: Documenso's own word on the envelope, applied the way the
        webhook applies it, then whatever a signature still leaves to do (spec § 6, probe
        § 11.3). The net for an event Documenso gave up on, a copy not downloaded yet, a
        letter whose release failed.

        This reads the envelope twice for a completion: once here, to apply a
        rejection or a cancellation the same way «Aggiorna stato» always has, and again
        inside `finish`'s own confirmation (REB-431), which a webhook's background task
        and `sweep` also go through and must not need an envelope handed in from
        somewhere else. An admin's own click, not a per-event webhook, pays that second
        GET; simpler than giving `finish` a second signature for one caller.

        A match the signature turns active is not linked to Pigro here (REB-499): the
        admin is waiting on this click, and a new freelancer's first link opens a space,
        which takes up to 90 seconds. It stays `to_link`, with its sentence, until
        the sweep or «Riprova» links it."""
        document = self.service.document(document_id)
        if document.documenso_id is None:
            raise InvalidState(
                "Questo documento non è mai partito per la firma: non c'è nulla da aggiornare.",
                status=document.status,
            )
        envelope = self.service.require_documenso().get(document.documenso_id)
        self.session.rollback()
        outcome = outcome_from_envelope(envelope)
        if outcome is not None:
            self.service.apply(outcome)
        self.recovery.finish_outcome(document_id, link=False)
        return self.service.read(document_id)

    def resend_mail(self, document_id: UUID, admin_id: UUID) -> ContractDocumentRead:
        """«Reinvia email»: the signing mail again, the same link, for a document that
        still waits for the signature."""
        self.service.require_sender()
        document = self.service.document(document_id)
        if document.status != "sent" or document.signing_url is None:
            raise InvalidState(
                "Si reinvia la mail solo di un documento che aspetta la firma.",
                status=document.status,
            )
        if not self.service.mail_signing_request(document):
            raise SigningUnavailable(
                "La mail non è partita: il provider l'ha rifiutata. Riprova tra qualche minuto."
            )
        self._record(document, "mail_resent", admin_id)
        return self.service.read(document_id)

    def cancel_document(self, document_id: UUID, admin_id: UUID) -> ContractDocumentRead:
        """«Annulla» on a framework agreement not signed yet: its envelope cancelled on
        Documenso first, under the row's lock (only a `PENDING` one can be, probe § 4),
        then the row. A letter is cancelled with its match. The letters that waited for
        this framework agreement keep waiting: «Invia per la firma» on their match writes
        a new one. A freelancer already told about this document by mail is told again,
        once it is gone (REB-407).

        The freelancer's row locks first, the global order: its id is read here with no
        lock of its own (`_document`), only to know which row to take."""
        freelancer_id = self.service.document(document_id).freelancer_id
        lock_freelancer(self.session, freelancer_id)
        document = self.service.lock_document(document_id)
        was_sent = False
        try:
            if document.kind != FRAMEWORK:
                raise InvalidState("Una lettera di incarico si annulla con il suo match.")
            if document.status not in ("generated", "sent"):
                raise InvalidState(
                    f"Si annulla solo un contratto quadro non ancora firmato: questo è "
                    f"{document.status}.",
                    status=document.status,
                )
            was_sent = document.status == "sent"
            if was_sent and document.documenso_id is not None:
                self._cancel_envelope(document.documenso_id, CANCELLED_BY_REBASE)
            document.status = "cancelled"
            document.cancel_reason = CANCELLED_BY_REBASE
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self._record(document, "document_cancelled", admin_id)
        if was_sent:
            self._mail_cancellation(document)
        return self.service.read(document_id)

    def cancel_match(self, match_id: UUID, admin_id: UUID) -> MatchRead:
        """«Annulla» on a match: a draft, still locked in the global order, delegates to
        `MatchService.cancel` (which takes the same locks again, a no-op on a row this
        transaction already holds); a match in signature also cancels its letter's
        envelope when the letter is out for signature, so the link the freelancer got
        stops working, and tells them by mail. The framework agreement is the
        freelancer's, not the match's, and stays."""
        freelancer_id = match_freelancer(self.session, match_id)
        lock_freelancer(self.session, freelancer_id)
        match = lock_match(self.session, match_id)
        if match.status == "draft":
            return self.service.matches.cancel(match_id, admin_id)
        letters: list[ContractDocument] = []
        mailed: list[ContractDocument] = []
        try:
            if match.status != "awaiting_signature":
                raise InvalidState(
                    f"Si annulla solo un match in bozza o in firma: questo è {match.status}.",
                    status=match.status,
                )
            letters = list(
                self.session.scalars(
                    select(ContractDocument)
                    .where(
                        ContractDocument.match_id == match.id,
                        ContractDocument.status.in_(("generated", "pending", "sent")),
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            for letter in letters:
                if letter.status == "sent":
                    if letter.documenso_id is not None:
                        self._cancel_envelope(letter.documenso_id, CANCELLED_BY_REBASE)
                    mailed.append(letter)
                letter.status = "cancelled"
                letter.cancel_reason = CANCELLED_WITH_MATCH
            match.status = "cancelled"
            match.cancelled_at = self.service.now()
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        AdminActionService(self.session).record(
            ENTITY, match_id, "match_cancelled", admin_id, {"documents": [d.id for d in letters]}
        )
        for letter in mailed:
            self._mail_cancellation(letter)
        return self.service.matches.reads.get(match_id)

    def record_notice(self, document_id: UUID, admin_id: UUID) -> ContractDocumentRead:
        """«Registra disdetta»: a notice or a withdrawal on an active framework agreement
        (spec § 1d). From now the freelancer has none active, and their next match writes
        a new one."""
        freelancer_id = self.service.document(document_id).freelancer_id
        lock_freelancer(self.session, freelancer_id)
        document = self.service.lock_document(document_id)
        if not is_active(document):
            self.session.rollback()
            raise InvalidState(
                "Si registra la disdetta solo di un contratto quadro attivo.",
                status=document.status,
            )
        document.notice_at = self.service.now()
        document.status = "notice_served"
        self.session.commit()
        self._record(document, "notice_recorded", admin_id)
        return self.service.read(document_id)

    def _record(self, document: ContractDocument, kind: str, admin_id: UUID) -> None:
        """A framework agreement's action lands on its freelancer's trail, since it
        belongs to no match; a letter's on its match's."""
        if document.match_id is not None:
            entity, entity_id = ENTITY, document.match_id
        else:
            entity, entity_id = FREELANCER, document.freelancer_id
        AdminActionService(self.session).record(
            entity, entity_id, kind, admin_id, {"document": document.id, "kind": document.kind}
        )

    def _mail_cancellation(self, document: ContractDocument) -> None:
        """The freelancer's own notice that a document already out for signature will
        not be signed after all: their link is dead from now on. A refused or missing
        sender is logged, never raised -- the cancel already happened, and nothing here
        may undo it (REB-407)."""
        if self.service.sender is None:
            _log.warning("no mail sender: nobody was told document %s was cancelled", document.id)
            return
        user = self.service.owner(document.freelancer_id)
        mail = signing_cancelled_mail(
            user.email, user.nome, document.kind, document.number, regime=document.regime
        )
        if not self.service.sender.send(mail):
            _log.warning(
                "the cancellation mail of document %s was refused by the provider", document.id
            )

    def _cancel_envelope(self, envelope_id: str, reason: str) -> None:
        """«Annulla», on a document or on a match's letter: Documenso refuses to cancel
        an envelope that is no longer `PENDING` (already completed, rejected or
        cancelled there, probe § 4), typically a webhook the hub missed. That refusal's
        own English sentence is not fit for an admin, so it becomes an Italian one that
        points at the way out; the row this call is inside stays untouched either way.
        Documenso not answering at all is a different failure and keeps its own
        sentence unchanged: it says nothing about the document's state, and translating
        it to `CANCEL_REFUSED` would tell the admin it might already be signed when
        Documenso is merely down (REB-391)."""
        try:
            self.service.require_documenso().cancel(envelope_id, reason)
        except DocumensoFailed as exc:
            if exc.message == UNREACHABLE:
                raise
            raise DocumensoFailed(CANCEL_REFUSED, exc.message) from exc
