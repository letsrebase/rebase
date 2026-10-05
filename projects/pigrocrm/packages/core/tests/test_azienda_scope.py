"""The scope is Postgres (REB-633, spec 2026-10-03 §4, §8 «Scope»).

A member scoped to one azienda sees, through every repository and through a raw
`SELECT`, the rows of that azienda alone; a session with nothing bound sees nothing
on any policied table; an `INSERT` across the line is refused by the policy. All of it
proven as a role of its own, `pigrocrm_app_test`, which the fixture creates on the
worker's server: the suite's user is a superuser and a superuser is never subject to a
policy, so every other test in this tree runs with them bypassed by design and this
file is where they are exercised.

Committed rows, like the dashboard files: the application role connects on an engine
of its own and sees only what was committed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from fakes.azienda_fixtures import committed_default_azienda, remove_azienda
from sqlalchemy import Engine, create_engine, delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.customers.schemas import CustomerListQuery
from pigrocrm.core.db import Base, session_factory
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.db.role import ensure_application_role
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.schemas import DealListQuery
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.documents.schemas import DocumentListQuery
from pigrocrm.core.emitter.models import Azienda
from pigrocrm.core.emitter.repository import AziendaRepository
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.invoices.schemas import InvoiceListQuery
from pigrocrm.core.people.models import Person
from pigrocrm.core.people.repository import PersonRepository
from pigrocrm.core.people.schemas import PersonListQuery
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.timetracking.models import Cost, CostCategory, TimeEntry
from pigrocrm.core.timetracking.repository import CostRepository, TimeEntryRepository
from pigrocrm.core.timetracking.schemas import CostListQuery, TimeEntryListQuery

_PREFIX = "SCOPE"
APP_ROLE = "pigrocrm_app_test"
APP_PASSWORD = "pigrocrm_app_test"
OGGI = date(2026, 3, 10)

# Every table the migration policies, as 0048 lists them: the introspection test below
# derives the same set from the models and refuses a table that joined one list and
# not the other.
POLICIED = {
    "emitter_profile",
    "fiscal_profile",
    "invoice_counters",
    "invoice_register_gaps",
    "customers",
    "deals",
    "contracts",
    "documents",
    "invoices",
    "costs",
    "people",
    "time_entries",
    "time_timers",
    "invoice_lines",
    "payment_reminders",
    "document_versions",
    "rate_cards",
    "renewal_assumptions",
    "contract_expenses",
    "work_units",
    "approvals",
    "work_unit_transitions",
    "proposals",
    "attivita",
    "gmail_message_links",
    "email_drafts",
    "gmail_messages",
    "activities",
}
# Tables that reach an azienda through a foreign key and carry no policy on purpose
# (spec §4): the scope's own row, and the registry-side tables that do not live in a
# space's database at all.
DECLARED_EXCEPTIONS = {"user_aziende"}


class World(NamedTuple):
    engine: Engine  # the superuser's
    app: Engine  # the application role's, same database
    studio: UUID
    estero: UUID
    user_id: UUID
    # What azienda «estero» owns, per table, written by hand below.
    estero_rows: dict[str, int]


def _require_empty(session: Session) -> None:
    for model in (Invoice, TimeEntry, Deal, Cost, Customer, Person, Contract, Document):
        leftovers = session.execute(select(func.count(model.id))).scalar_one()
        assert leftovers == 0, f"{leftovers} committed {model.__tablename__} row(s) already present"


def _application_role(engine: Engine) -> Engine:
    """The role the API connects as, made the way the boot makes it (`db/role.py`,
    REB-634): `LOGIN NOSUPERUSER NOBYPASSRLS`, granted on this worker's database.
    Cluster-wide, so the second file to ask finds it and only re-applies the grants."""
    app_url = engine.url.set(username=APP_ROLE, password=APP_PASSWORD)
    assert ensure_application_role(engine.url, app_url) is True
    return create_engine(app_url, future=True)


@pytest.fixture
def world(db_engine: Engine) -> Iterator[World]:
    factory = session_factory(db_engine)
    with factory() as session:
        _require_empty(session)
        inserted = committed_default_azienda(session)
        studio = AziendaRepository(session).default()
        assert studio is not None
        estero = Azienda(nome=f"{_PREFIX} ltd", ragione_sociale=f"{_PREFIX} Ltd", nazione="GB")
        stage = PipelineStage(
            nome=f"{_PREFIX} aperto", posizione=0, probabilita_default=20, tipo="open"
        )
        user = User(
            email=f"{_PREFIX.lower()}-{uuid7()}@example.test",
            password_hash="x",
            nome=f"{_PREFIX} operatore",
            ruolo="collaboratore",
        )
        categoria = CostCategory(nome=f"{_PREFIX} categoria")
        session.add_all([estero, stage, user, categoria])
        session.flush()

        def seed(azienda: UUID, tag: str, n_invoices: int) -> None:
            customer = Customer(
                ragione_sociale=f"{_PREFIX} {tag}",
                nazione="IT",
                custom_fields={},
                azienda_id=azienda,
            )
            session.add(customer)
            session.flush()
            session.add(
                Person(nome=f"{_PREFIX} {tag} contatto", customer_id=customer.id, custom_fields={})
            )
            deal = Deal(
                nome=f"{_PREFIX} {tag} deal",
                customer_id=customer.id,
                azienda_id=azienda,
                pipeline_stage_id=stage.id,
                probabilita=20,
                custom_fields={},
            )
            session.add(deal)
            session.flush()
            session.add(
                Contract(
                    customer_id=customer.id,
                    azienda_id=azienda,
                    titolo=f"{_PREFIX} {tag} contratto",
                    inizio=OGGI,
                    tipo_rinnovo="nessuno",
                    preavviso_disdetta_giorni=30,
                    cadenza_fatturazione="mensile",
                    politica_spese={"tipo": "non_rimborsabile"},
                    custom_fields={},
                )
            )
            session.add(
                Document(
                    customer_id=customer.id,
                    azienda_id=azienda,
                    tipo="offerta",
                    titolo=f"{_PREFIX} {tag} offerta",
                    stato="inviata",
                    stato_dal=OGGI,
                    versione_corrente=1,
                    custom_fields={},
                )
            )
            for numero in range(1, n_invoices + 1):
                invoice = Invoice(
                    customer_id=customer.id,
                    azienda_id=azienda,
                    deal_id=deal.id,
                    tipo="fattura",
                    stato="emessa",
                    anno=2026,
                    numero=numero,
                    imponibile=Decimal("100.00"),
                    imposta=Decimal("0.00"),
                    bollo=Decimal("0.00"),
                    totale=Decimal("100.00"),
                    data_emissione=OGGI,
                    custom_fields={},
                )
                session.add(invoice)
                session.flush()
                session.add(
                    InvoiceLine(
                        invoice_id=invoice.id,
                        numero_linea=1,
                        descrizione="Consulenza",
                        quantita=Decimal("1.000000"),
                        prezzo_unitario=Decimal("100.00"),
                        prezzo_totale=Decimal("100.00"),
                        aliquota_iva=Decimal("0.00"),
                        natura="N2.2",
                    )
                )
            session.add(
                TimeEntry(
                    deal_id=deal.id,
                    user_id=user.id,
                    data=OGGI,
                    ore=Decimal("2.00"),
                    descrizione="Lavorazione",
                    fatturabile=True,
                    tariffa_applicata=Decimal("50.000000"),
                    tariffa_origine="manuale",
                    costo_applicato=None,
                    costo_origine="assente",
                    custom_fields={},
                )
            )
            session.add(
                Cost(
                    deal_id=deal.id,
                    azienda_id=azienda,
                    category_id=categoria.id,
                    data=OGGI,
                    importo=Decimal("10.00"),
                    descrizione=f"{_PREFIX} {tag} costo",
                    custom_fields={},
                )
            )
            session.add(
                Activity(
                    entity_type="customer",
                    entity_id=customer.id,
                    kind=f"{_PREFIX}.test",
                    actor_id=None,
                    actor_type="system",
                    payload={},
                    occurred_at=datetime.now(UTC),
                )
            )
            # The register's own timeline row: no record behind its id, the azienda in
            # the payload (`InvoiceService.declare_gaps`), and a gap's reason inside.
            session.add(
                Activity(
                    entity_type="invoice_register",
                    entity_id=uuid5(NAMESPACE_URL, f"pigrocrm:invoice_register:{azienda}:2026"),
                    kind=f"{_PREFIX}.test",
                    actor_id=None,
                    actor_type="system",
                    payload={"azienda_id": str(azienda), "anno": 2026, "numero": 7, "motivo": tag},
                    occurred_at=datetime.now(UTC),
                )
            )

        seed(studio.id, "studio", 2)
        seed(estero.id, "estero", 1)
        # One shared cost and one space-level timeline row: «tutte»'s alone, and everyone's.
        session.add(
            Cost(
                deal_id=None,
                azienda_id=None,
                category_id=categoria.id,
                data=OGGI,
                importo=Decimal("5.00"),
                descrizione=f"{_PREFIX} condiviso",
                custom_fields={},
            )
        )
        session.add(
            Activity(
                entity_type="user",
                entity_id=user.id,
                kind=f"{_PREFIX}.test",
                actor_id=None,
                actor_type="system",
                payload={},
                occurred_at=datetime.now(UTC),
            )
        )
        session.commit()
        ids = World(
            db_engine,
            _application_role(db_engine),
            studio.id,
            estero.id,
            user.id,
            {
                "customers": 1,
                "people": 1,
                "deals": 1,
                "contracts": 1,
                "documents": 1,
                "invoices": 1,
                "invoice_lines": 1,
                "time_entries": 1,
                "costs": 1,
                "activities": 3,  # its customer's, its register's, and the space-level one
                "emitter_profile": 1,
            },
        )
    try:
        yield ids
    finally:
        ids.app.dispose()
        with factory() as session:
            clienti = select(Customer.id).where(Customer.ragione_sociale.like(f"{_PREFIX} %"))
            fatture = select(Invoice.id).where(Invoice.customer_id.in_(clienti))
            session.execute(delete(Activity).where(Activity.kind == f"{_PREFIX}.test"))
            session.execute(
                delete(TimeEntry).where(
                    TimeEntry.deal_id.in_(select(Deal.id).where(Deal.nome.like(f"{_PREFIX} %")))
                )
            )
            session.execute(delete(Cost).where(Cost.descrizione.like(f"{_PREFIX} %")))
            session.execute(delete(CostCategory).where(CostCategory.nome.like(f"{_PREFIX} %")))
            session.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id.in_(fatture)))
            session.execute(delete(Invoice).where(Invoice.customer_id.in_(clienti)))
            session.execute(delete(Document).where(Document.titolo.like(f"{_PREFIX} %")))
            session.execute(delete(Contract).where(Contract.titolo.like(f"{_PREFIX} %")))
            session.execute(delete(Deal).where(Deal.nome.like(f"{_PREFIX} %")))
            session.execute(delete(Person).where(Person.nome.like(f"{_PREFIX} %")))
            session.execute(delete(Customer).where(Customer.ragione_sociale.like(f"{_PREFIX} %")))
            session.execute(delete(PipelineStage).where(PipelineStage.nome.like(f"{_PREFIX} %")))
            session.execute(delete(User).where(User.nome == f"{_PREFIX} operatore"))
            session.execute(delete(Azienda).where(Azienda.id == ids.estero))
            remove_azienda(session, inserted)
            session.commit()


def _scoped(world: World, *aziende: UUID) -> Session:
    """A session of the application role, bound to `aziende` as a scoped
    collaboratore of this space would be."""
    session = session_factory(world.app)()
    actor = Actor(id=world.user_id, type="user", role="collaboratore", aziende=tuple(aziende))
    bind_scope(session, actor)
    return session


def _count(session: Session, table: str) -> int:
    """Rows of `table` this session may see. `activities` is narrowed to this file's own
    rows: a space-level timeline row is everyone's by design, and another file in the
    same worker may have committed some (a login, a user created through the service),
    which the policy rightly shows and this file's arithmetic must not count."""
    where = f" WHERE kind = '{_PREFIX}.test'" if table == "activities" else ""
    return int(session.execute(text(f"SELECT count(*) FROM {table}{where}")).scalar_one())  # noqa: S608


