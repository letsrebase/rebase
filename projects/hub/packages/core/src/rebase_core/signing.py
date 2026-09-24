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
through `REBASE_CONTRACTS_ALLOW_DRAFT`. If Documenso refuses or does not answer, the
transaction rolls back and nothing is marked sent; if only the mail fails, the document
is `inviato` and the report says so, for «Reinvia email».

Every write locks the freelancer's row first, as `MatchService.create` and
`FiscalService.save` already do, then the match, then its document, because two admins,
the webhook and «Aggiorna stato» can reach the same freelancer's documents at once.

The webhook's side is `apply` and `finish`. `apply` only locks the document, moves it
and commits, so Documenso gets its answer long before its ten seconds (probe § 5), and a
second delivery of the same event, which can arrive while the first is still running,
waits on the lock and finds nothing left to do. `finish` runs after that commit, in the
webhook's background task or under «Aggiorna stato»: the sealed copy downloaded, stored
and mailed to both parties once, then, for a framework agreement, the letters that
waited for it typeset with its signature date and sent. Each step checks under the lock
whether it is still to do, so running `finish` twice does everything once.
"""

import logging
from collections.abc import Callable, Mapping
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService, utcnow
from rebase_core.config import Settings
from rebase_core.contract_schemas import SendReport
from rebase_core.contracts.fields import ContractFailed, Value, signer_data
from rebase_core.contracts.render import Renderer
from rebase_core.documenso import COMPLETED, REJECTED, DocumensoClient, Outcome, fields_from_blanks
from rebase_core.errors import DocumensoFailed, InvalidState, NotFound, SigningUnavailable
from rebase_core.framework import active_framework, is_active, pending_framework, rome_today
from rebase_core.mail import (
    Attachment,
    EmailSender,
    document_name,
    signed_copy_mail,
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
        renderer (REB-406 fix round 1, I1)."""
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
            freelancer_id = self.session.scalar(
                select(Match.freelancer_id).where(Match.id == match_id)
            )
            if freelancer_id is None:
                raise NotFound(ENTITY, match_id)
            # The freelancer's row first, as `MatchService.create` already does, so
            # «Crea match» racing this send waits for it rather than reading a framework
            # this send is about to dispatch as still merely `generato`.
            self._lock_freelancer(freelancer_id)
            match = self._lock_match(match_id)
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
        Documenso, a best-effort cancel follows it (`_cancel_orphan`, REB-406 fix round
        1, M11), so a retried send does not pile up drafts under the same externalId."""
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
        sees (REB-406 fix round 1, M11)."""
        try:
            documenso.cancel(envelope_id, "invio non completato: annullo l'envelope orfano")
        except Exception:
            _log.warning("could not cancel the orphaned envelope %s", envelope_id, exc_info=True)

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
        alone. No call leaves this method. Returns the id of a document that has just
        been signed, for `finish` after the commit; `None` otherwise."""
        found = self.session.execute(
            select(
                ContractDocument.id, ContractDocument.freelancer_id, ContractDocument.match_id
            ).where(ContractDocument.documenso_id == outcome.envelope_id)
        ).first()
        if found is None:
            self.session.rollback()
            return None
        document_id, freelancer_id, match_id = found
        self._lock_freelancer(freelancer_id)
        match = self._lock_match(match_id) if match_id is not None else None
        document = self._lock(document_id)
        if document.stato != "inviato":
            self.session.rollback()
            return None
        signed: UUID | None = None
        if outcome.kind == COMPLETED:
            document.stato = "firmato"
            # The signer's own date; the moment the hub heard of it only if Documenso
            # said nothing, which a completed envelope never does.
            document.signed_at = outcome.signed_at or self.now()
            if match is not None and match.stato == "in_firma":
                match.stato = "attivo"
            signed = document.id
        else:
            document.stato = "annullato"
            document.cancel_reason = _cancel_reason(outcome)
        self.session.commit()
        return signed

    def finish(self, document_id: UUID) -> None:
        """What a signature leaves to do once it is committed, each step idempotent: the
        sealed copy downloaded, stored and mailed once; for an active framework agreement,
        the letters that waited for it released. A step that fails is logged and left for
        the next call (the next «Aggiorna stato»); the others still run."""
        try:
            stored = self._store_signed_copy(document_id)
        except (DocumensoFailed, SigningUnavailable, NotFound):
            self.session.rollback()
            _log.warning(
                "the signed copy of document %s is not stored yet", document_id, exc_info=True
            )
            stored = None
        if stored is not None:
            self._mail_signed_copy(stored)
        document = self.session.get(ContractDocument, document_id, populate_existing=True)
        if document is not None and is_active(document):
            self._release_letters(document)

    def _store_signed_copy(self, document_id: UUID) -> ContractDocument | None:
        """The download happens under the row's lock, so two callers download once: the
        second waits, then finds the copy stored. `None` when there is nothing to store.
        The freelancer's row is deliberately not locked here (amendment 3, out of the
        global order): this method holds one row lock and makes a network call (the
        download), so it cannot join a lock cycle, and taking the freelancer's row too
        would block «Crea match» for that freelancer for as long as Documenso takes."""
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

    def _mail_signed_copy(self, document: ContractDocument) -> None:
        """To the freelancer and to rebase's contracts address, each with the sealed copy.
        A refusal is logged: the copy stays on «Match e contratti» and in «Contratti»."""
        if self.sender is None:
            _log.warning(
                "no mail sender: the signed copy of document %s was not mailed", document.id
            )
            return
        user = self._owner(document.freelancer_id)
        pdf = self.matches.document_pdf(document.id, signed=True)
        attachment = Attachment(filename=pdf.filename, content=pdf.content)
        for to, for_rebase in ((user.email, False), (self.contracts_mail, True)):
            mail = signed_copy_mail(
                to,
                kind=document.kind,
                numero=document.numero,
                attachment=attachment,
                nome=user.nome,
                cognome=user.cognome,
                for_rebase=for_rebase,
            )
            if not self.sender.send(mail):
                _log.warning(
                    "the signed copy of document %s was refused by the provider", document.id
                )

    def _release_letters(self, framework: ContractDocument) -> None:
        """The letters that waited for this framework agreement (spec § 1e), each in a
        transaction of its own and mailed after its commit: a Documenso refusal leaves
        that letter waiting for the next `finish` and lets the others go. Only matches an
        admin sent (`in_firma`); a draft's letter leaves when its match is sent."""
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
                self._mail_signing_request(letter)

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
        self._lock_freelancer(freelancer_id)
        match = self._lock_match(match_id)
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

    # ---- plumbing ----------------------------------------------------------------------

    def _signer(self) -> Mapping[str, Value]:
        """`REBASE_SIGNER_JSON`, parsed once and cached, and only from here: the two
        paths that typeset (`_dispatch`, `_framework_to_send`) call this before they
        read `self.matches.signer`, so a malformed value 503s the send it actually
        breaks and nothing else (REB-406 fix round 1, I1)."""
        if self._signer_cache is None:
            self._signer_cache = signer_data(self._signer_json)
            self.matches.signer = self._signer_cache
        return self._signer_cache

    def _lock_freelancer(self, freelancer_id: UUID) -> None:
        """The freelancer's row, locked until this transaction ends: every other lock
        below (`_lock`, `_lock_match`, `_lock_letter`) and `MatchService.write_framework`
        assume the caller already took this one first (REB-406 fix round 1, M8)."""
        self.session.execute(
            select(Freelancer.id).where(Freelancer.id == freelancer_id).with_for_update()
        )

    def _lock(self, document_id: UUID) -> ContractDocument:
        """The document, row-locked until this transaction ends, and read again from the
        database rather than from the session's memory: another transaction may have
        moved it while this one waited. The caller must already hold the freelancer's
        row lock (`_lock_freelancer`)."""
        document = self.session.scalars(
            select(ContractDocument)
            .where(ContractDocument.id == document_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if document is None:
            raise NotFound("documento", document_id)
        return document

    def _lock_match(self, match_id: UUID) -> Match:
        """The caller must already hold the freelancer's row lock (`_lock_freelancer`)."""
        match = self.session.scalars(
            select(Match)
            .where(Match.id == match_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if match is None:
            raise NotFound(ENTITY, match_id)
        return match

    def _lock_letter(self, match_id: UUID) -> ContractDocument | None:
        """The caller must already hold the freelancer's row lock (`_lock_freelancer`)."""
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
