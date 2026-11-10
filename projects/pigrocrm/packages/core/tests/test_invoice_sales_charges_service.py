"""What `InvoiceService` stores and the readers read when a sale carries a withholding or a
split payment (REB-994): the net receivable on the row, in the due item and in the cash figure,
the XML of the issued document, and the refusals of the regime."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fpr12 import assert_valid
from lxml import etree
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.sales import SALES_INVOICE
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.schemas import (
    InvoiceCreate,
    InvoiceImport,
    InvoiceIssue,
    InvoiceLineImport,
    InvoiceLineIn,
    PaymentState,
)
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")
YEAR = today_local().year


@pytest.fixture
def service(db_session: Session, local_storage: LocalFileStorage) -> InvoiceService:
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(
            name="rossi",
            legal_name="Mario Rossi",
            vat_number="09876543210",
            tax_code="RSSMRA80A01H501U",
            address="Via Vittorio Veneto 12",
            postal_code="20124",
            city="Milano",
            province="MI",
            country="IT",
            email="info@example.com",
        ),
        ADMIN,
    )
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(pack_id="it-standard"), ADMIN)
    return InvoiceService(db_session, local_storage)


def _customer(db_session: Session, **fields: object) -> UUID:
    values: dict[str, object] = {
        "legal_name": "Acme S.r.l.",
        "vat_number": "12345678901",
        "sdi_code": "ABCDEFG",
        "address": "Corso Italia 5",
        "postal_code": "00100",
        "city": "Roma",
        "province": "RM",
        "country": "IT",
    }
    values.update(fields)
    customer = Organization(**values)
    db_session.add(customer)
    db_session.flush()
    return customer.id


def _issue(service: InvoiceService, customer_id: UUID) -> Invoice:
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("1000.00"))],
        ),
        ADMIN,
    )
    service.issue(draft.id, InvoiceIssue(issue_date=date(YEAR, 3, 1)), ADMIN)
    row = service.session.get(Invoice, draft.id)
    assert row is not None
    return row


def _xml(service: InvoiceService, invoice_id: UUID) -> etree._Element:
    service.export_xml(invoice_id, ADMIN)
    content, _, _ = service.download(invoice_id, "xml", ADMIN)
    assert_valid(content)
    return etree.fromstring(content)


def _item(db_session: Session, invoice_id: UUID) -> DueItem:
    return db_session.execute(
        select(DueItem).where(DueItem.source_kind == SALES_INVOICE, DueItem.source_id == invoice_id)
    ).scalar_one()


def test_an_invoice_to_a_withholding_agent_is_owed_its_total_minus_the_withholding(
    service: InvoiceService, db_session: Session
) -> None:
    """Done when: it exports with its withholding and a receivable equal to total minus
    withholding, in the row, in the due item and in the cash the month reports."""
    customer_id = _customer(db_session, is_withholding_agent=True)
    invoice = _issue(service, customer_id)

    assert invoice.total == Decimal("1220.00")
    assert invoice.withholding_amount == Decimal("200.00")
    assert invoice.withholding_rate == Decimal("20.00")
    assert invoice.net_receivable == Decimal("1020.00")
    assert invoice.split_payment is False
    assert _item(db_session, invoice.id).amount == Decimal("1020.00")

    root = _xml(service, invoice.id)
    assert root.findtext(".//DatiRitenuta/ImportoRitenuta") == "200.00"
    assert root.findtext(".//DettaglioPagamento/ImportoPagamento") == "1020.00"

    service.set_payment_state(
        invoice.id, PaymentState(payment_status="paid", payment_date=date(YEAR, 3, 20)), ADMIN
    )
    paid = AnalyticsRepository(db_session).monthly_paid(YEAR, "accrual")
    assert paid.get(3) == Decimal("1020.00")
    assert AnalyticsRepository(db_session).monthly_paid(YEAR, "collection").get(3) == Decimal(
        "1020.00"
    )


def test_an_invoice_to_a_public_body_is_fpa12_with_no_vat_collected(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(
        db_session, customer_kind="public_body", sdi_code="UFABCD", legal_name="Comune di Roma"
    )
    invoice = _issue(service, customer_id)

    assert invoice.split_payment is True
    assert invoice.withholding_amount == Decimal("0.00")
    assert invoice.total == Decimal("1220.00")
    assert invoice.net_receivable == Decimal("1000.00")
    assert _item(db_session, invoice.id).amount == Decimal("1000.00")

    root = _xml(service, invoice.id)
    assert root.get("versione") == "FPA12"
    assert root.findtext(".//CodiceDestinatario") == "UFABCD"
    assert root.findtext(".//DatiRiepilogo/EsigibilitaIVA") == "S"
    assert root.findtext(".//DettaglioPagamento/ImportoPagamento") == "1000.00"


def test_a_public_body_that_is_a_withholding_agent_is_withheld_and_not_split(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(
        db_session, customer_kind="public_body", sdi_code="UFABCD", is_withholding_agent=True
    )
    invoice = _issue(service, customer_id)
    assert (invoice.split_payment, invoice.withholding_amount) == (False, Decimal("200.00"))
    assert invoice.net_receivable == Decimal("1020.00")
    root = _xml(service, invoice.id)
    assert root.get("versione") == "FPA12"
    assert root.findtext(".//DatiRiepilogo/EsigibilitaIVA") == "I"


def test_a_service_to_a_business_abroad_is_outside_italian_vat_on_the_ordinary_regime(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(
        db_session,
        legal_name="Acme Ltd",
        vat_number="GB123456789",
        sdi_code=None,
        country="GB",
        customer_kind="business",
        postal_code="EC1V 9HL",
        province=None,
        city="London",
        address="1 High Street",
    )
    invoice = _issue(service, customer_id)
    assert (invoice.vat_amount, invoice.total) == (Decimal("0.00"), Decimal("1000.00"))
    root = _xml(service, invoice.id)
    assert root.findtext(".//DettaglioLinee/Natura") == "N2.1"
    assert root.findtext(".//DatiRiepilogo/Natura") == "N2.1"
    assert "art. 7-ter" in (root.findtext(".//DatiRiepilogo/RiferimentoNormativo") or "")
    assert root.findtext(".//DatiRiepilogo/EsigibilitaIVA") == "I"


def test_a_consumer_abroad_is_not_issued_on_the_ordinary_regime(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(
        db_session,
        legal_name="Jean Dupont",
        vat_number=None,
        tax_code="DPNJNX80A01Z110X",
        sdi_code=None,
        country="FR",
        customer_kind="consumer",
    )
    with pytest.raises(ValidationFailed) as caught:
        service.create(
            InvoiceCreate(
                customer_id=customer_id,
                lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("100.00"))],
            ),
            ADMIN,
        )
    assert caught.value.details["field"] == "customer_kind"


def test_an_imported_invoice_declares_its_withholding_and_the_net_follows(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(db_session)
    imported = service.import_issued(
        InvoiceImport(
            year=YEAR,
            number=1,
            issue_date=date(YEAR, 2, 1),
            customer_id=customer_id,
            lines=[
                InvoiceLineImport(
                    description="Consulenza",
                    unit_price=Decimal("1000.00"),
                    total_price=Decimal("1000.00"),
                    vat_rate=Decimal("22.00"),
                )
            ],
            taxable_amount=Decimal("1000.00"),
            vat_amount=Decimal("220.00"),
            stamp_duty=Decimal("0.00"),
            total=Decimal("1220.00"),
            withholding_amount=Decimal("200.00"),
        ),
        ADMIN,
    )
    assert (imported.withholding_amount, imported.net_receivable) == (
        Decimal("200.00"),
        Decimal("1020.00"),
    )
    assert _item(db_session, imported.id).amount == Decimal("1020.00")


def test_an_imported_split_payment_invoice_is_owed_its_taxable_amount(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(db_session)
    imported = service.import_issued(
        InvoiceImport(
            year=YEAR,
            number=1,
            issue_date=date(YEAR, 2, 1),
            customer_id=customer_id,
            lines=[
                InvoiceLineImport(
                    description="Consulenza",
                    unit_price=Decimal("1000.00"),
                    total_price=Decimal("1000.00"),
                    vat_rate=Decimal("22.00"),
                )
            ],
            taxable_amount=Decimal("1000.00"),
            vat_amount=Decimal("220.00"),
            stamp_duty=Decimal("0.00"),
            total=Decimal("1220.00"),
            split_payment=True,
        ),
        ADMIN,
    )
    assert (imported.split_payment, imported.net_receivable) == (True, Decimal("1000.00"))


def test_an_import_registers_an_invoice_as_declared_even_with_both(
    service: InvoiceService, db_session: Session
) -> None:
    """The law keeps a split payment and a withholding apart for what this product issues; an
    invoice another system issued is registered as it says, net of both."""
    customer_id = _customer(db_session)
    imported = service.import_issued(
        InvoiceImport(
            year=YEAR,
            number=1,
            issue_date=date(YEAR, 2, 1),
            customer_id=customer_id,
            lines=[
                InvoiceLineImport(
                    description="Consulenza",
                    unit_price=Decimal("1000.00"),
                    total_price=Decimal("1000.00"),
                    vat_rate=Decimal("22.00"),
                )
            ],
            taxable_amount=Decimal("1000.00"),
            vat_amount=Decimal("220.00"),
            stamp_duty=Decimal("0.00"),
            total=Decimal("1220.00"),
            withholding_amount=Decimal("200.00"),
            split_payment=True,
        ),
        ADMIN,
    )
    assert imported.net_receivable == Decimal("800.00")


@pytest.mark.parametrize("withheld", [Decimal("-1.00"), Decimal("1220.01")])
def test_an_import_refuses_a_withholding_outside_zero_and_the_total(
    service: InvoiceService, db_session: Session, withheld: Decimal
) -> None:
    customer_id = _customer(db_session)
    with pytest.raises(ValidationFailed) as caught:
        service.import_issued(
            InvoiceImport(
                year=YEAR,
                number=1,
                issue_date=date(YEAR, 2, 1),
                customer_id=customer_id,
                lines=[
                    InvoiceLineImport(
                        description="Consulenza",
                        unit_price=Decimal("1000.00"),
                        total_price=Decimal("1000.00"),
                        vat_rate=Decimal("22.00"),
                    )
                ],
                taxable_amount=Decimal("1000.00"),
                vat_amount=Decimal("220.00"),
                stamp_duty=Decimal("0.00"),
                total=Decimal("1220.00"),
                withholding_amount=withheld,
            ),
            ADMIN,
        )
    assert caught.value.details["field"] == "withholding_amount"


def test_a_draft_and_a_proforma_carry_the_net_they_will_print_and_issue_computes_it_again(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(db_session, is_withholding_agent=True)
    lines = [InvoiceLineIn(description="Consulenza", unit_price=Decimal("1000.00"))]
    draft = service.create(InvoiceCreate(customer_id=customer_id, lines=lines), ADMIN)
    assert (draft.withholding_amount, draft.net_receivable) == (
        Decimal("200.00"),
        Decimal("1020.00"),
    )
    replaced = service.replace_lines(
        draft.id,
        [InvoiceLineIn(description="Consulenza", unit_price=Decimal("500.00"))],
        ADMIN,
    )
    assert (replaced.total, replaced.withholding_amount, replaced.net_receivable) == (
        Decimal("610.00"),
        Decimal("100.00"),
        Decimal("510.00"),
    )

    proforma = service.create(
        InvoiceCreate(customer_id=customer_id, type="proforma", lines=lines), ADMIN
    )
    assert proforma.net_receivable == Decimal("1020.00")
    service.confirm_proforma(proforma.id, ADMIN)
    # The customer stopped being a withholding agent before the proforma became an invoice:
    # emission reads the customer as it is then.
    customer = db_session.get(Organization, customer_id)
    assert customer is not None
    customer.is_withholding_agent = False
    db_session.flush()
    issued = service.issue(proforma.id, InvoiceIssue(issue_date=date(YEAR, 3, 1)), ADMIN)
    assert (issued.withholding_amount, issued.net_receivable) == (
        Decimal("0.00"),
        Decimal("1220.00"),
    )


def test_the_unpaid_and_drafted_cash_figures_read_the_net(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(db_session, is_withholding_agent=True)
    lines = [InvoiceLineIn(description="Consulenza", unit_price=Decimal("1000.00"))]
    service.create(InvoiceCreate(customer_id=customer_id, lines=lines), ADMIN)
    invoice = _issue(service, customer_id)
    repository = AnalyticsRepository(db_session)
    assert repository.monthly_unpaid(YEAR, "accrual").get(3) == invoice.net_receivable
    assert repository.monthly_unpaid(YEAR, "collection").get(3) == invoice.net_receivable
    assert sum(repository.monthly_drafts(YEAR, "collection").values()) == Decimal("1020.00")
