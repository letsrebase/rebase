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
through `REBASE_CONTRACTS_ALLOW_DRAFT`; with every text `status: final` the preview
leaves it false too, kept for the next draft. If Documenso refuses or does not answer, the
transaction rolls back and nothing is marked sent; if only the mail fails, the document
is `inviato` and the report says so, for «Reinvia email».

A freelancer may hold framework agreements of both regimes (REB-687): a letter leaves
only once the framework agreement of its own regime is signed, and a signature releases
only the waiting letters of its own regime. An ordinary letter whose freelancer has lost
the partita IVA since the draft is refused on `regime`, as «Crea match» refuses it.

Every write locks the freelancer's row first, as `MatchService.create` and
`FiscalService.save` already do, then the match, then its document (`match_rows`), because
two admins, the webhook and «Aggiorna stato» can reach the same freelancer's documents at
once. The Documenso calls of `dispatch` run inside the send's transaction, under the
freelancer's row lock: a deliberate trade-off (the send is one admin's click, and a lock
released before the call could not roll the document back when Documenso refuses), the
opposite of `EngagementService.link`, which holds no lock across its call (REB-868).

The webhook's side is `apply`, and `signing.recovery.finish` after it. `apply` locks the
freelancer's row, its match (when it has one) and the document, in that order, and commits,
so Documenso gets its answer long before its ten seconds (probe § 5). A rejection or a
cancellation is moved and recorded there and then, the harm of forging one being small, so
a second delivery of the same event waits on the lock and finds it already `annullato`. A
completion is not moved by `apply`: the webhook's secret travels in clear (probe § 5), so
`apply` only notes which document to confirm and leaves it `inviato` -- a second delivery of
the same `DOCUMENT_COMPLETED`, arriving before the first's own confirmation has run, reads
the same `inviato` row and schedules its own `finish` too.

