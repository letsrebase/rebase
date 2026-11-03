"""The social-security surcharge (rivalsa) line through the real invoice lifecycle: create,
issue, export -- REB-361, proving REB-344 §9's own claim that a surcharge line is "an ordinary
InvoiceLine row" that "passes FatturaPA export unchanged."

The one thing no unit test can prove: `InvoiceService.issue` re-derives every
zero-rate line's own `(natura, riferimento_normativo)` from the regime and refuses a
stored line whose pair disagrees. A surcharge line only survives that check because
`social_security_surcharge_line_for_contract` leaves `vat_nature`/`legal_reference` to the same
`RegimeStrategy` every other line goes through, and marks itself only through
`description` -- the one column the regime never recomputes. This file is where
that claim is either true or is not.
"""

from decimal import Decimal
from uuid import UUID

import pytest
from fpr12 import assert_valid
from lxml import etree
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.fiscal.ceiling import social_security_surcharge_line_for_contract
from pigrocrm.core.fiscal.pack import IT_FLAT_RATE_PACK, SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")


@pytest.fixture
def storage(tmp_path) -> LocalFileStorage:  # type: ignore[no-untyped-def]
    return LocalFileStorage(tmp_path / "documents")


@pytest.fixture
def service(db_session: Session, storage: LocalFileStorage) -> InvoiceService:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(
            legal_name="Studio Rossi",
            vat_number="01234567890",
            tax_code="HMCRFT00A01H501K",
            address="Via Vittorio Veneto 12",
            postal_code="20124",
            city="Milano",
            province="MI",
            country="IT",
            email="mario@example.com",
        ),
        ADMIN,
    )
    return InvoiceService(db_session, storage)


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    customer = Customer(
        legal_name="Acme S.r.l.",
        vat_number="12345678901",
        sdi_code="ABCDEFG",
        address="Corso Italia 5",
        postal_code="00100",
        city="Roma",
        province="RM",
        country="IT",
    )
    db_session.add(customer)
    db_session.flush()
    return customer.id


def _lines_with_social_security_surcharge(fee: Decimal) -> list[InvoiceLineIn]:
    fee_line = InvoiceLineIn(description="Consulenza", unit_price=fee)
    social_security_surcharge = social_security_surcharge_line_for_contract(
        IT_FLAT_RATE_PACK, applies_social_charge=True, fee_subtotal=fee
    )
    assert social_security_surcharge is not None
    return [fee_line, social_security_surcharge]


def test_an_invoice_with_to_social_security_surcharge_line_is_created_with_the_right_total(
    service: InvoiceService, customer_id: UUID
) -> None:
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id, lines=_lines_with_social_security_surcharge(Decimal("1000.00"))
        ),
        ADMIN,
    )
    assert draft.taxable_amount == Decimal("1040.00")
    lines = service.repo.lines(draft.id)
    assert len(lines) == 2
    social_security_surcharge_line = lines[1]
    assert (
        social_security_surcharge_line.description
        == IT_FLAT_RATE_PACK.charge(SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID).description_line
    )
    assert social_security_surcharge_line.vat_nature == "N2.2"
    assert social_security_surcharge_line.vat_rate == Decimal("0.00")
    assert social_security_surcharge_line.total_price == Decimal("40.00")


def test_the_invoice_issues_without_the_vat_nature_consistency_check_refusing_it(
    service: InvoiceService, customer_id: UUID
) -> None:
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id, lines=_lines_with_social_security_surcharge(Decimal("1000.00"))
        ),
        ADMIN,
    )
    issued = service.issue(draft.id, InvoiceIssue(), ADMIN)
    assert issued.status == "issued"
    assert issued.taxable_amount == Decimal("1040.00")


def test_the_social_security_surcharge_line_passes_fatturapa_export_unchanged(
    service: InvoiceService, customer_id: UUID
) -> None:
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id, lines=_lines_with_social_security_surcharge(Decimal("1000.00"))
        ),
        ADMIN,
    )
    issued = service.issue(draft.id, InvoiceIssue(), ADMIN)
    service.export_xml(issued.id, ADMIN)

    data, content_type, _ = service.download(issued.id, "xml", ADMIN)
    assert_valid(data)
    assert content_type == "application/xml"

    lines = list(etree.fromstring(data).iter("DettaglioLinee"))
    assert len(lines) == 2
    social_security_surcharge_line = lines[1]
    assert social_security_surcharge_line.findtext("Natura") == "N2.2"
    assert social_security_surcharge_line.findtext("AliquotaIVA") == "0.00"
    assert social_security_surcharge_line.findtext("PrezzoTotale") == "40.00"
    assert (
        "rivalsa contributiva"
        in (social_security_surcharge_line.findtext("Descrizione") or "").lower()
    )


def test_a_contract_without_the_election_produces_no_such_line(
    service: InvoiceService, customer_id: UUID
) -> None:
    """The other half of the Done-when: no election, no line, and the invoice's own
    total is the fee alone."""
    assert (
        social_security_surcharge_line_for_contract(
            IT_FLAT_RATE_PACK, applies_social_charge=False, fee_subtotal=Decimal("1000.00")
        )
        is None
    )

    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("1000.00"))],
        ),
        ADMIN,
    )
    assert draft.taxable_amount == Decimal("1000.00")
    assert len(service.repo.lines(draft.id)) == 1
