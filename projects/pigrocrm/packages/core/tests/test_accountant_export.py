"""The period bundle for the accountant -- REB-1011.

What these hold the code to: a quarter's ZIP carries the sales register with its VAT columns,
the annulled and the unused numbers, the issued invoices' files, the open due items and the
payments, and an index that names every part (built, left out with its reason, or not
available yet); rows land in the period by each part's own date rule; CSVs read the way the
year register's do; a part whose module is off or whose pack does not apply is omitted; the
export is an admin's, an unscoped admin's for every entity, and an agent's only with
`full_access`.
"""

import csv
import datetime as dt
import io
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from conftest import _invoice_service
from pigrocrm.core.accountant_export.index import ExportIndex
from pigrocrm.core.accountant_export.models import AccountantExportRow
from pigrocrm.core.accountant_export.parts import (
    BundleFile,
    IndexEntry,
    PartContext,
    PartDefinition,
    PartOutput,
    Period,
    Spool,
)
from pigrocrm.core.accountant_export.registry import PARTS, UNAVAILABLE
from pigrocrm.core.accountant_export.retention import (
    RETENTION_MONTHS,
    ExportRetention,
    RetentionReport,
    add_months,
)
from pigrocrm.core.accountant_export.schemas import ExportRead
from pigrocrm.core.accountant_export.service import AccountantExportService
from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import get_settings
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.due_items.schemas import SettlementCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import (
    AgentForbidden,
    NotFound,
    PermissionDenied,
    ScopedAdmin,
    ValidationFailed,
)
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.invoices.models import Invoice, InvoiceRegisterGap
from pigrocrm.core.invoices.schemas import (
    InvoiceAnnul,
    InvoiceCreate,
    InvoiceIssue,
    InvoiceLineIn,
)
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.purchases.schemas import PurchaseCreate, VatLine
from pigrocrm.core.purchases.service import PurchaseService
from pigrocrm.core.storage.local import LocalFileStorage

D = dt.date
ADMIN = Actor(id=None, type="system", role="admin")
USER_ADMIN = Actor(id=uuid4(), type="user", role="admin")
COLLABORATOR = Actor(id=uuid4(), type="user", role="collaborator")
Q1 = Period(D(2026, 1, 1), D(2026, 3, 31))
NOW = dt.datetime(2026, 4, 2, 9, 30, tzinfo=dt.UTC)


@pytest.fixture
def invoices(db_session: Session, local_storage: LocalFileStorage) -> InvoiceService:
    service: InvoiceService = _invoice_service(db_session, local_storage)
    return service


@dataclass
class Built:
    """What a build stored, read back: the ZIP when it is one, the index it wrote and the
    export row's answer."""

    filename: str
    content: bytes
    index: ExportIndex
    stored: ExportRead


class Exports:
    """The service, reading each single-ZIP export back from the storage so a test can open it."""

    def __init__(self, service: AccountantExportService) -> None:
        self.service = service

    def build(self, *args: Any, **kwargs: Any) -> Built:
        stored = self.service.build_accountant_export(*args, **kwargs)
        assert len(stored.export.parts) == 1
        data, _, filename = self.service.download_part(stored.export.id, 1, ADMIN)
        return Built(filename, data, stored.index, stored.export)


@pytest.fixture
def exports(db_session: Session, local_storage: LocalFileStorage) -> Exports:
    return Exports(AccountantExportService(db_session, local_storage, get_settings()))


def _customer(session: Session, name: str = "Acme S.r.l.") -> Organization:
    customer = Organization(
        legal_name=name,
        vat_number="01234567890",
        sdi_code="ABCDEFG",
        address="Corso Italia 5",
        postal_code="00100",
        city="Roma",
        province="RM",
        country="IT",
    )
    session.add(customer)
    session.flush()
    return customer


def _issue(
    service: InvoiceService,
    customer: Organization,
    price: str,
    day: dt.date,
    *,
    files: bool = True,
):
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer.id,
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal(price))],
        ),
        ADMIN,
    )
    issued = service.issue(invoice.id, InvoiceIssue(issue_date=day), ADMIN)
    if files:
        service.produce_artifacts(issued.id, ADMIN)
    return issued


def _tables(text: str) -> list[list[list[str]]]:
    """The tables of a CSV the way an Italian spreadsheet splits them: blank-line separated,
    semicolon delimited."""
    assert text.startswith("\ufeff")
    assert "\r\n" in text and "\n" not in text.replace("\r\n", "")
    tables: list[list[list[str]]] = [[]]
    for row in csv.reader(io.StringIO(text.removeprefix("\ufeff"), newline=""), delimiter=";"):
        if row:
            tables[-1].append(row)
        else:
            tables.append([])
    return [table for table in tables if table]


def _open(content: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(content))


def _text(archive: zipfile.ZipFile, name: str) -> str:
    return archive.read(name).decode("utf-8")


# --- the bundle of a quarter ----------------------------------------------------------


def test_a_quarter_carries_every_built_part_and_an_index_that_names_each_one(
    db_session: Session, invoices: InvoiceService, exports: Exports
) -> None:
    acme = _customer(db_session)
    first = _issue(invoices, acme, "1000.00", D(2026, 2, 10))
    _issue(invoices, acme, "250.50", D(2026, 3, 20))
    _issue(invoices, acme, "999.00", D(2026, 4, 5))  # the next quarter

    export = exports.build(Q1, ADMIN, now=NOW)

    assert (
        export.filename
        == "commercialista-studio-rossi-2026-01-01_2026-03-31-20260402t093000z-"
        + (export.stored.id.hex[-8:] + ".zip")
    )
    archive = _open(export.content)
    names = archive.namelist()
    assert "indice.csv" in names
    assert "vendite/registro-vendite.csv" in names
    assert "vendite/numeri-annullati-e-non-usati.csv" in names
    assert "scadenze/scadenze-aperte.csv" in names
    assert "pagamenti/pagamenti.csv" in names
    pdfs = sorted(name for name in names if name.endswith(".pdf"))
    xmls = sorted(name for name in names if name.endswith(".xml"))
    assert pdfs == ["fatture-emesse/fattura-2026-1.pdf", "fatture-emesse/fattura-2026-2.pdf"]
    assert len(xmls) == 2 and all(name.startswith("fatture-emesse/IT") for name in xmls)
    assert first.number == 1

    parts = {(line.key, line.status) for line in export.index.parts}
    # The payslips are opt-in: no line in an export that did not ask for them.
    built = [part for part in PARTS if part.opt_in is None]
    assert {key for key, _ in parts if key in {part.key for part in PARTS}} == {
        part.key for part in built
    }
    assert {(part.key, "included") for part in built} <= parts
    assert {(part.key, "unavailable") for part in UNAVAILABLE} <= parts


