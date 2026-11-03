"""What «Crea match» sends: tax data an Italian contract can print, a letter whose
numbers the law allows, and nothing that could carry the client's budget (REB-387)."""

import json
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from rebase_core.contract_schemas import (
    LETTER_AUTO_FIELDS_BY_REGIME,
    TEMPLATE_KEYS,
    CustomerDate,
    FiscalData,
    LetterDraft,
    LetterFields,
    MatchCreate,
    MatchListItem,
    MemberContract,
    MemberContracts,
    SendReport,
)
from rebase_core.contracts.fields import DAYS, FIELD, MONTH_END, TERM
from rebase_core.contracts.render import text_path
from rebase_core.regimes import text_name

REQUIRED = {
    "role": "Backend developer",
    "activities": "Le API del prodotto.",
    "start_date": date(2026, 10, 1),
    "mode": "to_work_unit",
    "fee": Decimal("450"),
    "payment_days": 30,
    "month_end": True,
}


def test_tax_identifiers_are_stored_the_way_they_print() -> None:
    data = FiscalData(
        tax_code=" lvl daa85t50h501z ",
        vat_number="IT 012 345 678 90",
        domicile="Via Roma 1, Milano",
    )
    assert (data.tax_code, data.vat_number, data.certified_email) == (
        "LVLDAA85T50H501Z",
        "01234567890",
        None,
    )
    assert FiscalData(tax_code="01234567890", vat_number="01234567890", domicile="X")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tax_code", "LVLDAA85T50"),
        ("vat_number", "0123456789"),
        ("domicile", "Via Roma 1\nMilano"),
        ("certified_email", "non-una-pec"),
    ],
)
def test_tax_data_a_contract_could_not_print_are_refused(field: str, value: str) -> None:
    payload = {
        "tax_code": "LVLDAA85T50H501Z",
        "vat_number": "01234567890",
        "domicile": "Via Roma 1, Milano",
        field: value,
    }
    with pytest.raises(ValidationError):
        FiscalData(**payload)  # type: ignore[arg-type]


def test_a_letter_turns_into_the_markdowns_own_keys_and_printable_values() -> None:
    fields = LetterFields(**REQUIRED, end_date=date(2027, 1, 29)).to_fields()  # type: ignore[arg-type]
    assert fields["data-inizio"] == "1° ottobre 2026"
    assert fields["data-fine"] == "29 gennaio 2027"
    assert fields["compenso"] == 450 and isinstance(fields["compenso"], int)
    assert fields["giorni-pagamento"] == 30 and fields["fine-mese"] is True
    assert fields["risultati"] is None
    half = LetterFields(**{**REQUIRED, "fee": Decimal("450.50")}).to_fields()  # type: ignore[arg-type]
    assert half["compenso"] == 450.5


@pytest.mark.parametrize(
    "change",
    [
        {"payment_days": 45, "month_end": True},  # past 60 days from the invoice
        {"payment_days": 61, "month_end": False},
        {"fee": Decimal("0")},
        {"fee": Decimal("450.005")},
        {"role": "   "},
        {"start_date": date(2026, 10, 1), "end_date": date(2026, 9, 30)},
    ],
)
def test_a_letter_the_law_or_the_page_would_not_allow_is_refused(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LetterFields(**{**REQUIRED, **change})  # type: ignore[arg-type]


@pytest.mark.parametrize("mode", ["to_work_unit", "lump_sum"])
def test_the_two_payment_modes_are_kept_as_written(mode: str) -> None:
    assert LetterFields(**{**REQUIRED, "mode": mode}).mode == mode  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "mode",
    ["A giornata", " a corpo", "a giornata (non è a corpo)", "pagamento giornaliero", "", 5],
)
def test_a_payment_mode_that_is_not_one_of_the_two_is_refused(mode: object) -> None:
    with pytest.raises(ValidationError) as refused:
        LetterFields(**{**REQUIRED, "mode": mode})  # type: ignore[arg-type]
    assert "«a giornata» oppure «a corpo»" in str(refused.value)


def test_a_letter_needs_a_payment_mode_but_a_draft_may_leave_it_blank() -> None:
    with pytest.raises(ValidationError):
        LetterFields(**{key: value for key, value in REQUIRED.items() if key != "mode"})  # type: ignore[arg-type]
    assert LetterDraft().mode is None


CUSTOMER = {
    "customer_legal_name": "Rossi Studio S.r.l.",
    "customer_vat_number": "01234567890",
    "customer_registered_office": "Milano",
}


