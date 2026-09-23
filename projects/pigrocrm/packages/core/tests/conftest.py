import subprocess
from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from periodo_fiscale import OGGI
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import Base, create_engine_from_settings, session_factory
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.timetracking.models import CostCategory, TimeEntry


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    """Real PostgreSQL. JSONB, GIN and pg_trgm do not exist in SQLite, so there is no
    shortcut.

    `CREATE EXTENSION` runs **before** `create_all`, and that order is load-bearing: from
    slice 6 on, four models declare GIN indexes with `gin_trgm_ops`, and `create_all`
    fails outright with `operator class "gin_trgm_ops" does not exist` if the extension
    is not there yet. Migration 0021 creates the extension too -- this is the same
    statement for the path that bypasses the migrations. `btree_gist` is REB-358's own
    addition, for the identical reason: `RateCard`'s exclusion constraint needs it, and
    `create_all` fails with `operator class "gist_uuid_ops" does not exist` without it.
    """
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        settings = Settings(database_url=container.get_connection_url())
        engine = create_engine_from_settings(settings)
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        import pigrocrm.core.models_registry  # noqa: F401  (imports every model)

        Base.metadata.create_all(engine)
        yield engine
        engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """Each test runs in a transaction that is rolled back, so tests never see each other."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = session_factory(db_engine)(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def seeded_user_id(db_session: Session) -> UUID:
    user = User(
        email=f"tester-{uuid4()}@example.test",
        password_hash="x",
        nome="Tester",
        ruolo="collaboratore",
    )
    db_session.add(user)
    db_session.flush()
    return user.id


@pytest.fixture
def seeded_open_stage_id(db_session: Session) -> UUID:
    stage = PipelineStage(
        nome=f"Aperto {uuid4()}", posizione=0, probabilita_default=10, tipo="open"
    )
    db_session.add(stage)
    db_session.flush()
    return stage.id


@pytest.fixture
def seeded_won_stage_id(db_session: Session) -> UUID:
    stage = PipelineStage(nome=f"Vinto {uuid4()}", posizione=9, probabilita_default=100, tipo="won")
    db_session.add(stage)
    db_session.flush()
    return stage.id


@pytest.fixture
def seeded_deal_id(db_session: Session, seeded_open_stage_id: UUID) -> UUID:
    customer = Customer(ragione_sociale=f"Cliente {uuid4()}")
    db_session.add(customer)
    db_session.flush()
    deal = Deal(
        nome="Progetto di prova",
        customer_id=customer.id,
        pipeline_stage_id=seeded_open_stage_id,
        probabilita=10,
    )
    db_session.add(deal)
    db_session.flush()
    return deal.id


@pytest.fixture
def seeded_category_id(db_session: Session) -> UUID:
    category = CostCategory(nome=f"Categoria {uuid4()}", posizione=0, code=None)
    db_session.add(category)
    db_session.flush()
    return category.id


@pytest.fixture
def seeded_entry_id(db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID) -> UUID:
    entry = TimeEntry(
        deal_id=seeded_deal_id,
        user_id=seeded_user_id,
        data=date(2026, 3, 10),
        ore=Decimal("8.00"),
        descrizione="Analisi",
        tariffa_applicata=Decimal("80.000000"),
        tariffa_origine="manuale",
    )
    db_session.add(entry)
    db_session.flush()
    return entry.id


@pytest.fixture
def time_entry_factory(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> Callable[..., TimeEntry]:
    """One `time_entries` row, with only the columns an unbilled-backlog figure reads.

    Written directly rather than through `TimeEntryService.create`, which is the opposite
    of the choice `budgeted_deal` makes and for two reasons that do not apply there. The
    backlog has **no period**, so its tests need days years apart, and the service refuses
    any day after today and any day inside a closed month -- neither of which is the thing
    under test. And it needs `invoice_line_id` populated, which no create path sets: the
    link is written by `bind_time_to_invoice` and by slice 3's line replacement.

    `tariffa=None` also sets `tariffa_origine="assente"`, because a rate and its
    provenance are one fact: a row claiming `manuale` with no rate is a state no write
    path can produce, and a fixture that could produce it would let a test pass against
    an implementation that reads the wrong column.

    Returns the row so a caller can soft-delete it or re-point it at another invoice line
    without a second way to build a `TimeEntry` drifting into being.
    """

    def _make(
        *,
        data: date,
        ore: str = "8.00",
        tariffa: str | None = "50.000000",
        costo: str | None = None,
        fatturabile: bool = True,
        invoice_line_id: UUID | None = None,
        deal_id: UUID | None = None,
    ) -> TimeEntry:
        entry = TimeEntry(
            deal_id=seeded_deal_id if deal_id is None else deal_id,
            user_id=seeded_user_id,
            data=data,
            ore=Decimal(ore),
            descrizione="Lavorazione",
            fatturabile=fatturabile,
            tariffa_applicata=None if tariffa is None else Decimal(tariffa),
            tariffa_origine="assente" if tariffa is None else "manuale",
            costo_applicato=None if costo is None else Decimal(costo),
            costo_origine="assente" if costo is None else "manuale",
            invoice_line_id=invoice_line_id,
        )
        db_session.add(entry)
        db_session.flush()
        return entry

    return _make


@pytest.fixture
def local_storage(tmp_path) -> LocalFileStorage:
    """A tmp-dir backend, never the default `./var/documents` root -- a test must not
    write real files into this repository's working tree."""
    return LocalFileStorage(str(tmp_path / "documents"))


