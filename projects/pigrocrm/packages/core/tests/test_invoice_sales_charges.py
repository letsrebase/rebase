"""Withholding, split payment and `FPA12` on a sale (REB-994).

The arithmetic of `invoices/charges.py` is proven with no database, and the XML it feeds is
proven against the official schema (the rules are the register's: Agenzia delle Entrate on the
withholding, art. 17-ter c. 1-sexies DPR 633/72 on the split payment).
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fpr12 import assert_valid
from lxml import etree

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.regime import ORDINARY
from pigrocrm.core.fiscal.schemas import FiscalSnapshot
from pigrocrm.core.invoices.charges import compute_charges
from pigrocrm.core.invoices.fatturapa.checks import check_recipient_routing
from pigrocrm.core.invoices.fatturapa.exporter import InvoicePaExporter
from pigrocrm.core.invoices.pdf import build_scope, format_euro
from pigrocrm.core.invoices.schemas import (
    SNAPSHOT_VERSION,
    InvoiceForExport,
    InvoiceLineRead,
    InvoiceSnapshot,
    PartySnapshot,
)
from pigrocrm.core.invoices.totals import ComputedLine

LEGAL_ENTITY = PartySnapshot(
    legal_name="Mario Rossi",
    vat_number="01234567890",
    tax_code="RSSMRA80A01H501U",
    sdi_code=None,
    certified_email="mario@pec.it",
    address="Via Vittorio Veneto 12",
    postal_code="20124",
    city="Milano",
    province="MI",
    country="IT",
    email="mario@example.com",
    phone=None,
    website=None,
)


def _tax(**overrides: object) -> FiscalSnapshot:
    base: dict[str, object] = {
        "regime_code": "RF01",
        "pack_id": "it-standard",
        "default_vat_rate": Decimal("22.00"),
        "default_vat_nature": None,
        "legal_reference": None,
        "apply_stamp_duty": False,
        "stamp_duty_threshold": Decimal("77.47"),
        "stamp_duty_amount": Decimal("2.00"),
        "payment_terms": "TP02",
        "payment_method": "MP05",
        "due_days": 30,
        "iban": "IT60X0542811101000000123456",
    }
    base.update(overrides)
    return FiscalSnapshot(**base)  # type: ignore[arg-type]


def _customer(**overrides: object) -> PartySnapshot:
    base: dict[str, object] = {
        "legal_name": "Acme S.r.l.",
        "vat_number": "12345678901",
        "tax_code": None,
        "sdi_code": "ABCDEFG",
        "certified_email": None,
        "address": "Corso Italia 5",
        "postal_code": "00100",
        "city": "Roma",
        "province": "RM",
        "country": "IT",
        "email": None,
        "phone": None,
        "website": None,
        "customer_kind": "business",
        "is_withholding_agent": False,
    }
    base.update(overrides)
    return PartySnapshot(**base)  # type: ignore[arg-type]


def _computed(price: str = "1000.00", rate: str = "22.00") -> list[ComputedLine]:
    return [
        ComputedLine(
            line_number=1,
            description="Consulenza",
            quantity=Decimal("1"),
            unit_of_measure=None,
            unit_price=Decimal(price),
            discount_percentage=None,
            discount_amount=None,
            total_price=Decimal(price),
            vat_rate=Decimal(rate),
            vat_nature=None,
            legal_reference=None,
        )
    ]


def _invoice(
    tax: FiscalSnapshot, customer: PartySnapshot, computed: list[ComputedLine] | None = None
) -> InvoiceForExport:
    """What `InvoiceService` stores for these lines, built the way the exporter reads it."""
    lines = computed or _computed()
    charges = compute_charges(lines, tax=tax, customer=customer)
    return InvoiceForExport(
        year=2026,
        number=7,
        issue_date=date(2026, 8, 20),
        due_date=date(2026, 9, 19),
        sdi_document_type="TD01",
        currency="EUR",
        taxable_amount=charges.taxable_amount,
        vat_amount=charges.vat_amount,
        stamp_duty=Decimal("0.00"),
        total=charges.total,
        withholding_amount=charges.withholding_amount,
        withholding_rate=charges.withholding_rate,
        split_payment=charges.split_payment,
        net_receivable=charges.net_receivable,
        memo=None,
        snapshot=InvoiceSnapshot(
            version=SNAPSHOT_VERSION, legal_entity=LEGAL_ENTITY, customer=customer, tax=tax
        ),
        lines=tuple(
            InvoiceLineRead(
                id=uuid4(),
                invoice_id=uuid4(),
                line_number=line.line_number,
                description=line.description,
                quantity=line.quantity,
                unit_of_measure=line.unit_of_measure,
                unit_price=line.unit_price,
                discount_percentage=None,
                discount_amount=None,
                total_price=line.total_price,
                vat_rate=line.vat_rate,
                vat_nature=line.vat_nature,
                legal_reference=line.legal_reference,
            )
            for line in lines
        ),
    )


def _xml(tax: FiscalSnapshot, customer: PartySnapshot) -> etree._Element:
    xml = InvoicePaExporter().to_bytes(_invoice(tax, customer))
    assert_valid(xml)
    return etree.fromstring(xml)


# --- the arithmetic ----------------------------------------------------------------


def test_a_withholding_agent_keeps_twenty_percent_of_the_fee() -> None:
    charges = compute_charges(
        _computed(), tax=_tax(), customer=_customer(is_withholding_agent=True)
    )
    assert charges.total == Decimal("1220.00")
    assert charges.withholding_amount == Decimal("200.00")
    assert charges.withholding_rate == Decimal("20.00")
    assert charges.net_receivable == Decimal("1020.00")
    assert not charges.split_payment


def test_a_customer_that_is_not_a_withholding_agent_pays_the_whole_total() -> None:
    charges = compute_charges(_computed(), tax=_tax(), customer=_customer())
    assert charges.withholding_amount == Decimal("0.00")
    assert charges.withholding_rate is None
    assert charges.net_receivable == charges.total == Decimal("1220.00")


def test_a_public_body_that_withholds_gets_no_split_payment() -> None:
    """Art. 17-ter c. 1-sexies: a fee subject to withholding is outside the split payment."""
    customer = _customer(customer_kind="public_body", sdi_code="UF1234", is_withholding_agent=True)
    charges = compute_charges(_computed(), tax=_tax(), customer=customer)
    assert not charges.split_payment
    assert charges.withholding_amount == Decimal("200.00")
    assert charges.net_receivable == Decimal("1020.00")


def test_a_public_body_that_does_not_withhold_pays_the_vat_to_the_tax_office() -> None:
    customer = _customer(customer_kind="public_body", sdi_code="UF1234")
    charges = compute_charges(_computed(), tax=_tax(), customer=customer)
    assert charges.split_payment
    assert charges.vat_amount == Decimal("220.00")
    assert charges.total == Decimal("1220.00")
    assert charges.net_receivable == Decimal("1000.00")


def test_a_flat_rate_issuer_is_never_withheld_nor_split() -> None:
    """L. 190/2014, art. 1 c. 67: the flat-rate regime's own pack carries no withholding."""
    tax = _tax(
        pack_id="it-flat-rate",
        regime_code="RF19",
        default_vat_rate=Decimal("0.00"),
        default_vat_nature="N2.2",
    )
    flat = replace(_computed("1000.00", "0.00")[0], vat_nature="N2.2")
    customer = _customer(customer_kind="public_body", is_withholding_agent=True)
    charges = compute_charges([flat], tax=tax, customer=customer)
    assert charges.withholding_amount == Decimal("0.00")
    assert not charges.split_payment
    assert charges.net_receivable == charges.total == Decimal("1000.00")


