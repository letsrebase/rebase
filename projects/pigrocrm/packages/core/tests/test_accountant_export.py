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
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from conftest import _invoice_service
from pigrocrm.core.accountant_export.parts import (
    BundleFile,
    IndexEntry,
    PartContext,
    PartDefinition,
    PartOutput,
    Period,
)
from pigrocrm.core.accountant_export.registry import PARTS, UNAVAILABLE
from pigrocrm.core.accountant_export.service import AccountantExportService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import get_settings
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


@pytest.fixture
def exports(db_session: Session, local_storage: LocalFileStorage) -> AccountantExportService:
    return AccountantExportService(db_session, local_storage, get_settings())


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
    db_session: Session, invoices: InvoiceService, exports: AccountantExportService
) -> None:
    acme = _customer(db_session)
    first = _issue(invoices, acme, "1000.00", D(2026, 2, 10))
    _issue(invoices, acme, "250.50", D(2026, 3, 20))
    _issue(invoices, acme, "999.00", D(2026, 4, 5))  # the next quarter

    export = exports.build(Q1, ADMIN, now=NOW)

    assert export.filename == "commercialista-studio-rossi-2026-01-01_2026-03-31.zip"
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
    assert {key for key, _ in parts if key in {part.key for part in PARTS}} == {
        part.key for part in PARTS
    }
    assert {(part.key, "included") for part in PARTS} <= parts
    assert {(part.key, "unavailable") for part in UNAVAILABLE} <= parts


def test_the_index_is_a_csv_with_the_row_counts_and_the_reasons(
    db_session: Session, invoices: InvoiceService, exports: AccountantExportService
) -> None:
    acme = _customer(db_session)
    _issue(invoices, acme, "1000.00", D(2026, 2, 10))
    _issue(invoices, acme, "250.50", D(2026, 3, 20))

    archive = _open(exports.build(Q1, ADMIN, now=NOW).content)
    meta, parts_table, missing = _tables(_text(archive, "indice.csv"))
    header, *parts = parts_table

    assert ["Azienda", "Studio Rossi"] in meta
    assert ["Periodo", "2026-01-01 - 2026-03-31"] in meta
    assert header[0] == "Sezione" and missing == [["Sezione", "Documento", "File", "Motivo"]]
    by_file = {row[3]: row for row in parts if row[3]}
    assert by_file["vendite/registro-vendite.csv"][4:6] == ["2", "righe"]
    assert by_file["fatture-emesse/"][4:6] == ["4", "file"]
    unavailable = [row for row in parts if row[2] == "Non disponibile"]
    assert {row[1] for row in unavailable} == {part.label for part in UNAVAILABLE}
    assert all(row[7] for row in unavailable)


