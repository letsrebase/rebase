"""Matches as the admin area reads them: one match, the list, a freelancer's contracts,
and a document's PDF (REB-413, REB-892).

`MatchReads` writes nothing and typesets nothing. Each row's sentence needs where its
freelancer's framework agreement stands (`framework_states`), read for a whole page in one
query, never one per row.
"""

from collections.abc import Callable
from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from rebase_core.contract_schemas import (
    ContractDocumentRead,
    ContractPdf,
    FreelancerContracts,
    MatchList,
    MatchListItem,
    MatchRead,
)
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.fiscal import FiscalService
from rebase_core.framework import (
    current_quadro_version,
    document_facts,
    document_read,
    framework_state,
    framework_states,
    rome_today,
)
from rebase_core.match_rows import ENTITY, LETTERA, QUADRO, freelancer_row, full_name
from rebase_core.match_words import (
    MATCH_STATE_LABELS,
    DocumentFacts,
    Words,
    match_words,
)
from rebase_core.models import MATCH_STATES, Company, ContractDocument, Freelancer, Match, User
from rebase_core.referral_schemas import MatchReferral
from rebase_core.referrals import ReferralService, letter_unit
from rebase_core.regimes import OCCASIONALE, PARTITA_IVA, document_stem
from rebase_core.search import matches_any

LIST_LIMIT_DEFAULT = 100
LIST_LIMIT_MAX = 500


def shown_framework(quadri: list[ContractDocumentRead], regime: str) -> ContractDocumentRead | None:
    """The framework agreement of `regime` «Match e contratti» shows, one card per regime
    (REB-687): the active one when there is one; else the newest that actually left,
    whatever its state now. An `annullato` one is hidden only when it never left
    (`sent_at` is `None`, a stale draft «Crea match» replaced, spec § 6), never when a
    refusal or a cancellation turned a sent one `annullato`."""
    mine = [quadro for quadro in quadri if quadro.regime == regime]
    return next((quadro for quadro in mine if quadro.attivo), None) or next(
        (quadro for quadro in mine if quadro.stato != "annullato" or quadro.sent_at is not None),
        None,
    )


def _printed(letter: ContractDocument, key: str) -> str | None:
    """A letter's field as it printed it (`data-inizio` is already `1° ottobre 2026`)."""
    value = letter.data.get(key)
    return value if isinstance(value, str) else None


def _match_words(
    match: Match,
    letter: ContractDocument,
    facts: DocumentFacts,
    framework_stato: str | None,
    *,
    pigro_configurato: bool = True,
) -> Words:
    """`match_words` for a match and its letter, the one call `MatchRead` and the «Match»
    list's rows both make. `pigro_configurato` is whether this environment has the CRM's
    token (`MatchService`'s own)."""
    return match_words(
        match.stato,
        facts,
        framework_stato,
        _printed(letter, "data-inizio"),
        _printed(letter, "data-fine"),
        pigro_stato=match.pigro_stato,
        pigro_errore=match.pigro_errore,
        pigro_configurato=pigro_configurato,
    )


