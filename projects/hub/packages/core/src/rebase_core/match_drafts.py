"""A match before it is written: what the hub proposes, what saving would do, and the
documents as they would print (REB-387 phase 2, REB-476, REB-892).

`MatchDrafts` reads and renders; it writes and numbers nothing. `prefill` is «Chi e per chi»
and «Condizioni» as the hub can fill them; `proposal` is the prefill as a `MatchCreate`, with
the fields an MCP tool was given laid over it; `preview` renders the PDFs «Controlla e
invia», step 3 of «Crea match», opens; `check` says in sentences what saving would do, on
that same step, validated as `create` validates.
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import get_args
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError
from pydantic.fields import FieldInfo
from pydantic_core import ErrorDetails
from pydantic_core.core_schema import ErrorType
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.amounts import NotAnAmount, italian_amount
from rebase_core.contract_schemas import (
    DAY_RATE,
    ClienteDraft,
    ContractPdf,
    LetteraDraft,
    MatchCheck,
    MatchCreate,
    MatchPrefill,
)
from rebase_core.contracts.fields import DAYS, MONTH_END
from rebase_core.contracts.render import company_defaults
from rebase_core.errors import ValidationFailed
from rebase_core.fiscal import FiscalService
from rebase_core.framework import active_framework, current_quadro_version, document_read
from rebase_core.match_documents import MatchDocuments
from rebase_core.match_rows import (
    ENTITY,
    LETTERA,
    QUADRO,
    company_row,
    fiscal_row,
    framework_step,
    freelancer_row,
    full_name,
    matchable_company,
    require_regime_fits,
)
from rebase_core.match_words import check_sentences
from rebase_core.models import Company, FreelancerFiscal, Match
from rebase_core.regimes import OCCASIONALE, PARTITA_IVA, Regime, text_name

# Pydantic's own error types, whose messages are English; any other type is one the hub
# raised itself (`PydanticCustomError`), with an Italian message.
PYDANTIC_ERRORS = frozenset(get_args(ErrorType))
# The word after a field's name for a value Pydantic's own checks refused (`field_reason`).
NOT_VALID = "non valido"


def _italian_amounts(
    part: str, given: Mapping[str, object], known: Mapping[str, FieldInfo]
) -> dict[str, object]:
    """`given` with every amount typed as a string read the Italian way (REB-485): the
    letter's `compenso` «1.500» is 1500, where Pydantic's own reading of the string gives
    1.5. A number is already one and stays as it is. A string that is not an amount is
    refused here, by its field, with the word Pydantic's refusal gets («non valido»):
    handed on, Pydantic would take «1e3», «1_000» or digits of another script, which the
    web refuses."""
    read = dict(given)
    for name, value in given.items():
        annotation = known[name].annotation
        if isinstance(value, str) and Decimal in (annotation, *get_args(annotation)):
            try:
                read[name] = italian_amount(value)
            except NotAnAmount as exc:
                raise ValidationFailed(ENTITY, f"{part}.{name}", NOT_VALID) from exc
    return read


def luogo_suggestion(remoto: str, giorni_presenza: int | None) -> str:
    if remoto == "ibrido" and giorni_presenza:
        giornate = "1 giornata" if giorni_presenza == 1 else f"{giorni_presenza} giornate"
        return f"in parte da remoto, con {giornate} a settimana presso il Cliente"
    if remoto == "in_sede":
        return "presso la sede del Cliente"
    return "da remoto"


def field_reason(error: ErrorDetails) -> str:
    """What an admin reads after a field's name for one of a model's errors: «manca» for
    an empty field; the hub's own sentence when one of its validators refused the value,
    already Italian, without the field's name when the sentence starts with it
    (`SafeStr`'s refusal of a NUL byte does), so the name is not said twice; «non
    valido» for Pydantic's own checks, whose words are English and repeat the value.
    `MatchDrafts.proposal` and the MCP server's tax-data tool both name a refusal
    this way."""
    if error["type"] == "missing" or error["input"] is None:
        return "manca"
    if error["type"] == "value_error":
        reason = str(error.get("ctx", {}).get("error", NOT_VALID))
        return reason.removeprefix(f"{error['loc'][-1]}: ") if error["loc"] else reason
    return error["msg"] if error["type"] not in PYDANTIC_ERRORS else NOT_VALID


class MatchDrafts:
    def __init__(self, session: Session, documents: MatchDocuments) -> None:
        self.session = session
        self.documents = documents

    def prefill(self, freelancer_id: UUID, company_id: UUID) -> MatchPrefill:
        """«Chi e per chi» and «Condizioni» as the hub can fill them: the saved tax
        data, the client as the same company user's last match named it (else the
        request's company name), and the letter from the request, the card and
        `rebase.json`."""
        freelancer, _user = freelancer_row(self.session, freelancer_id)
        company, referente = company_row(self.session, company_id)
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
            referente_cliente=full_name(referente) or None,
            modalita=DAY_RATE,
            unita=DAY_RATE,
            compenso=freelancer.tariffa_giornaliera,
            giorni_pagamento=days if isinstance(days, int) and not isinstance(days, bool) else None,
            fine_mese=month_end if isinstance(month_end, bool) else None,
        )
        fiscale = FiscalService(self.session).get(freelancer.id)
        regime: Regime = (
            OCCASIONALE if fiscale is not None and fiscale.partita_iva is None else PARTITA_IVA
        )
        active, step = framework_step(self.session, freelancer.id, regime)
        today = self.documents.today()
        return MatchPrefill(
            fiscale=fiscale,
            cliente=cliente,
            lettera=lettera,
            regime=regime,
            quadro_attivo=(
                document_read(active, today, current_quadro_version(regime))
                if active is not None
                else None
            ),
            quadro_necessario=step == "da_inviare",
            lettera_in_attesa=active is None,
        )

    def proposal(
        self,
        freelancer_id: UUID,
        company_id: UUID,
        cliente: dict[str, object] | None = None,
        lettera: dict[str, object] | None = None,
        match_id: UUID | None = None,
        regime: Regime | None = None,
    ) -> MatchCreate:
        """`prefill` as the body `create` takes, each field given in `cliente` or
        `lettera` laid over what the hub suggests (a `None` given blanks a suggestion):
        what an MCP tool saves when the admin asked for a match in a sentence. `regime`
        `None` takes the letter type the prefill proposes (REB-687). A key that is not a
        field of the letter or of the client, and a required field still empty once laid
        over, are a `ValidationFailed` naming it (`lettera.compenso`), in Italian
        (`field_reason`)."""
        prefill = self.prefill(freelancer_id, company_id)
        body: dict[str, object] = {
            "id": match_id,
            "company_id": company_id,
            "regime": regime if regime is not None else prefill.regime,
        }
        for part, suggested, given, known in (
            ("cliente", prefill.cliente, cliente, ClienteDraft.model_fields),
            ("lettera", prefill.lettera, lettera, LetteraDraft.model_fields),
        ):
            unknown = sorted(set(given or {}) - set(known))
            if unknown:
                raise ValidationFailed(ENTITY, f"{part}.{unknown[0]}", "non è un campo del match")
            laid = _italian_amounts(part, given or {}, known)
            body[part] = {**suggested.model_dump(exclude_none=True), **laid}
        try:
            return MatchCreate.model_validate(body)
        except PydanticValidationError as exc:
            error = exc.errors()[0]
            field = ".".join(str(part) for part in error["loc"])
            raise ValidationFailed(ENTITY, field, field_reason(error)) from exc

    def preview(self, freelancer_id: UUID, data: MatchCreate, kind: str) -> ContractPdf:
        """One document as `create` would write it, of the match's regime, with no number,
        and nothing saved."""
        renderer = self.documents.require_renderer()
        freelancer, user = freelancer_row(self.session, freelancer_id)
        matchable_company(self.session, data.company_id)
        fiscal = fiscal_row(self.session, freelancer.id)
        require_regime_fits(fiscal.partita_iva, data.regime)
        today = self.documents.today()
        if kind not in (QUADRO, LETTERA):
            raise ValidationFailed(ENTITY, "documento", "uno fra lettera e quadro")
        name = text_name(kind, data.regime)
        if kind == QUADRO:
            fields = self.documents.quadro_data(user, fiscal, today, data.regime)
        else:
            active = active_framework(self.session, freelancer.id, data.regime)
            fields = self.documents.lettera_data(user, fiscal, data, None, active, today)
        rendered = renderer.render(name, fields)
        return ContractPdf(filename=f"anteprima-{name}.pdf", content=rendered.pdf)

    def check(self, freelancer_id: UUID, payload: MatchCreate) -> MatchCheck:
        """What saving `payload` would do, in sentences: step 3 of «Crea match», and what
        an MCP tool reads back before it saves. Refused as `create` refuses it (a card or
        a request gone, a closed request, an ordinary letter without a partita IVA),
        except for missing tax data, which are reported rather than refused since the
        wizard saves them on the way. Reads only: nothing is written, no letter number is
        taken, no renderer is needed."""
        _freelancer, user = freelancer_row(self.session, freelancer_id)
        matchable_company(self.session, payload.company_id)
        fiscal = self.session.scalar(
            select(FreelancerFiscal)
            .where(FreelancerFiscal.freelancer_id == freelancer_id)
            .execution_options(populate_existing=True)
        )
        if fiscal is not None:
            require_regime_fits(fiscal.partita_iva, payload.regime)
        _active, step = framework_step(self.session, freelancer_id, payload.regime)
        riepilogo, cosa_succede = check_sentences(
            full_name(user),
            payload.cliente.cliente_ragione_sociale,
            payload.lettera,
            step,
            dati_fiscali_mancanti=fiscal is None,
            regime=payload.regime,
            ha_partita_iva=fiscal is not None and fiscal.partita_iva is not None,
        )
        return MatchCheck(
            riepilogo=riepilogo,
            cosa_succede=cosa_succede,
            quadro_necessario=step == "da_inviare",
            dati_fiscali_mancanti=fiscal is None,
            regime=payload.regime,
        )
