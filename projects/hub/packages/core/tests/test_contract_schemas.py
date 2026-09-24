"""What «Crea match» sends: tax data an Italian contract can print, a letter whose
numbers the law allows, and nothing that could carry the client's budget (REB-387)."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from rebase_core.contract_schemas import (
    LETTER_AUTO_FIELDS,
    ClienteData,
    FiscalData,
    LetteraDraft,
    LetteraFields,
    MatchCreate,
    MatchListItem,
    MemberContract,
    MemberContracts,
    SendReport,
)
from rebase_core.contracts.fields import DAYS, FIELD, MONTH_END, TERM
from rebase_core.contracts.render import text_path

REQUIRED = {
    "ruolo": "Backend developer",
    "attivita": "Le API del prodotto.",
    "data_inizio": date(2026, 10, 1),
    "compenso": Decimal("450"),
    "giorni_pagamento": 30,
    "fine_mese": True,
}


def test_tax_identifiers_are_stored_the_way_they_print() -> None:
    data = FiscalData(
        codice_fiscale=" lvl daa85t50h501z ",
        partita_iva="IT 012 345 678 90",
        domicilio="Via Roma 1, Milano",
    )
    assert (data.codice_fiscale, data.partita_iva, data.pec) == (
        "LVLDAA85T50H501Z",
        "01234567890",
        None,
    )
    assert FiscalData(codice_fiscale="01234567890", partita_iva="01234567890", domicilio="X")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("codice_fiscale", "LVLDAA85T50"),
        ("partita_iva", "0123456789"),
        ("domicilio", "Via Roma 1\nMilano"),
        ("pec", "non-una-pec"),
    ],
)
def test_tax_data_a_contract_could_not_print_are_refused(field: str, value: str) -> None:
    payload = {
        "codice_fiscale": "LVLDAA85T50H501Z",
        "partita_iva": "01234567890",
        "domicilio": "Via Roma 1, Milano",
        field: value,
    }
    with pytest.raises(ValidationError):
        FiscalData(**payload)  # type: ignore[arg-type]


def test_a_letter_turns_into_the_markdowns_own_keys_and_printable_values() -> None:
    fields = LetteraFields(**REQUIRED, data_fine=date(2027, 1, 29)).to_fields()  # type: ignore[arg-type]
    assert fields["data-inizio"] == "1° ottobre 2026"
    assert fields["data-fine"] == "29 gennaio 2027"
    assert fields["compenso"] == 450 and isinstance(fields["compenso"], int)
    assert fields["giorni-pagamento"] == 30 and fields["fine-mese"] is True
    assert fields["risultati"] is None
    half = LetteraFields(**{**REQUIRED, "compenso": Decimal("450.50")}).to_fields()  # type: ignore[arg-type]
    assert half["compenso"] == 450.5


@pytest.mark.parametrize(
    "change",
    [
        {"giorni_pagamento": 45, "fine_mese": True},  # past 60 days from the invoice
        {"giorni_pagamento": 61, "fine_mese": False},
        {"compenso": Decimal("0")},
        {"compenso": Decimal("450.005")},
        {"ruolo": "   "},
        {"data_inizio": date(2026, 10, 1), "data_fine": date(2026, 9, 30)},
    ],
)
def test_a_letter_the_law_or_the_page_would_not_allow_is_refused(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LetteraFields(**{**REQUIRED, **change})  # type: ignore[arg-type]


def test_the_form_covers_every_field_of_the_letter_and_the_hub_fills_the_rest() -> None:
    """A field added to the Markdown and to neither list is a blank nobody fills."""
    asked = set(FIELD.findall(text_path("lettera-di-incarico").read_text(encoding="utf-8")))
    written = {name.replace("_", "-") for name in LetteraDraft.model_fields}
    assert asked == (written - {DAYS, MONTH_END}) | LETTER_AUTO_FIELDS | {TERM}


def test_nothing_in_the_flow_can_carry_the_client_budget() -> None:
    """Spec § 1h: what rebase agrees with the client never reaches a freelancer's
    document, so no model of this flow has a field it could travel in."""
    for model in (
        FiscalData,
        ClienteData,
        LetteraDraft,
        LetteraFields,
        MatchCreate,
        MatchListItem,
        SendReport,
        MemberContract,
        MemberContracts,
    ):
        assert not any("budget" in name for name in model.model_fields), model