class MatchReads:
    def __init__(
        self,
        session: Session,
        today: Callable[[], date] = rome_today,
        *,
        pigro_configurato: bool = True,
    ) -> None:
        """`pigro_configurato` is whether `REBASE_PIGRO_ENGAGEMENTS_TOKEN` is set: without
        it an active match's card says so and offers no «Riprova su Pigro» (spec § 3.2)."""
        self.session = session
        self.today = today
        self.pigro_configurato = pigro_configurato

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
        freelancer_id = row[0].freelancer_id
        framework = framework_states(self.session, {freelancer_id}).get(
            (freelancer_id, letter.regime)
        )
        return self._match_read(row[0], row[1], letter, framework)

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
        already takes. No tax field is read here, and the client's `budget_giornaliero` is read only
        inside a referral's projection, never returned. Each
        row's sentence needs where its freelancer's framework agreement stands: read for
        the whole page in one more query, never one per row; so are the referrals of the
        page's freelancers and companies (`ReferralService.for_matches`, REB-609)."""
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
        frameworks = framework_states(self.session, {row[0].freelancer_id for row in rows})
        referrals = ReferralService(self.session).for_matches(
            [(match, letter) for match, _, _, _, letter in rows]
        )
        today = self.today()
        return MatchList(
            totale=totale,
            items=[
                self._list_item(
                    match,
                    company,
                    freelancer,
                    admin,
                    letter,
                    frameworks.get((match.freelancer_id, letter.regime))
                    if letter is not None
                    else None,
                    referrals.get(match.id, []),
                    today,
                )
                for match, company, freelancer, admin, letter in rows
            ],
        )

    def for_freelancer(self, freelancer_id: UUID) -> FreelancerContracts:
        freelancer_row(self.session, freelancer_id)
        today = self.today()
        frameworks = list(
            self.session.scalars(
                select(ContractDocument)
                .where(
                    ContractDocument.kind == QUADRO, ContractDocument.freelancer_id == freelancer_id
                )
                .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
            )
        )
        quadri = [
            document_read(document, today, current_quadro_version(document.regime))
            for document in frameworks
        ]
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
            quadro=shown_framework(quadri, PARTITA_IVA),
            quadro_occasionale=shown_framework(quadri, OCCASIONALE),
            quadri=quadri,
            matches=[
                self._match_read(
                    match,
                    company,
                    letters[match.id],
                    framework_state(frameworks, letters[match.id].regime),
                )
                for match, company in rows
                if match.id in letters
            ],
            fiscale=FiscalService(self.session).get(freelancer_id),
        )

    def document_pdf(self, document_id: UUID, *, signed: bool = False) -> ContractPdf:
        document = self.session.get(ContractDocument, document_id)
        if document is None:
            raise NotFound("documento", document_id)
        content = document.signed_pdf if signed else document.pdf
        if content is None:
            raise NotFound("documento firmato", document_id)
        base = document_stem(document.kind, document.regime, document.numero, document.text_version)
        return ContractPdf(filename=f"{base}{'-firmato' if signed else ''}.pdf", content=content)

    def _match_read(
        self,
        match: Match,
        company: Company,
        letter: ContractDocument,
        framework_stato: str | None,
    ) -> MatchRead:
        """`framework_stato` is where the freelancer's framework agreement stands
        (`framework_states`): whether a waiting letter leaves by itself or needs «Invia
        per la firma»."""
        today = self.today()
        facts = document_facts(letter, today)
        situazione, prossima_azione, altre_azioni = _match_words(
            match, letter, facts, framework_stato, pigro_configurato=self.pigro_configurato
        )
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
            lettera=document_read(letter, today, current_quadro_version(letter.regime), facts),
            situazione=situazione,
            prossima_azione=prossima_azione,
            altre_azioni=altre_azioni,
            giorni_previsti=match.giorni_previsti,
            lettera_data_inizio=match.lettera_data_inizio,
            lettera_data_fine=match.lettera_data_fine,
            lettera_compenso=match.lettera_compenso,
            pigro_stato=match.pigro_stato,
            pigro_slug=match.pigro_slug,
            pigro_deal_id=match.pigro_deal_id,
            pigro_url=match.pigro_url,
            pigro_linked_at=match.pigro_linked_at,
            pigro_attempted_at=match.pigro_attempted_at,
            pigro_errore=match.pigro_errore,
            pigro_mail_sent_at=match.pigro_mail_sent_at,
        )

    def _list_item(
        self,
        match: Match,
        company: Company,
        freelancer_user: User,
        admin_user: User,
        letter: ContractDocument | None,
        framework_stato: str | None,
        referrals: list[MatchReferral],
        today: date,
    ) -> MatchListItem:
        situazione = (
            _match_words(
                match,
                letter,
                document_facts(letter, today),
                framework_stato,
                pigro_configurato=self.pigro_configurato,
            )[0]
            if letter is not None
            else f"{MATCH_STATE_LABELS.get(match.stato, match.stato)}."
        )
        return MatchListItem(
            id=match.id,
            freelancer_id=match.freelancer_id,
            company_id=match.company_id,
            company_deleted=company.deleted_at is not None,
            freelancer_nome=freelancer_user.nome,
            freelancer_cognome=freelancer_user.cognome,
            freelancer_email=freelancer_user.email,
            nome_azienda=company.nome_azienda,
            figura_richiesta=company.figura_richiesta,
            stato=match.stato,
            lettera_numero=letter.numero if letter is not None else None,
            lettera_stato=letter.stato if letter is not None else None,
            lettera_data_inizio=_printed(letter, "data-inizio") if letter is not None else None,
            lettera_data_fine=_printed(letter, "data-fine") if letter is not None else None,
            lettera_compenso=match.lettera_compenso,
            lettera_unita=(letter_unit(letter.data) or None) if letter is not None else None,
            created_at=match.created_at,
            created_by_nome=full_name(admin_user),
            created_by_email=admin_user.email,
            situazione=situazione,
            giorni_previsti=match.giorni_previsti,
            pigro_stato=match.pigro_stato,
            pigro_url=match.pigro_url,
            referrals=referrals,
        )