def test_a_customer_abroad_is_not_a_withholding_agent_in_italy() -> None:
    customer = _customer(country="FR", is_withholding_agent=True)
    charges = compute_charges(_computed(), tax=_tax(), customer=customer)
    assert charges.withholding_amount == Decimal("0.00")


# --- the XML -----------------------------------------------------------------------


def test_an_invoice_to_a_withholding_agent_exports_its_withholding_and_the_net() -> None:
    root = _xml(_tax(), _customer(is_withholding_agent=True))
    ns = ".//"
    block = root.find(f"{ns}DatiRitenuta")
    assert block is not None
    assert [child.text for child in block] == ["RT01", "200.00", "20.00", "A"]
    assert root.findtext(f"{ns}DettaglioLinee/Ritenuta") == "SI"
    assert root.findtext(f"{ns}ImportoTotaleDocumento") == "1220.00"
    assert root.findtext(f"{ns}DettaglioPagamento/ImportoPagamento") == "1020.00"
    assert root.findtext(".//FormatoTrasmissione") == "FPR12"


def test_an_invoice_without_a_withholding_writes_no_withholding_block() -> None:
    root = _xml(_tax(), _customer())
    assert root.find(".//DatiRitenuta") is None
    assert root.find(".//DettaglioLinee/Ritenuta") is None
    assert root.findtext(".//DettaglioPagamento/ImportoPagamento") == "1220.00"


def test_a_public_body_is_sent_as_fpa12_with_its_ipa_code_and_a_split_payment() -> None:
    customer = _customer(customer_kind="public_body", sdi_code="UFABCD", certified_email="a@pec.it")
    root = _xml(_tax(), customer)
    assert root.get("versione") == "FPA12"
    assert root.findtext(".//FormatoTrasmissione") == "FPA12"
    assert root.findtext(".//CodiceDestinatario") == "UFABCD"
    assert root.find(".//PECDestinatario") is None
    assert root.findtext(".//DatiRiepilogo/EsigibilitaIVA") == "S"
    assert root.findtext(".//DatiRiepilogo/Imposta") == "220.00"
    assert root.findtext(".//DettaglioPagamento/ImportoPagamento") == "1000.00"
    assert root.find(".//DatiRitenuta") is None