def test_the_index_is_a_csv_with_the_row_counts_and_the_reasons(
    db_session: Session, invoices: InvoiceService, exports: Exports
) -> None:
    acme = _customer(db_session)
    _issue(invoices, acme, "1000.00", D(2026, 2, 10))
    _issue(invoices, acme, "250.50", D(2026, 3, 20))

    archive = _open(exports.build(Q1, ADMIN, now=NOW).content)
    meta, parts_table, missing, readiness = _tables(_text(archive, "indice.csv"))
    header, *parts = parts_table

    assert ["Azienda", "Studio Rossi"] in meta
    assert ["Periodo", "2026-01-01 - 2026-03-31"] in meta
    assert header[0] == "Sezione" and missing == [["Sezione", "Documento", "File", "Motivo"]]
    assert readiness[0] == ["Il periodo è pronto"]
    assert readiness[1] == ["Controllo", "Stato", "Quantità", "Dettaglio"]
    by_file = {row[3]: row for row in parts if row[3]}
    assert by_file["vendite/registro-vendite.csv"][4:6] == ["2", "righe"]
    assert by_file["fatture-emesse/"][4:6] == ["4", "file"]
    unavailable = [row for row in parts if row[2] == "Non disponibile"]
    assert {row[1] for row in unavailable} == {part.label for part in UNAVAILABLE}
    assert all(row[7] for row in unavailable)


def test_the_sales_register_has_the_vat_columns_and_totals(
    db_session: Session, invoices: InvoiceService, exports: Exports
) -> None:
    acme = _customer(db_session)
    _issue(invoices, acme, "1000.00", D(2026, 2, 10))
    _issue(invoices, acme, "1234.56", D(2026, 3, 20))

    archive = _open(exports.build(Q1, ADMIN, now=NOW).content)
    tables = _tables(_text(archive, "vendite/registro-vendite.csv"))

    header, *rows = tables[0][1:]
    assert header == [
        "Numero",
        "Data emissione",
        "Cliente",
        "Partita IVA",
        "Codice fiscale",
        "Tipo documento",
        "Aliquota IVA",
        "Natura",
        "Riferimento normativo",
        "Imponibile",
        "Imposta",
        "Pagamento",
        "Data incasso",
    ]
    data, total = rows[:-1], rows[-1]
    assert [row[0] for row in data] == ['="2026/1"', '="2026/2"']
    assert data[0][1:4] == ["2026-02-10", "Acme S.r.l.", '="01234567890"']
    assert data[0][5:11] == ["TD01", "0,00", "N2.2", data[0][8], "1.000,00", "0,00"]
    assert data[0][8]  # the legal reference the flat-rate invoice printed
    assert data[1][9] == "1.234,56"
    assert total[0] == "Totale" and total[9:11] == ["2.234,56", "0,00"]


def test_a_vat_invoice_gets_a_row_per_rate_with_the_vat_of_each(
    db_session: Session, invoices: InvoiceService, exports: Exports
) -> None:
    """The recap the XML carries: a row per `(rate, nature)`, the VAT rounded from the group."""
    from pigrocrm.core.invoices.models import InvoiceLine

    acme = _customer(db_session)
    invoice = Invoice(
        customer_id=acme.id,
        type="invoice",
        status="issued",
        year=2026,
        number=1,
        issue_date=D(2026, 2, 1),
        taxable_amount=Decimal("300.00"),
        vat_amount=Decimal("46.00"),
        total=Decimal("346.00"),
    )
    db_session.add(invoice)
    db_session.flush()
    for number, price, rate, nature in (
        (1, "100.00", "22.00", None),
        (2, "100.00", "10.00", None),
        (3, "100.00", "0.00", "N4"),
    ):
        db_session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                line_number=number,
                description="Riga",
                quantity=Decimal("1"),
                unit_price=Decimal(price),
                total_price=Decimal(price),
                vat_rate=Decimal(rate),
                vat_nature=nature,
            )
        )
    db_session.flush()

    archive = _open(exports.build(Q1, ADMIN, now=NOW).content)
    rows = _tables(_text(archive, "vendite/registro-vendite.csv"))[0][2:]

    assert [(row[6], row[7], row[9], row[10]) for row in rows[:-2]] == [
        ("0,00", "N4", "100,00", "0,00"),
        ("10,00", "", "100,00", "10,00"),
        ("22,00", "", "100,00", "22,00"),
    ]
    # The document says 46,00 of VAT and its lines make 32,00: the register still adds up to
    # the stored total, with the difference on a row of its own and no invented Natura.
    assert (rows[-2][6], rows[-2][7], rows[-2][8]) == (
        "",
        "",
        "Differenza rispetto ai totali del documento",
    )
    assert rows[-2][9:11] == ["0,00", "14,00"]
    assert rows[-1][9:11] == ["300,00", "46,00"]
    # No snapshot to read a customer from: said so, not guessed.
    assert rows[0][2] == "Cliente non disponibile"


def test_annulled_numbers_and_declared_gaps_are_listed_between_the_numbers_of_the_period(
    db_session: Session, invoices: InvoiceService, exports: Exports
) -> None:
    acme = _customer(db_session)
    _issue(invoices, acme, "100.00", D(2026, 2, 1))
    struck = _issue(invoices, acme, "400.00", D(2026, 2, 2))
    invoices.annul(struck.id, InvoiceAnnul(reason="errore di battitura"), ADMIN)
    entity = LegalEntityRepository(db_session).default()
    assert entity is not None
    # A number the register will never carry, between two the period holds, another beyond
    # them, and one of another year.
    for year, number in ((2026, 7), (2026, 40), (2025, 5)):
        db_session.add(
            InvoiceRegisterGap(
                legal_entity_id=entity.id, year=year, number=number, reason="prova di stampa"
            )
        )
    db_session.add(
        Invoice(
            customer_id=acme.id,
            type="invoice",
            status="issued",
            year=2026,
            number=9,
            issue_date=D(2026, 3, 3),
            taxable_amount=Decimal("10.00"),
            total=Decimal("10.00"),
        )
    )
    db_session.flush()

    archive = _open(exports.build(Q1, ADMIN, now=NOW).content)
    rows = _tables(_text(archive, "vendite/numeri-annullati-e-non-usati.csv"))[0]

    assert rows[0] == ["Numero", "Data emissione", "Stato", "Data annullamento", "Motivo"]
    assert [(row[0], row[2], row[4]) for row in rows[1:]] == [
        ('="2026/2"', "Annullata", "errore di battitura"),
        ('="2026/7"', "Non usato", "prova di stampa"),
    ]
    # The annulled invoice counts for nothing in the register itself.
    register = _tables(_text(archive, "vendite/registro-vendite.csv"))[0][2:]
    assert [row[0] for row in register[:-1]] == ['="2026/1"', '="2026/9"']