def test_the_sales_register_has_the_vat_columns_and_totals(
    db_session: Session, invoices: InvoiceService, exports: AccountantExportService
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
    db_session: Session, invoices: InvoiceService, exports: AccountantExportService
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

    assert [(row[6], row[7], row[9], row[10]) for row in rows[:-1]] == [
        ("0,00", "N4", "100,00", "0,00"),
        ("10,00", "", "100,00", "10,00"),
        ("22,00", "", "100,00", "22,00"),
    ]
    assert rows[-1][9:11] == ["300,00", "32,00"]
    # No snapshot to read a customer from: said so, not guessed.
    assert rows[0][2] == "Cliente non disponibile"


def test_annulled_numbers_and_declared_gaps_are_listed_between_the_numbers_of_the_period(
    db_session: Session, invoices: InvoiceService, exports: AccountantExportService
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
    db_session: Session, invoices: InvoiceService, exports: AccountantExportService
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
    db_session: Session, invoices: InvoiceService, exports: AccountantExportService
) -> None:
    acme = _customer(db_session)
    _issue(invoices, acme, "100.00", D(2026, 2, 1))
    _issue(invoices, acme, "200.00", D(2026, 2, 2), files=False)

    export = exports.build(Q1, ADMIN, now=NOW)
    archive = _open(export.content)

    missing = [(item.missing.document, item.missing.kind) for item in export.index.missing]
    assert ("2026/2", "XML") in missing and ("2026/2", "PDF") in missing
    assert all(document == "2026/2" for document, _ in missing)
    rows = _tables(_text(archive, "indice.csv"))[-1]
    assert [row[1] for row in rows[1:]] == ["2026/2", "2026/2"]
    assert not any("2026-2" in name for name in archive.namelist() if name.endswith(".pdf"))


def test_the_open_items_are_those_of_the_period_end_and_the_payments_those_booked_in_it(
    db_session: Session, invoices: InvoiceService, exports: AccountantExportService
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

    return list(session.scalars(select(DueItem)))


# --- parts left out --------------------------------------------------------------------


def test_the_sales_register_is_omitted_for_a_company_outside_italy_and_the_index_says_why(
    db_session: Session, invoices: InvoiceService, exports: AccountantExportService
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
        "open_due_items",
        "settlements",
    }


def test_a_part_whose_module_is_off_is_omitted_with_the_module_named(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    def build(ctx: PartContext) -> PartOutput:
        return PartOutput([BundleFile("a.csv", b"x")], [IndexEntry("a.csv", 1, "rows")])

    part = PartDefinition("p", "p", "Parte", "per data", build, requires_modules=("deals",))
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
    db_session: Session, invoices: InvoiceService, exports: AccountantExportService
) -> None:
    acme = _customer(db_session)
    _issue(invoices, acme, "100.00", D(2026, 2, 1))
    _second_entity(db_session)

    export = exports.build(Q1, ADMIN, all_entities=True, now=NOW)
    names = _open(export.content).namelist()

    assert export.filename == "commercialista-tutte-le-aziende-2026-01-01_2026-03-31.zip"
    assert "studio-rossi/vendite/registro-vendite.csv" in names
    assert "rebase-ltd/pagamenti/pagamenti.csv" in names
    assert names.count("indice.csv") == 1
    sections = {line.section for line in export.index.parts}
    assert sections == {"Studio Rossi", "Rebase Ltd"}


def test_a_space_with_two_entities_asks_which_one(
    db_session: Session, exports: AccountantExportService
) -> None:
    other = _second_entity(db_session)

    with pytest.raises(ValidationFailed):
        exports.build(Q1, ADMIN, now=NOW)
    named = exports.build(Q1, ADMIN, other.id, now=NOW)
    assert named.filename.startswith("commercialista-rebase-ltd-")


@pytest.mark.parametrize("actor", [COLLABORATOR, Actor(id=uuid4(), type="user", role="readonly")])
def test_only_an_admin_builds_an_export(exports: AccountantExportService, actor: Actor) -> None:
    with pytest.raises(PermissionDenied):
        exports.build(Q1, actor, now=NOW)


def test_an_agent_needs_full_access(exports: AccountantExportService) -> None:
    with pytest.raises(AgentForbidden):
        exports.build(Q1, Actor(id=uuid4(), type="mcp", role="admin"), now=NOW)
    full = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
    assert exports.build(Q1, full, now=NOW).filename.endswith(".zip")


def test_a_scoped_admin_cannot_export_an_entity_outside_their_scope_or_all_of_them(
    db_session: Session, exports: AccountantExportService
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
    db_session: Session, exports: AccountantExportService
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
    exports: AccountantExportService, date_from: dt.date, date_to: dt.date
) -> None:
    with pytest.raises(ValidationFailed):
        exports.build(Period(date_from, date_to), ADMIN, now=NOW)


def test_a_whole_year_is_one_period(exports: AccountantExportService) -> None:
    exports.build(Period(D(2028, 1, 1), D(2028, 12, 31)), ADMIN, now=NOW)  # a leap year


def test_equal_inputs_make_the_same_bytes(
    db_session: Session, invoices: InvoiceService, exports: AccountantExportService
) -> None:
    _issue(invoices, _customer(db_session), "100.00", D(2026, 2, 1))

    assert exports.build(Q1, ADMIN, now=NOW).content == exports.build(Q1, ADMIN, now=NOW).content
