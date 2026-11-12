"""The scope is Postgres (REB-633, spec 2026-10-03 §4, §8 «Scope»).

A member scoped to one legal entity sees, through every repository and through a raw
`SELECT`, the rows of that legal entity alone; a session with nothing bound sees nothing
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
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from fakes.legal_entity_fixtures import committed_default_legal_entity, remove_legal_entity
from sqlalchemy import Engine, create_engine, delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.contact_points.models import ContactPoint
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.db import Base, session_factory
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.db.role import ensure_application_role
from pigrocrm.core.db.scope import bind_scope, is_policy_refusal
from pigrocrm.core.deals.models import Deal, DealValueLine
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.schemas import DealListQuery
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.documents.schemas import DocumentListQuery
from pigrocrm.core.errors import NotFound
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.invoices.schemas import InvoiceListQuery
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.notes.models import Note, NoteRevision
from pigrocrm.core.notes.schemas import NoteCreate, NoteUpdate
from pigrocrm.core.notes.service import NoteService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.organizations.repository import OrganizationRepository
from pigrocrm.core.organizations.schemas import OrganizationListQuery
from pigrocrm.core.people.models import Person
from pigrocrm.core.people.repository import PersonRepository
from pigrocrm.core.people.schemas import PersonListQuery
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.purchases.repository import PurchaseRepository
from pigrocrm.core.purchases.schemas import PurchaseListQuery
from pigrocrm.core.timetracking.models import CostCategory, TimeEntry
from pigrocrm.core.timetracking.repository import TimeEntryRepository
from pigrocrm.core.timetracking.schemas import TimeEntryListQuery

_PREFIX = "SCOPE"
APP_ROLE = "pigrocrm_app_test"
APP_PASSWORD = "pigrocrm_app_test"
TODAY = date(2026, 3, 10)

# Every table the migration policies, as 0048 lists them: the introspection test below
# derives the same set from the models and refuses a table that joined one list and
# not the other.
POLICIED = {
    "legal_entities",
    "fiscal_profile",
    "fiscal_estimates",
    "declared_tax_figures",
    "tax_advance_methods",
    "tax_losses",
    "tax_instalment_plans",
    "tax_notice_plans",
    "tax_credits",
    "legal_entity_sites",
    "f24_groups",
    "f24_group_items",
    "f24_group_receipts",
    "accountant_exports",
    "accountant_export_links",
    "accountant_export_link_openings",
    "accountant_export_link_codes",
    "accountant_export_drive_writes",
    "iva_settlements",
    "iva_period_locks",
    "recurring_expenses",
    "invoice_counters",
    "invoice_register_gaps",
    "organizations",
    "deals",
    "deal_value_lines",
    "contact_points",
    "due_items",
    "due_item_settlements",
    "bank_accounts",
    "bank_statements",
    "bank_movements",
    "statement_movements",
    "movement_matches",
    "movement_match_items",
    "organization_ibans",
    "staff_members",
    "staff_payslips",
    "expense_notes",
    "payroll_month_closures",
    "payroll_settings",
    "engagements",
    "engagement_monthly_reports",
    "contracts",
    "documents",
    "invoices",
    "purchases",
    "purchase_vat_lines",
    "people",
    "time_entries",
    "time_timers",
    "invoice_lines",
    "payment_reminders",
    "document_versions",
    "rate_cards",
    "renewal_assumptions",
    "contract_expenses",
    "contract_email_templates",
    "clause_notes",
    "notes",
    "note_revisions",
    "work_units",
    "approvals",
    "work_unit_transitions",
    "proposals",
    "tasks",
    "gmail_message_links",
    "email_drafts",
    "gmail_messages",
    "activities",
    "inbox_items",
}
# Tables that reach a legal entity through a foreign key and carry no policy on purpose
# (spec §4): the scope's own row, and the registry-side tables that do not live in a
# space's database at all.
DECLARED_EXCEPTIONS = {"user_legal_entities"}


class World(NamedTuple):
    engine: Engine  # the superuser's
    app: Engine  # the application role's, same database
    studio: UUID
    foreign: UUID
    user_id: UUID
    # What legal entity «estero» owns, per table, written by hand below.
    foreign_rows: dict[str, int]


def _require_empty(session: Session) -> None:
    for model in (Invoice, TimeEntry, Deal, Purchase, Organization, Person, Contract, Document):
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
        inserted = committed_default_legal_entity(session)
        studio = LegalEntityRepository(session).default()
        assert studio is not None
        foreign = LegalEntity(name=f"{_PREFIX} ltd", legal_name=f"{_PREFIX} Ltd", country="GB")
        stage = PipelineStage(
            name=f"{_PREFIX} aperto", position=0, default_probability=20, type="open"
        )
        user = User(
            email=f"{_PREFIX.lower()}-{uuid7()}@example.test",
            password_hash="x",
            name=f"{_PREFIX} operatore",
            role="collaborator",
        )
        category = CostCategory(name=f"{_PREFIX} categoria")
        session.add_all([foreign, stage, user, category])
        session.flush()

        def seed(legal_entity: UUID, tag: str, n_invoices: int) -> None:
            customer = Organization(
                legal_name=f"{_PREFIX} {tag}",
                country="IT",
                custom_fields={},
                legal_entity_id=legal_entity,
            )
            session.add(customer)
            session.flush()
            person = Person(
                first_name=f"{_PREFIX} {tag} contatto", customer_id=customer.id, custom_fields={}
            )
            session.add(person)
            # One point on the person and one on the customer: the table's policy reaches a
            # legal entity through whichever parent the row has.
            session.flush()
            session.add_all(
                [
                    ContactPoint(
                        person_id=person.id,
                        kind="email",
                        value=f"{tag}@scope.example",
                        normalized=f"{tag}@scope.example",
                        label="work",
                        is_primary=True,
                        position=0,
                    ),
                    ContactPoint(
                        customer_id=customer.id,
                        kind="phone",
                        value="0212345",
                        normalized="0212345",
                        label="work",
                        is_primary=True,
                        position=0,
                    ),
                ]
            )
            deal = Deal(
                name=f"{_PREFIX} {tag} deal",
                customer_id=customer.id,
                legal_entity_id=legal_entity,
                pipeline_stage_id=stage.id,
                probability=20,
                custom_fields={},
            )
            session.add(deal)
            session.flush()
            session.add(
                DealValueLine(
                    deal_id=deal.id,
                    position=0,
                    unit="flat",
                    delivery_period="one_off",
                    unit_price=Decimal("100.00"),
                    quantity=Decimal(1),
                )
            )
            session.flush()
            # One note on each of the three records a thread hangs on, written by hand.
            for kind, record in (("organization", customer), ("person", person), ("deal", deal)):
                session.add(
                    Note(
                        entity_type=kind,
                        entity_id=record.id,
                        text=f"{_PREFIX} {tag} {kind}",
                        source="web",
                    )
                )
            session.add(
                Contract(
                    customer_id=customer.id,
                    legal_entity_id=legal_entity,
                    title=f"{_PREFIX} {tag} contratto",
                    start=TODAY,
                    renewal_type="none",
                    termination_notice_days=30,
                    billing_cadence="monthly",
                    expense_policy={"kind": "not_reimbursable"},
                    custom_fields={},
                )
            )
            # A classified inbox item (REB-971): its legal entity's alone.
            session.add(
                InboxItem(
                    original_name=f"{_PREFIX} {tag}.pdf",
                    file_type="pdf",
                    size_bytes=1,
                    sha256="0" * 64,
                    content_sha256="0" * 64,
                    legal_entity_id=legal_entity,
                )
            )
            session.add(
                Document(
                    customer_id=customer.id,
                    legal_entity_id=legal_entity,
                    type="offer",
                    title=f"{_PREFIX} {tag} offerta",
                    status="sent",
                    status_since=TODAY,
                    current_version=1,
                    custom_fields={},
                )
            )
            for number in range(1, n_invoices + 1):
                invoice = Invoice(
                    customer_id=customer.id,
                    legal_entity_id=legal_entity,
                    deal_id=deal.id,
                    type="invoice",
                    status="issued",
                    year=2026,
                    number=number,
                    taxable_amount=Decimal("100.00"),
                    vat_amount=Decimal("0.00"),
                    stamp_duty=Decimal("0.00"),
                    total=Decimal("100.00"),
                    issue_date=TODAY,
                    custom_fields={},
                )
                session.add(invoice)
                session.flush()
                session.add(
                    InvoiceLine(
                        invoice_id=invoice.id,
                        line_number=1,
                        description="Consulenza",
                        quantity=Decimal("1.000000"),
                        unit_price=Decimal("100.00"),
                        total_price=Decimal("100.00"),
                        vat_rate=Decimal("0.00"),
                        vat_nature="N2.2",
                    )
                )
            session.add(
                TimeEntry(
                    deal_id=deal.id,
                    user_id=user.id,
                    date=TODAY,
                    hours=Decimal("2.00"),
                    description="Lavorazione",
                    billable=True,
                    applied_rate=Decimal("50.000000"),
                    rate_source="manual",
                    applied_cost=None,
                    cost_source="absent",
                    custom_fields={},
                )
            )
            session.add(
                Purchase(
                    deal_id=deal.id,
                    legal_entity_id=legal_entity,
                    category_id=category.id,
                    date=TODAY,
                    amount=Decimal("10.00"),
                    description=f"{_PREFIX} {tag} costo",
                    custom_fields={},
                )
            )
            session.add(
                Activity(
                    entity_type="organization",
                    entity_id=customer.id,
                    kind=f"{_PREFIX}.test",
                    actor_id=None,
                    actor_type="system",
                    payload={},
                    occurred_at=datetime.now(UTC),
                )
            )
            # The register's own timeline row: no record behind its id, the legal entity in
            # the payload (`InvoiceService.declare_gaps`), and a gap's reason inside.
            session.add(
                Activity(
                    entity_type="invoice_register",
                    entity_id=uuid5(
                        NAMESPACE_URL, f"pigrocrm:invoice_register:{legal_entity}:2026"
                    ),
                    kind=f"{_PREFIX}.test",
                    actor_id=None,
                    actor_type="system",
                    payload={
                        "legal_entity_id": str(legal_entity),
                        "year": 2026,
                        "number": 7,
                        "reason": tag,
                    },
                    occurred_at=datetime.now(UTC),
                )
            )

        seed(studio.id, "studio", 2)
        seed(foreign.id, "estero", 1)
        # One shared cost and one space-level timeline row: «all»'s alone, and everyone's.
        session.add(
            Purchase(
                deal_id=None,
                legal_entity_id=None,
                category_id=category.id,
                date=TODAY,
                amount=Decimal("5.00"),
                description=f"{_PREFIX} condiviso",
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
        # Two unclassified inbox items: one the operator uploaded, which is theirs whatever
        # their scope, and one nobody did, which is an unscoped administrator's alone.
        for owner, tag in ((user.id, "mio"), (None, "di nessuno")):
            session.add(
                InboxItem(
                    original_name=f"{_PREFIX} {tag}.pdf",
                    file_type="pdf",
                    size_bytes=1,
                    sha256="0" * 64,
                    content_sha256="0" * 64,
                    uploaded_by_id=owner,
                )
            )
        session.commit()
        ids = World(
            db_engine,
            _application_role(db_engine),
            studio.id,
            foreign.id,
            user.id,
            {
                "organizations": 1,
                "people": 1,
                "deals": 1,
                "deal_value_lines": 1,
                "contact_points": 2,
                "contracts": 1,
                "documents": 1,
                "notes": 3,
                "invoices": 1,
                "invoice_lines": 1,
                "time_entries": 1,
                "purchases": 1,
                "activities": 3,  # its customer's, its register's, and the space-level one
                "legal_entities": 1,
                "inbox_items": 2,  # its legal entity's, and the one the operator uploaded
            },
        )
    try:
        yield ids
    finally:
        ids.app.dispose()
        with factory() as session:
            organizations = select(Organization.id).where(
                Organization.legal_name.like(f"{_PREFIX} %")
            )
            invoices = select(Invoice.id).where(Invoice.customer_id.in_(organizations))
            session.execute(delete(Activity).where(Activity.kind == f"{_PREFIX}.test"))
            session.execute(
                delete(TimeEntry).where(
                    TimeEntry.deal_id.in_(select(Deal.id).where(Deal.name.like(f"{_PREFIX} %")))
                )
            )
            # The notes this file seeded and the ones its tests wrote as the operator, with
            # the timeline entries they left on this file's deals.
            session.execute(
                delete(NoteRevision).where(
                    NoteRevision.note_id.in_(select(Note.id).where(Note.author_id == ids.user_id))
                )
            )
            session.execute(
                delete(Note).where(Note.text.like(f"{_PREFIX} %") | (Note.author_id == ids.user_id))
            )
            session.execute(
                delete(Activity).where(
                    Activity.kind.like("note\\_%"),
                    Activity.entity_id.in_(select(Deal.id).where(Deal.name.like(f"{_PREFIX} %"))),
                )
            )
            session.execute(delete(InboxItem).where(InboxItem.original_name.like(f"{_PREFIX} %")))
            session.execute(delete(Purchase).where(Purchase.description.like(f"{_PREFIX} %")))
            session.execute(delete(CostCategory).where(CostCategory.name.like(f"{_PREFIX} %")))
            session.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id.in_(invoices)))
            session.execute(delete(Invoice).where(Invoice.customer_id.in_(organizations)))
            session.execute(delete(Document).where(Document.title.like(f"{_PREFIX} %")))
            session.execute(delete(Contract).where(Contract.title.like(f"{_PREFIX} %")))
            session.execute(
                delete(DealValueLine).where(
                    DealValueLine.deal_id.in_(select(Deal.id).where(Deal.name.like(f"{_PREFIX} %")))
                )
            )
            session.execute(delete(Deal).where(Deal.name.like(f"{_PREFIX} %")))
            session.execute(delete(Person).where(Person.first_name.like(f"{_PREFIX} %")))
            session.execute(
                delete(Organization).where(Organization.legal_name.like(f"{_PREFIX} %"))
            )
            session.execute(delete(PipelineStage).where(PipelineStage.name.like(f"{_PREFIX} %")))
            session.execute(delete(User).where(User.name == f"{_PREFIX} operatore"))
            session.execute(delete(LegalEntity).where(LegalEntity.id == ids.foreign))
            remove_legal_entity(session, inserted)
            session.commit()


def _scoped(world: World, *legal_entities: UUID) -> Session:
    """A session of the application role, bound to `legal_entities` as a scoped
    collaboratore of this space would be."""
    session = session_factory(world.app)()
    actor = Actor(
        id=world.user_id, type="user", role="collaborator", legal_entities=tuple(legal_entities)
    )
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
    with _scoped(world, world.foreign) as session:
        organizations = OrganizationRepository(session).list(OrganizationListQuery(limit=50))
        assert [c.legal_name for c in organizations] == [f"{_PREFIX} estero"]
        deals = DealRepository(session).list(DealListQuery(limit=50))
        assert [d.name for d in deals] == [f"{_PREFIX} estero deal"]
        # The lines of a deal out of sight are not readable by asking for its id.
        everyone = set(session.scalars(select(Deal.id).where(Deal.name.like(f"{_PREFIX} %"))))
        assert everyone == {deals[0].id}
        hidden = session.scalars(
            select(DealValueLine.deal_id).where(
                DealValueLine.deal_id.in_(select(Deal.id).where(Deal.name.like("% studio deal")))
            )
        ).all()
        assert hidden == []
        invoices = InvoiceRepository(session).list(InvoiceListQuery(limit=50))
        assert len(invoices) == 1 and invoices[0].legal_entity_id == world.foreign
        documents = DocumentRepository(session).list(DocumentListQuery(limit=50))
        assert [d.title for d in documents] == [f"{_PREFIX} estero offerta"]
        people = PersonRepository(session).list(PersonListQuery(limit=50))
        assert [p.first_name for p in people] == [f"{_PREFIX} estero contatto"]
        entries = TimeEntryRepository(session).list(TimeEntryListQuery(limit=50))
        assert len(entries) == 1
        costs = PurchaseRepository(session).list(PurchaseListQuery(limit=50))
        # The shared cost is «all»'s alone (§1.7): not here.
        assert [c.description for c in costs] == [f"{_PREFIX} estero costo"]


def test_a_raw_select_on_every_policied_table_agrees_with_the_lists(world: World) -> None:
    """No code filter is involved: the same numbers straight from the tables."""
    with _scoped(world, world.foreign) as session:
        for table, expected in world.foreign_rows.items():
            assert _count(session, table) == expected, table
    # Both legal entities in scope: everything but what belongs to neither.
    with _scoped(world, world.studio, world.foreign) as session:
        assert _count(session, "organizations") == 2
        assert _count(session, "deal_value_lines") == 2
        assert _count(session, "contact_points") == 4
        assert _count(session, "notes") == 6
        assert _count(session, "invoices") == 3
        assert _count(session, "purchases") == 2  # the shared one is still «all»'s alone
        assert _count(session, "activities") == 5
        assert _count(session, "inbox_items") == 3  # both classified, and the operator's own


def test_an_unscoped_actor_and_all_see_everything(world: World) -> None:
    session = session_factory(world.app)()
    bind_scope(session, Actor(id=world.user_id, type="user", role="collaborator"))
    with session:
        assert _count(session, "organizations") == 2
        assert _count(session, "purchases") == 3
        assert _count(session, "activities") == 5
        assert _count(session, "inbox_items") == 4


def test_a_session_with_nothing_bound_sees_nothing(world: World) -> None:
    """The closed default: a connection nobody bound reads no row of any policied
    table, which is what a forgotten `bind_scope` costs, rather than everything."""
    with session_factory(world.app)() as session:
        for table in POLICIED - {"activities"}:
            assert _count(session, table) == 0, table
        # The timeline is the one table whose space-level rows (a user's, a template's)
        # are everyone's by design (spec §4): the legal entity-bound ones are gone.
        assert _count(session, "activities") == 1


def test_a_shared_organization_is_seen_by_every_scope_that_has_a_legal_entity_and_by_no_other(
    world: World,
) -> None:
    """REB-970: `legal_entity_id` NULL is an organization shared across the legal entities, as
    a cost's is. Every scope with a legal entity in it reads the row and its people, one
    legal entity's scope as well as another's, and the closed default stands for a session with
    nothing bound or with no legal entity left."""
    with session_factory(world.engine)() as session:
        shared = Organization(legal_name=f"{_PREFIX} shared", country="IT", custom_fields={})
        session.add(shared)
        session.flush()
        shared.legal_entity_id = None  # explicit `None` at insert would take the column default
        session.add(
            Person(first_name=f"{_PREFIX} shared contatto", customer_id=shared.id, custom_fields={})
        )
        session.commit()

    def names(session: Session, table: str, column: str) -> set[str]:
        sql = f"SELECT {column} FROM {table} WHERE {column} LIKE '{_PREFIX} shared%'"  # noqa: S608
        return set(session.execute(text(sql)).scalars())

    for legal_entities in ((world.studio,), (world.foreign,), (world.studio, world.foreign)):
        with _scoped(world, *legal_entities) as session:
            assert names(session, "organizations", "legal_name") == {f"{_PREFIX} shared"}
            assert names(session, "people", "first_name") == {f"{_PREFIX} shared contatto"}
    with _scoped(world) as session:
        assert names(session, "organizations", "legal_name") == set()
        assert names(session, "people", "first_name") == set()
    with session_factory(world.app)() as session:
        assert names(session, "organizations", "legal_name") == set()
    with _scoped(world, world.studio) as session:
        visible = session.execute(
            text("SELECT organization_visible(id) FROM organizations WHERE legal_name = :name"),
            {"name": f"{_PREFIX} shared"},
        ).scalar_one()
        assert visible is True


def test_to_scope_with_no_legal_entity_left_sees_nothing_either(world: World) -> None:
    with _scoped(world) as session:
        assert _count(session, "organizations") == 0
        assert _count(session, "activities") == 1  # the space-level row is everyone's


def test_to_scoped_member_reads_and_writes_the_notes_of_their_legal_entities_alone(
    world: World,
) -> None:
    """REB-879: the notes table follows its parent's visibility through `entity_visible`.
    Through the service a record of the other legal entity is simply not there (a note cannot
    be listed, added, edited or deleted on it); through a raw `INSERT` the policy itself
    refuses the line being crossed."""
    with session_factory(world.engine)() as session:
        studio_deal = session.scalars(
            select(Deal.id).where(Deal.name == f"{_PREFIX} studio deal")
        ).one()
        foreign_deal = session.scalars(
            select(Deal.id).where(Deal.name == f"{_PREFIX} estero deal")
        ).one()
        studio_note = session.scalars(
            select(Note.id).where(Note.text == f"{_PREFIX} studio deal")
        ).one()
    actor = Actor(
        id=world.user_id, type="user", role="collaborator", legal_entities=(world.foreign,)
    )
    with _scoped(world, world.foreign) as session:
        service = NoteService(session)
        assert [n.text for n in service.list_for_entity("deal", foreign_deal, actor)] == [
            f"{_PREFIX} estero deal"
        ]
        with pytest.raises(NotFound):
            service.list_for_entity("deal", studio_deal, actor)
        with pytest.raises(NotFound):
            service.add("deal", studio_deal, NoteCreate(text="out of scope"), actor)

        mine = service.add("deal", foreign_deal, NoteCreate(text="in my scope"), actor)
        assert mine.source == "web" and mine.editable is True
        assert service.update(mine.id, NoteUpdate(text="cambiata"), actor).text == "cambiata"
        assert [r.text for r in service.revisions(mine.id, actor)] == ["in my scope"]
        service.delete(mine.id, actor)
        assert len(service.list_for_entity("deal", foreign_deal, actor)) == 1

        # A note of the other legal entity is not there to edit or delete either.
        with pytest.raises(NotFound):
            service.update(studio_note, NoteUpdate(text="no"), actor)
        with pytest.raises(NotFound):
            service.delete(studio_note, actor)
        with pytest.raises(NotFound):
            service.revisions(studio_note, actor)

        # The raw line the policy itself guards.
        with pytest.raises(DBAPIError) as refused:
            session.execute(
                text(
                    "INSERT INTO notes (id, entity_type, entity_id, text) "
                    "VALUES (gen_random_uuid(), 'deal', :deal, 'out of scope')"
                ),
                {"deal": studio_deal},
            )
        assert is_policy_refusal(refused.value)
    with _scoped(world, world.studio) as session:
        assert [
            n.text
            for n in NoteService(session).list_for_entity(
                "deal",
                studio_deal,
                Actor(
                    id=world.user_id,
                    type="user",
                    role="collaborator",
                    legal_entities=(world.studio,),
                ),
            )
        ] == [f"{_PREFIX} studio deal"]


def test_the_register_s_timeline_rows_follow_their_legal_entity(world: World) -> None:
    """An `invoice_register` row has no record behind its id: the policy reads the
    legal entity from its payload, so a gap's number and reason reach only that legal entity's
    members and «all» (the adversarial pass on PR #513 found them everyone's)."""
    query = text(
        "SELECT payload->>'reason' FROM activities WHERE entity_type = 'invoice_register' "
        f"AND kind = '{_PREFIX}.test' ORDER BY 1"
    )
    with _scoped(world, world.studio) as session:
        assert session.execute(query).scalars().all() == ["studio"]
    with _scoped(world, world.foreign) as session:
        assert session.execute(query).scalars().all() == ["estero"]
    with _scoped(world, world.studio, world.foreign) as session:
        assert session.execute(query).scalars().all() == ["estero", "studio"]
    with session_factory(world.app)() as session:
        assert session.execute(query).scalars().all() == []


def test_a_reminder_s_draft_and_timeline_row_follow_the_invoice_not_the_moved_customer(
    world: World,
) -> None:
    """A payment reminder is prepared on the customer (the draft and the timeline row
    hang there) but copies the invoice's number, total and IBAN. A customer moved to
    the other legal entity keeps its invoices where they were (spec §1.7): the draft and
    the row stay with the invoice, so the new legal entity's members read neither, the old
    one's cannot open the customer anyway, and «all» reads both (CodeRabbit's second
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
            select(Organization.id).where(Organization.legal_entity_id == world.studio)
        ).scalar_one()
        foreign_invoice = session.execute(
            select(Invoice.id).where(Invoice.legal_entity_id == world.foreign)
        ).scalar_one()
        # The invoice's reminder, filed on a customer that now belongs to «studio».
        reminder = PaymentReminder(invoice_id=foreign_invoice, sequence=1)
        session.add(reminder)
        session.flush()
        draft = EmailDraft(
            entity_type="organization",
            entity_id=studio_customer,
            message_id_header=f"<{_PREFIX}@example.test>",
            subject=f"{_PREFIX} sollecito",
            body_markdown="Fattura 2026/1, totale 100,00 EUR, IBAN IT00",
            payment_reminder_id=reminder.id,
        )
        session.add(draft)
        session.add(
            Activity(
                entity_type="organization",
                entity_id=studio_customer,
                kind=f"{_PREFIX}.sollecito",
                actor_id=None,
                actor_type="system",
                payload={"invoice_id": str(foreign_invoice), "number": "2026/1"},
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
                gmail_message_id=message.id, entity_type="organization", entity_id=studio_customer
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
        with _scoped(world, world.foreign) as session:
            assert session.execute(drafts).scalar_one() == 0
            assert session.execute(rows).scalar_one() == 0
            # Own mailbox and own invoice: the message, though the customer is gone.
            assert session.execute(messages).scalar_one() == 1
        with _scoped(world, world.studio, world.foreign) as session:
            assert session.execute(drafts).scalar_one() == 1
            assert session.execute(rows).scalar_one() == 1
            assert session.execute(messages).scalar_one() == 1
        session = session_factory(world.app)()
        bind_scope(session, Actor(id=world.user_id, type="user", role="collaborator"))
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
    """The invoice keeps its legal entity when its customer moves (spec §1.7); the PDF and
    the XML rendered for it afterwards are the invoice's, not the customer's new
    legal entity's (CodeRabbit's fourth adversarial pass on PR #513)."""
    from pigrocrm.core.config import Settings
    from pigrocrm.core.invoices.service import InvoiceService
    from pigrocrm.core.storage import LocalFileStorage

    factory = session_factory(world.engine)
    with factory() as session:
        invoice = session.execute(
            select(Invoice).where(Invoice.legal_entity_id == world.foreign)
        ).scalar_one()
        customer = session.get(Organization, invoice.customer_id)
        assert customer is not None
        customer.legal_entity_id = world.studio  # the move, by hand: the rows under it stay
        session.commit()
        service = InvoiceService(
            session,
            LocalFileStorage(tmp_path),
            Settings(_env_file=None),  # type: ignore[call-arg]
        )
        document = service.artifacts.artifact_document(
            invoice, "invoice_xml", f"{_PREFIX} xml", Actor.system()
        )
        session.commit()
        document_id = document.id
    pdf_document_id = None
    try:
        with factory() as session:
            row = session.get(Document, document_id)
            assert row is not None and row.legal_entity_id == world.foreign
        with _scoped(world, world.studio) as session:
            assert session.get(Document, document_id) is None
        with _scoped(world, world.foreign) as session:
            assert session.get(Document, document_id) is not None
            # And the invoice's own member renders the other stream although the
            # customer is no longer theirs to see (Greptile on PR #513).
            invoice = session.execute(
                select(Invoice).where(Invoice.legal_entity_id == world.foreign)
            ).scalar_one()
            scoped_service = InvoiceService(
                session,
                LocalFileStorage(tmp_path),
                Settings(_env_file=None),  # type: ignore[call-arg]
            )
            pdf = scoped_service.artifacts.artifact_document(
                invoice,
                "invoice",
                f"{_PREFIX} pdf",
                Actor(
                    id=world.user_id,
                    type="user",
                    role="collaborator",
                    legal_entities=(world.foreign,),
                ),
            )
            session.commit()
            pdf_document_id = pdf.id
            assert pdf.legal_entity_id == world.foreign
    finally:
        with factory() as session:
            for doc_id in (document_id, pdf_document_id):
                if doc_id is not None:
                    session.execute(delete(Activity).where(Activity.entity_id == doc_id))
            invoice = session.execute(
                select(Invoice).where(Invoice.legal_entity_id == world.foreign)
            ).scalar_one()
            invoice.xml_document_id = None
            invoice.pdf_document_id = None
            session.flush()
            for doc_id in (document_id, pdf_document_id):
                if doc_id is not None:
                    session.execute(delete(Document).where(Document.id == doc_id))
            customer = session.get(Organization, invoice.customer_id)
            assert customer is not None
            customer.legal_entity_id = world.foreign
            session.commit()


def test_an_imported_original_takes_the_legal_entity_the_caller_names(
    world: World, tmp_path: Path
) -> None:
    """`import_bytes` is the one seam imported bytes enter through: an imported
    invoice's original PDF names the invoice's legal entity, so it stays there whatever
    legal entity the customer is in (CodeRabbit's fifth adversarial pass on PR #513)."""
    from pigrocrm.core.config import Settings
    from pigrocrm.core.documents.service import DocumentService
    from pigrocrm.core.storage import LocalFileStorage

    factory = session_factory(world.engine)
    with factory() as session:
        studio_customer = session.execute(
            select(Organization.id).where(Organization.legal_entity_id == world.studio)
        ).scalar_one()
        created = DocumentService(
            session,
            LocalFileStorage(tmp_path),
            Settings(_env_file=None),  # type: ignore[call-arg]
        ).import_bytes(
            customer_id=studio_customer,
            type="invoice",
            title=f"{_PREFIX} originale",
            data=b"%PDF-1.4 x",
            content_type="application/pdf",
            actor=Actor.system(),
            origin={"drive_file_id": "x"},
            legal_entity_id=world.foreign,
        )
    try:
        assert created.legal_entity_id == world.foreign
        with _scoped(world, world.studio) as session:
            assert session.get(Document, created.id) is None
        with _scoped(world, world.foreign) as session:
            assert session.get(Document, created.id) is not None
    finally:
        with factory() as session:
            from pigrocrm.core.documents.models import DocumentVersion

            session.execute(delete(Activity).where(Activity.entity_id == created.id))
            session.execute(
                delete(DocumentVersion).where(DocumentVersion.document_id == created.id)
            )
            session.execute(delete(Document).where(Document.id == created.id))
            session.commit()


def test_a_proposal_follows_its_contract_as_well_as_its_document(world: World) -> None:
    """A work-day proposal names a contract and an evidence document; after the customer
    moved, the document may sit in the new legal entity while the contract stayed. Neither
    legal entity alone reads the proposal, both together and «all» do (CodeRabbit's sixth
    adversarial pass on PR #513)."""
    from decimal import Decimal as D

    from pigrocrm.core.proposals.models import Proposal

    factory = session_factory(world.engine)
    with factory() as session:
        foreign_contract = session.execute(
            select(Contract.id).where(Contract.legal_entity_id == world.foreign)
        ).scalar_one()
        studio_document = session.execute(
            select(Document.id).where(Document.legal_entity_id == world.studio)
        ).scalar_one()
        proposal = Proposal(
            document_id=studio_document,
            contract_id=foreign_contract,
            target_type="work_unit",
            proposed_fields={"day": "2026-03-10"},
            excerpt=f"{_PREFIX} evidenza",
            confidence=D("0.80"),
        )
        session.add(proposal)
        session.commit()
        proposal_id = proposal.id
    query = text(f"SELECT count(*) FROM proposals WHERE excerpt = '{_PREFIX} evidenza'")
    try:
        with _scoped(world, world.studio) as session:
            assert session.execute(query).scalar_one() == 0
        with _scoped(world, world.foreign) as session:
            assert session.execute(query).scalar_one() == 0
        with _scoped(world, world.studio, world.foreign) as session:
            assert session.execute(query).scalar_one() == 1
        with session_factory(world.app)() as session:
            assert session.execute(query).scalar_one() == 0
    finally:
        with factory() as session:
            session.execute(delete(Proposal).where(Proposal.id == proposal_id))
            session.commit()


def test_hours_billed_on_an_invoice_out_of_sight_stay_frozen(world: World) -> None:
    """A time entry follows its deal, and may be bound to a line of an invoice in another
    legal entity (an import put it there). The member who sees the deal and not the invoice
    cannot resolve the line, and the guard that freezes billed hours must read that as
    frozen rather than as free (CodeRabbit's seventh adversarial pass on PR #513)."""
    from decimal import Decimal as D

    from pigrocrm.core.errors import ImmutableField
    from pigrocrm.core.timetracking.schemas import TimeEntryUpdate
    from pigrocrm.core.timetracking.service import TimeEntryService

    factory = session_factory(world.engine)
    with factory() as session:
        entry = session.execute(
            select(TimeEntry)
            .join(Deal, Deal.id == TimeEntry.deal_id)
            .where(Deal.legal_entity_id == world.studio)
        ).scalar_one()
        line = session.execute(
            select(InvoiceLine)
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .where(Invoice.legal_entity_id == world.foreign)
        ).scalar_one()
        entry.invoice_line_id = line.id
        session.commit()
        entry_id = entry.id
    try:
        with _scoped(world, world.studio) as session, pytest.raises(ImmutableField):
            TimeEntryService(session).update(
                entry_id,
                TimeEntryUpdate(hours=D("3.00")),
                Actor(
                    id=world.user_id,
                    type="user",
                    role="collaborator",
                    legal_entities=(world.studio,),
                ),
            )
        with _scoped(world, world.studio, world.foreign) as session, pytest.raises(ImmutableField):
            TimeEntryService(session).update(
                entry_id,
                TimeEntryUpdate(hours=D("3.00")),
                Actor(id=world.user_id, type="user", role="collaborator"),
            )
        # And the backlog does not count those hours as still to invoice, from the
        # deal's scope (the line is out of sight) as from «all» (the line is issued).
        from pigrocrm.core.analytics.repository import AnalyticsRepository

        with _scoped(world, world.studio) as session:
            hours, _, _, entries = AnalyticsRepository(session).unbilled_backlog(
                legal_entity_id=world.studio
            )
            assert hours == D("0.00") and entries == 0
        with _scoped(world, world.studio, world.foreign) as session:
            hours, _, _, entries = AnalyticsRepository(session).unbilled_backlog(
                legal_entity_id=world.studio
            )
            assert hours == D("0.00") and entries == 0
    finally:
        with factory() as session:
            stored_entry = session.get(TimeEntry, entry_id)
            assert stored_entry is not None
            stored_entry.invoice_line_id = None
            session.commit()


def test_a_non_pdf_version_is_refused_on_an_invoice_s_document_out_of_sight(
    world: World, tmp_path: Path
) -> None:
    """The guard that keeps a non-PDF version off an invoice's PDF asks past the policies
    (`invoice_type_of_pdf`, a definer function): an invoice the uploader cannot see
    still answers, so «no invoice» cannot mean «go ahead» for the one member who may
    not (CodeRabbit's eighth adversarial pass on PR #513)."""
    from pigrocrm.core.config import Settings
    from pigrocrm.core.documents.service import DocumentService
    from pigrocrm.core.errors import ValidationFailed
    from pigrocrm.core.storage import LocalFileStorage

    factory = session_factory(world.engine)
    with factory() as session:
        studio_document = session.execute(
            select(Document).where(Document.legal_entity_id == world.studio)
        ).scalar_one()
        invoice = session.execute(
            select(Invoice).where(Invoice.legal_entity_id == world.foreign)
        ).scalar_one()
        invoice.pdf_document_id = studio_document.id  # a link legacy data may hold
        session.commit()
        document_id, invoice_id = studio_document.id, invoice.id
    try:
        with _scoped(world, world.studio) as session:
            service = DocumentService(
                session,
                LocalFileStorage(tmp_path),
                Settings(_env_file=None),  # type: ignore[call-arg]
            )
            with pytest.raises(ValidationFailed) as refused:
                service.add_version(
                    document_id,
                    b"<xml/>",
                    "application/xml",
                    Actor(
                        id=world.user_id,
                        type="user",
                        role="collaborator",
                        legal_entities=(world.studio,),
                    ),
                )
            assert refused.value.details["field"] == "content_type"
    finally:
        with factory() as session:
            stored_invoice = session.get(Invoice, invoice_id)
            assert stored_invoice is not None
            stored_invoice.pdf_document_id = None
            session.commit()


def test_no_active_deal_ends_up_on_an_archived_customer_across_the_line(world: World) -> None:
    """The invariant holds past what the archiver or the restorer can see: a customer
    with an active deal in another legal entity cannot be archived by a member of its own,
    and a deal cannot be restored under a customer archived in a legal entity the deal's
    member cannot see (CodeRabbit's ninth adversarial pass on PR #513)."""
    from pigrocrm.core.deals.service import DealService
    from pigrocrm.core.errors import Conflict
    from pigrocrm.core.organizations.service import OrganizationService

    factory = session_factory(world.engine)
    with factory() as session:
        studio_customer = session.execute(
            select(Organization).where(Organization.legal_entity_id == world.studio)
        ).scalar_one()
        foreign_deal = session.execute(
            select(Deal).where(Deal.legal_entity_id == world.foreign)
        ).scalar_one()
        original_customer = foreign_deal.customer_id
        foreign_deal.customer_id = studio_customer.id  # «estero»'s deal under «studio»'s customer
        session.commit()
        customer_id, deal_id = studio_customer.id, foreign_deal.id
    try:
        studio_actor = Actor(
            id=world.user_id, type="user", role="collaborator", legal_entities=(world.studio,)
        )
        with _scoped(world, world.studio) as session, pytest.raises(Conflict) as refused:
            OrganizationService(session).soft_delete(customer_id, studio_actor)
        # Refused on both deals, said with the one in sight: the other is another
        # legal entity's count.
        assert refused.value.details["active_deals"] == 1
        # Archive the deal, then the customer (by hand, as the superuser), and try to
        # restore the deal as a member who cannot see the customer.
        with factory() as session:
            deal = session.get(Deal, deal_id)
            customer = session.get(Organization, customer_id)
            assert deal is not None and customer is not None
            deal.deleted_at = datetime.now(UTC)
            customer.deleted_at = datetime.now(UTC)
            session.commit()
        foreign_actor = Actor(
            id=world.user_id, type="user", role="collaborator", legal_entities=(world.foreign,)
        )
        with _scoped(world, world.foreign) as session, pytest.raises(Conflict):
            DealService(session).restore(deal_id, foreign_actor)
    finally:
        with factory() as session:
            deal = session.get(Deal, deal_id)
            customer = session.get(Organization, customer_id)
            assert deal is not None and customer is not None
            deal.deleted_at = None
            deal.customer_id = original_customer
            customer.deleted_at = None
            session.execute(
                delete(Activity).where(
                    Activity.entity_id.in_([deal_id, customer_id]),
                    Activity.kind != f"{_PREFIX}.test",
                )
            )
            session.commit()


def test_an_insert_across_the_line_is_refused_by_the_policy(world: World) -> None:
    with _scoped(world, world.foreign) as session:
        studio_customer = session.execute(text("SELECT count(*) FROM organizations")).scalar_one()
        assert studio_customer == 1
        with pytest.raises(DBAPIError) as refused:
            session.add(
                Organization(
                    legal_name=f"{_PREFIX} intruso",
                    country="IT",
                    custom_fields={},
                    legal_entity_id=world.studio,
                )
            )
            session.flush()
        assert "legal_entity_scope" in str(refused.value) or "row-level security" in str(
            refused.value
        )
        session.rollback()
    # And the row was not written: the superuser sees none.
    with session_factory(world.engine)() as session:
        assert (
            session.execute(
                select(func.count(Organization.id)).where(
                    Organization.legal_name == f"{_PREFIX} intruso"
                )
            ).scalar_one()
            == 0
        )


def test_the_scope_survives_a_commit_on_the_same_session(world: World) -> None:
    """`set_config(..., true)` dies with the transaction; the listener rebinds it."""
    with _scoped(world, world.foreign) as session:
        assert _count(session, "organizations") == 1
        session.commit()
        assert _count(session, "organizations") == 1
        session.rollback()
        assert _count(session, "organizations") == 1


# --- the introspection ----------------------------------------------------------------


def _tables_reaching_an_legal_entity() -> set[str]:
    """Every table with an `legal_entity_id`, or a foreign-key path to one, from the models."""
    reaching = {t.name for t in Base.metadata.tables.values() if "legal_entity_id" in t.c}
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


def test_every_table_that_reaches_an_legal_entity_carries_the_policy(db_engine: Engine) -> None:
    """A table added later without a policy fails the build, the way `test_architecture`
    fails an undeclared import. The polymorphic tables (`activities`, the Gmail links and
    drafts) reach a legal entity through `entity_id`, which is no foreign key: they are in
    0048's list and checked against `pg_policies` below like the others."""
    with db_engine.connect() as connection:
        policied = {
            row[0]
            for row in connection.execute(
                text("SELECT tablename FROM pg_policies WHERE policyname = 'legal_entity_scope'")
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
    expected = (_tables_reaching_an_legal_entity() & space_tables) - DECLARED_EXCEPTIONS
    missing = expected - policied
    assert missing == set(), f"tables reaching a legal entity with no policy: {sorted(missing)}"
    assert "users" not in policied and "user_legal_entities" not in policied


# The tables whose policy reaches a legal entity through a parent row, as 0049 lists them: an
# unscoped session answers «all» on the policy's first arm and calls no function.
FAST_PATH = {
    "inbox_items",  # not through a parent: its own legal entity and uploader, after the «all» arm
    "people",
    "deal_value_lines",
    "contact_points",
    "due_items",
    "due_item_settlements",
    "bank_accounts",
    "bank_statements",
    "bank_movements",
    "statement_movements",
    "movement_matches",
    "movement_match_items",
    "organization_ibans",
    "staff_members",
    "staff_payslips",
    "expense_notes",
    "payroll_month_closures",
    "payroll_settings",
    "engagements",
    "engagement_monthly_reports",
    "purchase_vat_lines",
    "time_entries",
    "time_timers",
    "invoice_lines",
    "payment_reminders",
    "document_versions",
    "proposals",
    "rate_cards",
    "renewal_assumptions",
    "contract_expenses",
    "contract_email_templates",
    "clause_notes",
    "notes",
    "note_revisions",
    "work_units",
    "approvals",
    "work_unit_transitions",
    "tasks",
    "gmail_message_links",
    "email_drafts",
    "gmail_messages",
    "activities",
}
THROUGH_THE_PARENT = (
    "organization_visible",
    "person_visible",
    "deal_visible",
    "invoice_visible",
    "contract_visible",
    "document_visible",
    "work_unit_visible",
    "entity_visible",
)


def test_the_through_the_parent_policies_put_the_all_fast_path_first(db_engine: Engine) -> None:
    """Migration 0049 (REB-656): measured on REB-633's corpus, the dashboard's recent 50
    cost 15.8 ms as «all» against 0.04 ms with no policy, one SQL function call per row
    before `*` answered. The fast path is the shape of the stored predicate, which the
    template applies from the revision itself; this reads it back, so a template rebuilt
    without 0049 or a later revision that writes the policy afresh fails here by name."""
    with db_engine.connect() as connection:
        policies = {
            row.tablename: (row.qual, row.with_check)
            for row in connection.execute(
                text(
                    "SELECT tablename, qual, with_check FROM pg_policies "
                    "WHERE policyname = 'legal_entity_scope'"
                )
            )
        }
        bodies = dict(
            connection.execute(
                text("SELECT proname, prosrc FROM pg_proc WHERE proname = ANY(:names)"),
                {"names": list(THROUGH_THE_PARENT)},
            ).all()
        )
    # Postgres deparses the arm as written, minus the parentheses around a lone call and
    # with a `CASE` on a line of its own, so the prefix is read on the tokens.
    fast = "(scope_all()OR"
    for table in FAST_PATH:
        using, check = policies[table]
        assert "".join(using.split()).startswith(fast), f"{table}: {using[:60]}"
        # A link is written after the mail has left: its `WITH CHECK` stays `true`.
        assert check == ("true" if table == "gmail_message_links" else using), table
    for table in POLICIED - FAST_PATH:
        assert not "".join(policies[table][0].split()).startswith(fast), table
    assert set(bodies) == set(THROUGH_THE_PARENT)
    spelled_out = "current_setting('pigrocrm.legal_entities', true) = '*' OR "
    for name, body in bodies.items():
        if name == "entity_visible":
            # Inside each branch, so an unknown type still answers the third argument.
            assert body.lstrip().startswith("SELECT CASE $1"), body.strip()[:70]
            assert f"THEN {spelled_out}EXISTS (" in body and "ELSE $3" in body
            assert "scope_all() OR" not in body
        else:
            assert body.lstrip().startswith(f"SELECT {spelled_out}"), f"{name}: {body[:70]}"


def test_a_scoped_member_reads_the_inbox_items_of_their_legal_entities_and_their_own_uploads(
    world: World,
) -> None:
    """REB-971: `inbox_items` is policied on its own legal entity (set at classification) and
    on its uploader, since an item has no legal entity until then. The service adds the rule
    that an unclassified item is nobody else's; this is the scope under it, as the role."""
    from pigrocrm.core.inbox.schemas import InboxListQuery
    from pigrocrm.core.inbox.service import InboxService

    actor = Actor(
        id=world.user_id, type="user", role="collaborator", legal_entities=(world.foreign,)
    )
    with _scoped(world, world.foreign) as session:
        names = sorted(
            i.original_name
            for i in InboxService(session).list(InboxListQuery(limit=50), actor).items
        )
        assert names == [f"{_PREFIX} estero.pdf", f"{_PREFIX} mio.pdf"]
        # The other legal entity's item is not there, and cannot be written across the line.
        with pytest.raises(DBAPIError) as refused:
            session.execute(
                text(
                    "INSERT INTO inbox_items (id, source, original_name, size_bytes, sha256, "
                    "content_sha256, state, legal_entity_id) "
                    "VALUES (gen_random_uuid(), 'upload', 'x.pdf', 1, 'a', 'a', 'new', :studio)"
                ),
                {"studio": world.studio},
            )
        assert is_policy_refusal(refused.value)
    with _scoped(world) as session:  # no legal entity left: only what they uploaded themselves
        assert _count(session, "inbox_items") == 1
    with session_factory(world.app)() as session:  # nothing bound: nothing
        assert _count(session, "inbox_items") == 0


def test_a_copy_the_member_cannot_see_still_follows_an_item_that_becomes_a_copy(
    world: World,
) -> None:
    """REB-985: marking an item as a copy re-points the copies it had, and the update runs over
    every legal entity: a copy of another company's, which the member's scope hides, would
    otherwise keep pointing at an item that is now a copy. The member is told nothing of it."""
    from pigrocrm.core.inbox.schemas import InboxDuplicate
    from pigrocrm.core.inbox.service import InboxService

    ids = {name: uuid4() for name in ("original", "middle", "hidden_copy")}
    insert = (
        "INSERT INTO inbox_items (id, source, original_name, size_bytes, sha256, content_sha256, "
        "state, legal_entity_id, duplicate_of_id) "
        "VALUES (:id, 'upload', :name, 1, :hash, :hash, :state, :entity, :of)"
    )
    rows = [
        ("original", "new", world.foreign, None),
        ("middle", "new", world.foreign, None),
        ("hidden_copy", "duplicate", world.studio, "middle"),
    ]
    with session_factory(world.engine)() as admin_session:
        for name, state, entity, of in rows:
            admin_session.execute(
                text(insert),
                {
                    "id": ids[name],
                    "name": f"{_PREFIX} {name}.pdf",
                    "hash": name,
                    "state": state,
                    "entity": entity,
                    "of": ids[of] if of else None,
                },
            )
        admin_session.commit()
    actor = Actor(
        id=world.user_id, type="user", role="collaborator", legal_entities=(world.foreign,)
    )
    try:
        with _scoped(world, world.foreign) as session:
            assert session.get(InboxItem, ids["hidden_copy"]) is None  # not theirs to see
            InboxService(session).mark_duplicate(
                ids["middle"], InboxDuplicate(original_id=ids["original"]), actor
            )
            # And the scope is the member's again after it: the hidden copy is still hidden.
            assert session.get(InboxItem, ids["hidden_copy"]) is None
        with session_factory(world.engine)() as admin_session:
            copy = admin_session.get(InboxItem, ids["hidden_copy"])
            assert copy is not None and copy.duplicate_of_id == ids["original"]
    finally:
        with session_factory(world.engine)() as admin_session:
            admin_session.execute(delete(InboxItem).where(InboxItem.id.in_(list(ids.values()))))
            admin_session.commit()


def test_a_scoped_member_makes_and_reads_the_proposals_of_the_items_they_may_see(
    world: World,
) -> None:
    """REB-972: a proposal read from an inbox item has no document or contract, so its
    visibility is its item's (migration 0080): an operator limited to a legal entity creates and
    reads the proposal of the unclassified item they uploaded and of a classified item of theirs,
    and cannot make one for an item they do not see, nor read the proposal of one."""
    from decimal import Decimal as D

    from pigrocrm.core.proposals.models import Proposal

    factory = session_factory(world.engine)
    with factory() as session:
        items = {
            item.original_name: item.id
            for item in session.execute(
                select(InboxItem).where(InboxItem.original_name.like(f"{_PREFIX} %"))
            ).scalars()
        }
    mine, nobodys, foreign = (
        items[f"{_PREFIX} mio.pdf"],
        items[f"{_PREFIX} di nessuno.pdf"],
        items[f"{_PREFIX} estero.pdf"],
    )

    def proposal(item_id: UUID, tag: str) -> Proposal:
        return Proposal(
            inbox_item_id=item_id,
            target_type="receipt",
            proposed_fields={},
            excerpt=f"{_PREFIX} {tag}",
            confidence=D("0.80"),
        )

    try:
        with factory() as session:  # the superuser plants one on an item nobody uploaded
            session.add(proposal(nobodys, "di nessuno"))
            session.commit()
        with _scoped(world, world.studio) as session:
            session.add(proposal(mine, "mio"))  # the item they uploaded: theirs whatever the scope
            session.commit()
            seen = session.execute(
                text(f"SELECT excerpt FROM proposals WHERE excerpt LIKE '{_PREFIX} %'")
            ).scalars()
            assert sorted(seen) == [f"{_PREFIX} mio"]
        with _scoped(world, world.studio) as session:
            session.add(proposal(foreign, "estero"))  # another legal entity's item: refused
            with pytest.raises(DBAPIError) as refused:
                session.commit()
            assert is_policy_refusal(refused.value)
        with _scoped(world, world.foreign) as session:
            seen = session.execute(
                text(f"SELECT excerpt FROM proposals WHERE excerpt LIKE '{_PREFIX} %'")
            ).scalars()
            assert sorted(seen) == [
                f"{_PREFIX} mio"
            ]  # the operator uploaded it, whatever the scope
        with session_factory(world.app)() as session:  # nothing bound: nothing
            assert _count(session, "proposals") == 0
    finally:
        with factory() as session:
            session.execute(delete(Proposal).where(Proposal.excerpt.like(f"{_PREFIX} %")))
            session.commit()


def test_an_admin_scoped_away_from_the_client_reads_an_engagement_without_what_is_the_clients(
    world: World,
) -> None:
    """REB-976: a freelancer shared across the legal entities, an engagement whose client sits
    under the other one. As the application role, scoped to the studio alone, the days and the
    state are readable and the role, fee and client links are not; with both in scope nothing
    is left out."""
    import datetime as dt
    from decimal import Decimal

    from pigrocrm.core.collaborators.models import Engagement
    from pigrocrm.core.collaborators.service import EngagementService

    with session_factory(world.engine)() as session:
        shared = Organization(
            legal_name=f"{_PREFIX} freelance",
            country="IT",
            custom_fields={},
            legal_entity_id=None,
            roles=["freelancer"],
        )
        client = Organization(
            legal_name=f"{_PREFIX} cliente estero",
            country="GB",
            custom_fields={},
            legal_entity_id=world.foreign,
        )
        session.add_all([shared, client])
        session.flush()
        engagement = Engagement(
            freelancer_id=shared.id,
            client_organization_id=client.id,
            role="Backend developer",
            starts_on=dt.date(2026, 11, 2),
            days_per_week=Decimal("3"),
            fee=Decimal("450.00"),
            fee_unit="day",
            payment_rule="fixed_term",
            payment_days=30,
            state="active",
        )
        session.add(engagement)
        session.commit()
        ids = (engagement.id, shared.id, client.id)

    try:
        for legal_entities, hidden in (
            ((world.studio,), True),
            ((world.studio, world.foreign), False),
        ):
            session = _scoped(world, *legal_entities)
            try:
                admin = Actor(
                    id=world.user_id, type="user", role="admin", legal_entities=legal_entities
                )
                read = EngagementService(session).get(ids[0], admin)
                assert read.redacted is hidden
                assert (read.role is None, read.fee is None) == (hidden, hidden)
                assert (read.client_organization_id is None) == hidden
                assert read.days_per_week == Decimal("3") and read.state == "active"
            finally:
                session.rollback()
                session.close()
    finally:
        with session_factory(world.engine)() as session:
            session.execute(text("DELETE FROM engagements WHERE id = :i"), {"i": ids[0]})
            session.execute(
                text("DELETE FROM organizations WHERE id IN (:a, :b)"), {"a": ids[1], "b": ids[2]}
            )
            session.commit()