def test_a_row_lands_in_the_period_by_its_own_date_rule(
    db_session: Session, invoices: InvoiceService, exports: Exports
) -> None:
    """A monthly export carries the invoices dated in the month and no others."""
    acme = _customer(db_session)
    _issue(invoices, acme, "100.00", D(2026, 2, 28))
    _issue(invoices, acme, "200.00", D(2026, 3, 1))

    month = exports.build(Period(D(2026, 2, 1), D(2026, 2, 28)), ADMIN, now=NOW)
    archive = _open(month.content)

    register = _tables(_text(archive, "vendite/registro-vendite.csv"))[0][2:]
    assert [row[0] for row in register[:-1]] == ['="2026/1"']
    assert not any("fattura-2026-2" in name for name in archive.namelist())


def test_an_invoice_with_no_stored_file_is_listed_by_number_in_the_index(
    db_session: Session, invoices: InvoiceService, exports: Exports
) -> None:
    acme = _customer(db_session)
    _issue(invoices, acme, "100.00", D(2026, 2, 1))
    _issue(invoices, acme, "200.00", D(2026, 2, 2), files=False)

    export = exports.build(Q1, ADMIN, now=NOW)
    archive = _open(export.content)

    missing = [(item.missing.document, item.missing.kind) for item in export.index.missing]
    assert ("2026/2", "XML") in missing and ("2026/2", "PDF") in missing
    assert all(document == "2026/2" for document, _ in missing)
    rows = _tables(_text(archive, "indice.csv"))[2]
    assert [row[1] for row in rows[1:]] == ["2026/2", "2026/2"]
    assert not any("2026-2" in name for name in archive.namelist() if name.endswith(".pdf"))


def test_the_open_items_are_those_of_the_period_end_and_the_payments_those_booked_in_it(
    db_session: Session, invoices: InvoiceService, exports: Exports
) -> None:
    acme = _customer(db_session)
    paid_in_period = _issue(invoices, acme, "1000.00", D(2026, 1, 10), files=False)
    paid_later = _issue(invoices, acme, "500.00", D(2026, 2, 10), files=False)
    _issue(invoices, acme, "300.00", D(2026, 5, 10), files=False)  # born after the period
    items = DueItemService(db_session)
    by_invoice = {item.source_id: item for item in _items_of(db_session)}
    items.settle(
        by_invoice[paid_in_period.id].id,
        SettlementCreate(amount=Decimal("1000.00"), settled_on=D(2026, 3, 5)),
        ADMIN,
    )
    items.settle(
        by_invoice[paid_later.id].id,
        SettlementCreate(amount=Decimal("200.00"), settled_on=D(2026, 4, 5)),
        ADMIN,
    )

    archive = _open(exports.build(Q1, ADMIN, now=NOW).content)
    open_rows = _tables(_text(archive, "scadenze/scadenze-aperte.csv"))[0][2:]
    payments = _tables(_text(archive, "pagamenti/pagamenti.csv"))[0][2:]

    assert [(row[0], row[6], row[7], row[8]) for row in open_rows] == [
        ("Da incassare", "500,00", "0,00", "500,00")
    ]
    assert [(row[0], row[2], row[4], row[6]) for row in payments] == [
        ("2026-03-05", "Da incassare", "Fattura emessa", "1.000,00")
    ]


def _items_of(session: Session):
    from sqlalchemy import select

    from pigrocrm.core.due_items.models import DueItem

    return list(session.scalars(select(DueItem).where(DueItem.source_kind == "sales_invoice")))


# --- parts left out --------------------------------------------------------------------


def test_the_sales_register_is_omitted_for_a_company_outside_italy_and_the_index_says_why(
    db_session: Session, invoices: InvoiceService, exports: Exports
) -> None:
    entity = LegalEntityRepository(db_session).default()
    assert entity is not None
    profile = db_session.query(FiscalProfile).filter_by(legal_entity_id=entity.id).one()
    profile.pack_id = "non-it"
    db_session.flush()

    export = exports.build(Q1, ADMIN, now=NOW)

    assert "vendite/registro-vendite.csv" not in _open(export.content).namelist()
    omitted = [line for line in export.index.parts if line.status == "omitted"]
    assert [(line.key, line.reason) for line in omitted] == [
        ("sales_register", "non si applica al regime dell'azienda (non-it)")
    ]
    assert {line.key for line in export.index.parts if line.status == "included"} == {
        "issued_invoice_files",
        "received_invoice_files",
        "receipts_and_expenses",
        "open_due_items",
        "settlements",
    }