@pytest.fixture
def extract_pdf_text() -> Callable[[LocalFileStorage, Session, UUID], str]:
    """Reads the stored PDF back and extracts its text with `pdftotext`, which ships in
    the same API image as Pandoc and Typst. Reading the produced artefact rather than
    the intermediate Markdown is the only assertion that proves what the client
    actually receives.

    A fixture returning the callable, deliberately, rather than a plain module-level
    function a test imports as `from conftest import extract_pdf_text`. This repository
    has three test roots -- `packages/core/tests`, `apps/api/tests`, `apps/mcp/tests` --
    each with its own `conftest.py` and none with an `__init__.py`, so `conftest` is an
    ambiguous top-level module name: whichever one pytest imports first claims
    `sys.modules["conftest"]` and every later import binds to *that* file. Running one
    root at a time hid it; `uv run pytest` with no path -- the CI command, which
    collects all three -- resolved the import against `apps/mcp/tests/conftest.py` and
    died at collection. Fixture resolution is scoped per directory by pytest itself, so
    it cannot collide however the roots are combined."""

    def _extract(storage: LocalFileStorage, session: Session, document_id: UUID) -> str:
        from pigrocrm.core.documents.repository import DocumentRepository

        version = DocumentRepository(session).version(document_id, 1)
        assert version is not None
        data = storage.get(version.storage_key)
        result = subprocess.run(
            ["pdftotext", "-layout", "-", "-"], input=data, capture_output=True, check=True
        )
        return result.stdout.decode("utf-8", errors="replace")

    return _extract


# --- slice 3 invoices, as states rather than as rows -------------------------------
#
# The three fixtures below build a real invoice through `InvoiceService` instead of
# inserting `invoice_lines` by hand, so `(tipo, stato)`, the line invariants and the
# register number are the ones that service produces. Task 4B-3 needs them because
# `time_entries.invoice_line_id` is a real foreign key from migration 0012 on: a random
# UUID no longer stands in for a line, and the rule the column feeds -- "frozen only
# when the invoice is *issued*" -- is a question about the invoice's state that only a
# genuinely issued row can answer.
#
# The imports are inside the functions, not at module scope: this file is loaded for
# every test in the package, and the invoices service drags in storage, rendering and
# the emitter with it.


