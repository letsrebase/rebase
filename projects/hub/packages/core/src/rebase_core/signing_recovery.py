"""Signing's recovery: what a signature leaves to do once it is committed, and the sweep
that redoes what a lost background task or a restart left behind (REB-391, REB-431,
REB-499, REB-892).

`finish` runs after `SigningService.apply`'s commit, in the webhook's background task or
under «Aggiorna stato»: it first confirms a still-`sent` document with Documenso itself,
over the hub's own API token, and only moves it to `signed` (or, for a rejection or a
cancellation the webhook never delivered, `cancelled`) once Documenso says so (REB-431, a
forged completion then needs the token too); then the sealed copy is downloaded and stored,
the two mails (the freelancer's and rebase's) sent, each recorded on its own once accepted
(`signed_copy_to_freelancer_at`, `signed_copy_to_rebase_at`), then, for a framework
agreement, the letters that waited for it typeset with its signature date and sent. Each
step checks under the document's own row lock whether it is still to do, so running `finish`
twice -- two overlapping deliveries' own background tasks among them -- does everything
once; a lost background task or a restart between steps leaves whichever of the two mail
columns is unset, which the next `finish` reads as still to do for that recipient alone
(REB-391).

`sweep` is the recovery `rebase contracts-sweep` runs, every ten minutes on production and
the preview alike, from the `sweep` service in `docker-compose.yml` (REB-393): `finish`
again, for every document a webhook or an admin's «Aggiorna stato» never reached.

A signature that turns a match `active` also marks its link to Pigro due (`pigro_status`
`to_link`, REB-499), and the webhook's `finish` then asks `EngagementService.link` for
it, with no row lock held, after the signature's own steps: a CRM that takes its 90 seconds
to open a space delays no signed copy. Nothing a person waits on runs that provisioning:
«Aggiorna stato» (`SigningAdmin.refresh`) applies the signature and leaves the link to the
sweep, whose round after the documents (`link_pending`) retries every match still waiting or
in error, or to the admin's «Riprova». A link that fails is left for the sweep too. A
service built without the engagement service links nothing, as one without Documenso signs
nothing.

The lock order is the global one (`match_rows`): the freelancer's row, the match, the
document. `SigningRecovery` is built by `SigningService` and reached as
`signing.recovery`; it reads the service's collaborators and plumbing.
"""

import logging
from typing import TYPE_CHECKING, NamedTuple
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import aliased

from rebase_core.audit import AdminActionService
from rebase_core.contracts.fields import ContractFailed
from rebase_core.documenso import COMPLETED, outcome_from_envelope
from rebase_core.errors import (
    DocumensoFailed,
    InvalidState,
    NotFound,
    SigningUnavailable,
    ValidationFailed,
)
from rebase_core.framework import is_active
from rebase_core.mail import Attachment
from rebase_core.mail_contracts import signed_copy_mail
from rebase_core.match_rows import (
    ENTITY,
    FRAMEWORK,
    LETTER,
    fiscal_row,
    lock_freelancer,
    lock_match,
    require_regime_fits,
)
from rebase_core.match_words import TO_LINK
from rebase_core.models import ContractDocument, Match
from rebase_core.regimes import text_name
from rebase_core.signing_words import DRAFT_REFUSED, cancel_reason

if TYPE_CHECKING:
    from rebase_core.signing import SigningService

_log = logging.getLogger(__name__)


class SweepResult(NamedTuple):
    """`SigningRecovery.sweep`'s own count (REB-431): `touched` is REB-433's, how many
    documents actually moved; `unconfirmed` is how many stayed `sent` because
    Documenso itself could not be asked -- a refusal (an expired, revoked or wrong
    token, an envelope answering 404) or an unreachable instance, never a document
    merely not completed yet, and never a routine no-Documenso environment. `linked` and
    `link_failed` are the round after the documents (REB-499,
    `EngagementService.link_pending`): how many active matches it took to `linked`,
    and how many it tried and left otherwise; both zero without the engagement service
    or its token."""

    touched: int
    unconfirmed: int
    linked: int = 0
    link_failed: int = 0


class _FinishOutcome(NamedTuple):
    """What one `finish` call did to one document, before `sweep` sums it into a
    `SweepResult` across every document it visits."""

    moved: bool
    unconfirmed: bool


