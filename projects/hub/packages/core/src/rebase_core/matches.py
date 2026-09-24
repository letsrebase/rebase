"""Matches: a freelancer card paired with a company request, and the contracts they
write (REB-387, phase 2: generate, never send).

`create` writes in one transaction the match (`bozza`), the letter of engagement with
the year's next number, and the framework agreement when the freelancer has none
active. A letter whose framework agreement is not signed yet waits for it (`in_attesa`,
spec § 1e) and prints its date as a blank line until phase 3 regenerates it on the
signature. A framework agreement generated for an earlier draft and never sent is
replaced, so the newest tax data win; one already out for signature (`inviato`, phase 3)
is waited for instead. A render that fails rolls everything back, the number included.
`preview` renders the same documents and writes nothing: step 5 of «Crea match».

The company's `budget_giornaliero` is read nowhere in this module: what rebase agrees
with the client never reaches a freelancer's document (spec § 1h).
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from rebase_core.audit import AdminActionService, utcnow
from rebase_core.contract_schemas import (
    ClienteDraft,
    ContractPdf,
    FreelancerContracts,
    LetteraDraft,
    MatchCreate,
    MatchList,
    MatchListItem,
    MatchPrefill,
    MatchRead,
)
from rebase_core.contracts.fields import (
    DAYS,
    MONTH_END,
    ContractFailed,
    Value,
    italian_date,
    merge_data,
)
from rebase_core.contracts.render import Renderer, company_defaults, text_version
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.fiscal import FiscalService
from rebase_core.framework import (
    active_framework,
    document_read,
    next_letter_number,
    pending_framework,
    rome_today,
    signed_on,
)
from rebase_core.models import (
    MATCH_STATES,
    Company,
    ContractDocument,
    Freelancer,
    FreelancerFiscal,
    Match,
    User,
)
from rebase_core.search import matches_any

ENTITY = "match"
QUADRO, LETTERA = "quadro", "lettera"
DOCUMENT_BY_KIND = {QUADRO: "contratto-quadro", LETTERA: "lettera-di-incarico"}
SIGNED_ELECTRONICALLY = "firmato elettronicamente"
PEC_MISSING = "non indicata"
DAY_RATE = "a giornata"
LIST_LIMIT_DEFAULT = 100
LIST_LIMIT_MAX = 500


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


def issued_by_rebase(day: date) -> str:
    """What rebase's signature blank prints, since rebase does not sign (spec § 1c)."""
    return f"Documento emesso da rebase il {italian_date(day)}"


def luogo_suggestion(remoto: str, giorni_presenza: int | None) -> str:
    if remoto == "ibrido" and giorni_presenza:
        giornate = "1 giornata" if giorni_presenza == 1 else f"{giorni_presenza} giornate"
        return f"in parte da remoto, con {giornate} a settimana presso il Cliente"
    if remoto == "in_sede":
        return "presso la sede del Cliente"
    return "da remoto"


def _full_name(user: User) -> str:
    return f"{user.nome} {user.cognome}".strip()