def _invoice_service(session: Session, storage: LocalFileStorage) -> Any:
    """`InvoiceService` with the two profiles `issue()` reads already in place."""
    from pigrocrm.core.emitter.repository import EmitterProfileRepository
    from pigrocrm.core.emitter.schemas import EmitterProfileUpsert
    from pigrocrm.core.emitter.service import EmitterProfileService
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService
    from pigrocrm.core.invoices.service import InvoiceService

    admin = Actor(id=None, type="system", role="admin")
    if FiscalProfileRepository(session).get() is None:
        FiscalProfileService(session).upsert(FiscalProfileUpsert(codice_regime="RF19"), admin)
    # Guarded on its own row and not on the fiscal profile's. The two used to share one
    # `if`, which meant that a test installing a different regime first -- `rf01_fiscal_profile`
    # does exactly that -- left the emitter profile uncreated, and `issue()` then failed
    # on a missing emitter for a reason with no visible connection to the regime.
    if EmitterProfileRepository(session).get() is None:
        EmitterProfileService(session).upsert(
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
            admin,
        )
    return InvoiceService(session, storage)


def _fiscal_customer_id(session: Session) -> UUID:
    """A customer with enough identity to appear on an issued document."""
    customer = Customer(
        ragione_sociale=f"Acme {uuid4()}",
        partita_iva="12345678901",
        codice_sdi="ABCDEFG",
        indirizzo="Corso Italia 5",
        cap="00100",
        comune="Roma",
        provincia="RM",
        nazione="IT",
    )
    session.add(customer)
    session.flush()
    return customer.id


def _invoice_of(session: Session, line_id: UUID) -> UUID:
    invoice_id: UUID = session.execute(
        text("SELECT invoice_id FROM invoice_lines WHERE id = :line"), {"line": line_id}
    ).scalar_one()
    return invoice_id


@pytest.fixture
def draft_invoice_line_id(db_session: Session, local_storage: LocalFileStorage) -> UUID:
    """A line on a `bozza`.

    The invoice carries its own customer and no `deal_id`: `_check_owner` requires the
    deal to belong to the invoice's customer, and the customer `seeded_deal_id` builds
    has only a `ragione_sociale`, which is not identity enough to issue against. What
    the tests using these fixtures need from an invoice is its *state*, never its link
    to a deal.
    """
    from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn

    invoice = _invoice_service(db_session, local_storage).create(
        InvoiceCreate(
            customer_id=_fiscal_customer_id(db_session),
            tipo="fattura",
            righe=[
                InvoiceLineIn(
                    descrizione="Attività",
                    quantita=Decimal("1.000000"),
                    prezzo_unitario=Decimal("100.000000"),
                )
            ],
        ),
        Actor(id=None, type="system", role="admin"),
    )
    line_id: UUID = db_session.execute(
        text("SELECT id FROM invoice_lines WHERE invoice_id = :inv ORDER BY numero_linea LIMIT 1"),
        {"inv": invoice.id},
    ).scalar_one()
    return line_id


@pytest.fixture
def proforma_invoice_line_id(db_session: Session, local_storage: LocalFileStorage) -> UUID:
    """A line on a `confermata` proforma -- the state closest to an emission that is not
    one. It never touches the register (slice 3 §5)."""
    from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn

    service = _invoice_service(db_session, local_storage)
    admin = Actor(id=None, type="system", role="admin")
    invoice = service.create(
        InvoiceCreate(
            customer_id=_fiscal_customer_id(db_session),
            tipo="proforma",
            righe=[
                InvoiceLineIn(
                    descrizione="Attività",
                    quantita=Decimal("1.000000"),
                    prezzo_unitario=Decimal("100.000000"),
                )
            ],
        ),
        admin,
    )
    service.confirm_proforma(invoice.id, admin)
    line_id: UUID = db_session.execute(
        text("SELECT id FROM invoice_lines WHERE invoice_id = :inv ORDER BY numero_linea LIMIT 1"),
        {"inv": invoice.id},
    ).scalar_one()
    return line_id


