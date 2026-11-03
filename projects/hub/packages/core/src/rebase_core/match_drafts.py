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
    ContractPdf,
    CustomerDraft,
    LetterDraft,
    MatchCheck,
    MatchCreate,
    MatchPrefill,
)
from rebase_core.contracts.fields import DAYS, MONTH_END
from rebase_core.contracts.render import company_defaults
from rebase_core.errors import ValidationFailed
from rebase_core.fiscal import FiscalService
from rebase_core.framework import active_framework, current_framework_version, document_read
from rebase_core.match_documents import MatchDocuments
from rebase_core.match_rows import (
    ENTITY,
    FRAMEWORK,
    LETTER,
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
from rebase_core.regimes import OCCASIONAL, VAT_REGISTERED, Regime, text_name

# Pydantic's own error types, whose messages are English; any other type is one the hub
# raised itself (`PydanticCustomError`), with an Italian message.
PYDANTIC_ERRORS = frozenset(get_args(ErrorType))
# The word after a field's name for a value Pydantic's own checks refused (`field_reason`).
NOT_VALID = "non valido"


def _italian_amounts(
    part: str, given: Mapping[str, object], known: Mapping[str, FieldInfo]
) -> dict[str, object]:
    """`given` with every amount typed as a string read the Italian way (REB-485): the
    letter's `fee` «1.500» is 1500, where Pydantic's own reading of the string gives
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


def location_suggestion(remote: str, onsite_days: int | None) -> str:
    if remote == "hybrid" and onsite_days:
        work_units = "1 giornata" if onsite_days == 1 else f"{onsite_days} giornate"
        return f"in parte da remoto, con {work_units} a settimana presso il Cliente"
    if remote == "on_site":
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
        company, contact_person = company_row(self.session, company_id)
        previous = self.session.scalars(
            select(Match)
            .join(Company, Company.id == Match.company_id)
            .where(Company.user_id == company.user_id, Match.status != "cancelled")
            .order_by(Match.created_at.desc(), Match.id.desc())
            .limit(1)
        ).first()
        customer = (
            CustomerDraft(
                customer_legal_name=previous.customer_legal_name,
                customer_vat_number=previous.customer_vat_number,
                customer_registered_office=previous.customer_registered_office,
            )
            if previous is not None
            else CustomerDraft(customer_legal_name=company.company_name)
        )
        defaults = company_defaults()
        days, month_end = defaults.get(DAYS), defaults.get(MONTH_END)
        letter = LetterDraft(
            role=company.requested_role,
            activities=company.project,
            start_date=company.period_from,
            commitment=company.duration,
            location=location_suggestion(company.remote, company.onsite_days),
            customer_contact=full_name(contact_person) or None,
            mode=DAY_RATE,
            unit=DAY_RATE,
            fee=freelancer.daily_rate,
            payment_days=days if isinstance(days, int) and not isinstance(days, bool) else None,
            month_end=month_end if isinstance(month_end, bool) else None,
        )
        tax = FiscalService(self.session).get(freelancer.id)
        regime: Regime = (
            OCCASIONAL if tax is not None and tax.vat_number is None else VAT_REGISTERED
        )
        active, step = framework_step(self.session, freelancer.id, regime)
        today = self.documents.today()
        return MatchPrefill(
            tax=tax,
            customer=customer,
            letter=letter,
            regime=regime,
            active_framework=(
                document_read(active, today, current_framework_version(regime))
                if active is not None
                else None
            ),
            required_framework=step == "to_send",
            letter_pending=active is None,
        )

    def proposal(
        self,
        freelancer_id: UUID,
        company_id: UUID,
        customer: dict[str, object] | None = None,
        letter: dict[str, object] | None = None,
        match_id: UUID | None = None,
        regime: Regime | None = None,
    ) -> MatchCreate:
        """`prefill` as the body `create` takes, each field given in `customer` or
        `letter` laid over what the hub suggests (a `None` given blanks a suggestion):
        what an MCP tool saves when the admin asked for a match in a sentence. `regime`
        `None` takes the letter type the prefill proposes (REB-687). A key that is not a
        field of the letter or of the client, and a required field still empty once laid
        over, are a `ValidationFailed` naming it (`letter.fee`), in Italian
        (`field_reason`)."""
        prefill = self.prefill(freelancer_id, company_id)
        body: dict[str, object] = {
            "id": match_id,
            "company_id": company_id,
            "regime": regime if regime is not None else prefill.regime,
        }
        for part, suggested, given, known in (
            ("customer", prefill.customer, customer, CustomerDraft.model_fields),
            ("letter", prefill.letter, letter, LetterDraft.model_fields),
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
        require_regime_fits(fiscal.vat_number, data.regime)
        today = self.documents.today()
        if kind not in (FRAMEWORK, LETTER):
            raise ValidationFailed(ENTITY, "document", "uno fra lettera e quadro")
        name = text_name(kind, data.regime)
        if kind == FRAMEWORK:
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
            require_regime_fits(fiscal.vat_number, payload.regime)
        _active, step = framework_step(self.session, freelancer_id, payload.regime)
        recap, what_happens = check_sentences(
            full_name(user),
            payload.customer.customer_legal_name,
            payload.letter,
            step,
            missing_tax_data=fiscal is None,
            regime=payload.regime,
            has_vat_number=fiscal is not None and fiscal.vat_number is not None,
        )
        return MatchCheck(
            recap=recap,
            what_happens=what_happens,
            required_framework=step == "to_send",
            missing_tax_data=fiscal is None,
            regime=payload.regime,
        )