# --- the boundary -------------------------------------------------------------------


def test_every_repository_list_answers_the_scope_s_rows_alone(world: World) -> None:
    with _scoped(world, world.estero) as session:
        customers = CustomerRepository(session).list(CustomerListQuery(limit=50))
        assert [c.ragione_sociale for c in customers] == [f"{_PREFIX} estero"]
        deals = DealRepository(session).list(DealListQuery(limit=50))
        assert [d.nome for d in deals] == [f"{_PREFIX} estero deal"]
        invoices = InvoiceRepository(session).list(InvoiceListQuery(limit=50))
        assert len(invoices) == 1 and invoices[0].azienda_id == world.estero
        documents = DocumentRepository(session).list(DocumentListQuery(limit=50))
        assert [d.titolo for d in documents] == [f"{_PREFIX} estero offerta"]
        people = PersonRepository(session).list(PersonListQuery(limit=50))
        assert [p.nome for p in people] == [f"{_PREFIX} estero contatto"]
        entries = TimeEntryRepository(session).list(TimeEntryListQuery(limit=50))
        assert len(entries) == 1
        costs = CostRepository(session).list(CostListQuery(limit=50))
        # The shared cost is «tutte»'s alone (§1.7): not here.
        assert [c.descrizione for c in costs] == [f"{_PREFIX} estero costo"]