@pytest.fixture
def issued_invoice_line_id(
    db_session: Session, local_storage: LocalFileStorage, draft_invoice_line_id: UUID
) -> UUID:
    """The same line, once `issue()` has consumed a register number for it."""
    from pigrocrm.core.invoices.schemas import InvoiceIssue

    _invoice_service(db_session, local_storage).issue(
        _invoice_of(db_session, draft_invoice_line_id),
        InvoiceIssue(),
        Actor(id=None, type="system", role="admin"),
    )
    return draft_invoice_line_id


@pytest.fixture
def annulled_invoice_line_id(
    db_session: Session, local_storage: LocalFileStorage, issued_invoice_line_id: UUID
) -> UUID:
    """The same line again, on an invoice that keeps its number and loses its revenue."""
    from pigrocrm.core.invoices.schemas import InvoiceAnnul

    _invoice_service(db_session, local_storage).annul(
        _invoice_of(db_session, issued_invoice_line_id),
        InvoiceAnnul(motivo="errore di emissione"),
        Actor(id=None, type="system", role="admin"),
    )
    return issued_invoice_line_id


# --- slice 4B: deals that carry their own invoices ---------------------------------
#
# The fixtures above deliberately keep their invoices free of a `deal_id`, because what
# a freeze test needs from an invoice is its *state*. A P&L is per deal, so the ones
# below need the opposite: an invoice that carries a `deal_id`. `_check_owner` allows
# that only when the deal belongs to the invoice's own customer, and the customer
# `seeded_deal_id` builds has a `ragione_sociale` and nothing else -- not identity
# enough to issue against -- so these build their own customer, deal and stage.


def _deal_of_fiscal_customer(
    session: Session, nome: str, *, tipo: str = "open"
) -> tuple[UUID, UUID]:
    """`(deal_id, customer_id)`, on an **open** stage so the state starts at "in corso".

    `tipo="won"` puts it on a closed-won stage instead, which is the first of the two
    conditions `deal_summary` requires before it will call a deal `chiuso` -- the second
    being that no billable hour is still unbilled. A period P&L needs both columns
    populated to be worth asserting on at all, so the choice has to be expressible here.
    """
    customer_id = _fiscal_customer_id(session)
    stage = PipelineStage(
        nome=f"{'Vinto' if tipo == 'won' else 'Aperto'} {uuid4()}",
        posizione=9 if tipo == "won" else 0,
        probabilita_default=100 if tipo == "won" else 10,
        tipo=tipo,
    )
    session.add(stage)
    session.flush()
    deal = Deal(nome=nome, customer_id=customer_id, pipeline_stage_id=stage.id, probabilita=10)
    session.add(deal)
    session.flush()
    return deal.id, customer_id


def _draft_for_deal(
    session: Session,
    storage: LocalFileStorage,
    *,
    customer_id: UUID,
    deal_id: UUID,
    importo: Decimal,
    tipo: str = "fattura",
) -> UUID:
    """One single-line draft of `importo`, through `InvoiceService.create`."""
    from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn

    invoice = _invoice_service(session, storage).create(
        InvoiceCreate(
            customer_id=customer_id,
            deal_id=deal_id,
            tipo=tipo,
            righe=[
                InvoiceLineIn(
                    descrizione="Attività",
                    quantita=Decimal("1.000000"),
                    prezzo_unitario=importo,
                )
            ],
        ),
        Actor(id=None, type="system", role="admin"),
    )
    invoice_id: UUID = invoice.id
    return invoice_id


def _issue(
    session: Session,
    storage: LocalFileStorage,
    invoice_id: UUID,
    data_emissione: date | None = None,
) -> None:
    """`data_emissione` explicit where the period matters: a period P&L attributes
    revenue by that column, so the caller says which day the revenue lands on rather than
    inheriting the default. The callers now name `periodo_fiscale.OGGI`, which is the
    same day the default would have produced -- passing it anyway keeps the date visible
    at the call site next to the hours and costs it has to share a window with, which is
    the coupling that has to stay obvious."""
    from pigrocrm.core.invoices.schemas import InvoiceIssue

    _invoice_service(session, storage).issue(
        invoice_id,
        InvoiceIssue(data_emissione=data_emissione),
        Actor(id=None, type="system", role="admin"),
    )