def _match(expected_days: object) -> MatchCreate:
    return MatchCreate.model_validate(
        {
            "company_id": uuid4(),
            "customer": CUSTOMER,
            "letter": REQUIRED,
            "expected_days": expected_days,
        }
    )


@pytest.mark.parametrize("days", [0, -3, 367])
def test_expected_days_outside_a_year_are_refused_in_the_admins_words(days: int) -> None:
    """REB-502: the page and the MCP tools repeat this sentence, never Pydantic's English
    «Input should be less than or equal to 366»."""
    with pytest.raises(ValidationError) as refused:
        _match(days)
    (error,) = refused.value.errors()
    assert error["loc"] == ("expected_days",)
    assert error["msg"] == "I giorni previsti vanno da 1 a 366."


@pytest.mark.parametrize("days", [1, 366, None])
def test_expected_days_from_one_to_366_or_none_are_taken(days: int | None) -> None:
    assert _match(days).expected_days == days


@pytest.mark.parametrize("days", [2.5, "2,5", "quaranta", float("nan")])
def test_expected_days_that_are_not_a_whole_number_are_refused_in_the_admins_words(
    days: object,
) -> None:
    """The field's own parsing would answer before the range check, in English («Input
    should be a valid integer, got a number with a fractional part»): a fraction or a
    word gets the admin's sentence too."""
    with pytest.raises(ValidationError) as refused:
        _match(days)
    (error,) = refused.value.errors()
    assert error["loc"] == ("expected_days",)
    assert error["msg"] == "I giorni previsti sono un numero intero da 1 a 366."


def test_a_fraction_of_a_day_in_the_pages_json_is_refused_in_the_admins_words() -> None:
    body = {
        "company_id": str(uuid4()),
        "customer": CUSTOMER,
        "letter": {**REQUIRED, "start_date": "2026-10-01", "fee": "450"},
        "expected_days": 2.5,
    }
    with pytest.raises(ValidationError) as refused:
        MatchCreate.model_validate_json(json.dumps(body))
    (error,) = refused.value.errors()
    assert (error["loc"], error["msg"]) == (
        ("expected_days",),
        "I giorni previsti sono un numero intero da 1 a 366.",
    )


@pytest.mark.parametrize("days", [40.0, "40"])
def test_expected_days_written_as_a_whole_number_are_taken(days: object) -> None:
    assert _match(days).expected_days == 40


@pytest.mark.parametrize("regime", ["vat_registered", "occasional"])
def test_the_form_covers_every_field_of_the_letter_and_the_hub_fills_the_rest(regime: str) -> None:
    """A field added to a letter's Markdown and to neither list is a blank nobody fills.
    The occasional letter asks the same form and names its collaborator by the
    `codice fiscale` instead of a `partita IVA` (REB-687)."""
    letter = text_path(text_name("letter", regime)).read_text(encoding="utf-8")
    asked = set(FIELD.findall(letter))
    written = {TEMPLATE_KEYS[name] for name in LetterDraft.model_fields}
    assert asked == (written - {DAYS, MONTH_END}) | LETTER_AUTO_FIELDS_BY_REGIME[regime] | {TERM}


def test_nothing_in_the_flow_can_carry_the_client_budget() -> None:
    """Spec § 1h: what rebase agrees with the client never reaches a freelancer's
    document, so no model of this flow has a field it could travel in."""
    for model in (
        FiscalData,
        CustomerDate,
        LetterDraft,
        LetterFields,
        MatchCreate,
        MatchListItem,
        SendReport,
        MemberContract,
        MemberContracts,
    ):
        assert not any("budget" in name for name in model.model_fields), model


@pytest.mark.parametrize("vat_number", [None, "", "   "])
def test_tax_data_without_a_vat_number_are_stored_as_none(vat_number: str | None) -> None:
    """REB-687: a member paid as a `prestazione occasionale` has no `partita IVA`."""
    data = FiscalData(
        tax_code="LVLDAA85T50H501Z", vat_number=vat_number, domicile="Via Roma 1, Milano"
    )
    assert data.vat_number is None
    omitted = FiscalData(tax_code="LVLDAA85T50H501Z", domicile="Via Roma 1, Milano")
    assert omitted.vat_number is None


@pytest.mark.parametrize("vat_number", ["IT", "0123456789", "IT 0123456789", "012345678901"])
def test_to_vat_number_that_is_given_still_has_eleven_digits(vat_number: str) -> None:
    with pytest.raises(ValidationError, match="la partita IVA ha 11 cifre"):
        FiscalData(
            tax_code="LVLDAA85T50H501Z",
            vat_number=vat_number,
            domicile="Via Roma 1, Milano",
        )