def test_a_raw_select_on_every_policied_table_agrees_with_the_lists(world: World) -> None:
    """No code filter is involved: the same numbers straight from the tables."""
    with _scoped(world, world.estero) as session:
        for table, expected in world.estero_rows.items():
            assert _count(session, table) == expected, table
    # Both aziende in scope: everything but what belongs to neither.
    with _scoped(world, world.studio, world.estero) as session:
        assert _count(session, "customers") == 2
        assert _count(session, "invoices") == 3
        assert _count(session, "costs") == 2  # the shared one is still «tutte»'s alone
        assert _count(session, "activities") == 5


def test_an_unscoped_actor_and_tutte_see_everything(world: World) -> None:
    session = session_factory(world.app)()
    bind_scope(session, Actor(id=world.user_id, type="user", role="collaboratore"))
    with session:
        assert _count(session, "customers") == 2
        assert _count(session, "costs") == 3
        assert _count(session, "activities") == 5


def test_a_session_with_nothing_bound_sees_nothing(world: World) -> None:
    """The closed default: a connection nobody bound reads no row of any policied
    table, which is what a forgotten `bind_scope` costs, rather than everything."""
    with session_factory(world.app)() as session:
        for table in POLICIED - {"activities"}:
            assert _count(session, table) == 0, table
        # The timeline is the one table whose space-level rows (a user's, a template's)
        # are everyone's by design (spec §4): the azienda-bound ones are gone.
        assert _count(session, "activities") == 1