@pytest.fixture
def rf01_fiscal_profile(db_session: Session) -> None:
    """Slice 3 §14.8's synthetic ordinary regime: 22% and a taxed total.

    Slice 3 exercised `RF01` by constructing `FiscalSnapshot` values in-process
    (`test_invoice_totals.py`, `test_invoice_fatturapa.py`) and by upserting the profile
    inline in one artefact test, never as a shared fixture -- so there was no second
    declaration to reuse and this is the first. It exists because the difference between
    `imponibile` and `totale` is unobservable under the forfettario the product ships,
    and a P&L that followed `totale` would pass every test written in that regime.
    """
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService

    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(
            codice_regime="RF01",
            aliquota_iva_default=Decimal("22.00"),
            # `None` on both, and required to be: the ordinary regime pairs no `Natura`
            # with a non-zero rate, and `FiscalProfileService._check` refuses the pair.
            natura_default=None,
            riferimento_normativo=None,
        ),
        Actor(id=None, type="system", role="admin"),
    )


@pytest.fixture
def deal_with_mixed_invoices(db_session: Session, local_storage: LocalFileStorage) -> UUID:
    """One deal carrying every invoice state that exists, so a revenue figure has
    something to be wrong about.

    Three issued `fattura` rows -- 1000.00 + 250.50 + 333.33 = **1583.83** -- and, not
    counting towards it: one issued-then-annulled invoice, which keeps its number and
    loses its revenue; one `confermata` proforma, which never touches the register; and
    one `bozza`, which is a proposal. The draft is here beyond the three states §7.1
    names because a soft delete is only expressible on a row with no number
    (`ck_invoices_no_delete_once_consumed`), and one of the tests needs a row it can
    soft-delete.
    """
    deal_id, customer_id = _deal_of_fiscal_customer(db_session, "Progetto fatturato")
    for importo in (Decimal("1000.00"), Decimal("250.50"), Decimal("333.33")):
        _issue(
            db_session,
            local_storage,
            _draft_for_deal(
                db_session,
                local_storage,
                customer_id=customer_id,
                deal_id=deal_id,
                importo=importo,
            ),
        )

    from pigrocrm.core.invoices.schemas import InvoiceAnnul

    annullata = _draft_for_deal(
        db_session,
        local_storage,
        customer_id=customer_id,
        deal_id=deal_id,
        importo=Decimal("500.00"),
    )
    _issue(db_session, local_storage, annullata)
    _invoice_service(db_session, local_storage).annul(
        annullata,
        InvoiceAnnul(motivo="errore di emissione"),
        Actor(id=None, type="system", role="admin"),
    )

    proforma = _draft_for_deal(
        db_session,
        local_storage,
        customer_id=customer_id,
        deal_id=deal_id,
        importo=Decimal("999.00"),
        tipo="proforma",
    )
    _invoice_service(db_session, local_storage).confirm_proforma(
        proforma, Actor(id=None, type="system", role="admin")
    )

    _draft_for_deal(
        db_session,
        local_storage,
        customer_id=customer_id,
        deal_id=deal_id,
        importo=Decimal("120.00"),
    )
    return deal_id


@pytest.fixture
def deal_with_vat_invoice(
    db_session: Session, local_storage: LocalFileStorage, rf01_fiscal_profile: None
) -> UUID:
    """A deal with one issued invoice under `RF01`: 100.00 + 22% = 122.00, so
    `imponibile` and `totale` are two different numbers and a P&L can follow the wrong
    one."""
    deal_id, customer_id = _deal_of_fiscal_customer(db_session, "Progetto con IVA")
    _issue(
        db_session,
        local_storage,
        _draft_for_deal(
            db_session,
            local_storage,
            customer_id=customer_id,
            deal_id=deal_id,
            importo=Decimal("100.00"),
        ),
    )
    return deal_id


