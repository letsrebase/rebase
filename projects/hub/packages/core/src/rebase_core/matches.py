"""Matches: a freelancer card paired with a company request, and the contracts they
write (REB-387, phase 2: generate, never send).

A match's regime is its letter's (REB-687), `partita_iva` or `occasionale`, chosen on
«Crea match» and proposed from the tax data: every framework lookup here takes it, so an
ordinary framework agreement never stands in for an occasional one, nor the reverse. An
ordinary letter needs a saved partita IVA (`require_regime_fits`).

`create` writes in one transaction the match (`bozza`), the letter of engagement with
the year's next number, and the framework agreement when the freelancer has none
active. A letter whose framework agreement is not signed yet waits for it (`in_attesa`,
spec § 1e) and prints its date as a blank line until phase 3 regenerates it on the
signature. A framework agreement generated for an earlier draft and never sent is
replaced, so the newest tax data win; one already out for signature (`inviato`, phase 3)
is waited for instead. A render that fails rolls everything back, the number included.

`MatchService` writes: `create`, `cancel`, `close`. What it is built from is split by
responsibility (REB-892): `reads` (`match_reads.MatchReads`: one match, the list, a
freelancer's contracts, a PDF), `drafts` (`match_drafts.MatchDrafts`: `prefill`, `proposal`,
`preview` and `check`, which write and number nothing) and `documents`
(`match_documents.MatchDocuments`: the data the documents print, and the framework agreement
written for a freelancer who has none). The shared rows and the lock order are
`match_rows`.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService, utcnow
from rebase_core.contract_schemas import MatchCreate, MatchRead
from rebase_core.contracts.fields import Value
from rebase_core.contracts.render import Renderer
from rebase_core.errors import InvalidState, NotFound
from rebase_core.framework import (
    active_framework,
    next_letter_number,
    pending_framework,
    rome_today,
)
from rebase_core.match_documents import MatchDocuments
from rebase_core.match_drafts import MatchDrafts
from rebase_core.match_reads import MatchReads
from rebase_core.match_rows import (
    DOCUMENT_ENTITY,
    ENTITY,
    LETTERA,
    QUADRO,
    fiscal_row,
    freelancer_row,
    lock_freelancer,
    lock_match,
    match_freelancer,
    matchable_company,
    require_regime_fits,
)
from rebase_core.models import ContractDocument, Freelancer, Match
from rebase_core.regimes import PARTITA_IVA


def require_live_freelancer(
    session: Session, freelancer_id: UUID, entity: str, identifier: UUID
) -> None:
    """`get` and `document_pdf` read no further than the row itself -- a match or a
    document of a soft-deleted freelancer is not gone on its own, which is why this
    guard exists: the caller enforces liveness, naming its own entity and identifier (a
    match id, a document id) so the refusal answers the thing that was actually asked
    for, not the freelancer underneath it. Used by both the admin API
    (`routers/matches.py`) and the admin MCP server, since a soft-deleted freelancer's
    match must read as «match not found» wherever it is asked from (REB-417)."""
    freelancer = session.get(Freelancer, freelancer_id)
    if freelancer is None or freelancer.deleted_at is not None:
        raise NotFound(entity, identifier)


def require_live_match(session: Session, match_id: UUID) -> Match:
    """The guard before a match's action or read: «match ... non trovato» when the match
    itself is gone or its freelancer is soft-deleted, before the service is reached. The
    admin API's match routes and the admin MCP server's match tools both run this one
    (REB-478)."""
    match = session.get(Match, match_id)
    if match is None:
        raise NotFound(ENTITY, match_id)
    require_live_freelancer(session, match.freelancer_id, ENTITY, match_id)
    return match


def require_live_document(session: Session, document_id: UUID) -> ContractDocument:
    """The guard before a document's action or its PDF: «documento ... non trovato» when
    the document itself is gone or its freelancer is soft-deleted, before the service is
    reached. The admin API's contract routes and the admin MCP server's contract tools
    both run this one (REB-478), so the two doors answer the same."""
    document = session.get(ContractDocument, document_id)
    if document is None:
        raise NotFound(DOCUMENT_ENTITY, document_id)
    require_live_freelancer(session, document.freelancer_id, DOCUMENT_ENTITY, document_id)
    return document


def _request_fingerprint(data: MatchCreate) -> str:
    """The SHA-256 of `data` as `create` actually reads it (REB-406): `id` names the
    match, it is not part of what a retry must match again, so it is excluded. Sorted
    keys make the digest the same however Python happened to build the model. An
    ordinary letter leaves `regime` out (REB-687), so its digest is the one stored
    before the field existed and a retry across the release still finds its match."""
    exclude = {"id", "regime"} if data.regime == PARTITA_IVA else {"id"}
    canonical = json.dumps(data.model_dump(mode="json", exclude=exclude), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


MATCH_ID_CONSTRAINT = "matches_pkey"


def _violates_match_id(exc: IntegrityError) -> bool:
    """Whether an `IntegrityError` at the match's own flush is the id's own primary key
    (`matches_pkey`, migration 0017's `id UUID PRIMARY KEY`) -- the one thing two
    different freelancers' `create()` calls can genuinely race past the id check on
    (REB-433). Read from the DBAPI's own diagnostics (`psycopg`'s `.diag`), not from
    the message, which is English and not meant for an admin. Any other integrity
    error at that same flush (a freelancer or a company gone missing mid-transaction,
    say) is not this race and must not be swallowed into the same sentence."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == MATCH_ID_CONSTRAINT