def test_a_scope_with_no_azienda_left_sees_nothing_either(world: World) -> None:
    with _scoped(world) as session:
        assert _count(session, "customers") == 0
        assert _count(session, "activities") == 1  # the space-level row is everyone's


def test_the_register_s_timeline_rows_follow_their_azienda(world: World) -> None:
    """An `invoice_register` row has no record behind its id: the policy reads the
    azienda from its payload, so a gap's number and reason reach only that azienda's
    members and «tutte» (the adversarial pass on PR #513 found them everyone's)."""
    query = text(
        "SELECT payload->>'motivo' FROM activities WHERE entity_type = 'invoice_register' "
        f"AND kind = '{_PREFIX}.test' ORDER BY 1"
    )
    with _scoped(world, world.studio) as session:
        assert session.execute(query).scalars().all() == ["studio"]
    with _scoped(world, world.estero) as session:
        assert session.execute(query).scalars().all() == ["estero"]
    with _scoped(world, world.studio, world.estero) as session:
        assert session.execute(query).scalars().all() == ["estero", "studio"]
    with session_factory(world.app)() as session:
        assert session.execute(query).scalars().all() == []


def test_a_reminder_s_draft_and_timeline_row_follow_the_invoice_not_the_moved_customer(
    world: World,
) -> None:
    """A payment reminder is prepared on the customer (the draft and the timeline row
    hang there) but copies the invoice's number, total and IBAN. A customer moved to
    the other azienda keeps its invoices where they were (spec §1.7): the draft and
    the row stay with the invoice, so the new azienda's members read neither, the old
    one's cannot open the customer anyway, and «tutte» reads both (CodeRabbit's second
    adversarial pass on PR #513)."""
    from pigrocrm.core.gmail.models import (
        EmailDraft,
        GmailMessage,
        GmailMessageLink,
        GoogleAccount,
        PaymentReminder,
    )

    factory = session_factory(world.engine)
    with factory() as session:
        studio_customer = session.execute(
            select(Customer.id).where(Customer.azienda_id == world.studio)
        ).scalar_one()
        estero_invoice = session.execute(
            select(Invoice.id).where(Invoice.azienda_id == world.estero)
        ).scalar_one()
        # The invoice's reminder, filed on a customer that now belongs to «studio».
        reminder = PaymentReminder(invoice_id=estero_invoice, sequence=1)
        session.add(reminder)
        session.flush()
        draft = EmailDraft(
            entity_type="customer",
            entity_id=studio_customer,
            message_id_header=f"<{_PREFIX}@example.test>",
            subject=f"{_PREFIX} sollecito",
            body_markdown="Fattura 2026/1, totale 100,00 EUR, IBAN IT00",
            payment_reminder_id=reminder.id,
        )
        session.add(draft)
        session.add(
            Activity(
                entity_type="customer",
                entity_id=studio_customer,
                kind=f"{_PREFIX}.sollecito",
                actor_id=None,
                actor_type="system",
                payload={"invoice_id": str(estero_invoice), "numero": "2026/1"},
                occurred_at=datetime.now(UTC),
            )
        )
        session.commit()
        draft_id, reminder_id = draft.id, reminder.id

        # The reminder was sent: the message, in the scoped member's own mailbox,
        # linked to the moved customer, with the draft remembering its Gmail id.
        account = GoogleAccount(
            user_id=world.user_id,
            google_sub=f"{_PREFIX}-sub",
            email_address=f"{_PREFIX.lower()}@example.test",
            refresh_token_ciphertext=b"x",
            refresh_token_nonce=b"x",
        )
        session.add(account)
        session.flush()
        message = GmailMessage(
            google_account_id=account.id,
            gmail_message_id=f"{_PREFIX}-m1",
            gmail_thread_id=f"{_PREFIX}-t1",
            direction="outbound",
            subject=f"{_PREFIX} sollecito",
            snippet="Fattura 2026/1",
            internal_date=datetime.now(UTC),
        )
        session.add(message)
        session.flush()
        session.add(
            GmailMessageLink(
                gmail_message_id=message.id, entity_type="customer", entity_id=studio_customer
            )
        )
        draft.google_account_id = account.id
        draft.sent_gmail_message_id = message.gmail_message_id
        session.commit()
        draft_id, reminder_id, account_id, message_id = (
            draft.id,
            reminder.id,
            account.id,
            message.id,
        )

    drafts = text(f"SELECT count(*) FROM email_drafts WHERE subject = '{_PREFIX} sollecito'")
    rows = text(f"SELECT count(*) FROM activities WHERE kind = '{_PREFIX}.sollecito'")
    messages = text(f"SELECT count(*) FROM gmail_messages WHERE gmail_message_id = '{_PREFIX}-m1'")
    try:
        with _scoped(world, world.studio) as session:
            assert session.execute(drafts).scalar_one() == 0
            assert session.execute(rows).scalar_one() == 0
            # The mailbox is this member's own and the link is to a customer they see:
            # the invoice behind the message is not theirs, so neither is the message.
            assert session.execute(messages).scalar_one() == 0
        with _scoped(world, world.estero) as session:
            assert session.execute(drafts).scalar_one() == 0
            assert session.execute(rows).scalar_one() == 0
            # Own mailbox and own invoice: the message, though the customer is gone.
            assert session.execute(messages).scalar_one() == 1
        with _scoped(world, world.studio, world.estero) as session:
            assert session.execute(drafts).scalar_one() == 1
            assert session.execute(rows).scalar_one() == 1
            assert session.execute(messages).scalar_one() == 1
        session = session_factory(world.app)()
        bind_scope(session, Actor(id=world.user_id, type="user", role="collaboratore"))
        with session:
            assert session.execute(drafts).scalar_one() == 1
            assert session.execute(rows).scalar_one() == 1
            assert session.execute(messages).scalar_one() == 1
        with session_factory(world.app)() as session:
            assert session.execute(messages).scalar_one() == 0
    finally:
        with factory() as session:
            session.execute(delete(Activity).where(Activity.kind == f"{_PREFIX}.sollecito"))
            session.execute(
                delete(GmailMessageLink).where(GmailMessageLink.gmail_message_id == message_id)
            )
            session.execute(delete(GmailMessage).where(GmailMessage.id == message_id))
            session.execute(delete(EmailDraft).where(EmailDraft.id == draft_id))
            session.execute(delete(PaymentReminder).where(PaymentReminder.id == reminder_id))
            session.execute(delete(GoogleAccount).where(GoogleAccount.id == account_id))
            session.commit()


