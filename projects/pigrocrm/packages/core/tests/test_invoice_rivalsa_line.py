"""The rivalsa line through the real invoice lifecycle: create, issue, export --
REB-361, proving REB-344 §9's own claim that a rivalsa line is "an ordinary
InvoiceLine row" that "passes FatturaPA export unchanged."

The one thing no unit test can prove: `InvoiceService.issue` re-derives every
zero-rate line's own `(natura, riferimento_normativo)` from the regime and refuses a
stored line whose pair disagrees. A rivalsa line only survives that check because
`rivalsa_line_for_contract` leaves `natura`/`riferimento_normativo` to the same
`RegimeStrategy` every other line goes through, and marks itself only through
`descrizione` -- the one column the regime never recomputes. This file is where
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
from pigrocrm.core.emitter.schemas import EmitterProfileUpsert
from pigrocrm.core.emitter.service import EmitterProfileService
from pigrocrm.core.fiscal.ceiling import rivalsa_line_for_contract
from pigrocrm.core.fiscal.pack import IT_FLAT_RATE_PACK, RIVALSA_INPS_CHARGE_ID
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")


@pytest.fixture
def storage(tmp_path) -> LocalFileStorage:  # type: ignore[no-untyped-def]
    return LocalFileStorage(tmp_path / "documents")


@pytest.fixture
def service(db_session: Session, storage: LocalFileStorage) -> InvoiceService:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    EmitterProfileService(db_session).upsert(
        EmitterProfileUpsert(
            ragione_sociale="Studio Rossi",
            partita_iva="01234567890",
            codice_fiscale="HMCRFT00A01H501K",
            indirizzo="Via Vittorio Veneto 12",
            cap="20124",
            comune="Milano",
            provincia="MI",
            nazione="IT",
            email="mario@example.com",
        ),
        ADMIN,
    )
    return InvoiceService(db_session, storage)


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    customer = Customer(
        ragione_sociale="Acme S.r.l.",
        partita_iva="12345678901",
        codice_sdi="ABCDEFG",
        indirizzo="Corso Italia 5",
        cap="00100",
        comune="Roma",
        provincia="RM",
        nazione="IT",
    )
    db_session.add(customer)
    db_session.flush()
    return customer.id


def _righe_with_rivalsa(fee: Decimal) -> list[InvoiceLineIn]:
    fee_line = InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=fee)
    rivalsa = rivalsa_line_for_contract(
        IT_FLAT_RATE_PACK, applies_social_charge=True, fee_subtotal=fee
    )
    assert rivalsa is not None
    return [fee_line, rivalsa]


def test_an_invoice_with_a_rivalsa_line_is_created_with_the_right_total(
    service: InvoiceService, customer_id: UUID
) -> None:
    draft = service.create(
        InvoiceCreate(customer_id=customer_id, righe=_righe_with_rivalsa(Decimal("1000.00"))),
        ADMIN,
    )
    assert draft.imponibile == Decimal("1040.00")
    righe = service.repo.lines(draft.id)
    assert len(righe) == 2
    rivalsa_riga = righe[1]
    assert (
        rivalsa_riga.descrizione
        == IT_FLAT_RATE_PACK.charge(RIVALSA_INPS_CHARGE_ID).descrizione_riga
    )
    assert rivalsa_riga.natura == "N2.2"
    assert rivalsa_riga.aliquota_iva == Decimal("0.00")
    assert rivalsa_riga.prezzo_totale == Decimal("40.00")


def test_the_invoice_issues_without_the_natura_consistency_check_refusing_it(
    service: InvoiceService, customer_id: UUID
) -> None:
    draft = service.create(
        InvoiceCreate(customer_id=customer_id, righe=_righe_with_rivalsa(Decimal("1000.00"))),
        ADMIN,
    )
    issued = service.issue(draft.id, InvoiceIssue(), ADMIN)
    assert issued.stato == "emessa"
    assert issued.imponibile == Decimal("1040.00")


def test_the_rivalsa_line_passes_fatturapa_export_unchanged(
    service: InvoiceService, customer_id: UUID
) -> None:
    draft = service.create(
        InvoiceCreate(customer_id=customer_id, righe=_righe_with_rivalsa(Decimal("1000.00"))),
        ADMIN,
    )
    issued = service.issue(draft.id, InvoiceIssue(), ADMIN)
    service.export_xml(issued.id, ADMIN)

    data, content_type, _ = service.download(issued.id, "xml", ADMIN)
    assert_valid(data)
    assert content_type == "application/xml"

    linee = list(etree.fromstring(data).iter("DettaglioLinee"))
    assert len(linee) == 2
    rivalsa_linea = linee[1]
    assert rivalsa_linea.findtext("Natura") == "N2.2"
    assert rivalsa_linea.findtext("AliquotaIVA") == "0.00"
    assert rivalsa_linea.findtext("PrezzoTotale") == "40.00"
    assert "rivalsa" in (rivalsa_linea.findtext("Descrizione") or "").lower()


def test_a_contract_without_the_election_produces_no_such_line(
    service: InvoiceService, customer_id: UUID
) -> None:
    """The other half of the Done-when: no election, no line, and the invoice's own
    total is the fee alone."""
    assert (
        rivalsa_line_for_contract(
            IT_FLAT_RATE_PACK, applies_social_charge=False, fee_subtotal=Decimal("1000.00")
        )
        is None
    )

    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("1000.00"))],
        ),
        ADMIN,
    )
    assert draft.imponibile == Decimal("1000.00")
    assert len(service.repo.lines(draft.id)) == 1