class MatchService:
    def __init__(
        self,
        session: Session,
        renderer: Renderer | None = None,
        signer: Mapping[str, Value] | None = None,
        today: Callable[[], date] = rome_today,
        *,
        pigro_configurato: bool = True,
    ) -> None:
        """`renderer` is needed only to write or preview a document; the reads never
        typeset anything. `signer` is `REBASE_SIGNER_JSON` as `signer_data` read it.
        `pigro_configurato` is whether `REBASE_PIGRO_ENGAGEMENTS_TOKEN` is set: without
        it an active match's card says so and offers no «Riprova su Pigro» (spec § 3.2);
        the API and the MCP server pass it, the tests may leave it on."""
        self.session = session
        self.documents = MatchDocuments(session, renderer, signer, today)
        self.reads = MatchReads(session, today, pigro_configurato=pigro_configurato)
        self.drafts = MatchDrafts(session, self.documents)

    def create(self, freelancer_id: UUID, data: MatchCreate, admin_id: UUID) -> MatchRead:
        """Two admins racing to match the same freelancer (or one double click on «Salva
        senza inviare») must not both read "no active framework" and both write a fresh
        `generato` one: the first statement of the transaction locks the freelancer's own
        row (`SELECT ... FOR UPDATE`), so the second waits here, before it reads
        `active_framework`/`pending_framework`, for the first to commit or roll back.
        Everything the documents print is read after that lock, never before it: the card,
        the tax data (`FiscalService.save` takes the same lock first, so they cannot change
        until this commits) and the request, read under a shared lock (`FOR SHARE`) that
        makes an admin closing it wait for this draft rather than have it saved for a
        request already closed. The lock order is always the freelancer row, then the
        request's, and only then the letter counter's row (`next_letter_number`), so two of
        these transactions can never deadlock on each other. The counter's own row lock is
        held on purpose through the letter's render, for gapless numbering (see
        `test_a_render_that_fails_takes_no_number_and_leaves_nothing_behind`) -- nobody
        may move the render or the number-taking earlier to "speed this up".

        `data.id`, when given, makes this call idempotent (REB-406): a retry after the
        response is lost (the wizard sends the same client-generated id with both
        «Salva senza inviare» and «Invia per la firma») returns the match already
        written rather than creating a second one with another letter number. The same
        id already used by another freelancer's match is a 409 -- writing under it would
        silently steal someone else's row. The retry must also carry the same request: a
        SHA-256 of it (`_request_fingerprint`) is stored on every match this writes, and
        a same id with a changed one -- an admin who corrected the company or the letter
        before retrying -- is a 409 too, never the stale match returned as if nothing
        had changed; a match with no fingerprint stored (written before this check
        existed) is treated the same as a mismatch, since there is nothing to compare it
        against.

        Two different freelancers' browsers sending the same id at the same moment can
        both read "nothing written yet" here, since neither has committed: the id's own
        primary key is what actually catches the second one, at the flush right below,
        never a 500 -- an `IntegrityError` there, but only when it is that same
        primary key (`_violates_match_id`), is the same 409 the check above gives; any
        other integrity error at that flush is not this race and is left to propagate,
        an admin's own failure, not a freelancer's (REB-433)."""
        renderer = self.documents.require_renderer()
        fingerprint = _request_fingerprint(data)
        try:
            lock_freelancer(self.session, freelancer_id)
            if data.id is not None:
                existing = self.session.get(Match, data.id, populate_existing=True)
                if existing is not None:
                    if existing.freelancer_id != freelancer_id:
                        raise InvalidState(
                            "Questo id di match appartiene già a un altro freelance."
                        )
                    if existing.request_fingerprint != fingerprint:
                        raise InvalidState(
                            "Questo match è già stato salvato con dati diversi: aprilo da "
                            "«Match e contratti» e controllalo prima di inviarlo."
                        )
                    self.session.rollback()
                    return self.reads.get(existing.id)
            freelancer, user = freelancer_row(self.session, freelancer_id)
            company, _referente = matchable_company(self.session, data.company_id, lock=True)
            fiscal = fiscal_row(self.session, freelancer.id)
            require_regime_fits(fiscal.partita_iva, data.regime)
            today = self.documents.today()
            active = active_framework(self.session, freelancer.id, data.regime)
            documents: list[ContractDocument] = []
            stale_ids: list[UUID] = []
            if active is None:
                pending = pending_framework(self.session, freelancer.id, data.regime)
                if pending is None or pending.stato != "inviato":
                    for stale in list(
                        self.session.scalars(
                            select(ContractDocument).where(
                                ContractDocument.kind == QUADRO,
                                ContractDocument.freelancer_id == freelancer.id,
                                ContractDocument.regime == data.regime,
                                ContractDocument.stato == "generato",
                            )
                        )
                    ):
                        stale.stato = "annullato"
                        stale_ids.append(stale.id)
                    # REB-406: the same write `write_framework` makes for a match whose
                    # letter waits on a cancelled or refused one -- `create` already
                    # holds the freelancer's row lock `write_framework` assumes.
                    documents.append(
                        self.documents.write_framework(freelancer.id, admin_id, data.regime)
                    )
            match = Match(
                **({"id": data.id} if data.id is not None else {}),
                freelancer_id=freelancer.id,
                company_id=company.id,
                cliente_ragione_sociale=data.cliente.cliente_ragione_sociale,
                cliente_piva=data.cliente.cliente_piva,
                cliente_sede=data.cliente.cliente_sede,
                stato="bozza",
                created_by=admin_id,
                request_fingerprint=fingerprint,
                giorni_previsti=data.giorni_previsti,
                lettera_data_inizio=data.lettera.data_inizio,
                lettera_data_fine=data.lettera.data_fine,
                lettera_compenso=data.lettera.compenso,
                # A referral reward reads this later, at signing, which can be weeks
                # after the request the budget belonged to (P-REB-44); snapshotted
                # here for the same reason `lettera_compenso` already is, so an edit
                # to the company's own request afterward never changes what a reward
                # already promised.
                company_budget_giornaliero=company.budget_giornaliero,
            )
            self.session.add(match)
            try:
                self.session.flush()
            except IntegrityError as exc:
                if data.id is None or not _violates_match_id(exc):
                    raise
                # The id check above read "nothing written yet" because the other
                # freelancer's write had not committed either: the primary key is the
                # one thing that actually serializes the two, and it is this insert
                # that loses the race, not the winner's (REB-433).
                self.session.rollback()
                raise InvalidState(
                    "Questo id di match appartiene già a un altro freelance."
                ) from exc
            numero = next_letter_number(self.session, today.year)
            documents.append(
                self.documents.document(
                    renderer,
                    LETTERA,
                    data.regime,
                    self.documents.lettera_data(user, fiscal, data, numero, active, today),
                    freelancer.id,
                    match.id,
                    numero,
                    "generato" if active is not None else "in_attesa",
                    admin_id,
                )
            )
            self.session.add_all(documents)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        AdminActionService(self.session).record(
            ENTITY,
            match.id,
            "match_created",
            admin_id,
            {
                "company_id": company.id,
                "numero": numero,
                "documenti": [d.id for d in documents],
                "quadri_annullati": stale_ids,
            },
        )
        return self.reads.get(match.id)

    def cancel(self, match_id: UUID, admin_id: UUID) -> MatchRead:
        """A draft and its letter become `annullato`; the framework agreement, which is
        the freelancer's and not the match's, stays as it is. `SigningService.
        cancel_match` extends this to a match in signature, which also has an envelope
        to cancel.

        The freelancer's row locks first, then the match, then its letters (the global
        lock order): a cancel racing a send for the same match must not read a stale
        `bozza` and overwrite a letter the send already put out for signature with
        `annullato` while its envelope is still live on Documenso."""
        lock_freelancer(self.session, match_freelancer(self.session, match_id))
        match = lock_match(self.session, match_id)
        if match.stato != "bozza":
            self.session.rollback()
            raise InvalidState(
                f"Si annulla solo un match in bozza: questo è {match.stato}.", stato=match.stato
            )
        letters = list(
            self.session.scalars(
                select(ContractDocument)
                .where(
                    ContractDocument.match_id == match.id,
                    ContractDocument.stato.in_(("generato", "in_attesa")),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        match.stato = "annullato"
        match.cancelled_at = utcnow()
        for letter in letters:
            letter.stato = "annullato"
        self.session.commit()
        AdminActionService(self.session).record(
            ENTITY, match.id, "match_cancelled", admin_id, {"documenti": [d.id for d in letters]}
        )
        return self.reads.get(match.id)

    def close(self, match_id: UUID, admin_id: UUID) -> MatchRead:
        """The freelancer's row locks first, then the match, the same order `cancel`
        takes: a close racing a webhook that just turned this match `attivo` (or
        `concluso` again) must re-check its state under the lock, not before it."""
        lock_freelancer(self.session, match_freelancer(self.session, match_id))
        match = lock_match(self.session, match_id)
        if match.stato != "attivo":
            self.session.rollback()
            raise InvalidState(
                f"Si chiude solo un match attivo: questo è {match.stato}.", stato=match.stato
            )
        match.stato = "concluso"
        self.session.commit()
        AdminActionService(self.session).record(ENTITY, match.id, "match_closed", admin_id, {})
        return self.reads.get(match.id)