def test_an_artefact_rendered_after_the_customer_moved_stays_with_the_invoice(
    world: World, tmp_path: Path
) -> None:
    """The invoice keeps its azienda when its customer moves (spec §1.7); the PDF and
    the XML rendered for it afterwards are the invoice's, not the customer's new
    azienda's (CodeRabbit's fourth adversarial pass on PR #513)."""
    from pigrocrm.core.config import Settings
    from pigrocrm.core.invoices.service import InvoiceService
    from pigrocrm.core.storage import LocalFileStorage

    factory = session_factory(world.engine)
    with factory() as session:
        invoice = session.execute(
            select(Invoice).where(Invoice.azienda_id == world.estero)
        ).scalar_one()
        customer = session.get(Customer, invoice.customer_id)
        assert customer is not None
        customer.azienda_id = world.studio  # the move, by hand: the rows under it stay
        session.commit()
        service = InvoiceService(
            session,
            LocalFileStorage(tmp_path),
            Settings(_env_file=None),  # type: ignore[call-arg]
        )
        document = service._artifact_document(  # noqa: SLF001 - the one seam the render uses
            invoice, "fattura_xml", f"{_PREFIX} xml", Actor.system()
        )
        session.commit()
        document_id = document.id
    try:
        with factory() as session:
            row = session.get(Document, document_id)
            assert row is not None and row.azienda_id == world.estero
        with _scoped(world, world.studio) as session:
            assert session.get(Document, document_id) is None
        with _scoped(world, world.estero) as session:
            assert session.get(Document, document_id) is not None
    finally:
        with factory() as session:
            session.execute(delete(Activity).where(Activity.entity_id == document_id))
            invoice = session.execute(
                select(Invoice).where(Invoice.azienda_id == world.estero)
            ).scalar_one()
            invoice.xml_document_id = None
            session.execute(delete(Document).where(Document.id == document_id))
            customer = session.get(Customer, invoice.customer_id)
            assert customer is not None
            customer.azienda_id = world.estero
            session.commit()