`SigningService` is split by responsibility (REB-892), the parts built by it: `recovery`
(`signing_recovery.SigningRecovery`: `finish`, the sealed copy, the mails, the framework
agreement's waiting letters, the Pigro link, and `sweep`, the recovery `rebase
contracts-sweep` runs) and `admin` (`signing_admin.SigningAdmin`: «Aggiorna stato»
(`refresh`), «Reinvia email» (`resend_mail`), «Annulla» on a framework agreement
(`cancel_document`) or on a match (`cancel_match`), and «Registra disdetta»
(`record_notice`)). Their sentences are `signing_words`.

`signing_from_settings` builds this service as an environment configures it, the one
builder both the admin API and the admin MCP server use (REB-478).
"""

import logging
from collections.abc import Callable, Mapping
from datetime import date, datetime
from enum import Enum
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService, utcnow
from rebase_core.config import Settings
from rebase_core.contract_schemas import ContractDocumentRead, SendReport
from rebase_core.contracts.fields import ContractFailed, Value, signer_data
from rebase_core.contracts.render import ContractRenderer, Renderer
from rebase_core.documenso import (
    COMPLETED,
    DocumensoClient,
    Outcome,
    client_from_settings,
    fields_from_blanks,
)
from rebase_core.engagements import EngagementService
from rebase_core.errors import InvalidState, NotFound, SigningUnavailable
from rebase_core.framework import (
    active_framework,
    current_quadro_version,
    document_read,
    pending_framework,
    rome_today,
)
from rebase_core.http import HttpCall, urllib_engagements_call
from rebase_core.mail import EmailSender, sender_from_settings
from rebase_core.mail_contracts import document_name, signing_request_mail
from rebase_core.match_rows import (
    ENTITY,
    LETTERA,
    QUADRO,
    fiscal_row,
    full_name,
    lock_freelancer,
    lock_match,
    match_freelancer,
    require_regime_fits,
)
from rebase_core.matches import MatchService
from rebase_core.models import ContractDocument, Freelancer, User
from rebase_core.regimes import document_stem, text_name
from rebase_core.signing_admin import SigningAdmin
from rebase_core.signing_recovery import SigningRecovery
from rebase_core.signing_words import (
    DRAFT_REFUSED,
    NO_DOCUMENSO,
    NO_SENDER,
    cancel_reason,
)

_log = logging.getLogger(__name__)

DEFAULT_CONTRACTS_MAIL = str(Settings.model_fields["contracts_mail"].default)
# What a document must print once it leaves: rebase's, the freelancer's and the client's
# data, and the fields the hub fills itself. What may stay blank is what the signing site
# fills (the signature, the date) and the engagement's optional lines.
MUST_PRINT_PREFIXES = ("rebase-", "professionista-", "cliente-")
MUST_PRINT = frozenset({"numero", "data-contratto-quadro", "luogo-firma", "firma-rebase"})


def _filename(document: ContractDocument, version: str) -> str:
    """The name Documenso keeps and seals as `<name>_signed.pdf` (probe § 4): the
    document's own text, so the two families never share one (REB-687)."""
    return f"{document_stem(document.kind, document.regime, document.numero, version)}.pdf"


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
        engagements: EngagementService | None = None,
    ) -> None:
        """Each collaborator is needed only by the steps that use it: a webhook that
        cancels a document needs no renderer, a draft's cancellation no Documenso, and
        only a signature that turns a match active needs `engagements`, which links it
        to its deal on Pigro (REB-499); without it nothing is linked.

        `signer` is the resolved mapping, for tests that already have one (including an
        explicit `{}`, which the caller means literally: no signer, and no
        `REBASE_SIGNER_JSON` to fall back on). `signer_json` is the raw setting, read
        and cached on first use by a path that actually typesets a document (`dispatch`,
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
        self.engagements = engagements
        self.recovery = SigningRecovery(self)
        self.admin = SigningAdmin(self, self.recovery)

    # ---- «Invia per la firma» ---------------------------------------------------------

    def send_match(self, match_id: UUID, admin_id: UUID) -> SendReport:
        renderer = self.require_renderer()
        self.require_documenso()
        self.require_sender()
        leaving: ContractDocument | None = None
        try:
            freelancer_id = match_freelancer(self.session, match_id)
            # The freelancer's row first, as `MatchService.create` already does, so
            # «Crea match» racing this send waits for it rather than reading a framework
            # this send is about to dispatch as still merely `generato`.
            lock_freelancer(self.session, freelancer_id)
            match = lock_match(self.session, match_id)
            if match.stato not in ("bozza", "in_firma"):
                raise InvalidState(
                    f"Si invia per la firma solo un match in bozza o in firma: questo è "
                    f"{match.stato}.",
                    stato=match.stato,
                )
            letter = self.lock_letter(match.id)
            if letter is None or letter.stato not in ("generato", "in_attesa"):
                raise InvalidState(
                    "La lettera di questo match è già partita, firmata o annullata: non c'è "
                    "nulla da inviare."
                )
            regime = letter.regime
            # The tax data may have changed since the draft: an ordinary letter whose
            # freelancer no longer has a partita IVA is refused as «Crea match» refuses
            # it, before any document is typeset (REB-687).
            require_regime_fits(fiscal_row(self.session, freelancer_id).partita_iva, regime)
            active = active_framework(self.session, match.freelancer_id, regime)
            if not self.allow_draft:
                for kind in (LETTERA,) if active is not None else (QUADRO, LETTERA):
                    if renderer.is_draft(text_name(kind, regime)):
                        raise InvalidState(DRAFT_REFUSED[kind])
            if active is not None:
                leaving = letter
                self.dispatch(letter, active, admin_id)
            else:
                letter.stato = "in_attesa"
                leaving = self._framework_to_send(match.freelancer_id, admin_id, regime)
                if leaving is not None:
                    self.dispatch(leaving, None, admin_id)
            match.stato = "in_firma"
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        sent_kind = leaving.kind if leaving is not None else None
        sent_id = leaving.id if leaving is not None else None
        mailed = self.mail_signing_request(leaving) if leaving is not None else None
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
        return SendReport(
            match=self.matches.reads.get(match_id), inviato=sent_kind, mail_inviata=mailed
        )

    def _framework_to_send(
        self, freelancer_id: UUID, admin_id: UUID, regime: str
    ) -> ContractDocument | None:
        """The framework agreement of `regime` a waiting letter needs, locked: none when
        one is out for signature already (the letter leaves after it), the one generated
        for a draft, else a new one, written now because the last was cancelled or
        refused. The caller must already hold the freelancer's row lock."""
        self.require_signer()
        pending = pending_framework(self.session, freelancer_id, regime)
        if pending is None:
            return self.matches.documents.write_framework(freelancer_id, admin_id, regime)
        framework = self.lock_document(pending.id)
        if framework.stato == "inviato":
            return None
        if framework.stato == "generato":
            return framework
        raise InvalidState(
            "Il contratto quadro è cambiato mentre lo inviavi: ricarica la pagina e riprova."
        )

    def dispatch(
        self, document: ContractDocument, framework: ContractDocument | None, sent_by: UUID
    ) -> None:
        """Typeset `document` as it leaves, hand it to Documenso and record the envelope,
        inside the caller's transaction: nothing here commits, so a refusal at any step
        leaves the document as it was. The caller must already hold the freelancer's row
        lock. If `get` or `distribute` fails after `create` already left an envelope on
        Documenso, a best-effort delete follows it (`_remove_orphan`, REB-406, REB-432;
        a cancel only when the delete itself is refused), so a retried send does not
        pile up drafts under the same externalId."""
        self.require_signer()
        renderer, documenso = self.require_renderer(), self.require_documenso()
        name = text_name(document.kind, document.regime)
        data = self.matches.documents.data_for_sending(document, self.today(), framework)
        rendered = renderer.render(name, data, signing=True)
        if rendered.draft and not self.allow_draft:
            raise InvalidState(DRAFT_REFUSED[document.kind])
        _refuse_blanks(rendered.blank)
        fields = fields_from_blanks(renderer.signature_blanks(name, data, signing=True))
        user = self.owner(document.freelancer_id)
        title = document_name(document.kind, document.numero, document.regime)
        envelope_id = documenso.create(
            title=title[0].upper() + title[1:],
            external_id=str(document.id),
            filename=_filename(document, rendered.version),
            pdf=rendered.pdf,
            signer_email=user.email,
            signer_name=full_name(user),
            fields=fields,
        )
        try:
            envelope = documenso.get(envelope_id)
            signing_url = documenso.distribute(envelope_id)
        except Exception:
            self._remove_orphan(documenso, envelope_id)
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

    def _remove_orphan(self, documenso: DocumensoClient, envelope_id: str) -> None:
        """`get` or `distribute` failed after `create` already left an envelope on
        Documenso: a best-effort delete, so a retried send does not pile up drafts under
        the same externalId. Documenso deletes a draft or a pending envelope outright,
        with no refusal by the envelope's state (its own v2.18.0 source, probe § 4,
        REB-432); a delete this token cannot make -- the envelope already gone, no
        access to it, or any other refusal -- falls back to a cancel, which a still-
        `PENDING` envelope still answers. Both are best effort: a failure of either is
        logged with the envelope id and never raised over the failure the admin already
        sees (REB-406, REB-432)."""
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

    def mail_signing_request(self, document: ContractDocument) -> bool:
        """After the commit: a refused mail leaves the document `inviato`, and says so."""
        if document.signing_url is None or self.sender is None:
            return False
        user = self.owner(document.freelancer_id)
        mail = signing_request_mail(
            user.email,
            user.nome,
            document.kind,
            document.numero,
            document.signing_url,
            regime=document.regime,
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
        lock_freelancer(self.session, freelancer_id)
        if match_id is not None:
            lock_match(self.session, match_id)
        document = self.lock_document(document_id)
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
            document.cancel_reason = cancel_reason(outcome)
        self.session.commit()
        return signed

    def document(self, document_id: UUID) -> ContractDocument:
        document = self.session.get(ContractDocument, document_id, populate_existing=True)
        if document is None:
            raise NotFound("documento", document_id)
        return document

    def read(self, document_id: UUID) -> ContractDocumentRead:
        document = self.document(document_id)
        return document_read(document, self.today(), current_quadro_version(document.regime))

    # ---- plumbing ----------------------------------------------------------------------

    def require_signer(self) -> Mapping[str, Value]:
        """`REBASE_SIGNER_JSON`, parsed once and cached, and only from here: the two
        paths that typeset (`dispatch`, `_framework_to_send`) call this before they
        read `self.matches.documents.signer`, so a malformed value 503s the send it actually
        breaks and nothing else (REB-406)."""
        if self._signer_cache is None:
            self._signer_cache = signer_data(self._signer_json)
            self.matches.documents.signer = self._signer_cache
        return self._signer_cache

    def lock_document(self, document_id: UUID) -> ContractDocument:
        """The document, row-locked until this transaction ends, and read again from the
        database rather than from the session's memory: another transaction may have
        moved it while this one waited. The caller must already hold the freelancer's
        row lock (`match_rows.lock_freelancer`)."""
        document = self.session.scalars(
            select(ContractDocument)
            .where(ContractDocument.id == document_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if document is None:
            raise NotFound("documento", document_id)
        return document

    def lock_letter(self, match_id: UUID) -> ContractDocument | None:
        """The caller must already hold the freelancer's row lock
        (`match_rows.lock_freelancer`)."""
        return self.session.scalars(
            select(ContractDocument)
            .where(ContractDocument.match_id == match_id)
            .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()

    def owner(self, freelancer_id: UUID) -> User:
        user = self.session.scalar(
            select(User)
            .join(Freelancer, Freelancer.user_id == User.id)
            .where(Freelancer.id == freelancer_id)
        )
        if user is None:
            raise NotFound("freelancer", freelancer_id)
        return user

    def require_renderer(self) -> Renderer:
        if self.renderer is None:
            raise ContractFailed("no renderer was handed to SigningService")
        return self.renderer

    def require_documenso(self) -> DocumensoClient:
        if self.documenso is None:
            raise SigningUnavailable(NO_DOCUMENSO)
        return self.documenso

    def require_sender(self) -> EmailSender:
        if self.sender is None:
            raise SigningUnavailable(NO_SENDER)
        return self.sender


SigningFactory = Callable[[Session], SigningService]


class _FromSettings(Enum):
    """`signing_from_settings`' own default: a collaborator not handed in is built from
    the settings, while `None` handed in means that one is off."""

    TOKEN = "from settings"


FROM_SETTINGS: Final = _FromSettings.TOKEN


def signing_from_settings(
    settings: Settings,
    renderer: Renderer | None = None,
    *,
    documenso: DocumensoClient | None | _FromSettings = FROM_SETTINGS,
    sender: EmailSender | None | _FromSettings = FROM_SETTINGS,
    http: HttpCall = urllib_engagements_call,
) -> SigningFactory:
    """`SigningService` as this environment configures it, for any session: the one
    builder the admin API's `get_signing_factory` and the admin MCP server both use
    (REB-478), so a setting reaches both doors or neither. The renderer defaults to
    `ContractRenderer`; Documenso and the mail sender are built from the settings unless
    handed in (the API hands its own dependencies, which a test overrides), and `None`
    handed in means off. `REBASE_SIGNER_JSON` goes over raw: `SigningService` parses it
    only where a document is about to be typeset, so a malformed value fails the send it
    breaks and nothing else (REB-406). The engagement service that links an active match
    to Pigro (REB-499) is built beside it, on the same session and with the same mail
    sender, reading through `http`: `urllib_engagements_call` unless handed in (the API
    hands its own `HttpCallDep`, which a test overrides)."""
    typesetter = renderer if renderer is not None else ContractRenderer()

    def build(session: Session) -> SigningService:
        mailer = sender_from_settings(settings) if isinstance(sender, _FromSettings) else sender
        return SigningService(
            session,
            renderer=typesetter,
            documenso=(
                client_from_settings(settings)
                if isinstance(documenso, _FromSettings)
                else documenso
            ),
            sender=mailer,
            signer_json=settings.signer_json,
            contracts_mail=settings.contracts_mail,
            allow_draft=settings.contracts_allow_draft,
            engagements=EngagementService(session, settings, http, sender=mailer),
        )

    return build
