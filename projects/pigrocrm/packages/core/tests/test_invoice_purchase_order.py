"""The purchase order's own rules (REB-693), with no session: what `InvoiceService`
stores is what `checked_order` returns."""

from datetime import date

import pytest

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.invoices.purchase_order import ORDER_FIELDS, Order, checked_order


def test_no_order_is_four_nones() -> None:
    assert checked_order(None, None, None, None) == Order(None, None, None, None)


def test_a_whole_order_is_stripped_and_its_codes_upper_cased() -> None:
    """A CIG copied from a PDF arrives with spaces and in whatever case the reader
    typed it; the customer's office matches it upper case."""
    assert checked_order(
        "  ODA-14 ", date(2026, 10, 9), " z0a1b2c3d4 ", "j41e22000110005"
    ) == Order("ODA-14", date(2026, 10, 9), "Z0A1B2C3D4", "J41E22000110005")


def test_a_number_alone_is_an_order() -> None:
    assert checked_order("8", None, None, None) == Order("8", None, None, None)


def test_columns_are_keyed_by_the_invoice_fields() -> None:
    order = Order("ODA-14", date(2026, 10, 9), "Z0A1B2C3D4", None)
    assert tuple(order.columns()) == ORDER_FIELDS
    assert order.columns() == {
        "purchase_order_number": "ODA-14",
        "purchase_order_date": date(2026, 10, 9),
        "purchase_order_tender_code": "Z0A1B2C3D4",
        "purchase_order_project_code": None,
    }


@pytest.mark.parametrize(
    "data, cig, cup",
    [
        (date(2026, 10, 9), None, None),
        (None, "Z0A1B2C3D4", None),
        (None, None, "J41E22000110005"),
    ],
)
def test_to_date_tender_code_or_project_code_without_the_number_is_refused_on_the_number(
    data: date | None, tender_code: str | None, cup: str | None
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        checked_order(None, data, tender_code, cup)
    assert caught.value.details["field"] == "purchase_order_number"
    assert "numero dell'ordine" in caught.value.details["reason"]


@pytest.mark.parametrize("number", ["", "   "])
def test_an_empty_number_is_refused_rather_than_stored_as_text(number: str) -> None:
    with pytest.raises(ValidationFailed) as caught:
        checked_order(number, None, None, None)
    assert caught.value.details["field"] == "purchase_order_number"
    assert caught.value.details["reason"].endswith("svuota tutti i suoi campi dall'applicazione")


def test_a_number_longer_than_twenty_characters_is_refused() -> None:
    with pytest.raises(ValidationFailed) as caught:
        checked_order("x" * 21, None, None, None)
    assert caught.value.details["field"] == "purchase_order_number"


@pytest.mark.parametrize(
    "numero, code_point",
    [
        ("Ordine città 5", "U+00E0"),
        ("N°5", "U+00B0"),
        ("ODA\t14", "U+0009"),
        ("ODA\u201314", "U+2013"),
        ("ODA\u00a014", "U+00A0"),
    ],
)
def test_a_number_outside_printable_basic_latin_is_refused_not_transliterated(
    number: str, code_point: str
) -> None:
    """An order number is an identifier the customer matches character by character:
    rewriting `città` as `citta` would hand back a number they never issued."""
    with pytest.raises(ValidationFailed) as caught:
        checked_order(number, None, None, None)
    assert caught.value.details["field"] == "purchase_order_number"
    assert code_point in caught.value.details["reason"]


@pytest.mark.parametrize(
    "cig", ["Z0A1B2C3D", "Z0A1B2C3D45", "Z0A1-2C3D4", "Z0A1B2C3Dè", "ß12345678"]
)
def test_to_tender_code_is_exactly_ten_letters_or_digits(tender_code: str) -> None:
    with pytest.raises(ValidationFailed) as caught:
        checked_order("ODA-14", None, tender_code, None)
    assert caught.value.details["field"] == "purchase_order_tender_code"
    assert caught.value.details["reason"] == "il CIG ha 10 caratteri, lettere e cifre"


@pytest.mark.parametrize(
    "cup", ["J41E2200011000", "J41E220001100055", "J41E2200011000-", "\ufb01J41E220001100"]
)
def test_a_cup_is_exactly_fifteen_letters_or_digits(cup: str) -> None:
    with pytest.raises(ValidationFailed) as caught:
        checked_order("ODA-14", None, None, cup)
    assert caught.value.details["field"] == "purchase_order_project_code"
    assert caught.value.details["reason"] == "il CUP ha 15 caratteri, lettere e cifre"
