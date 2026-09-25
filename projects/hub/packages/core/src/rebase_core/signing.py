"""Signing: a match's documents go out through Documenso and come back signed (REB-387,
phase 3).

`send_match` is «Invia per la firma». At most one document leaves at a time: the
framework agreement when the freelancer has none active (the letter waits for it,
`in_attesa`, spec § 1e), else the letter. A document is typeset again as it leaves, with
the parties as they are today, the day it leaves in rebase's blank («Documento emesso da
rebase il ...», spec § 5) and the labels under the signing blanks left undrawn; Documenso
gets the PDF with the freelancer as its one signer (`rebase_core.documenso`), and the hub
mails the link itself, one mail per document. A text that says `status: draft` never
leaves (spec § 1f), unless `allow_draft` is true, which only the preview's `.env` sets
through `REBASE_CONTRACTS_ALLOW_DRAFT`; with both texts `status: final` the preview
leaves it false too, kept for the next draft. If Documenso refuses or does not answer, the
transaction rolls back and nothing is marked sent; if only the mail fails, the document
is `inviato` and the report says so, for «Reinvia email».

Every write locks the freelancer's row first, as `MatchService.create` and
`FiscalService.save` already do, then the match, then its document, because two admins,
the webhook and «Aggiorna stato» can reach the same freelancer's documents at once.

The webhook's side is `apply` and `finish`. `apply` locks the freelancer's row, its match
(when it has one) and the document, in that order, and commits, so Documenso gets its
answer long before its ten seconds (probe § 5). A rejection or a cancellation is moved
and recorded there and then, the harm of forging one being small, so a second delivery of
the same event waits on the lock and finds it already `annullato`. A completion is not
moved by `apply`: the webhook's secret travels in clear (probe § 5), so `apply` only
notes which document to confirm and leaves it `inviato` -- a second delivery of the same
`DOCUMENT_COMPLETED`, arriving before the first's own confirmation has run, reads the
same `inviato` row and schedules its own `finish` too. `finish` runs after `apply`'s
commit, in the webhook's background task or under «Aggiorna stato»: it first confirms a
still-`inviato` document with Documenso itself, over the hub's own API token, and only
moves it to `firmato` (or, for a rejection or a cancellation the webhook never delivered,
`annullato`) once Documenso says so (REB-431, a forged completion then needs the token
too); then the sealed copy is downloaded and stored, the two mails (the freelancer's and
rebase's) sent, each recorded on its own once accepted (`signed_copy_to_freelancer_at`,
`signed_copy_to_rebase_at`), then, for a framework agreement, the letters that waited for
it typeset with its signature date and sent. Each step checks under the document's own
row lock whether it is still to do, so running `finish` twice -- two overlapping
deliveries' own background tasks among them -- does everything once; a lost background
task or a restart between steps leaves whichever of the two mail columns is unset, which
the next `finish` reads as still to do for that recipient alone (REB-391).

`sweep` is the recovery `rebase contracts-sweep` runs, meant every ten minutes once
production schedules it with the Documenso rollout: `finish` again, for every document a
webhook or an admin's «Aggiorna stato» never reached.

The recovery actions are the admin's: «Aggiorna stato» (`refresh`) for the event
Documenso gave up on, «Reinvia email» (`resend_mail`), «Annulla» on a framework agreement
(`cancel_document`) or on a match (`cancel_match`), both cancelling the envelope on
Documenso before the row, and «Registra disdetta» (`record_notice`).
"""

import logging
from collections.abc import Callable, Mapping
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, aliased

from rebase_core.audit import AdminActionService, utcnow
from rebase_core.config import Settings
from rebase_core.contract_schemas import ContractDocumentRead, MatchRead, SendReport
from rebase_core.contracts.fields import ContractFailed, Value, signer_data
from rebase_core.contracts.render import Renderer, text_version
from rebase_core.documenso import (
    COMPLETED,
    REJECTED,
    UNREACHABLE,
    DocumensoClient,
    Outcome,
    fields_from_blanks,
    outcome_from_envelope,
)
from rebase_core.errors import DocumensoFailed, InvalidState, NotFound, SigningUnavailable
from rebase_core.framework import (
    active_framework,
    document_read,
    is_active,
    pending_framework,
    rome_today,
)
from rebase_core.mail import (
    Attachment,
    EmailSender,
    document_name,
    signed_copy_mail,
    signing_cancelled_mail,
    signing_request_mail,
)
from rebase_core.matches import DOCUMENT_BY_KIND, ENTITY, LETTERA, QUADRO, MatchService, _full_name
from rebase_core.models import CANCEL_REASON_MAX_LENGTH, ContractDocument, Freelancer, Match, User