@pytest.fixture
def deal_with_bollo_invoice(db_session: Session, local_storage: LocalFileStorage) -> UUID:
    """A deal with one forfettario invoice over the 77.47 stamp-duty threshold, so
    `invoices.bollo` is 2.00 and not zero -- which is what makes "the stamp duty is not a
    deal cost" a statement about a real value."""
    deal_id, customer_id = _deal_of_fiscal_customer(db_session, "Progetto con bollo")
    _issue(
        db_session,
        local_storage,
        _draft_for_deal(
            db_session,
            local_storage,
            customer_id=customer_id,
            deal_id=deal_id,
            importo=Decimal("1000.00"),
        ),
    )
    return deal_id


# --- slice 4B: the two columns of a period P&L -------------------------------------
#
# `period_pnl` splits deals into `chiusi` and `in corso` and refuses to add the two
# together. Testing that refusal needs one deal of each kind inside the same window,
# each carrying figures of its own that a wrong implementation could move.
#
# That window is `periodo_fiscale.OGGI` and the month it belongs to, never a literal
# month: one of these deals carries an *issued* invoice, and `issue()` refuses a
# `data_emissione` outside the current fiscal year. See `periodo_fiscale.py` for why the
# derivation cannot simply be "this year, March" either.


@pytest.fixture
def open_deal_with_hours(
    db_session: Session, seeded_user_id: UUID, seeded_category_id: UUID
) -> UUID:
    """Today: 10 priced hours at an internal cost of 40, and 150.00 of licences.

    Labour cost 400.00, direct costs 150.00, revenue nothing -- an unfinished job, whose
    margin is provisional by construction and must never be added to a finished one's.
    """
    from pigrocrm.core.timetracking.costs import CostService
    from pigrocrm.core.timetracking.schemas import CostCreate, TimeEntryCreate
    from pigrocrm.core.timetracking.service import TimeEntryService

    writer = Actor(id=None, type="user", role="collaboratore")
    deal_id, _ = _deal_of_fiscal_customer(db_session, "Progetto in corso")
    TimeEntryService(db_session).create(
        TimeEntryCreate(
            deal_id=deal_id,
            user_id=seeded_user_id,
            data=OGGI,
            ore=Decimal("10.00"),
            descrizione="Sviluppo",
            tariffa_applicata=Decimal("100.000000"),
            costo_applicato=Decimal("40.000000"),
        ),
        writer,
    )
    CostService(db_session).create(
        CostCreate(
            deal_id=deal_id,
            category_id=seeded_category_id,
            data=OGGI,
            importo=Decimal("150.00"),
            descrizione="Licenze",
        ),
        writer,
    )
    return deal_id


@pytest.fixture
def closed_deal_with_invoice(
    db_session: Session, local_storage: LocalFileStorage, seeded_category_id: UUID
) -> UUID:
    """Today: 2000.00 invoiced and issued, 200.00 of direct costs, no hours.

    On a won stage and with no billable hour left unbilled, which is what makes
    `deal_summary` call it `chiuso` -- the state `period_pnl` reads to choose a column.
    Margin 1800.00, that is 90.00% of revenue: a reportable figure, and one that would
    move visibly if a general expense were ever apportioned onto it.
    """
    from pigrocrm.core.timetracking.costs import CostService
    from pigrocrm.core.timetracking.schemas import CostCreate

    deal_id, customer_id = _deal_of_fiscal_customer(db_session, "Progetto chiuso", tipo="won")
    _issue(
        db_session,
        local_storage,
        _draft_for_deal(
            db_session,
            local_storage,
            customer_id=customer_id,
            deal_id=deal_id,
            importo=Decimal("2000.00"),
        ),
        OGGI,
    )
    CostService(db_session).create(
        CostCreate(
            deal_id=deal_id,
            category_id=seeded_category_id,
            data=OGGI,
            importo=Decimal("200.00"),
            descrizione="Stampa",
        ),
        Actor(id=None, type="user", role="collaboratore"),
    )
    return deal_id