def _request_fingerprint(data: MatchCreate) -> str:
    """The SHA-256 of `data` as `create` actually reads it (REB-406): `id` names the
    match, it is not part of what a retry must match again, so it is excluded. Sorted
    keys make the digest the same however Python happened to build the model."""
    canonical = json.dumps(data.model_dump(mode="json", exclude={"id"}), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class MatchService:
    def __init__(
        self,
        session: Session,
        renderer: Renderer | None = None,
        signer: Mapping[str, Value] | None = None,
        today: Callable[[], date] = rome_today,
    ) -> None:
        """`renderer` is needed only to write or preview a document; the reads never
        typeset anything. `signer` is `REBASE_SIGNER_JSON` as `signer_data` read it."""
        self.session = session
        self.renderer = renderer
        self.signer: Mapping[str, Value] = signer or {}
        self.today = today

    # ---- reads -------------------------------------------------------------------------

    def get(self, match_id: UUID) -> MatchRead:
        row = self.session.execute(
            select(Match, Company)
            .join(Company, Company.id == Match.company_id)
            .where(Match.id == match_id)
        ).first()
        if row is None:
            raise NotFound(ENTITY, match_id)
        letter = self.session.scalars(
            select(ContractDocument)
            .where(ContractDocument.match_id == match_id)
            .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
            .limit(1)
        ).first()
        if letter is None:
            raise NotFound("lettera", match_id)
        return self._match_read(row[0], row[1], letter)

    def list_all(
        self,
        *,
        stato: str | None,
        q: str | None,
        limit: int = LIST_LIMIT_DEFAULT,
        offset: int = 0,
    ) -> MatchList:
        """«Match» (REB-413): every match the admin area lists, newest first, one query
        for the freelancer, the company, the creating admin and the letter -- no N+1.
        The letter is the match's newest, the one `get` and `for_freelancer` call its own:
        a match that has more than one (a waiting letter regenerated on the framework's
        signature) is still one row, counted once. A soft-deleted request still names the
        match that came from it, the same as `get`; a soft-deleted freelancer's match is
        gone, the same as `for_freelancer`. `stato` is one of `MATCH_STATES` or a
        `ValidationFailed` naming the field, the same shape a 422 elsewhere in this module
        already takes. Neither `budget_giornaliero` nor a tax field is read here."""
        if stato is not None and stato not in MATCH_STATES:
            raise ValidationFailed(ENTITY, "stato", "stato sconosciuto")
        limit = max(1, min(limit, LIST_LIMIT_MAX))
        offset = max(0, offset)

        freelancer_user = aliased(User)
        admin_user = aliased(User)
        newer = aliased(ContractDocument)
        current_letter = (
            select(newer.id)
            .where(newer.match_id == Match.id, newer.kind == LETTERA)
            .order_by(newer.created_at.desc(), newer.id.desc())
            .limit(1)
            .correlate(Match)
            .scalar_subquery()
        )
        base = (
            select(Match, Company, freelancer_user, admin_user, ContractDocument)
            .join(Company, Company.id == Match.company_id)
            .join(Freelancer, Freelancer.id == Match.freelancer_id)
            .join(freelancer_user, freelancer_user.id == Freelancer.user_id)
            .join(admin_user, admin_user.id == Match.created_by)
            .outerjoin(ContractDocument, ContractDocument.id == current_letter)
            .where(Freelancer.deleted_at.is_(None))
        )
        if stato is not None:
            base = base.where(Match.stato == stato)
        term = (q or "").strip()
        if term:
            base = base.where(
                matches_any(
                    (
                        freelancer_user.nome,
                        freelancer_user.cognome,
                        freelancer_user.email,
                        Company.nome_azienda,
                    ),
                    term,
                )
            )

        totale = self.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = self.session.execute(
            base.order_by(Match.created_at.desc(), Match.id.desc()).limit(limit).offset(offset)
        ).all()
        return MatchList(
            totale=totale,
            items=[
                self._list_item(match, company, freelancer, admin, letter)
                for match, company, freelancer, admin, letter in rows
            ],
        )

    def for_freelancer(self, freelancer_id: UUID) -> FreelancerContracts:
        self._freelancer(freelancer_id)
        today, current = self.today(), text_version(DOCUMENT_BY_KIND[QUADRO])
        frameworks = self.session.scalars(
            select(ContractDocument)
            .where(ContractDocument.kind == QUADRO, ContractDocument.freelancer_id == freelancer_id)
            .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
        )
        quadri = [document_read(document, today, current) for document in frameworks]
        # The active one when there is one (unchanged); else the newest framework that
        # actually left, whatever its state now: an `annullato` one is hidden only when
        # it never left (`sent_at` is `None`, a stale draft «Crea match» replaced, spec
        # § 6), never when a refusal or a cancellation turned a sent one `annullato`.
        shown = next((q for q in quadri if q.attivo), None) or next(
            (q for q in quadri if q.stato != "annullato" or q.sent_at is not None), None
        )
        rows = self.session.execute(
            select(Match, Company)
            .join(Company, Company.id == Match.company_id)
            .where(Match.freelancer_id == freelancer_id)
            .order_by(Match.created_at.desc(), Match.id.desc())
        ).all()
        letters: dict[UUID, ContractDocument] = {}
        if rows:
            for document in self.session.scalars(
                select(ContractDocument)
                .where(ContractDocument.match_id.in_([row[0].id for row in rows]))
                .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
            ):
                if document.match_id is not None:
                    letters.setdefault(document.match_id, document)
        return FreelancerContracts(
            freelancer_id=freelancer_id,
            quadro=shown,
            quadri=quadri,
            matches=[
                self._match_read(match, company, letters[match.id])
                for match, company in rows
                if match.id in letters
            ],
            fiscale=FiscalService(self.session).get(freelancer_id),
        )

    def prefill(self, freelancer_id: UUID, company_id: UUID) -> MatchPrefill:
        """Steps 2 to 4 as the hub can fill them: the saved tax data, the client as the
        same company user's last match named it (else the request's company name), and
        the letter from the request, the card and `rebase.json`."""
        freelancer, _user = self._freelancer(freelancer_id)
        company, referente = self._company(company_id)
        previous = self.session.scalars(
            select(Match)
            .join(Company, Company.id == Match.company_id)
            .where(Company.user_id == company.user_id, Match.stato != "annullato")
            .order_by(Match.created_at.desc(), Match.id.desc())
            .limit(1)
        ).first()
        cliente = (
            ClienteDraft(
                cliente_ragione_sociale=previous.cliente_ragione_sociale,
                cliente_piva=previous.cliente_piva,
                cliente_sede=previous.cliente_sede,
            )
            if previous is not None
            else ClienteDraft(cliente_ragione_sociale=company.nome_azienda)
        )
        defaults = company_defaults()
        days, month_end = defaults.get(DAYS), defaults.get(MONTH_END)
        lettera = LetteraDraft(
            ruolo=company.figura_richiesta,
            attivita=company.progetto,
            data_inizio=company.periodo_da,
            impegno=company.durata,
            luogo=luogo_suggestion(company.remoto, company.giorni_presenza),
            referente_cliente=_full_name(referente) or None,
            modalita=DAY_RATE,
            unita=DAY_RATE,
            compenso=freelancer.tariffa_giornaliera,
            giorni_pagamento=days if isinstance(days, int) and not isinstance(days, bool) else None,
            fine_mese=month_end if isinstance(month_end, bool) else None,
        )
        active = active_framework(self.session, freelancer.id)
        pending = pending_framework(self.session, freelancer.id)
        today = self.today()
        return MatchPrefill(
            fiscale=FiscalService(self.session).get(freelancer.id),
            cliente=cliente,
            lettera=lettera,
            quadro_attivo=(
                document_read(active, today, text_version(DOCUMENT_BY_KIND[QUADRO]))
                if active is not None
                else None
            ),
            quadro_necessario=active is None and (pending is None or pending.stato != "inviato"),
            lettera_in_attesa=active is None,
        )

    def document_pdf(self, document_id: UUID, *, signed: bool = False) -> ContractPdf:
        document = self.session.get(ContractDocument, document_id)
        if document is None:
            raise NotFound("documento", document_id)
        content = document.signed_pdf if signed else document.pdf
        if content is None:
            raise NotFound("documento firmato", document_id)
        base = (
            f"lettera-di-incarico-{document.numero}"
            if document.kind == LETTERA
            else f"contratto-quadro-v{document.text_version}"
        )
        return ContractPdf(filename=f"{base}{'-firmato' if signed else ''}.pdf", content=content)

    # ---- writes ------------------------------------------------------------------------

    def preview(self, freelancer_id: UUID, data: MatchCreate, kind: str) -> ContractPdf:
        """One document as `create` would write it, with no number, and nothing saved."""
        renderer = self._renderer()
        freelancer, user = self._freelancer(freelancer_id)
        self._matchable_company(data.company_id)
        fiscal = self._fiscal(freelancer.id)
        today = self.today()
        if kind == QUADRO:
            rendered = renderer.render(
                DOCUMENT_BY_KIND[QUADRO], self._quadro_data(user, fiscal, today)
            )
            return ContractPdf(filename="anteprima-contratto-quadro.pdf", content=rendered.pdf)
        if kind == LETTERA:
            active = active_framework(self.session, freelancer.id)
            data_fields = self._lettera_data(user, fiscal, data, None, active, today)
            rendered = renderer.render(DOCUMENT_BY_KIND[LETTERA], data_fields)
            return ContractPdf(filename="anteprima-lettera-di-incarico.pdf", content=rendered.pdf)
        raise ValidationFailed(ENTITY, "documento", "uno fra lettera e quadro")

    def create(self, freelancer_id: UUID, data: MatchCreate, admin_id: UUID) -> MatchRead:
        """Two admins racing to match the same freelancer (or one double click on «Salva
        come bozza») must not both read "no active framework" and both write a fresh
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
        response is lost (the wizard sends the same client-generated id with both «Salva
        come bozza» and «Invia per la firma») returns the match already written rather
        than creating a second one with another letter number. The same id already used
        by another freelancer's match is a 409 -- writing under it would silently steal
        someone else's row. The retry must also carry the same request: a SHA-256 of it
        (`_request_fingerprint`) is stored on every match this writes, and a same id with
        a changed one -- an admin who corrected the company or the letter before retrying
        -- is a 409 too, never the stale match returned as if nothing had changed; a
        match with no fingerprint stored (written before this check existed) is treated
        the same as a mismatch, since there is nothing to compare it against."""
        renderer = self._renderer()
        fingerprint = _request_fingerprint(data)
        try:
            self.lock_freelancer(freelancer_id)
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
                    return self.get(existing.id)
            freelancer, user = self._freelancer(freelancer_id)
            company, _referente = self._matchable_company(data.company_id, lock=True)
            fiscal = self._fiscal(freelancer.id)
            today = self.today()
            active = active_framework(self.session, freelancer.id)
            documents: list[ContractDocument] = []
            stale_ids: list[UUID] = []
            if active is None:
                pending = pending_framework(self.session, freelancer.id)
                if pending is None or pending.stato != "inviato":
                    for stale in list(
                        self.session.scalars(
                            select(ContractDocument).where(
                                ContractDocument.kind == QUADRO,
                                ContractDocument.freelancer_id == freelancer.id,
                                ContractDocument.stato == "generato",
                            )
                        )
                    ):
                        stale.stato = "annullato"
                        stale_ids.append(stale.id)
                    # REB-406: the same write `write_framework` makes for a match whose
                    # letter waits on a cancelled or refused one -- `create` already
                    # holds the freelancer's row lock `write_framework` assumes.
                    documents.append(self.write_framework(freelancer.id, admin_id))
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
            )
            self.session.add(match)
            self.session.flush()
            numero = next_letter_number(self.session, today.year)
            documents.append(
                self._document(
                    renderer,
                    LETTERA,
                    self._lettera_data(user, fiscal, data, numero, active, today),
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
        return self.get(match.id)

    def cancel(self, match_id: UUID, admin_id: UUID) -> MatchRead:
        """A draft and its letter become `annullato`; the framework agreement, which is
        the freelancer's and not the match's, stays as it is. `SigningService.
        cancel_match` extends this to a match in signature, which also has an envelope
        to cancel.

        The freelancer's row locks first, then the match, then its letters (the global
        lock order): a cancel racing a send for the same match must not read a stale
        `bozza` and overwrite a letter the send already put out for signature with
        `annullato` while its envelope is still live on Documenso."""
        self.lock_freelancer(self.match_freelancer(match_id))
        match = self.lock_match(match_id)
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
        return self.get(match.id)

    def close(self, match_id: UUID, admin_id: UUID) -> MatchRead:
        """The freelancer's row locks first, then the match, the same order `cancel`
        takes: a close racing a webhook that just turned this match `attivo` (or
        `concluso` again) must re-check its state under the lock, not before it."""
        self.lock_freelancer(self.match_freelancer(match_id))
        match = self.lock_match(match_id)
        if match.stato != "attivo":
            self.session.rollback()
            raise InvalidState(
                f"Si chiude solo un match attivo: questo è {match.stato}.", stato=match.stato
            )
        match.stato = "concluso"
        self.session.commit()
        AdminActionService(self.session).record(ENTITY, match.id, "match_closed", admin_id, {})
        return self.get(match.id)

    # ---- the documents' data -----------------------------------------------------------

    def _rebase_fields(self) -> dict[str, Value]:
        return merge_data(company_defaults(), self.signer)

    def _signing_fields(self, today: date) -> dict[str, Value]:
        """The date and the freelancer's signature stay blank for the signing site; rebase,
        which does not sign, prints who issued the document and when (spec § 5)."""
        return {
            "luogo-firma": SIGNED_ELECTRONICALLY,
            "data-firma": None,
            "firma-rebase": issued_by_rebase(today),
            "firma-professionista": None,
        }

    def _quadro_data(self, user: User, fiscal: FreelancerFiscal, today: date) -> dict[str, Value]:
        return {
            **self._rebase_fields(),
            "professionista-nome": _full_name(user),
            "professionista-cf": fiscal.codice_fiscale,
            "professionista-piva": fiscal.partita_iva,
            "professionista-domicilio": fiscal.domicilio,
            "professionista-email": user.email,
            "professionista-pec": fiscal.pec or PEC_MISSING,
            **self._signing_fields(today),
        }

    def _letter_party_fields(
        self, user: User, fiscal: FreelancerFiscal, signed: date | None
    ) -> dict[str, Value]:
        """A letter's own fields that must always be today's, not the draft's: the
        framework agreement's signature date, the freelancer's name and VAT number.
        Shared by `_lettera_data` (`create`) and `data_for_sending` (a send), so a
        field added to one cannot print stale data on the other (REB-406)."""
        return {
            "data-contratto-quadro": italian_date(signed) if signed is not None else None,
            "professionista-nome": _full_name(user),
            "professionista-piva": fiscal.partita_iva,
        }

    def _lettera_data(
        self,
        user: User,
        fiscal: FreelancerFiscal,
        data: MatchCreate,
        numero: str | None,
        active: ContractDocument | None,
        today: date,
    ) -> dict[str, Value]:
        signed = signed_on(active) if active is not None else None
        return {
            **self._rebase_fields(),
            **data.lettera.to_fields(),
            "numero": numero,
            **self._letter_party_fields(user, fiscal, signed),
            "cliente-ragione-sociale": data.cliente.cliente_ragione_sociale,
            "cliente-piva": data.cliente.cliente_piva,
            "cliente-sede": data.cliente.cliente_sede,
            **self._signing_fields(today),
        }

    def _document(
        self,
        renderer: Renderer,
        kind: str,
        data: dict[str, Value],
        freelancer_id: UUID,
        match_id: UUID | None,
        numero: str | None,
        stato: str,
        admin_id: UUID,
    ) -> ContractDocument:
        rendered = renderer.render(DOCUMENT_BY_KIND[kind], data)
        return ContractDocument(
            kind=kind,
            freelancer_id=freelancer_id,
            match_id=match_id,
            numero=numero,
            text_version=rendered.version,
            testo_bozza=rendered.draft,
            data=dict(data),
            pdf=rendered.pdf,
            stato=stato,
            created_by=admin_id,
        )

    # ---- phase 3: the copy that leaves ---------------------------------------------------

    def data_for_sending(
        self, document: ContractDocument, today: date, framework: ContractDocument | None
    ) -> dict[str, Value]:
        """The fields `document` prints when it goes out for signature (REB-387 phase 3):
        the engagement as the admin wrote it, the parties as they are today (the tax data
        and the name saved since the draft, rebase's signer as the setting says now), the
        day it leaves in rebase's blank, and for a letter the date its framework
        agreement was signed, in Rome."""
        freelancer, user = self._freelancer(document.freelancer_id)
        fiscal = self._fiscal(freelancer.id)
        if document.kind == QUADRO:
            return self._quadro_data(user, fiscal, today)
        signed = signed_on(framework) if framework is not None else None
        return {
            **document.data,
            **self._rebase_fields(),
            **self._letter_party_fields(user, fiscal, signed),
            **self._signing_fields(today),
        }

    def write_framework(self, freelancer_id: UUID, admin_id: UUID) -> ContractDocument:
        """A new framework agreement, added and flushed and not committed: written by
        `create` for a fresh draft, and by `SigningService._framework_to_send` for a
        match whose letter waits on one that was cancelled or refused, sent in the same
        transaction (REB-406: one write, two callers). The caller must already hold the
        freelancer's row lock (REB-406)."""
        renderer = self._renderer()
        freelancer, user = self._freelancer(freelancer_id)
        fiscal = self._fiscal(freelancer.id)
        document = self._document(
            renderer,
            QUADRO,
            self._quadro_data(user, fiscal, self.today()),
            freelancer.id,
            None,
            None,
            "generato",
            admin_id,
        )
        self.session.add(document)
        self.session.flush()
        return document

    # ---- lookups -----------------------------------------------------------------------

    def _match_read(self, match: Match, company: Company, letter: ContractDocument) -> MatchRead:
        return MatchRead(
            id=match.id,
            freelancer_id=match.freelancer_id,
            company_id=match.company_id,
            nome_azienda=company.nome_azienda,
            figura_richiesta=company.figura_richiesta,
            cliente_ragione_sociale=match.cliente_ragione_sociale,
            cliente_piva=match.cliente_piva,
            cliente_sede=match.cliente_sede,
            stato=match.stato,
            created_at=match.created_at,
            created_by=match.created_by,
            cancelled_at=match.cancelled_at,
            updated_at=match.updated_at,
            lettera=document_read(letter, self.today(), text_version(DOCUMENT_BY_KIND[QUADRO])),
        )

    def _list_item(
        self,
        match: Match,
        company: Company,
        freelancer_user: User,
        admin_user: User,
        letter: ContractDocument | None,
    ) -> MatchListItem:
        return MatchListItem(
            id=match.id,
            freelancer_id=match.freelancer_id,
            freelancer_nome=freelancer_user.nome,
            freelancer_cognome=freelancer_user.cognome,
            freelancer_email=freelancer_user.email,
            nome_azienda=company.nome_azienda,
            figura_richiesta=company.figura_richiesta,
            stato=match.stato,
            lettera_numero=letter.numero if letter is not None else None,
            lettera_stato=letter.stato if letter is not None else None,
            lettera_data_inizio=letter.data.get("data-inizio") if letter is not None else None,
            lettera_data_fine=letter.data.get("data-fine") if letter is not None else None,
            created_at=match.created_at,
            created_by_nome=_full_name(admin_user),
            created_by_email=admin_user.email,
        )

    def _renderer(self) -> Renderer:
        if self.renderer is None:
            raise ContractFailed("no renderer was handed to MatchService")
        return self.renderer

    # The lookups below read with `populate_existing`: the session keeps its objects across
    # commits (`expire_on_commit=False`), so a card, a request or tax data it still holds
    # from an earlier read would otherwise come back as they were then, not as `create`
    # must see them under its lock.

    def _freelancer(self, freelancer_id: UUID) -> tuple[Freelancer, User]:
        row = self.session.execute(
            select(Freelancer, User)
            .join(User, User.id == Freelancer.user_id)
            .where(Freelancer.id == freelancer_id)
            .execution_options(populate_existing=True)
        ).first()
        if row is None or row[0].deleted_at is not None:
            raise NotFound("freelancer", freelancer_id)
        return row[0], row[1]

    def _company(self, company_id: UUID, *, lock: bool = False) -> tuple[Company, User]:
        stmt = (
            select(Company, User)
            .join(User, User.id == Company.user_id)
            .where(Company.id == company_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            stmt = stmt.with_for_update(read=True, of=Company)
        row = self.session.execute(stmt).first()
        if row is None or row[0].deleted_at is not None:
            raise NotFound("company", company_id)
        return row[0], row[1]

    def _matchable_company(self, company_id: UUID, *, lock: bool = False) -> tuple[Company, User]:
        company, referente = self._company(company_id, lock=lock)
        if company.stato == "chiuso":
            raise ValidationFailed(ENTITY, "company_id", "la richiesta è chiusa: riaprila prima")
        return company, referente

    def _fiscal(self, freelancer_id: UUID) -> FreelancerFiscal:
        row = self.session.scalar(
            select(FreelancerFiscal)
            .where(FreelancerFiscal.freelancer_id == freelancer_id)
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise ValidationFailed(ENTITY, "fiscale", "mancano i dati fiscali del freelance")
        return row

    def match_freelancer(self, match_id: UUID) -> UUID:
        """The freelancer a match belongs to, read with no lock of its own -- only to
        know which row `lock_freelancer` must take next, the first step of the global
        lock order every write in this module and in `SigningService` follows. Shared
        by `cancel`, `close` and `SigningService`, which calls it through `self.matches`
        rather than keep its own copy (REB-407)."""
        freelancer_id = self.session.scalar(select(Match.freelancer_id).where(Match.id == match_id))
        if freelancer_id is None:
            raise NotFound(ENTITY, match_id)
        return freelancer_id

    def lock_freelancer(self, freelancer_id: UUID) -> None:
        """The freelancer's row, locked until this transaction ends: the first step of
        the global lock order, every other lock in this module and in `SigningService`
        assumes the caller already took. Called by `create`, `cancel` and `close` in
        this module, and by `SigningService` through `self.matches` (REB-407)."""
        self.session.execute(
            select(Freelancer.id).where(Freelancer.id == freelancer_id).with_for_update()
        )

    def lock_match(self, match_id: UUID) -> Match:
        """The match, row-locked until this transaction ends, and read again from the
        database rather than from the session's memory. The caller must already hold
        the freelancer's row lock, the global order. Shared the same way
        `match_freelancer` is (REB-407)."""
        match = self.session.scalars(
            select(Match)
            .where(Match.id == match_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if match is None:
            raise NotFound(ENTITY, match_id)
        return match