class SigningRecovery:
    def __init__(self, service: "SigningService") -> None:
        self.service = service
        self.session = service.session

    def finish(self, document_id: UUID) -> bool:
        """What a signature leaves to do once it is committed, each step idempotent: the
        outcome confirmed with Documenso itself and the document moved to `signed`, or
        to `cancelled` for a rejection or a cancellation the webhook never delivered
        (REB-431); the sealed copy downloaded and stored; the signed-copy mails sent,
        once; for an active framework agreement, the letters that waited for it
        released; for a match the signature turned active, its link to Pigro
        (REB-499). A step that fails is logged and left for the next call (the next
        «Aggiorna stato», or `sweep`); the others still run. Returns whether this call
        actually moved anything -- the confirmation, a stored copy, an accepted mail, a
        released letter -- so `sweep` counts only documents it truly advanced (REB-433),
        not one still waiting on Documenso or a mail provider that keeps refusing. See
        `_finish_outcome` for whether Documenso itself could not be asked at all."""
        return self.finish_outcome(document_id).moved

    def finish_outcome(self, document_id: UUID, *, link: bool = True) -> _FinishOutcome:
        """As `finish`, but also says whether the confirmation step itself could not
        reach Documenso for an answer (REB-431): a refusal or an unreachable instance,
        not the routine "not completed yet" or "no Documenso configured here". `sweep`
        reads this so a real failure is visible instead of silently retried every ten
        minutes forever; `finish` itself still answers a plain bool, unchanged, for its
        other callers (the webhook's background task, «Aggiorna stato»). `sweep` passes
        `link=False`: its own round after the documents links every match waiting, the
        ones this run turned active among them, so each is asked for once a run and
        counted once. `refresh` passes it too: an admin waiting on «Aggiorna stato» never
        waits on the CRM opening a space. A link is the match's step, not the
        document's, and never counts as `moved`."""
        confirmed = self._confirm_completion(document_id)
        moved = confirmed is True
        try:
            moved = self._store_signed_copy(document_id) is not None or moved
        except (DocumensoFailed, SigningUnavailable, NotFound):
            self.session.rollback()
            _log.warning(
                "the signed copy of document %s is not stored yet", document_id, exc_info=True
            )
        moved = self._mail_signed_copy_once(document_id) or moved
        document = self.session.get(ContractDocument, document_id, populate_existing=True)
        if document is not None and is_active(document):
            moved = self._release_letters(document) or moved
        if link:
            self._link_to_pigro(document_id)
        return _FinishOutcome(moved=moved, unconfirmed=confirmed is None)

    def _link_to_pigro(self, document_id: UUID) -> None:
        """The document's match linked to its deal on Pigro, when a signature has just
        turned it active (REB-499). The match is read again rather than trusted from the
        confirmation, which answers `True` for a rejection as well: only a match
        `active` and still `to_link` is handed to `link`, which takes its own locks
        and holds none across the call. Last of `finish`'s steps, so a CRM slow to open
        a space delays nothing of the signature's, and nothing after it depends on it: a
        link that fails, for any reason, a database error among them, is logged and left
        for the sweep, never raised past a signature that is already done."""
        if self.service.engagements is None:
            return
        found = self.session.execute(
            select(Match.id, Match.status, Match.pigro_status)
            .join(ContractDocument, ContractDocument.match_id == Match.id)
            .where(ContractDocument.id == document_id)
        ).first()
        self.session.rollback()
        if found is None:
            return
        match_id, status, pigro_status = found
        if status != "active" or pigro_status != TO_LINK:
            return
        try:
            self.service.engagements.link(match_id)
        except Exception as exc:
            self.session.rollback()
            _log.warning(
                "the match %s of document %s is not linked to Pigro yet (%s): the sweep "
                "tries again",
                match_id,
                document_id,
                type(exc).__name__,
                exc_info=True,
            )

    def _confirm_completion(self, document_id: UUID) -> bool | None:
        """Before a document counts as signed: Documenso's own word on the envelope,
        read with no row lock held (a network call), so a forged webhook alone can no
        longer move a document to `signed` -- a forged event now needs the hub's own
        API token too (REB-431). Nothing to confirm for a document not `sent`, or
        with no envelope. The freelancer's row, the document's match (when it has one)
        and the document itself are then locked in that order, the global rule, and the
        document re-read: only one still `sent` moves. A `COMPLETED` envelope signs
        it, with the signer's own date (`outcome_from_envelope(envelope).signed_at`),
        turning its match `active` as `apply` used to, with its link to Pigro marked
        `to_link` in the same commit (REB-499); a `REJECTED` or `CANCELLED` one
        is applied the same way `refresh` already applies one, from the same envelope
        this call already read, so a rejection or a cancellation a webhook never
        delivered is recovered by the next `finish` or `sweep` too, not just a missed
        signature (REB-431). Still `PENDING` (or `DRAFT`) leaves the row `sent`,
        logged at info level, tried again next time. Documenso not configured on this
        environment at all (`SigningUnavailable`, `require_documenso`'s own refusal) is
        a routine, expected state (a preview with signing off, say) and is logged once
        and left for the next call, returning `False` like every other no-op. Documenso
        itself refusing or not answering (`DocumensoFailed`: an expired, revoked or
        wrong token, an envelope answering 404, or the instance unreachable) is also
        logged and left for the next call, but returns `None` rather than `False`, so
        `sweep` can tell a real failure apart from a benign one and count it. Returns
        `True` only when this call actually moved the document."""
        document = self.service.document(document_id)
        if document.status != "sent" or document.documenso_id is None:
            return False
        envelope_id = document.documenso_id
        try:
            envelope = self.service.require_documenso().get(envelope_id)
        except SigningUnavailable:
            # No REBASE_DOCUMENSO_URL/REBASE_DOCUMENSO_API_TOKEN on this environment: a
            # routine, expected state (a preview with signing off, say), not a failure
            # worth a traceback on every sweep run.
            _log.info(
                "no Documenso configured on this environment: document %s stays inviato",
                document_id,
            )
            return False
        except DocumensoFailed:
            _log.warning(
                "could not confirm envelope %s of document %s with Documenso",
                envelope_id,
                document_id,
                exc_info=True,
            )
            return None
        outcome = outcome_from_envelope(envelope)
        lock_freelancer(self.session, document.freelancer_id)
        match = (
            lock_match(self.session, document.match_id) if document.match_id is not None else None
        )
        document = self.service.lock_document(document_id)
        if document.status != "sent":
            self.session.rollback()
            return False
        if outcome is None:
            self.session.rollback()
            _log.info("envelope %s of document %s is not completed yet", envelope_id, document_id)
            return False
        if outcome.kind != COMPLETED:
            document.status = "cancelled"
            document.cancel_reason = cancel_reason(outcome)
            self.session.commit()
            return True
        document.status = "signed"
        # The signer's own date; the moment the hub heard of it only if Documenso said
        # nothing, which a completed envelope never does.
        document.signed_at = outcome.signed_at or self.service.now()
        # The reward of a referral no longer matures here (REB-1106): the first signed letter of
        # an engagement is told to the hub by the CRM's notice, which writes it.
        if match is not None and match.status == "awaiting_signature":
            match.status = "active"
            # Its link to Pigro is due from this commit on (REB-499): `finish` asks
            # for it next, and the sweep keeps asking until it holds.
            match.pigro_status = TO_LINK
        self.session.commit()
        return True

    def sweep(self) -> SweepResult:
        """`rebase contracts-sweep` (REB-391): redoes what a lost background task or a
        restart left behind, for every document `finish` still has something to do for.
        Each document runs in its own transaction, through `_finish_outcome`, so the two
        steps stay exactly as idempotent as the webhook's own recovery; a failure is
        logged and the next document is still tried, counted in neither number.
        `touched` is how many `finish` actually moved (REB-433), not how many it merely
        looked at -- a document still waiting on Documenso, tried again next time, does
        not count. `unconfirmed` is how many stayed `sent` because Documenso itself
        refused the confirmation or could not be reached (REB-431): failed silently
        before, now visible so a stuck token or a wrong URL is not read as "nothing to
        do" forever. Then, with the engagement service, its own round (REB-499): every
        active match whose link to Pigro is still due or failed last time, the ones this
        run just turned active among them, is asked for once, `linked` and
        `link_failed` counting what came of it."""
        touched = 0
        unconfirmed = 0
        for document_id in self._to_finish():
            try:
                outcome = self.finish_outcome(document_id, link=False)
            except Exception:
                self.session.rollback()
                _log.warning(
                    "contracts-sweep: document %s could not be finished", document_id, exc_info=True
                )
                continue
            if outcome.moved:
                touched += 1
            if outcome.unconfirmed:
                unconfirmed += 1
        if self.service.engagements is None:
            return SweepResult(touched=touched, unconfirmed=unconfirmed)
        pigro = self.service.engagements.link_pending()
        return SweepResult(
            touched=touched, unconfirmed=unconfirmed, linked=pigro.linked, link_failed=pigro.failed
        )

    def _to_finish(self) -> list[UUID]:
        """Every document a sweep must run `finish` on: a document `sent` with an
        envelope, whose outcome (a completion, a rejection or a cancellation) a lost
        webhook delivery never confirmed (REB-431); a signature or a notice whose sealed
        copy is missing or not yet mailed to either recipient; and every active
        framework agreement that still has a letter of its own regime (REB-687)
        `pending` on a match `awaiting_signature` (a release `finish` itself missed, or never ran
        for). `finish` is idempotent either way, so the three sets are simply run
        together. The first costs one GET to Documenso per waiting document per sweep --
        acceptable at the hub's volume."""
        awaiting_confirmation = select(ContractDocument.id).where(
            ContractDocument.status == "sent", ContractDocument.documenso_id.is_not(None)
        )
        unfinished_signatures = select(ContractDocument.id).where(
            ContractDocument.status.in_(("signed", "notice_served")),
            or_(
                ContractDocument.signed_pdf.is_(None),
                ContractDocument.signed_copy_to_freelancer_at.is_(None),
                ContractDocument.signed_copy_to_rebase_at.is_(None),
            ),
        )
        waiting_letter = aliased(ContractDocument)
        frameworks_with_waiting_letters = select(ContractDocument.id).where(
            ContractDocument.kind == FRAMEWORK,
            ContractDocument.status == "signed",
            ContractDocument.notice_at.is_(None),
            exists(
                select(1)
                .select_from(waiting_letter)
                .join(Match, Match.id == waiting_letter.match_id)
                .where(
                    waiting_letter.kind == LETTER,
                    waiting_letter.status == "pending",
                    waiting_letter.freelancer_id == ContractDocument.freelancer_id,
                    waiting_letter.regime == ContractDocument.regime,
                    Match.status == "awaiting_signature",
                )
            ),
        )
        ids = set(self.session.scalars(awaiting_confirmation).all())
        ids.update(self.session.scalars(unfinished_signatures).all())
        ids.update(self.session.scalars(frameworks_with_waiting_letters).all())
        return sorted(ids, key=str)

    def _store_signed_copy(self, document_id: UUID) -> ContractDocument | None:
        """The download happens under the row's lock, so two callers download once: the
        second waits, then finds the copy stored. `None` when there is nothing to store.
        The freelancer's row is deliberately not locked here, out of the global order
        (REB-391): a network call under one row lock cannot join a lock cycle, and adding
        the freelancer's row would block «Crea match» for as long as Documenso takes."""
        document = self.service.lock_document(document_id)
        if (
            document.status not in ("signed", "notice_served")
            or document.signed_pdf is not None
            or document.documenso_item_id is None
        ):
            self.session.rollback()
            return None
        document.signed_pdf = self.service.require_documenso().download_signed(
            document.documenso_item_id
        )
        self.session.commit()
        return document

    def _mail_signed_copy_once(self, document_id: UUID) -> bool:
        """The signed-copy mails, the freelancer's and rebase's, each sent at most once
        and recorded on its own: one recipient's provider refusing forever must not
        keep the other from ever getting theirs again once `finish` runs next (REB-391).
        Returns whether either mail was actually accepted this call (REB-433)."""
        freelancer_mailed = self._mail_signed_copy_to(document_id, to_freelancer=True)
        rebase_mailed = self._mail_signed_copy_to(document_id, to_freelancer=False)
        return freelancer_mailed or rebase_mailed

    def _mail_signed_copy_to(self, document_id: UUID, *, to_freelancer: bool) -> bool:
        """One recipient's own signed-copy mail, locked under the document's own row,
        same as `_store_signed_copy`, so two concurrent `finish` calls mail this
        recipient once. Nothing to mail yet (no stored copy) or this recipient already
        mailed leaves the row untouched; their own column
        (`signed_copy_to_freelancer_at` or `signed_copy_to_rebase_at`) is set, and
        committed, only once their mail is accepted, still under this lock, so a refusal
        is retried on the next `finish` without repeating the other recipient's mail,
        already accepted and recorded under its own column (REB-391). Returns whether
        this recipient's mail was accepted and recorded now (REB-433)."""
        document = self.service.lock_document(document_id)
        mailed_at = (
            document.signed_copy_to_freelancer_at
            if to_freelancer
            else document.signed_copy_to_rebase_at
        )
        if document.signed_pdf is None or mailed_at is not None:
            self.session.rollback()
            return False
        if self.service.sender is None:
            self.session.rollback()
            _log.warning(
                "no mail sender: the signed copy of document %s was not mailed", document.id
            )
            return False
        user = self.service.owner(document.freelancer_id)
        pdf = self.service.matches.reads.document_pdf(document.id, signed=True)
        attachment = Attachment(filename=pdf.filename, content=pdf.content)
        to = user.email if to_freelancer else self.service.contracts_mail
        mail = signed_copy_mail(
            to,
            kind=document.kind,
            number=document.number,
            attachment=attachment,
            first_name=user.first_name,
            last_name=user.last_name,
            for_rebase=not to_freelancer,
            regime=document.regime,
        )
        if not self.service.sender.send(mail):
            _log.warning(
                "the signed copy of document %s was refused by the provider (%s)",
                document.id,
                "freelancer" if to_freelancer else "rebase",
            )
            self.session.rollback()
            return False
        if to_freelancer:
            document.signed_copy_to_freelancer_at = self.service.now()
        else:
            document.signed_copy_to_rebase_at = self.service.now()
        self.session.commit()
        return True

    def _release_letters(self, framework: ContractDocument) -> bool:
        """The letters of this framework agreement's regime that waited for it (spec § 1e,
        REB-687), each in a transaction of its own and mailed after its commit: a
        Documenso refusal leaves that letter waiting for the next `finish` and lets the
        others go. Only matches an admin sent (`awaiting_signature`); a draft's letter leaves when
        its match is sent.

        `send_match` refuses up front without a mail sender (`_sender`), so a release
        must not dispatch a letter to Documenso either when nobody could then be told
        about it (REB-391): checked before any letter is even read. Returns whether at
        least one letter was actually released this call (REB-433); its own signing-mail
        need not have been accepted to count, the same as `send_match`'s own report."""
        if self.service.sender is None:
            _log.warning(
                "no mail sender: the letters waiting on framework agreement %s stay waiting",
                framework.id,
            )
            return False
        # `Match.id`, not `ContractDocument.match_id`: the two are equal by the join, and
        # the match's own key is typed as never missing, which a letter's nullable
        # `match_id` is not.
        waiting = list(
            self.session.execute(
                select(ContractDocument.id, Match.id)
                .join(Match, Match.id == ContractDocument.match_id)
                .where(
                    ContractDocument.kind == LETTER,
                    ContractDocument.freelancer_id == framework.freelancer_id,
                    ContractDocument.regime == framework.regime,
                    ContractDocument.status == "pending",
                    Match.status == "awaiting_signature",
                )
                .order_by(ContractDocument.created_at, ContractDocument.id)
            )
        )
        framework_id = framework.id
        sent_by = framework.sent_by or framework.created_by
        freelancer_id = framework.freelancer_id
        self.session.rollback()
        released = False
        for letter_id, match_id in waiting:
            try:
                letter = self._send_waiting(
                    letter_id, match_id, framework_id, freelancer_id, sent_by
                )
            except (
                ContractFailed,
                DocumensoFailed,
                InvalidState,
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
                mailed = self.service.mail_signing_request(letter)
                # A letter released this way leaves the same trail `send_match` leaves
                # for one it sends itself, attributed to whoever sent the framework
                # agreement that just freed it (REB-391).
                AdminActionService(self.session).record(
                    ENTITY,
                    match_id,
                    "documents_sent",
                    sent_by,
                    {"document": letter.id, "kind": LETTER, "mail": mailed},
                )
        return released

    def _send_waiting(
        self,
        letter_id: UUID,
        match_id: UUID,
        framework_id: UUID,
        freelancer_id: UUID,
        sent_by: UUID,
    ) -> ContractDocument | None:
        """The same lock order as `apply` (global rule): the freelancer's row, the
        letter's match, then the letter itself; the framework agreement is only read,
        never locked, since nothing here writes it."""
        lock_freelancer(self.session, freelancer_id)
        match = lock_match(self.session, match_id)
        letter = self.service.lock_document(letter_id)
        framework = self.session.get(ContractDocument, framework_id, populate_existing=True)
        if (
            letter.status != "pending"
            or match.status != "awaiting_signature"
            or framework is None
            or not is_active(framework)
            or framework.regime != letter.regime
        ):
            self.session.rollback()
            return None
        if not self.service.allow_draft and self.service.require_renderer().is_draft(
            text_name(LETTER, letter.regime)
        ):
            raise InvalidState(DRAFT_REFUSED[LETTER])
        # The same rule `send_match` applies (REB-687): a partita IVA removed while the
        # letter waited refuses it in those words, not as a blank field.
        require_regime_fits(fiscal_row(self.session, freelancer_id).vat_number, letter.regime)
        self.service.dispatch(letter, framework, sent_by)
        self.session.commit()
        return letter