# --- slice 4B: estimate against actual ---------------------------------------------


@pytest.fixture
def budgeted_deal(
    db_session: Session, local_storage: LocalFileStorage, seeded_user_id: UUID
) -> Callable[..., UUID]:
    """A factory: one deal with the given estimate columns, hours and revenue, all dated
    today.

    The revenue is built by creating and **issuing** a real invoice rather than by
    inserting a row, because criterion 1 of this slice is precisely that the figure comes
    from an invoice that exists -- a fixture that wrote `invoices` directly could not tell
    a correct implementation from one reading a column nobody ever fills.

    The estimates arrive as strings (or `None`) so that "no estimate" and "an estimate of
    zero" are two visibly different arguments at every call site: they are the two states
    §9.2 requires the report to keep apart, and a test that could not spell both would be
    testing only one.
    """
    from pigrocrm.core.timetracking.schemas import TimeEntryCreate
    from pigrocrm.core.timetracking.service import TimeEntryService

    def _make(
        *,
        ore_preventivate: str | None,
        valore_preventivato: str | None,
        ore_registrate: str | None = None,
        ricavi: str | None = None,
        nome: str = "Progetto preventivato",
    ) -> UUID:
        deal_id, customer_id = _deal_of_fiscal_customer(db_session, nome)
        deal = db_session.get(Deal, deal_id)
        assert deal is not None
        deal.ore_preventivate = None if ore_preventivate is None else Decimal(ore_preventivate)
        deal.valore_preventivato = (
            None if valore_preventivato is None else Decimal(valore_preventivato)
        )
        db_session.flush()

        if ore_registrate is not None:
            # Several entries at up to 8 hours each, not one: `TimeEntryCreate.ore` is
            # capped at 24 because an entry is one person's one day, and the totals this
            # fixture exists to feed are tens of hours. They all carry today's date
            # rather than consecutive days, which the model allows on purpose -- there is
            # no unique key on (deal, user, data), because "two 14-hour entries on one
            # day are a likely error but not an impossible one". Spreading them forward
            # from a fixed day was what made this fixture expire: the days have to stay
            # inside the reporting window *and* not run past today, and only today itself
            # satisfies both on every run day (see `periodo_fiscale.py`).
            residuo = Decimal(ore_registrate)
            while residuo > 0:
                quota = min(residuo, Decimal("8.00"))
                TimeEntryService(db_session).create(
                    TimeEntryCreate(
                        deal_id=deal_id,
                        user_id=seeded_user_id,
                        data=OGGI,
                        ore=quota,
                        descrizione="Lavorazione",
                    ),
                    Actor(id=None, type="user", role="collaboratore"),
                )
                residuo -= quota
        if ricavi is not None:
            _issue(
                db_session,
                local_storage,
                _draft_for_deal(
                    db_session,
                    local_storage,
                    customer_id=customer_id,
                    deal_id=deal_id,
                    importo=Decimal(ricavi),
                ),
                OGGI,
            )
        return deal_id

    return _make


# --- slice 4B-7: hours that become an invoice line ---------------------------------


@pytest.fixture
def billable_deal_id(db_session: Session, local_storage: LocalFileStorage) -> UUID:
    """A deal whose customer carries enough identity to be **issued** against, with the
    fiscal and emitter profiles already installed.

    `seeded_deal_id`'s customer has a `ragione_sociale` and nothing else, which is right
    for every test that only needs a deal to hang hours off. `bind_time_to_invoice` goes
    the whole way -- draft, then emission -- so it needs the opposite, and it also needs
    a fiscal profile to exist before `InvoiceService.create` will resolve a regime at
    all. `_invoice_service` is called for its side effect of installing both profiles;
    the service it returns is discarded, because the point of these tests is that
    `AnalyticsService` builds its own.
    """
    _invoice_service(db_session, local_storage)
    deal_id, _ = _deal_of_fiscal_customer(db_session, "Progetto da fatturare")
    return deal_id
