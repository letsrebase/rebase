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

from collections.abc import Callable, Mapping
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService, utcnow
from rebase_core.contract_schemas import (
    ClienteDraft,
    ContractPdf,
    FreelancerContracts,
    LetteraDraft,
    MatchCreate,
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
from rebase_core.models import Company, ContractDocument, Freelancer, FreelancerFiscal, Match, User

ENTITY = "match"
QUADRO, LETTERA = "quadro", "lettera"
DOCUMENT_BY_KIND = {QUADRO: "contratto-quadro", LETTERA: "lettera-di-incarico"}
SIGNED_ELECTRONICALLY = "firmato elettronicamente"
PEC_MISSING = "non indicata"
DAY_RATE = "a giornata"


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

    def for_freelancer(self, freelancer_id: UUID) -> FreelancerContracts:
        self._freelancer(freelancer_id)
        today, current = self.today(), text_version(DOCUMENT_BY_KIND[QUADRO])
        frameworks = self.session.scalars(
            select(ContractDocument)
            .where(ContractDocument.kind == QUADRO, ContractDocument.freelancer_id == freelancer_id)
            .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
        )
        quadri = [document_read(document, today, current) for document in frameworks]
        shown = next((q for q in quadri if q.attivo), None) or next(
            (q for q in quadri if q.stato != "annullato"), None
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
        `active_framework`/`pending_framework`, for the first to commit or roll back. The
        lock order is always the freelancer row first and only then the letter counter's
        row (`next_letter_number`), so two of these transactions can never deadlock on each
        other. The counter's own row lock is held on purpose through the letter's render,
        for gapless numbering (see `test_a_render_that_fails_takes_no_number_and_leaves_
        nothing_behind`) -- nobody may move the render or the number-taking earlier to
        "speed this up"."""
        renderer = self._renderer()
        freelancer, user = self._freelancer(freelancer_id)
        company, _referente = self._matchable_company(data.company_id)
        fiscal = self._fiscal(freelancer.id)
        today = self.today()
        try:
            self.session.execute(
                select(Freelancer.id).where(Freelancer.id == freelancer.id).with_for_update()
            )
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
                    documents.append(
                        self._document(
                            renderer,
                            QUADRO,
                            self._quadro_data(user, fiscal, today),
                            freelancer.id,
                            None,
                            None,
                            "generato",
                            admin_id,
                        )
                    )
            match = Match(
                freelancer_id=freelancer.id,
                company_id=company.id,
                cliente_ragione_sociale=data.cliente.cliente_ragione_sociale,
                cliente_piva=data.cliente.cliente_piva,
                cliente_sede=data.cliente.cliente_sede,
                stato="bozza",
                created_by=admin_id,
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
        the freelancer's and not the match's, stays as it is. Phase 3 extends this to a
        match in signature, which also has an envelope to cancel."""
        match = self._match(match_id)
        if match.stato != "bozza":
            raise InvalidState(
                f"Si annulla solo un match in bozza: questo è {match.stato}.", stato=match.stato
            )
        letters = list(
            self.session.scalars(
                select(ContractDocument).where(
                    ContractDocument.match_id == match.id,
                    ContractDocument.stato.in_(("generato", "in_attesa")),
                )
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
        match = self._match(match_id)
        if match.stato != "attivo":
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
            "data-contratto-quadro": italian_date(signed) if signed is not None else None,
            "professionista-nome": _full_name(user),
            "professionista-piva": fiscal.partita_iva,
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

    def _renderer(self) -> Renderer:
        if self.renderer is None:
            raise ContractFailed("no renderer was handed to MatchService")
        return self.renderer

    def _freelancer(self, freelancer_id: UUID) -> tuple[Freelancer, User]:
        row = self.session.execute(
            select(Freelancer, User)
            .join(User, User.id == Freelancer.user_id)
            .where(Freelancer.id == freelancer_id)
        ).first()
        if row is None or row[0].deleted_at is not None:
            raise NotFound("freelancer", freelancer_id)
        return row[0], row[1]

    def _company(self, company_id: UUID) -> tuple[Company, User]:
        row = self.session.execute(
            select(Company, User)
            .join(User, User.id == Company.user_id)
            .where(Company.id == company_id)
        ).first()
        if row is None or row[0].deleted_at is not None:
            raise NotFound("company", company_id)
        return row[0], row[1]

    def _matchable_company(self, company_id: UUID) -> tuple[Company, User]:
        company, referente = self._company(company_id)
        if company.stato == "chiuso":
            raise ValidationFailed(ENTITY, "company_id", "la richiesta è chiusa: riaprila prima")
        return company, referente

    def _fiscal(self, freelancer_id: UUID) -> FreelancerFiscal:
        row = self.session.scalar(
            select(FreelancerFiscal).where(FreelancerFiscal.freelancer_id == freelancer_id)
        )
        if row is None:
            raise ValidationFailed(ENTITY, "fiscale", "mancano i dati fiscali del freelance")
        return row

    def _match(self, match_id: UUID) -> Match:
        match = self.session.get(Match, match_id)
        if match is None:
            raise NotFound(ENTITY, match_id)
        return match