_log = logging.getLogger(__name__)

FREELANCER = "freelancer"
DEFAULT_CONTRACTS_MAIL = str(Settings.model_fields["contracts_mail"].default)
# What a document must print once it leaves: rebase's, the freelancer's and the client's
# data, and the fields the hub fills itself. What may stay blank is what the signing site
# fills (the signature, the date) and the engagement's optional lines.
MUST_PRINT_PREFIXES = ("rebase-", "professionista-", "cliente-")
MUST_PRINT = frozenset({"numero", "data-contratto-quadro", "luogo-firma", "firma-rebase"})
_ALLOW_DRAFT_NOTE = " Su un ambiente di prova lo permette REBASE_CONTRACTS_ALLOW_DRAFT."
DRAFT_REFUSED = {
    QUADRO: (
        "Il testo del contratto quadro è ancora una bozza (status: draft): si genera e si "
        "salva, ma non parte per la firma." + _ALLOW_DRAFT_NOTE
    ),
    LETTERA: (
        "Il testo della lettera di incarico è ancora una bozza (status: draft): si genera "
        "e si salva, ma non parte per la firma." + _ALLOW_DRAFT_NOTE
    ),
}
NO_DOCUMENSO = (
    "La firma elettronica non è attiva su questo ambiente: mancano l'indirizzo o il token "
    "di Documenso."
)
NO_SENDER = (
    "L'invio delle email non è attivo su questo ambiente: il link per firmare non "
    "arriverebbe a nessuno."
)
REFUSED_ON_SITE = "Rifiutato dal freelance sul sito di firma."
CANCELLED_ON_DOCUMENSO = "Annullato su Documenso."
CANCELLED_BY_REBASE = "Annullato da rebase."
CANCELLED_WITH_MATCH = "Annullato da rebase con il suo match."
CANCEL_REFUSED = (
    "Documenso non annulla questo documento: forse è già stato firmato, rifiutato o "
    "annullato. Premi «Aggiorna stato» e riprova."
)


def _cancel_reason(outcome: Outcome) -> str:
    """Why a document the webhook cancels is `annullato`, as the page shows it."""
    if outcome.kind == REJECTED:
        reason = (outcome.reason or "").strip()
        if not reason:
            return REFUSED_ON_SITE
        return f"Rifiutato dal freelance: {reason}"[:CANCEL_REASON_MAX_LENGTH]
    return CANCELLED_ON_DOCUMENSO


def _filename(document: ContractDocument, version: str) -> str:
    """The name Documenso keeps and seals as `<name>_signed.pdf` (probe § 4)."""
    if document.kind == LETTERA:
        return f"lettera-di-incarico-{document.numero}.pdf"
    return f"contratto-quadro-v{version}.pdf"


def _refuse_blanks(blank: list[str]) -> None:
    """A document that would leave with a party's data missing is refused, naming it: the
    signer's gaps are this environment's setting (a 503), any other gap the match's."""
    unfilled = [key for key in blank if key.startswith(MUST_PRINT_PREFIXES) or key in MUST_PRINT]
    signer = [key for key in unfilled if key.startswith("rebase-")]
    if signer:
        raise SigningUnavailable(
            f"Mancano i dati di chi firma per rebase ({', '.join(signer)}): vanno in "
            "REBASE_SIGNER_JSON prima di inviare."
        )
    if unfilled:
        raise InvalidState(
            f"Il documento lascerebbe in bianco {', '.join(unfilled)}: completali prima di "
            "inviarlo."
        )