def test_a_public_body_that_withholds_is_fpa12_with_vat_collected_and_a_withholding() -> None:
    customer = _customer(customer_kind="public_body", sdi_code="UFABCD", is_withholding_agent=True)
    root = _xml(_tax(), customer)
    assert root.get("versione") == "FPA12"
    assert root.findtext(".//DatiRiepilogo/EsigibilitaIVA") == "I"
    assert root.findtext(".//DatiRitenuta/ImportoRitenuta") == "200.00"
    assert root.findtext(".//DettaglioPagamento/ImportoPagamento") == "1020.00"


def test_a_private_customer_stays_fpr12() -> None:
    root = _xml(_tax(), _customer())
    assert root.get("versione") == "FPR12"
    assert root.findtext(".//CodiceDestinatario") == "ABCDEFG"


@pytest.mark.parametrize("code", [None, "", "ABCDEFG", "UF12"])
def test_a_public_body_without_a_six_character_ipa_code_is_refused_before_a_number_is_spent(
    code: str | None,
) -> None:
    customer = _customer(customer_kind="public_body", sdi_code=code, certified_email="a@pec.it")
    with pytest.raises(ValidationFailed) as caught:
        check_recipient_routing(customer)
    assert caught.value.details["field"] == "sdi_code"


def test_a_public_body_with_an_ipa_code_passes_the_routing_check() -> None:
    check_recipient_routing(_customer(customer_kind="public_body", sdi_code="UFABCD"))


# --- the ordinary regime abroad ----------------------------------------------------


@pytest.mark.parametrize(
    ("country", "reference"),
    [
        ("FR", "Inversione contabile - art. 7-ter DPR 633/1972"),
        ("US", "Operazione non soggetta a IVA in Italia ai sensi dell'art. 7-ter DPR 633/1972"),
    ],
)
def test_the_ordinary_regime_writes_n2_1_for_a_business_abroad(
    country: str, reference: str
) -> None:
    assert ORDINARY.resolve_line_vat(
        None, _tax(), country_customer=country, customer_kind="business"
    ) == (Decimal("0.00"), "N2.1", reference)


def test_the_ordinary_regime_refuses_a_consumer_abroad_and_a_rate_on_a_business_abroad() -> None:
    with pytest.raises(ValidationFailed) as consumer:
        ORDINARY.resolve_line_vat(None, _tax(), country_customer="FR", customer_kind="consumer")
    assert consumer.value.details["field"] == "customer_kind"
    with pytest.raises(ValidationFailed) as rate:
        ORDINARY.resolve_line_vat(
            Decimal("22.00"), _tax(), country_customer="FR", customer_kind="business"
        )
    assert rate.value.details["field"] == "vat_rate"


def test_the_ordinary_regime_still_taxes_a_customer_in_italy_whatever_its_kind() -> None:
    for kind in (None, "business", "consumer", "public_body"):
        assert ORDINARY.resolve_line_vat(
            None, _tax(), country_customer="IT", customer_kind=kind
        ) == (Decimal("22.00"), None, None)


# --- the PDF --------------------------------------------------------------------------


def test_the_pdf_scope_prints_the_withholding_and_the_net() -> None:
    scope = build_scope(_invoice(_tax(), _customer(is_withholding_agent=True)), reference=None)
    document = scope["fattura"]
    assert document["mostra_totali"] is True
    assert document["ritenuta"] == format_euro(Decimal("200.00"))
    assert document["iva_scissione"] == ""
    assert document["mostra_netto"] is True
    assert document["netto"] == format_euro(Decimal("1020.00"))


def test_the_pdf_scope_prints_the_vat_of_a_split_payment() -> None:
    customer = _customer(customer_kind="public_body", sdi_code="UFABCD")
    document = build_scope(_invoice(_tax(), customer), reference=None)["fattura"]
    assert document["ritenuta"] == ""
    assert document["iva_scissione"] == format_euro(Decimal("220.00"))
    assert document["netto"] == format_euro(Decimal("1000.00"))
    assert document["mostra_netto"] is True


def test_the_pdf_scope_of_a_plain_invoice_adds_no_row() -> None:
    document = build_scope(_invoice(_tax(), _customer()), reference=None)["fattura"]
    assert (document["ritenuta"], document["iva_scissione"]) == ("", "")
    assert document["mostra_netto"] is False
    assert document["netto"] == document["totale"]