def test_a_part_whose_module_is_off_is_omitted_with_the_module_named(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    def build(ctx: PartContext) -> PartOutput:
        return PartOutput([BundleFile("a.csv", b"x")], [IndexEntry("a.csv", 1, "rows")])

    part = PartDefinition("p", "p", "Parte", "per data", "CSV", build, requires_modules=("deals",))
    entity = LegalEntityRepository(db_session).default()
    assert entity is not None

    def context(settings) -> PartContext:  # type: ignore[no-untyped-def]
        return PartContext(db_session, local_storage, settings, ADMIN, Q1, entity, "it-flat-rate")

    off = get_settings().model_copy(update={"module_deals": False})
    on = get_settings().model_copy(update={"module_deals": True})
    assert part.omission(context(off)) == "il modulo «deals» è spento"
    assert part.omission(context(on)) is None


def test_no_part_is_both_built_and_declared_missing() -> None:
    assert {part.key for part in PARTS}.isdisjoint({part.key for part in UNAVAILABLE})
    assert len({part.key for part in PARTS}) == len(PARTS)
    assert len({part.folder for part in PARTS}) == len(PARTS)


# --- every entity, and who may ask -----------------------------------------------------


def _second_entity(session: Session) -> LegalEntity:
    entity = LegalEntity(
        name="Rebase Ltd",
        legal_name="Rebase Ltd",
        country="GB",
        is_default=False,
    )
    session.add(entity)
    session.flush()
    return entity


def test_an_all_entities_export_has_a_folder_per_entity_and_one_index(
    db_session: Session, invoices: InvoiceService, exports: Exports
) -> None:
    acme = _customer(db_session)
    _issue(invoices, acme, "100.00", D(2026, 2, 1))
    _second_entity(db_session)

    export = exports.build(Q1, ADMIN, all_entities=True, now=NOW)
    names = _open(export.content).namelist()

    assert export.filename.startswith("commercialista-tutte-le-aziende-2026-01-01_2026-03-31-")
    assert "studio-rossi/vendite/registro-vendite.csv" in names
    assert "rebase-ltd/pagamenti/pagamenti.csv" in names
    assert names.count("indice.csv") == 1
    sections = {line.section for line in export.index.parts}
    assert sections == {"Studio Rossi", "Rebase Ltd", "Acquisti condivisi"}


def test_a_space_with_two_entities_asks_which_one(db_session: Session, exports: Exports) -> None:
    other = _second_entity(db_session)

    with pytest.raises(ValidationFailed):
        exports.build(Q1, ADMIN, now=NOW)
    named = exports.build(Q1, ADMIN, other.id, now=NOW)
    assert named.filename.startswith("commercialista-rebase-ltd-")


@pytest.mark.parametrize("actor", [COLLABORATOR, Actor(id=uuid4(), type="user", role="readonly")])
def test_only_an_admin_builds_an_export(exports: Exports, actor: Actor) -> None:
    with pytest.raises(PermissionDenied):
        exports.build(Q1, actor, now=NOW)


def test_an_agent_needs_full_access(exports: Exports) -> None:
    with pytest.raises(AgentForbidden):
        exports.build(Q1, Actor(id=uuid4(), type="mcp", role="admin"), now=NOW)
    full = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
    assert exports.build(Q1, full, now=NOW).filename.endswith(".zip")


def test_a_scoped_admin_cannot_export_an_entity_outside_their_scope_or_all_of_them(
    db_session: Session, exports: Exports
) -> None:
    default = LegalEntityRepository(db_session).default()
    assert default is not None
    other = _second_entity(db_session)
    scoped = Actor(id=uuid4(), type="user", role="admin", legal_entities=(default.id,))

    with pytest.raises(NotFound):
        exports.build(Q1, scoped, other.id, now=NOW)
    with pytest.raises(ScopedAdmin):
        exports.build(Q1, scoped, all_entities=True, now=NOW)
    assert exports.build(Q1, scoped, default.id, now=NOW).filename.endswith(".zip")


def test_all_entities_and_one_entity_are_not_asked_together(
    db_session: Session, exports: Exports
) -> None:
    default = LegalEntityRepository(db_session).default()
    assert default is not None
    with pytest.raises(ValidationFailed):
        exports.build(Q1, ADMIN, default.id, all_entities=True, now=NOW)


@pytest.mark.parametrize(
    ("date_from", "date_to"),
    [(D(2026, 3, 31), D(2026, 1, 1)), (D(2026, 1, 1), D(2027, 1, 2))],
)
def test_a_period_is_ordered_and_no_longer_than_a_year(
    exports: Exports, date_from: dt.date, date_to: dt.date
) -> None:
    with pytest.raises(ValidationFailed):
        exports.build(Period(date_from, date_to), ADMIN, now=NOW)


def test_a_whole_year_is_one_period(exports: Exports) -> None:
    exports.build(Period(D(2028, 1, 1), D(2028, 12, 31)), ADMIN, now=NOW)  # a leap year


def test_equal_inputs_make_the_same_bytes(
    db_session: Session, invoices: InvoiceService, exports: Exports
) -> None:
    _issue(invoices, _customer(db_session), "100.00", D(2026, 2, 1))

    assert exports.build(Q1, ADMIN, now=NOW).content == exports.build(Q1, ADMIN, now=NOW).content


# --- what the space bought -------------------------------------------------------------

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def _supplier(session: Session) -> Organization:
    supplier = Organization(legal_name="Vendor Srl", country="IT", roles=["supplier"])
    session.add(supplier)
    session.flush()
    return supplier


def _document(session: Session, storage: LocalFileStorage, owner: Organization, title: str) -> UUID:
    return (
        DocumentService(session, storage, get_settings())
        .import_bytes(
            customer_id=owner.id,
            type="document",
            title=title,
            data=PDF,
            content_type="application/pdf",
            actor=ADMIN,
            origin={"source": "test"},
        )
        .id
    )


def _buy(
    session: Session,
    category_id: UUID,
    entity_id: UUID | None,
    day: dt.date,
    **overrides: object,
):
    values: dict[str, object] = {
        "category_id": category_id,
        "legal_entity_id": entity_id,
        "date": day,
        "amount": Decimal("12.20"),
        "description": "Biglietto",
        "merchant_name": "Trenitalia",
        "kind": "expense",
    }
    values.update(overrides)
    return PurchaseService(session).create(PurchaseCreate(**values), ADMIN)  # type: ignore[arg-type]


def test_receipts_and_expenses_are_listed_with_their_files_and_received_invoices_are_filed(
    db_session: Session,
    local_storage: LocalFileStorage,
    exports: Exports,
    seeded_category_id: UUID,
) -> None:
    entity = LegalEntityRepository(db_session).default()
    assert entity is not None
    supplier = _supplier(db_session)
    receipt_doc = _document(db_session, local_storage, supplier, "Scontrino")
    invoice_doc = _document(db_session, local_storage, supplier, "Fattura FA/12")
    _buy(
        db_session,
        seeded_category_id,
        entity.id,
        D(2026, 2, 3),
        kind="receipt",
        document_id=receipt_doc,
    )
    _buy(db_session, seeded_category_id, entity.id, D(2026, 2, 9))  # an expense with no file
    _buy(db_session, seeded_category_id, entity.id, D(2026, 5, 1))  # next quarter
    _buy(
        db_session,
        seeded_category_id,
        entity.id,
        D(2026, 3, 5),
        kind="invoice",
        supplier_id=supplier.id,
        number="FA/12",
        amount=Decimal("122.00"),
        vat_lines=[
            VatLine(
                rate=Decimal("22.00"), taxable_amount=Decimal("100.00"), vat_amount=Decimal("22.00")
            )
        ],
        document_id=invoice_doc,
    )

    export = exports.build(Q1, ADMIN, now=NOW)
    archive = _open(export.content)
    names = archive.namelist()

    listing = _tables(_text(archive, "ricevute-e-spese/elenco.csv"))[0]
    assert listing[0][:3] == ["Data", "Tipo", "Fornitore o esercente"]
    assert [(row[0], row[1], row[2], row[6], row[10]) for row in listing[1:]] == [
        ("2026-02-03", "Ricevuta", "Trenitalia", "12,20", listing[1][10]),
        ("2026-02-09", "Spesa", "Trenitalia", "12,20", ""),
    ]
    assert listing[1][10].startswith("2026-02-03-") and listing[1][10] in [
        name.removeprefix("ricevute-e-spese/") for name in names
    ]
    assert [name for name in names if name.startswith("fatture-ricevute/")] == [
        next(name for name in names if name.startswith("fatture-ricevute/2026-03-05-FA-12-"))
    ]
    missing = [
        (item.missing.document.split()[0], item.missing.kind, item.missing.reason)
        for item in export.index.missing
    ]
    assert missing == [("2026-02-09", "Spesa", "nessun documento allegato")]
    entries = {line.entry.path: line.entry.count for line in export.index.parts if line.entry}
    assert entries["ricevute-e-spese/elenco.csv"] == 2
    assert entries["ricevute-e-spese/"] == 1 and entries["fatture-ricevute/"] == 1


def test_a_shared_purchase_is_in_the_all_entities_export_only(
    db_session: Session,
    exports: Exports,
    seeded_category_id: UUID,
) -> None:
    entity = LegalEntityRepository(db_session).default()
    assert entity is not None
    _buy(db_session, seeded_category_id, entity.id, D(2026, 2, 3), description="Dell'azienda")
    _buy(db_session, seeded_category_id, None, D(2026, 2, 4), description="Condivisa")
    _second_entity(db_session)  # with two entities «shared» belongs to neither

    one = _open(exports.build(Q1, ADMIN, entity.id, now=NOW).content)
    everything = exports.build(Q1, ADMIN, all_entities=True, now=NOW)
    all_of_them = _open(everything.content)

    assert "Condivisa" not in _text(one, "ricevute-e-spese/elenco.csv")
    assert "condivisi/ricevute-e-spese/elenco.csv" in all_of_them.namelist()
    shared = _text(all_of_them, "condivisi/ricevute-e-spese/elenco.csv")
    assert "Condivisa" in shared and "Dell'azienda" not in shared
    assert "Dell'azienda" in _text(all_of_them, "studio-di-prova/ricevute-e-spese/elenco.csv")
    shared_lines = [line for line in everything.index.parts if line.section == "Acquisti condivisi"]
    assert {line.key for line in shared_lines if line.status == "included"} == {
        "received_invoice_files",
        "receipts_and_expenses",
    }
    assert {line.key for line in shared_lines if line.status == "unavailable"} == {
        "purchase_register"
    }


def test_a_cancelled_purchase_counts_nowhere(
    db_session: Session,
    exports: Exports,
    seeded_category_id: UUID,
) -> None:
    entity = LegalEntityRepository(db_session).default()
    assert entity is not None
    purchase = _buy(db_session, seeded_category_id, entity.id, D(2026, 2, 3))
    PurchaseService(db_session).cancel(purchase.id, ADMIN)

    listing = _tables(
        _text(_open(exports.build(Q1, ADMIN, now=NOW).content), "ricevute-e-spese/elenco.csv")
    )[0]

    assert len(listing) == 1  # the header only


def test_the_purchase_parts_are_omitted_when_the_purchases_module_is_off(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    settings = get_settings().model_copy(update={"module_purchases": False})
    export = Exports(AccountantExportService(db_session, local_storage, settings)).build(
        Q1, ADMIN, now=NOW
    )

    omitted = {line.key: line.reason for line in export.index.parts if line.status == "omitted"}
    assert omitted == {
        "received_invoice_files": "il modulo «purchases» è spento",
        "receipts_and_expenses": "il modulo «purchases» è spento",
    }
    assert not any(name.startswith("ricevute-e-spese") for name in _open(export.content).namelist())


def test_each_declared_gap_is_listed_in_exactly_one_of_two_monthly_exports(
    db_session: Session, exports: Exports
) -> None:
    acme = _customer(db_session)
    entity = LegalEntityRepository(db_session).default()
    assert entity is not None
    for number, day in ((1, D(2026, 1, 20)), (2, D(2026, 1, 25)), (5, D(2026, 2, 10))):
        db_session.add(
            Invoice(
                customer_id=acme.id,
                type="invoice",
                status="issued",
                year=2026,
                number=number,
                issue_date=day,
                taxable_amount=Decimal("10.00"),
                total=Decimal("10.00"),
            )
        )
    for number in (3, 4):
        db_session.add(
            InvoiceRegisterGap(
                legal_entity_id=entity.id, year=2026, number=number, reason="numero saltato"
            )
        )
    db_session.flush()

    def struck(period: Period) -> list[str]:
        archive = _open(exports.build(period, ADMIN, now=NOW).content)
        rows = _tables(_text(archive, "vendite/numeri-annullati-e-non-usati.csv"))[0]
        return [row[0] for row in rows[1:]]

    assert struck(Period(D(2026, 1, 1), D(2026, 1, 31))) == []
    assert struck(Period(D(2026, 2, 1), D(2026, 2, 28))) == ['="2026/3"', '="2026/4"']


def test_an_open_item_is_read_as_of_the_period_end_whatever_happened_to_it_after(
    db_session: Session, invoices: InvoiceService, exports: Exports
) -> None:
    from pigrocrm.core.due_items.schemas import DueItemCreate

    entity = LegalEntityRepository(db_session).default()
    assert entity is not None
    acme = _customer(db_session)
    service = DueItemService(db_session)

    def item(amount: str, **fields: object):
        values: dict[str, object] = {
            "legal_entity_id": entity.id,
            "direction": "out",
            "organization_id": acme.id,
            "amount": Decimal(amount),
            "source_kind": "recurring_expense",
            "source_id": uuid4(),
            "own_date": D(2026, 2, 10),
            "description": "x",
        }
        values.update(fields)
        return service.create(DueItemCreate(**values))  # type: ignore[arg-type]

    partly = item("100.00", description="pagata a metà")
    reversed_later = item("50.00", description="pagata e stornata dopo")
    later = item("70.00", description="di aprile", own_date=D(2026, 4, 10))
    service.settle(
        partly.id, SettlementCreate(amount=Decimal("40.00"), settled_on=D(2026, 3, 1)), ADMIN
    )
    paid = service.settle(
        reversed_later.id,
        SettlementCreate(amount=Decimal("50.00"), settled_on=D(2026, 3, 2)),
        ADMIN,
    )
    service.reverse_settlement(paid.settlements[0].id, "errore", ADMIN)
    assert later.id

    archive = _open(exports.build(Q1, ADMIN, now=NOW).content)
    rows = _tables(_text(archive, "scadenze/scadenze-aperte.csv"))[0][2:]

    # Paid and reversed after the quarter: it was paid at the quarter's end. Written after
    # the quarter by its own date: not there yet. Only the half-paid one was open.
    assert [(row[3], row[7], row[8]) for row in rows] == [("pagata a metà", "40,00", "60,00")]


def test_in_a_space_with_one_legal_entity_the_shared_purchases_are_that_entitys(
    db_session: Session, exports: Exports, seeded_category_id: UUID
) -> None:
    """`PurchaseService` leaves a receipt with no deal and no entity «shared», and with one
    legal entity that can only mean it: its export carries the receipt, and the all-entities
    export carries it once, in the shared section."""
    _buy(db_session, seeded_category_id, None, D(2026, 2, 4), description="Senza azienda")

    one = _open(exports.build(Q1, ADMIN, now=NOW).content)
    everything = _open(exports.build(Q1, ADMIN, all_entities=True, now=NOW).content)

    assert "Senza azienda" in _text(one, "ricevute-e-spese/elenco.csv")
    assert "Senza azienda" in _text(everything, "condivisi/ricevute-e-spese/elenco.csv")
    assert "Senza azienda" not in _text(everything, "studio-di-prova/ricevute-e-spese/elenco.csv")


def test_two_received_invoices_with_one_number_and_one_title_keep_both_files(
    db_session: Session,
    local_storage: LocalFileStorage,
    exports: Exports,
    seeded_category_id: UUID,
) -> None:
    entity = LegalEntityRepository(db_session).default()
    assert entity is not None
    for name in ("Vendor A Srl", "Vendor B Srl"):
        supplier = Organization(legal_name=name, country="IT", roles=["supplier"])
        db_session.add(supplier)
        db_session.flush()
        _buy(
            db_session,
            seeded_category_id,
            entity.id,
            D(2026, 3, 5),
            kind="invoice",
            supplier_id=supplier.id,
            number="1",
            amount=Decimal("122.00"),
            vat_lines=[
                VatLine(
                    rate=Decimal("22.00"),
                    taxable_amount=Decimal("100.00"),
                    vat_amount=Decimal("22.00"),
                )
            ],
            document_id=_document(db_session, local_storage, supplier, "Fattura"),
        )

    names = _open(exports.build(Q1, ADMIN, now=NOW).content).namelist()

    assert len(set(names)) == len(names)
    assert len([name for name in names if name.startswith("fatture-ricevute/")]) == 2


# --- stored exports (REB-1012) ---------------------------------------------------------


def _stored(export: Built) -> list[str]:
    return [part.download_url for part in export.stored.parts]


def test_building_the_same_period_twice_keeps_both_exports(
    db_session: Session,
    invoices: InvoiceService,
    exports: Exports,
    local_storage: LocalFileStorage,
) -> None:
    _issue(invoices, _customer(db_session), "100.00", D(2026, 2, 1))

    first = exports.build(Q1, ADMIN, now=NOW)
    second = exports.build(Q1, ADMIN, now=NOW)

    assert first.stored.id != second.stored.id
    assert first.filename != second.filename
    keys = {row.prefix: row.parts[0]["key"] for row in db_session.query(AccountantExportRow).all()}
    assert len(keys) == 2
    for key in keys.values():
        assert local_storage.get(key).startswith(b"PK")
    assert first.content == second.content
    # The prefix is the one the Drive delivery names: entity, period, then the moment.
    assert all(
        prefix.startswith("commercialista/studio-rossi/2026-01-01_2026-03-31/20260402t093000z-")
        for prefix in keys
    )


def test_a_build_leaves_a_trace_and_a_snapshot_of_its_index(
    db_session: Session, invoices: InvoiceService, exports: Exports
) -> None:
    _issue(invoices, _customer(db_session), "100.00", D(2026, 2, 1))

    built = exports.build(Q1, ADMIN, now=NOW)

    row = db_session.get(AccountantExportRow, built.stored.id)
    assert row is not None
    assert row.expires_at == dt.datetime(2027, 5, 2, 9, 30, tzinfo=dt.UTC)
    assert {line["key"] for line in row.checklist["parts"]} >= {"sales_register"}
    omitted = {
        line["key"]: line["cause"] for line in row.checklist["parts"] if line["status"] == "omitted"
    }
    assert omitted == {}
    kinds = [a.kind for a in db_session.query(Activity).filter_by(entity_id=row.id)]
    assert kinds == ["accountant_export_built"]


def test_a_bundle_over_the_ceiling_comes_out_in_parts_that_each_open_alone(
    db_session: Session,
    invoices: InvoiceService,
    local_storage: LocalFileStorage,
    exports: Exports,
) -> None:
    customer = _customer(db_session)
    for day in range(1, 6):
        _issue(invoices, customer, "100.00", D(2026, 2, day))
    whole = exports.build(Q1, ADMIN, now=NOW)
    sizes = {name: info.file_size for name, info in _sizes(whole.content).items()}
    ceiling = max(sizes.values()) + 1
    assert sum(sizes.values()) > 2 * ceiling

    service = AccountantExportService(db_session, local_storage, get_settings())
    service.part_limit = ceiling
    stored = service.build_accountant_export(Q1, ADMIN, now=NOW)

    parts = stored.export.parts
    assert len(parts) > 1
    assert [p.filename.split("-part-")[1] for p in parts] == [
        f"{n}-of-{len(parts)}.zip" for n in range(1, len(parts) + 1)
    ]
    members: list[str] = []
    for part in parts:
        data, _, _ = service.download_part(stored.export.id, part.part, ADMIN)
        archive = _open(data)
        assert archive.testzip() is None
        members.extend(archive.namelist())
        # a part is never over the ceiling unless one file alone is
        body = [i for i in archive.infolist() if i.filename != "indice.csv"]
        assert sum(i.file_size for i in body) <= ceiling
    assert sorted(members) == sorted(whole_names := _open(whole.content).namelist())
    assert whole_names.count("indice.csv") == 1
    index = _text(_open(service.download_part(stored.export.id, 1, ADMIN)[0]), "indice.csv")
    assert "Archivio" in index and parts[1].filename in index


def _sizes(content: bytes) -> dict[str, zipfile.ZipInfo]:
    return {info.filename: info for info in _open(content).infolist()}


def test_originals_wait_on_disk_while_the_bundle_is_built(
    db_session: Session,
    invoices: InvoiceService,
    exports: Exports,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _issue(invoices, _customer(db_session), "100.00", D(2026, 2, 1))
    kept: list[str] = []
    original = Spool.keep

    def keep(self: Spool, path: str, data: bytes) -> BundleFile:
        file = original(self, path, data)
        assert file.source is not None and file.source.read_bytes() == data
        kept.append(path)
        return file

    monkeypatch.setattr(Spool, "keep", keep)

    built = exports.build(Q1, ADMIN, now=NOW)

    assert len(kept) == 2  # the invoice's XML and PDF
    names = _open(built.content).namelist()
    assert all(any(n.endswith(path) for n in names) for path in kept)


def test_a_failed_put_leaves_no_row_and_no_file(
    db_session: Session, invoices: InvoiceService, local_storage: LocalFileStorage
) -> None:
    _issue(invoices, _customer(db_session), "100.00", D(2026, 2, 1))

    class Failing:
        def __init__(self) -> None:
            self.put_keys: list[str] = []
            self.deleted: list[str] = []

        def put(self, key: str, data: bytes, content_type: str) -> None:
            self.put_keys.append(key)
            if len(self.put_keys) == 2:
                raise OSError("disk full")
            local_storage.put(key, data, content_type)

        def get(self, key: str) -> bytes:
            return local_storage.get(key)

        def delete(self, key: str) -> None:
            self.deleted.append(key)
            local_storage.delete(key)

        def signed_url(self, key: str, ttl: dt.timedelta) -> str | None:
            return None

    storage = Failing()
    service = AccountantExportService(db_session, storage, get_settings())
    service.part_limit = 1  # one file a part: more than one part

    with pytest.raises(OSError):
        service.build_accountant_export(Q1, ADMIN, now=NOW)

    assert db_session.query(AccountantExportRow).count() == 0
    assert storage.deleted == storage.put_keys[:1]


def test_an_export_is_deleted_after_thirteen_months_and_leaves_a_trace(
    db_session: Session,
    invoices: InvoiceService,
    exports: Exports,
    local_storage: LocalFileStorage,
) -> None:
    _issue(invoices, _customer(db_session), "100.00", D(2026, 2, 1))
    built = exports.build(Q1, ADMIN, now=NOW)
    key = db_session.get(AccountantExportRow, built.stored.id).parts[0]["key"]  # type: ignore[union-attr]
    retention = ExportRetention(db_session, lambda: local_storage)

    early = retention.run(now=dt.datetime(2027, 5, 1, tzinfo=dt.UTC), dry_run=False)
    assert early == RetentionReport(deleted=0)
    assert local_storage.get(key).startswith(b"PK")

    later = dt.datetime(2027, 5, 3, tzinfo=dt.UTC)
    assert retention.run(now=later, dry_run=True) == RetentionReport(deleted=1)
    assert local_storage.get(key).startswith(b"PK")
    assert retention.run(now=later, dry_run=False) == RetentionReport(deleted=1)

    with pytest.raises(NotFound):
        local_storage.get(key)
    row = db_session.get(AccountantExportRow, built.stored.id)
    assert row is not None and row.deleted_at == later
    kinds = [
        a.kind for a in db_session.query(Activity).filter_by(entity_id=row.id).order_by(Activity.id)
    ]
    assert kinds == ["accountant_export_built", "accountant_export_deleted"]
    # Nothing left to fetch, and it is said so; a second pass finds nothing.
    service = AccountantExportService(db_session, local_storage, get_settings())
    assert service.get_export_download_url(row.id, ADMIN).parts == []
    with pytest.raises(NotFound):
        service.download_part(row.id, 1, ADMIN)
    assert retention.run(now=later, dry_run=False) == RetentionReport(deleted=0)


def test_an_export_whose_files_cannot_be_deleted_is_kept_for_the_next_pass(
    db_session: Session, invoices: InvoiceService, exports: Exports
) -> None:
    _issue(invoices, _customer(db_session), "100.00", D(2026, 2, 1))
    built = exports.build(Q1, ADMIN, now=NOW)

    class Stuck:
        def delete(self, key: str) -> None:
            raise OSError("read only")

    report = ExportRetention(db_session, lambda: Stuck()).run(  # type: ignore[arg-type,return-value]
        now=dt.datetime(2027, 6, 1, tzinfo=dt.UTC), dry_run=False
    )

    assert report == RetentionReport(deleted=0, failed=1)
    row = db_session.get(AccountantExportRow, built.stored.id)
    assert row is not None and row.deleted_at is None


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (dt.datetime(2026, 4, 2, tzinfo=dt.UTC), dt.datetime(2027, 5, 2, tzinfo=dt.UTC)),
        (dt.datetime(2026, 12, 31, tzinfo=dt.UTC), dt.datetime(2028, 1, 31, tzinfo=dt.UTC)),
        (dt.datetime(2027, 1, 31, tzinfo=dt.UTC), dt.datetime(2028, 2, 29, tzinfo=dt.UTC)),
        (dt.datetime(2026, 11, 30, tzinfo=dt.UTC), dt.datetime(2027, 12, 30, tzinfo=dt.UTC)),
    ],
)
def test_thirteen_months_are_calendar_months(start: dt.datetime, expected: dt.datetime) -> None:
    assert add_months(start, RETENTION_MONTHS) == expected


def test_who_may_fetch_a_stored_export(
    db_session: Session, exports: Exports, local_storage: LocalFileStorage
) -> None:
    default = LegalEntityRepository(db_session).default()
    assert default is not None
    other = _second_entity(db_session)
    built = exports.build(Q1, ADMIN, other.id, now=NOW)
    everything = exports.build(Q1, ADMIN, all_entities=True, now=NOW)
    service = AccountantExportService(db_session, local_storage, get_settings())
    scoped = Actor(id=uuid4(), type="user", role="admin", legal_entities=(default.id,))

    with pytest.raises(NotFound):
        service.get_export_download_url(built.stored.id, scoped)
    with pytest.raises(ScopedAdmin):
        service.get_export_download_url(everything.stored.id, scoped)
    with pytest.raises(PermissionDenied):
        service.get_export_download_url(built.stored.id, COLLABORATOR)
    with pytest.raises(AgentForbidden):
        service.get_export_download_url(
            built.stored.id, Actor(id=uuid4(), type="mcp", role="admin")
        )
    full = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
    read = service.get_export_download_url(built.stored.id, full)
    assert read.parts[0].download_url == f"/api/accountant-export/exports/{read.id}/parts/1"
    assert {e.id for e in service.list_exports(ADMIN)} == {built.stored.id, everything.stored.id}


@pytest.mark.parametrize("name", ["Studio " + "Rossi e Bianchi " * 4, "Aux", "!!!"])
def test_a_long_or_reserved_entity_name_still_makes_a_storable_key(
    db_session: Session, exports: Exports, local_storage: LocalFileStorage, name: str
) -> None:
    entity = LegalEntityRepository(db_session).default()
    assert entity is not None
    entity.name = name[:80]
    db_session.flush()

    stored = exports.service.build_accountant_export(Q1, ADMIN, now=NOW)

    key = db_session.get(AccountantExportRow, stored.export.id).parts[0]["key"]  # type: ignore[union-attr]
    assert len(key) <= 255
    assert local_storage.get(key).startswith(b"PK")


def test_a_partly_deleted_export_is_finished_by_the_next_pass(
    db_session: Session,
    invoices: InvoiceService,
    local_storage: LocalFileStorage,
) -> None:
    _issue(invoices, _customer(db_session), "100.00", D(2026, 2, 1))
    service = AccountantExportService(db_session, local_storage, get_settings())
    service.part_limit = 1
    stored = service.build_accountant_export(Q1, ADMIN, now=NOW)
    keys = [
        part["key"]
        for part in db_session.get(AccountantExportRow, stored.export.id).parts  # type: ignore[union-attr]
    ]
    assert len(keys) > 1

    class FirstOnly:
        def delete(self, key: str) -> None:
            if key != keys[0]:
                raise OSError("read only")
            local_storage.delete(key)

    later = dt.datetime(2027, 6, 1, tzinfo=dt.UTC)
    first = ExportRetention(db_session, lambda: FirstOnly()).run(now=later, dry_run=False)  # type: ignore[arg-type,return-value]
    assert first == RetentionReport(deleted=0, failed=1)
    second = ExportRetention(db_session, lambda: local_storage).run(now=later, dry_run=False)
    assert second == RetentionReport(deleted=1)
    for key in keys:
        with pytest.raises(NotFound):
            local_storage.get(key)


# --- what the page reads before a build: the parts, the list's year and count -----------


def test_the_parts_that_apply_follow_the_modules_and_the_pack(
    db_session: Session, local_storage: LocalFileStorage, invoices: InvoiceService
) -> None:
    service = AccountantExportService(db_session, local_storage, get_settings())
    keys = [part.key for part in service.describe_parts(ADMIN)]
    assert keys == [part.key for part in PARTS]
    sales = service.describe_parts(ADMIN)[0]
    assert (sales.label, sales.date_rule, sales.contents) == (
        "Registro delle vendite",
        "per data di emissione",
        "Elenco CSV",
    )

    off = get_settings().model_copy(update={"module_purchases": False})
    without_purchases = AccountantExportService(db_session, local_storage, off)
    assert "received_invoice_files" not in [p.key for p in without_purchases.describe_parts(ADMIN)]

    entity = LegalEntityRepository(db_session).default()
    assert entity is not None
    profile = db_session.query(FiscalProfile).filter_by(legal_entity_id=entity.id).one()
    profile.pack_id = "non-it"
    db_session.flush()
    assert "sales_register" not in [part.key for part in service.describe_parts(ADMIN)]


def test_the_parts_read_follows_the_builds_rules_about_who_asks_for_what(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = AccountantExportService(db_session, local_storage, get_settings())
    default = LegalEntityRepository(db_session).default()
    assert default is not None
    _second_entity(db_session)
    scoped = Actor(id=uuid4(), type="user", role="admin", legal_entities=(default.id,))

    with pytest.raises(PermissionDenied):
        service.describe_parts(COLLABORATOR, default.id)
    with pytest.raises(ScopedAdmin):
        service.describe_parts(scoped, all_entities=True)
    with pytest.raises(NotFound):
        service.describe_parts(scoped, uuid4())
    with pytest.raises(ValidationFailed):
        service.describe_parts(ADMIN, default.id, all_entities=True)
    assert service.describe_parts(ADMIN, all_entities=True)


def test_a_part_left_out_says_whether_a_module_or_the_pack_did_it(
    db_session: Session, local_storage: LocalFileStorage, invoices: InvoiceService
) -> None:
    settings = get_settings().model_copy(update={"module_purchases": False})
    export = Exports(AccountantExportService(db_session, local_storage, settings)).build(
        Q1, ADMIN, now=NOW
    )
    causes = {
        line["key"]: line["cause"]
        for line in db_session.get(AccountantExportRow, export.stored.id).checklist["parts"]  # type: ignore[union-attr]
        if line["status"] == "omitted"
    }
    assert causes == {"received_invoice_files": "module", "receipts_and_expenses": "module"}

    entity = LegalEntityRepository(db_session).default()
    assert entity is not None
    db_session.query(FiscalProfile).filter_by(legal_entity_id=entity.id).one().pack_id = "non-it"
    db_session.flush()
    again = Exports(AccountantExportService(db_session, local_storage, get_settings())).build(
        Q1, ADMIN, now=NOW
    )
    packed = {
        line["key"]: line["cause"]
        for line in db_session.get(AccountantExportRow, again.stored.id).checklist["parts"]  # type: ignore[union-attr]
        if line["status"] == "omitted"
    }
    assert packed == {"sales_register": "pack"}


def test_the_list_pages_by_offset_and_filters_by_the_year_the_period_starts(
    db_session: Session, exports: Exports, local_storage: LocalFileStorage
) -> None:
    service = exports.service
    first = exports.build(Q1, ADMIN, now=NOW)
    second = exports.build(Q1, ADMIN, now=NOW + dt.timedelta(minutes=1))
    last_year = exports.build(
        Period(D(2025, 10, 1), D(2025, 12, 31)), ADMIN, now=NOW + dt.timedelta(minutes=2)
    )

    assert [e.id for e in service.list_exports(ADMIN)] == [
        last_year.stored.id,
        second.stored.id,
        first.stored.id,
    ]
    assert [e.id for e in service.list_exports(ADMIN, offset=1, limit=1)] == [second.stored.id]
    assert [e.id for e in service.list_exports(ADMIN, year=2026)] == [
        second.stored.id,
        first.stored.id,
    ]
    assert service.count_exports(ADMIN) == 3
    assert service.count_exports(ADMIN, year=2025) == 1
    assert service.count_exports(ADMIN, year=2024) == 0
    with pytest.raises(PermissionDenied):
        service.count_exports(COLLABORATOR)