def test_an_insert_across_the_line_is_refused_by_the_policy(world: World) -> None:
    with _scoped(world, world.estero) as session:
        studio_customer = session.execute(text("SELECT count(*) FROM customers")).scalar_one()
        assert studio_customer == 1
        with pytest.raises(DBAPIError) as refused:
            session.add(
                Customer(
                    ragione_sociale=f"{_PREFIX} intruso",
                    nazione="IT",
                    custom_fields={},
                    azienda_id=world.studio,
                )
            )
            session.flush()
        assert "ambito_azienda" in str(refused.value) or "row-level security" in str(refused.value)
        session.rollback()
    # And the row was not written: the superuser sees none.
    with session_factory(world.engine)() as session:
        assert (
            session.execute(
                select(func.count(Customer.id)).where(
                    Customer.ragione_sociale == f"{_PREFIX} intruso"
                )
            ).scalar_one()
            == 0
        )


def test_the_scope_survives_a_commit_on_the_same_session(world: World) -> None:
    """`set_config(..., true)` dies with the transaction; the listener rebinds it."""
    with _scoped(world, world.estero) as session:
        assert _count(session, "customers") == 1
        session.commit()
        assert _count(session, "customers") == 1
        session.rollback()
        assert _count(session, "customers") == 1


# --- the introspection ----------------------------------------------------------------


def _tables_reaching_an_azienda() -> set[str]:
    """Every table with an `azienda_id`, or a foreign-key path to one, from the models."""
    reaching = {t.name for t in Base.metadata.tables.values() if "azienda_id" in t.c}
    changed = True
    while changed:
        changed = False
        for table in Base.metadata.tables.values():
            if table.name in reaching:
                continue
            for fk in table.foreign_keys:
                if fk.column.table.name in reaching:
                    reaching.add(table.name)
                    changed = True
                    break
    return reaching


def test_every_table_that_reaches_an_azienda_carries_the_policy(db_engine: Engine) -> None:
    """A table added later without a policy fails the build, the way `test_architecture`
    fails an undeclared import. The polymorphic tables (`activities`, the Gmail links and
    drafts) reach an azienda through `entity_id`, which is no foreign key: they are in
    0048's list and checked against `pg_policies` below like the others."""
    with db_engine.connect() as connection:
        policied = {
            row[0]
            for row in connection.execute(
                text("SELECT tablename FROM pg_policies WHERE policyname = 'ambito_azienda'")
            )
        }
        forced = {
            row[0]
            for row in connection.execute(
                text("SELECT relname FROM pg_class WHERE relrowsecurity AND relforcerowsecurity")
            )
        }
    assert policied == POLICIED
    assert forced >= POLICIED
    space_tables = {t.name for t in Base.metadata.tables.values()}
    expected = (_tables_reaching_an_azienda() & space_tables) - DECLARED_EXCEPTIONS
    missing = expected - policied
    assert missing == set(), f"tables reaching an azienda with no policy: {sorted(missing)}"
    assert "users" not in policied and "user_aziende" not in policied