class SigningService:
    def __init__(
        self,
        session: Session,
        *,
        renderer: Renderer | None = None,
        documenso: DocumensoClient | None = None,
        sender: EmailSender | None = None,
        signer: Mapping[str, Value] | None = None,
        signer_json: str = "",
        contracts_mail: str = DEFAULT_CONTRACTS_MAIL,
        allow_draft: bool = False,
        today: Callable[[], date] = rome_today,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        """Each collaborator is needed only by the steps that use it: a webhook that
        cancels a document needs no renderer, a draft's cancellation no Documenso.

        `signer` is the resolved mapping, for tests that already have one (including an
        explicit `{}`, which the caller means literally: no signer, and no
        `REBASE_SIGNER_JSON` to fall back on). `signer_json` is the raw setting, read
        and cached on first use by a path that actually typesets a document (`_dispatch`,
        `_framework_to_send`), never here: this constructor runs for every `SigningDep`
        route (a future cancel, refresh or the webhook among them), and a malformed
        value must 503 only the send it breaks, not a route that never reaches a
        renderer (REB-406)."""
        self.session = session
        self.renderer = renderer
        self.documenso = documenso
        self.sender = sender
        self.contracts_mail = contracts_mail
        self.allow_draft = allow_draft
        self.today = today
        self.now = now
        self._signer_json = signer_json
        self._signer_cache: Mapping[str, Value] | None = signer
        self.matches = MatchService(session, renderer, signer or {}, today)

    # ---- «Invia per la firma» ---------------------------------------------------------

    def send_match(self, match_id: UUID, admin_id: UUID) -> SendReport:
        renderer = self._renderer()
        self._documenso()
        self._sender()
        leaving: ContractDocument | None = None
        try:
            freelancer_id = self.matches.match_freelancer(match_id)
            # The freelancer's row first, as `MatchService.create` already does, so
            # «Crea match» racing this send waits for it rather than reading a framework
            # this send is about to dispatch as still merely `generato`.
            self.matches.lock_freelancer(freelancer_id)
            match = self.matches.lock_match(match_id)
            if match.stato not in ("bozza", "in_firma"):
                raise InvalidState(
                    f"Si invia per la firma solo un match in bozza o in firma: questo è "
                    f"{match.stato}.",
                    stato=match.stato,
                )
            letter = self._lock_letter(match.id)
            if letter is None or letter.stato not in ("generato", "in_attesa"):
                raise InvalidState(
                    "La lettera di questo match è già partita, firmata o annullata: non c'è "
                    "nulla da inviare."
                )
            active = active_framework(self.session, match.freelancer_id)
            if not self.allow_draft:
                for kind in (LETTERA,) if active is not None else (QUADRO, LETTERA):
                    if renderer.is_draft(DOCUMENT_BY_KIND[kind]):
                        raise InvalidState(DRAFT_REFUSED[kind])
            if active is not None:
                leaving = letter
                self._dispatch(letter, active, admin_id)
            else:
                letter.stato = "in_attesa"
                leaving = self._framework_to_send(match.freelancer_id, admin_id)
                if leaving is not None:
                    self._dispatch(leaving, None, admin_id)
            match.stato = "in_firma"
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        sent_kind = leaving.kind if leaving is not None else None
        sent_id = leaving.id if leaving is not None else None
        mailed = self._mail_signing_request(leaving) if leaving is not None else None
        # Nothing left when the letter waits on a framework agreement already out for
        # signature (`leaving` is `None`): no trail entry for a send that sent nothing.
        if leaving is not None:
            AdminActionService(self.session).record(
                ENTITY,
                match_id,
                "documents_sent",
                admin_id,
                {"documento": sent_id, "kind": sent_kind, "mail": mailed},
            )
        return SendReport(match=self.matches.get(match_id), inviato=sent_kind, mail_inviata=mailed)

    def _framework_to_send(self, freelancer_id: UUID, admin_id: UUID) -> ContractDocument | None:
        """The framework agreement a waiting letter needs, locked: none when one is out for
        signature already (the letter leaves after it), the one generated for a draft,
        else a new one, written now because the last was cancelled or refused. The
        caller must already hold the freelancer's row lock."""
        self._signer()
        pending = pending_framework(self.session, freelancer_id)
        if pending is None:
            return self.matches.write_framework(freelancer_id, admin_id)
        framework = self._lock(pending.id)
        if framework.stato == "inviato":
            return None
        if framework.stato == "generato":
            return framework
        raise InvalidState(
            "Il contratto quadro è cambiato mentre lo inviavi: ricarica la pagina e riprova."
        )

    def _dispatch(
        self, document: ContractDocument, framework: ContractDocument | None, sent_by: UUID
    ) -> None:
        """Typeset `document` as it leaves, hand it to Documenso and record the envelope,
        inside the caller's transaction: nothing here commits, so a refusal at any step
        leaves the document as it was. The caller must already hold the freelancer's row
        lock. If `get` or `distribute` fails after `create` already left an envelope on
        Documenso, a best-effort cancel follows it (`_cancel_orphan`, REB-406), so a retried
        send does not pile up drafts under the same externalId."""
        self._signer()
        renderer, documenso = self._renderer(), self._documenso()
        name = DOCUMENT_BY_KIND[document.kind]
        data = self.matches.data_for_sending(document, self.today(), framework)
        rendered = renderer.render(name, data, signing=True)
        if rendered.draft and not self.allow_draft:
            raise InvalidState(DRAFT_REFUSED[document.kind])
        _refuse_blanks(rendered.blank)
        fields = fields_from_blanks(renderer.signature_blanks(name, data, signing=True))
        user = self._owner(document.freelancer_id)
        title = document_name(document.kind, document.numero)
        envelope_id = documenso.create(
            title=title[0].upper() + title[1:],
            external_id=str(document.id),
            filename=_filename(document, rendered.version),
            pdf=rendered.pdf,
            signer_email=user.email,
            signer_name=_full_name(user),
            fields=fields,
        )
        try:
            envelope = documenso.get(envelope_id)
            signing_url = documenso.distribute(envelope_id)
        except Exception:
            self._cancel_orphan(documenso, envelope_id)
            raise
        document.data = dict(data)
        document.pdf = rendered.pdf
        document.text_version = rendered.version
        document.testo_bozza = rendered.draft
        document.documenso_id = envelope_id
        document.documenso_item_id = envelope.item_id
        document.signing_url = signing_url
        document.stato = "inviato"
        document.sent_at = self.now()
        document.sent_by = sent_by

    def _cancel_orphan(self, documenso: DocumensoClient, envelope_id: str) -> None:
        """`get` or `distribute` failed after `create` already left an envelope on
        Documenso: a best-effort cancel, so a retried send does not pile up drafts under
        the same externalId. A failure of this cancel (a still-draft envelope refuses
        one, probe § 4) is logged and never raised over the failure the admin already
        sees (REB-406)."""
        try:
            documenso.cancel(envelope_id, "invio non completato: annullo l'envelope orfano")
        except Exception:
            _log.warning("could not cancel the orphaned envelope %s", envelope_id, exc_info=True)

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
            self._documenso().cancel(envelope_id, reason)
        except DocumensoFailed as exc:
            if exc.message == UNREACHABLE:
                raise
            raise DocumensoFailed(CANCEL_REFUSED, exc.message) from exc

    def _mail_signing_request(self, document: ContractDocument) -> bool:
        """After the commit: a refused mail leaves the document `inviato`, and says so."""
        if document.signing_url is None or self.sender is None:
            return False
        user = self._owner(document.freelancer_id)
        mail = signing_request_mail(
            user.email, user.nome, document.kind, document.numero, document.signing_url
        )
        if not self.sender.send(mail):
            _log.warning("the signing mail of document %s was refused by the provider", document.id)
            return False
        return True

    # ---- the webhook -------------------------------------------------------------------

    def apply(self, outcome: Outcome) -> UUID | None:
        """One envelope's outcome, applied once (probe § 11.2). The document is found by
        its envelope with no lock; then the freelancer's row, its match (when it has
        one) and the document itself are locked in that order, the global rule (freelancer,
        match, document), because a letter's webhook can race a `send_match` or a cancel
        of the same match. Its state is re-checked once every lock is held, in case
        anything moved while this delivery waited: only a document still `inviato`
        moves, and an unknown envelope (the other environment's, or one the hub never
        recorded) and a document already signed or cancelled are acknowledged and left
        alone. No call leaves this method.

        A rejection or a cancellation is moved and recorded here, same as before: the
        harm of forging one is small, and both only ever cancel. A completion is not
        moved here (REB-431): the webhook's own secret travels in clear (probe § 5), so a
        forged `DOCUMENT_COMPLETED` must not by itself turn a document `firmato`. This
        only returns the document's id, for `finish` after the commit, which confirms the
        completion with Documenso itself before it counts. Returns `None` when nothing
        needs `finish`."""
        found = self.session.execute(
            select(
                ContractDocument.id, ContractDocument.freelancer_id, ContractDocument.match_id
            ).where(ContractDocument.documenso_id == outcome.envelope_id)
        ).first()
        if found is None:
            self.session.rollback()
            return None
        document_id, freelancer_id, match_id = found
        self.matches.lock_freelancer(freelancer_id)
        if match_id is not None:
            self.matches.lock_match(match_id)
        document = self._lock(document_id)
        if document.stato != "inviato":
            self.session.rollback()
            return None
        signed: UUID | None = None
        if outcome.kind == COMPLETED:
            # Left `inviato` on purpose: `finish` confirms it with Documenso itself
            # before moving it (REB-431).
            signed = document.id
        else:
            document.stato = "annullato"
            document.cancel_reason = _cancel_reason(outcome)
        self.session.commit()
        return signed

    def finish(self, document_id: UUID) -> bool:
        """What a signature leaves to do once it is committed, each step idempotent: the
        outcome confirmed with Documenso itself and the document moved to `firmato`, or
        to `annullato` for a rejection or a cancellation the webhook never delivered
        (REB-431); the sealed copy downloaded and stored; the signed-copy mails sent,
        once; for an active framework agreement, the letters that waited for it
        released. A step that fails is logged and left for the next call (the next
        «Aggiorna stato», or `sweep`); the others still run. Returns whether this call
        actually moved anything -- the confirmation, a stored copy, an accepted mail, a
        released letter -- so `sweep` counts only documents it truly advanced (REB-433),
        not one still waiting on Documenso or a mail provider that keeps refusing."""
        moved = self._confirm_completion(document_id)
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
        return moved

    def _confirm_completion(self, document_id: UUID) -> bool:
        """Before a document counts as signed: Documenso's own word on the envelope,
        read with no row lock held (a network call), so a forged webhook alone can no
        longer move a document to `firmato` -- a forged event now needs the hub's own
        API token too (REB-431). Nothing to confirm for a document not `inviato`, or
        with no envelope. The freelancer's row, the document's match (when it has one)
        and the document itself are then locked in that order, the global rule, and the
        document re-read: only one still `inviato` moves. A `COMPLETED` envelope signs
        it, with the signer's own date (`outcome_from_envelope(envelope).signed_at`),
        turning its match `attivo` as `apply` used to; a `REJECTED` or `CANCELLED` one
        is applied the same way `refresh` already applies one, from the same envelope
        this call already read, so a rejection or a cancellation a webhook never
        delivered is recovered by the next `finish` or `sweep` too, not just a missed
        signature (REB-431). Still `PENDING` (or `DRAFT`) leaves the row `inviato`,
        logged at info level, tried again next time. Documenso unreachable
        (`DocumensoFailed`) or not configured on this environment at all
        (`SigningUnavailable`, `self._documenso()`'s own refusal) is logged once and
        left for the next call rather than raised, so the sweep never logs a traceback
        merely for running where signing is off. Returns whether this call moved the
        document."""
        document = self._document(document_id)
        if document.stato != "inviato" or document.documenso_id is None:
            return False
        envelope_id = document.documenso_id
        try:
            envelope = self._documenso().get(envelope_id)
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
            return False
        outcome = outcome_from_envelope(envelope)
        self.matches.lock_freelancer(document.freelancer_id)
        match = (
            self.matches.lock_match(document.match_id) if document.match_id is not None else None
        )
        document = self._lock(document_id)
        if document.stato != "inviato":
            self.session.rollback()
            return False
        if outcome is None:
            self.session.rollback()
            _log.info("envelope %s of document %s is not completed yet", envelope_id, document_id)
            return False
        if outcome.kind != COMPLETED:
            document.stato = "annullato"
            document.cancel_reason = _cancel_reason(outcome)
            self.session.commit()
            return True
        document.stato = "firmato"
        # The signer's own date; the moment the hub heard of it only if Documenso said
        # nothing, which a completed envelope never does.
        document.signed_at = outcome.signed_at or self.now()
        if match is not None and match.stato == "in_firma":
            match.stato = "attivo"
        self.session.commit()
        return True

    def sweep(self) -> int:
        """`rebase contracts-sweep` (REB-391): redoes what a lost background task or a
        restart left behind, for every document `finish` still has something to do for.
        Each document runs in its own transaction, through `finish` itself, so the two
        steps stay exactly as idempotent as the webhook's own recovery; a failure is
        logged and the next document is still tried. Returns how many `finish` actually
        moved (REB-433), not how many it merely looked at -- a document still waiting on
        Documenso, tried again next time, does not count."""
        touched = 0
        for document_id in self._to_finish():
            try:
                moved = self.finish(document_id)
            except Exception:
                self.session.rollback()
                _log.warning(
                    "contracts-sweep: document %s could not be finished", document_id, exc_info=True
                )
                continue
            if moved:
                touched += 1
        return touched

    def _to_finish(self) -> list[UUID]:
        """Every document a sweep must run `finish` on: a document `inviato` with an
        envelope, whose outcome (a completion, a rejection or a cancellation) a lost
        webhook delivery never confirmed (REB-431); a signature or a notice whose sealed
        copy is missing or not yet mailed to either recipient; and every active
        framework agreement that still has a letter `in_attesa` on a match `in_firma` (a
        release `finish` itself missed, or never ran
        for). `finish` is idempotent either way, so the three sets are simply run
        together. The first costs one GET to Documenso per waiting document per sweep --
        acceptable at the hub's volume."""
        awaiting_confirmation = select(ContractDocument.id).where(
            ContractDocument.stato == "inviato", ContractDocument.documenso_id.is_not(None)
        )
        unfinished_signatures = select(ContractDocument.id).where(
            ContractDocument.stato.in_(("firmato", "disdetto")),
            or_(
                ContractDocument.signed_pdf.is_(None),
                ContractDocument.signed_copy_to_freelancer_at.is_(None),
                ContractDocument.signed_copy_to_rebase_at.is_(None),
            ),
        )
        waiting_letter = aliased(ContractDocument)
        frameworks_with_waiting_letters = select(ContractDocument.id).where(
            ContractDocument.kind == QUADRO,
            ContractDocument.stato == "firmato",
            ContractDocument.notice_at.is_(None),
            exists(
                select(1)
                .select_from(waiting_letter)
                .join(Match, Match.id == waiting_letter.match_id)
                .where(
                    waiting_letter.kind == LETTERA,
                    waiting_letter.stato == "in_attesa",
                    waiting_letter.freelancer_id == ContractDocument.freelancer_id,
                    Match.stato == "in_firma",
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
        document = self._lock(document_id)
        if (
            document.stato not in ("firmato", "disdetto")
            or document.signed_pdf is not None
            or document.documenso_item_id is None
        ):
            self.session.rollback()
            return None
        document.signed_pdf = self._documenso().download_signed(document.documenso_item_id)
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
        document = self._lock(document_id)
        mailed_at = (
            document.signed_copy_to_freelancer_at
            if to_freelancer
            else document.signed_copy_to_rebase_at
        )
        if document.signed_pdf is None or mailed_at is not None:
            self.session.rollback()
            return False
        if self.sender is None:
            self.session.rollback()
            _log.warning(
                "no mail sender: the signed copy of document %s was not mailed", document.id
            )
            return False
        user = self._owner(document.freelancer_id)
        pdf = self.matches.document_pdf(document.id, signed=True)
        attachment = Attachment(filename=pdf.filename, content=pdf.content)
        to = user.email if to_freelancer else self.contracts_mail
        mail = signed_copy_mail(
            to,
            kind=document.kind,
            numero=document.numero,
            attachment=attachment,
            nome=user.nome,
            cognome=user.cognome,
            for_rebase=not to_freelancer,
        )
        if not self.sender.send(mail):
            _log.warning(
                "the signed copy of document %s was refused by the provider (%s)",
                document.id,
                "freelancer" if to_freelancer else "rebase",
            )
            self.session.rollback()
            return False
        if to_freelancer:
            document.signed_copy_to_freelancer_at = self.now()
        else:
            document.signed_copy_to_rebase_at = self.now()
        self.session.commit()
        return True

    def _release_letters(self, framework: ContractDocument) -> bool:
        """The letters that waited for this framework agreement (spec § 1e), each in a
        transaction of its own and mailed after its commit: a Documenso refusal leaves
        that letter waiting for the next `finish` and lets the others go. Only matches an
        admin sent (`in_firma`); a draft's letter leaves when its match is sent.

        `send_match` refuses up front without a mail sender (`_sender`), so a release
        must not dispatch a letter to Documenso either when nobody could then be told
        about it (REB-391): checked before any letter is even read. Returns whether at
        least one letter was actually released this call (REB-433); its own signing-mail
        need not have been accepted to count, the same as `send_match`'s own report."""
        if self.sender is None:
            _log.warning(
                "no mail sender: the letters waiting on framework agreement %s stay waiting",
                framework.id,
            )
            return False
        waiting = list(
            self.session.execute(
                select(ContractDocument.id, ContractDocument.match_id)
                .join(Match, Match.id == ContractDocument.match_id)
                .where(
                    ContractDocument.kind == LETTERA,
                    ContractDocument.freelancer_id == framework.freelancer_id,
                    ContractDocument.stato == "in_attesa",
                    Match.stato == "in_firma",
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
            except (ContractFailed, DocumensoFailed, InvalidState, NotFound, SigningUnavailable):
                self.session.rollback()
                _log.warning(
                    "letter %s keeps waiting: its release failed", letter_id, exc_info=True
                )
                continue
            if letter is not None:
                released = True
                mailed = self._mail_signing_request(letter)
                # A letter released this way leaves the same trail `send_match` leaves
                # for one it sends itself, attributed to whoever sent the framework
                # agreement that just freed it (REB-391).
                AdminActionService(self.session).record(
                    ENTITY,
                    match_id,
                    "documents_sent",
                    sent_by,
                    {"documento": letter.id, "kind": LETTERA, "mail": mailed},
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
        self.matches.lock_freelancer(freelancer_id)
        match = self.matches.lock_match(match_id)
        letter = self._lock(letter_id)
        framework = self.session.get(ContractDocument, framework_id, populate_existing=True)
        if (
            letter.stato != "in_attesa"
            or match.stato != "in_firma"
            or framework is None
            or not is_active(framework)
        ):
            self.session.rollback()
            return None
        if not self.allow_draft and self._renderer().is_draft(DOCUMENT_BY_KIND[LETTERA]):
            raise InvalidState(DRAFT_REFUSED[LETTERA])
        self._dispatch(letter, framework, sent_by)
        self.session.commit()
        return letter

    # ---- recovery, and the framework agreement's end -----------------------------------

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
        GET; simpler than giving `finish` a second signature for one caller."""
        document = self._document(document_id)
        if document.documenso_id is None:
            raise InvalidState(
                "Questo documento non è mai partito per la firma: non c'è nulla da aggiornare.",
                stato=document.stato,
            )
        envelope = self._documenso().get(document.documenso_id)
        self.session.rollback()
        outcome = outcome_from_envelope(envelope)
        if outcome is not None:
            self.apply(outcome)
        self.finish(document_id)
        return self._read(document_id)

    def resend_mail(self, document_id: UUID, admin_id: UUID) -> ContractDocumentRead:
        """«Reinvia email»: the signing mail again, the same link, for a document that
        still waits for the signature."""
        self._sender()
        document = self._document(document_id)
        if document.stato != "inviato" or document.signing_url is None:
            raise InvalidState(
                "Si reinvia la mail solo di un documento che aspetta la firma.",
                stato=document.stato,
            )
        if not self._mail_signing_request(document):
            raise SigningUnavailable(
                "La mail non è partita: il provider l'ha rifiutata. Riprova tra qualche minuto."
            )
        self._record(document, "mail_resent", admin_id)
        return self._read(document_id)

    def cancel_document(self, document_id: UUID, admin_id: UUID) -> ContractDocumentRead:
        """«Annulla» on a framework agreement not signed yet: its envelope cancelled on
        Documenso first, under the row's lock (only a `PENDING` one can be, probe § 4),
        then the row. A letter is cancelled with its match. The letters that waited for
        this framework agreement keep waiting: «Invia per la firma» on their match writes
        a new one. A freelancer already told about this document by mail is told again,
        once it is gone (REB-407).

        The freelancer's row locks first, the global order: its id is read here with no
        lock of its own (`_document`), only to know which row to take."""
        freelancer_id = self._document(document_id).freelancer_id
        self.matches.lock_freelancer(freelancer_id)
        document = self._lock(document_id)
        was_sent = False
        try:
            if document.kind != QUADRO:
                raise InvalidState("Una lettera di incarico si annulla con il suo match.")
            if document.stato not in ("generato", "inviato"):
                raise InvalidState(
                    f"Si annulla solo un contratto quadro non ancora firmato: questo è "
                    f"{document.stato}.",
                    stato=document.stato,
                )
            was_sent = document.stato == "inviato"
            if was_sent and document.documenso_id is not None:
                self._cancel_envelope(document.documenso_id, CANCELLED_BY_REBASE)
            document.stato = "annullato"
            document.cancel_reason = CANCELLED_BY_REBASE
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self._record(document, "document_cancelled", admin_id)
        if was_sent:
            self._mail_cancellation(document)
        return self._read(document_id)

    def cancel_match(self, match_id: UUID, admin_id: UUID) -> MatchRead:
        """«Annulla» on a match: a draft, still locked in the global order, delegates to
        `MatchService.cancel` (which takes the same locks again, a no-op on a row this
        transaction already holds); a match in signature also cancels its letter's
        envelope when the letter is out for signature, so the link the freelancer got
        stops working, and tells them by mail. The framework agreement is the
        freelancer's, not the match's, and stays."""
        freelancer_id = self.matches.match_freelancer(match_id)
        self.matches.lock_freelancer(freelancer_id)
        match = self.matches.lock_match(match_id)
        if match.stato == "bozza":
            return self.matches.cancel(match_id, admin_id)
        letters: list[ContractDocument] = []
        mailed: list[ContractDocument] = []
        try:
            if match.stato != "in_firma":
                raise InvalidState(
                    f"Si annulla solo un match in bozza o in firma: questo è {match.stato}.",
                    stato=match.stato,
                )
            letters = list(
                self.session.scalars(
                    select(ContractDocument)
                    .where(
                        ContractDocument.match_id == match.id,
                        ContractDocument.stato.in_(("generato", "in_attesa", "inviato")),
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            for letter in letters:
                if letter.stato == "inviato":
                    if letter.documenso_id is not None:
                        self._cancel_envelope(letter.documenso_id, CANCELLED_BY_REBASE)
                    mailed.append(letter)
                letter.stato = "annullato"
                letter.cancel_reason = CANCELLED_WITH_MATCH
            match.stato = "annullato"
            match.cancelled_at = self.now()
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        AdminActionService(self.session).record(
            ENTITY, match_id, "match_cancelled", admin_id, {"documenti": [d.id for d in letters]}
        )
        for letter in mailed:
            self._mail_cancellation(letter)
        return self.matches.get(match_id)

    def record_notice(self, document_id: UUID, admin_id: UUID) -> ContractDocumentRead:
        """«Registra disdetta»: a notice or a withdrawal on an active framework agreement
        (spec § 1d). From now the freelancer has none active, and their next match writes
        a new one."""
        freelancer_id = self._document(document_id).freelancer_id
        self.matches.lock_freelancer(freelancer_id)
        document = self._lock(document_id)
        if not is_active(document):
            self.session.rollback()
            raise InvalidState(
                "Si registra la disdetta solo di un contratto quadro attivo.",
                stato=document.stato,
            )
        document.notice_at = self.now()
        document.stato = "disdetto"
        self.session.commit()
        self._record(document, "notice_recorded", admin_id)
        return self._read(document_id)

    def _document(self, document_id: UUID) -> ContractDocument:
        document = self.session.get(ContractDocument, document_id, populate_existing=True)
        if document is None:
            raise NotFound("documento", document_id)
        return document

    def _read(self, document_id: UUID) -> ContractDocumentRead:
        return document_read(
            self._document(document_id), self.today(), text_version(DOCUMENT_BY_KIND[QUADRO])
        )

    def _record(self, document: ContractDocument, kind: str, admin_id: UUID) -> None:
        """A framework agreement's action lands on its freelancer's trail, since it
        belongs to no match; a letter's on its match's."""
        if document.match_id is not None:
            entity, entity_id = ENTITY, document.match_id
        else:
            entity, entity_id = FREELANCER, document.freelancer_id
        AdminActionService(self.session).record(
            entity, entity_id, kind, admin_id, {"documento": document.id, "kind": document.kind}
        )

    def _mail_cancellation(self, document: ContractDocument) -> None:
        """The freelancer's own notice that a document already out for signature will
        not be signed after all: their link is dead from now on. A refused or missing
        sender is logged, never raised -- the cancel already happened, and nothing here
        may undo it (REB-407)."""
        if self.sender is None:
            _log.warning("no mail sender: nobody was told document %s was cancelled", document.id)
            return
        user = self._owner(document.freelancer_id)
        mail = signing_cancelled_mail(user.email, user.nome, document.kind, document.numero)
        if not self.sender.send(mail):
            _log.warning(
                "the cancellation mail of document %s was refused by the provider", document.id
            )

    # ---- plumbing ----------------------------------------------------------------------

    def _signer(self) -> Mapping[str, Value]:
        """`REBASE_SIGNER_JSON`, parsed once and cached, and only from here: the two
        paths that typeset (`_dispatch`, `_framework_to_send`) call this before they
        read `self.matches.signer`, so a malformed value 503s the send it actually
        breaks and nothing else (REB-406)."""
        if self._signer_cache is None:
            self._signer_cache = signer_data(self._signer_json)
            self.matches.signer = self._signer_cache
        return self._signer_cache

    def _lock(self, document_id: UUID) -> ContractDocument:
        """The document, row-locked until this transaction ends, and read again from the
        database rather than from the session's memory: another transaction may have
        moved it while this one waited. The caller must already hold the freelancer's
        row lock (`self.matches.lock_freelancer`)."""
        document = self.session.scalars(
            select(ContractDocument)
            .where(ContractDocument.id == document_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if document is None:
            raise NotFound("documento", document_id)
        return document

    def _lock_letter(self, match_id: UUID) -> ContractDocument | None:
        """The caller must already hold the freelancer's row lock
        (`self.matches.lock_freelancer`)."""
        return self.session.scalars(
            select(ContractDocument)
            .where(ContractDocument.match_id == match_id)
            .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()

    def _owner(self, freelancer_id: UUID) -> User:
        user = self.session.scalar(
            select(User)
            .join(Freelancer, Freelancer.user_id == User.id)
            .where(Freelancer.id == freelancer_id)
        )
        if user is None:
            raise NotFound("freelancer", freelancer_id)
        return user

    def _renderer(self) -> Renderer:
        if self.renderer is None:
            raise ContractFailed("no renderer was handed to SigningService")
        return self.renderer

    def _documenso(self) -> DocumensoClient:
        if self.documenso is None:
            raise SigningUnavailable(NO_DOCUMENSO)
        return self.documenso

    def _sender(self) -> EmailSender:
        if self.sender is None:
            raise SigningUnavailable(NO_SENDER)
        return self.sender
