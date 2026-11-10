"""REB-995 (design 2026-11-09 suppliers § 1, «Duplicates»): folding two organizations that are one
company into one, with a preview that writes nothing."""

import datetime as dt
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fakes.contacts import points
from fakes.customer_fixtures import make_customer
from fakes.gmail_fixtures import connected_account
from fakes.invoice_fixtures import unpaid_invoice
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators.models import Engagement
from pigrocrm.core.contact_points.models import ContactPoint
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.db import Base
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ScopedAdmin,
    ValidationFailed,
)
from pigrocrm.core.gmail.models import EmailDraft, GmailMessage, GmailMessageLink
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.notes.models import Note
from pigrocrm.core.organizations.duplicates import duplicate_vat_groups, ensure_vat_index
from pigrocrm.core.organizations.merge import COVERED_COLUMNS
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.organizations.roles import VAT_UNIQUE_INDEX
from pigrocrm.core.organizations.service import OrganizationService
from pigrocrm.core.people.models import Person
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.tasks.models import Task

ADMIN = Actor(id=None, type="user", role="admin")  # type: ignore[arg-type]
COLLABORATOR = Actor(id=None, type="user", role="collaborator")  # type: ignore[arg-type]
AGENT_ADMIN = Actor(id=uuid4(), type="mcp", role="admin")
AGENT_ADMIN_FULL = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
SCOPED_ADMIN = Actor(id=None, type="user", role="admin", legal_entities=())  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _a_space_that_holds_duplicates(db_session: Session) -> None:
    """The rows to merge share a VAT number, which the unique index refuses: a space that has
    them is one the index was never built on (migration 0078)."""
    db_session.execute(text(f"DROP INDEX {VAT_UNIQUE_INDEX}"))


def _count(session: Session, model: type[Base], column: str, owner: UUID) -> int:
    table = model.__table__  # type: ignore[attr-defined]
    return int(
        session.execute(
            select(func.count()).select_from(table).where(table.c[column] == owner)
        ).scalar_one()
    )


def _linked(session: Session, model: type[Base], owner: UUID) -> int:
    table = model.__table__  # type: ignore[attr-defined]
    return int(
        session.execute(
            select(func.count())
            .select_from(table)
            .where(table.c.entity_type == "organization", table.c.entity_id == owner)
        ).scalar_one()
    )


class Pair:
    """Two organizations of one company, the loser holding one of everything that can point at
    an organization, and some that the survivor has an equal of."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.survivor = make_customer(
            session, legal_name="Acme S.r.l.", vat_number="01234567897", website=None
        )
        self.loser = make_customer(
            session,
            legal_name="ACME srl",
            vat_number="01234567897",
            roles=["supplier"],
            website="https://acme.example",
            tax_code="01234567897",
            supplier_kind="company",
            custom_fields={"sector": "metal", "tier": "gold"},
        )
        self.survivor.custom_fields = {"tier": "silver"}
        loser = self.loser.id
        stage = PipelineStage(
            name=f"Open {uuid4()}", position=0, default_probability=10, type="open"
        )
        session.add(stage)
        session.flush()
        self.deal = Deal(
            name="Site",
            customer_id=loser,
            pipeline_stage_id=stage.id,
            probability=10,
            custom_fields={},
        )
        self.contract = Contract(
            customer_id=loser,
            title="CTO",
            start=dt.date(2026, 1, 1),
            renewal_type="none",
            termination_notice_days=30,
            billing_cadence="monthly",
            expense_policy={"kind": "not_reimbursable"},
        )
        self.person = Person(
            first_name="Giulia", last_name="Rossi", customer_id=loser, custom_fields={}
        )
        self.document = Document(customer_id=loser, type="document", title="NDA", current_version=0)
        self.note = Note(
            entity_type="organization", entity_id=loser, text="Call back", source="web"
        )
        self.activity = Activity(
            entity_type="organization",
            entity_id=loser,
            kind="updated",
            actor_id=None,
            actor_type="system",
            payload={},
            occurred_at=datetime.now(UTC),
        )
        self.draft = EmailDraft(
            entity_type="organization",
            entity_id=loser,
            message_id_header=f"<{uuid4()}@example.test>",
            subject="Hello",
            body_markdown="Hi",
        )
        self.task = Task(title="Call", customer_id=loser)
        for row in (
            self.deal,
            self.contract,
            self.person,
            self.document,
            self.note,
            self.activity,
            self.draft,
            self.task,
        ):
            session.add(row)
        session.flush()
        self.invoice = unpaid_invoice(session, due=dt.date(2026, 3, 1), customer=self.loser)
        self.due = DueItemService(session).create(
            DueItemCreate(
                legal_entity_id=self.deal.legal_entity_id,
                direction="in",
                organization_id=loser,
                amount=Decimal("10.00"),
                source_kind="recurring_expense",
                source_id=uuid4(),
                own_date=dt.date(2026, 2, 1),
            )
        )
        self.engagement = Engagement(
            freelancer_id=loser,
            client_organization_id=loser,
            role="Dev",
            starts_on=dt.date(2026, 11, 2),
            fee=Decimal("400"),
            fee_unit="day",
            payment_rule="fixed_term",
            payment_days=30,
        )
        session.add(self.engagement)
        account = connected_account(session)
        self.message = GmailMessage(
            google_account_id=account.id,
            gmail_message_id=f"g{uuid4().hex[:8]}",
            gmail_thread_id="t1",
            message_id_header=f"<{uuid4()}@x>",
            direction="inbound",
            from_address="a@acme.example",
            to_addresses=["me@example.test"],
            cc_addresses=[],
            subject="s",
            snippet="",
            internal_date=datetime(2026, 3, 1, tzinfo=UTC),
            body_text="b",
            attachments=[],
        )
        session.add(self.message)
        session.flush()
        session.add(
            GmailMessageLink(
                gmail_message_id=self.message.id, entity_type="organization", entity_id=loser
            )
        )
        session.flush()


def _merge(session: Session, pair: Pair, **kwargs: object):  # type: ignore[no-untyped-def]
    return OrganizationService(session).merge(
        pair.survivor.id,
        pair.loser.id,
        ADMIN,
        **kwargs,  # type: ignore[arg-type]
    )


def test_the_preview_says_what_moves_and_writes_nothing(db_session: Session) -> None:
    pair = Pair(db_session)
    preview = _merge(db_session, pair)

    assert preview.dry_run is True
    assert preview.survivor is None
    assert preview.moved == {
        "deals": 1,
        "contracts": 1,
        "invoices": 1,
        "people": 1,
        "documents": 1,
        "due_items": 2,  # the one made, and the issued invoice's own
        "engagements_freelancer": 1,
        "engagements_client": 1,
        "tasks": 1,
        "notes": 1,
        "activities": 1,
        "email_drafts": 1,
        "gmail_message_links": 1,
    }
    assert preview.total_moved == 14
    assert preview.roles == ["customer", "supplier"]
    assert preview.filled_fields == ["tax_code", "website", "supplier_kind"]
    assert preview.custom_fields_added == ["sector"]
    for model, column in ((Deal, "customer_id"), (Invoice, "customer_id")):
        assert _count(db_session, model, column, pair.loser.id) == 1
        assert _count(db_session, model, column, pair.survivor.id) == 0
    db_session.refresh(pair.loser)
    assert pair.loser.deleted_at is None
    assert len(duplicate_vat_groups(db_session)) == 1


def test_the_merge_moves_every_reference_and_archives_the_loser(db_session: Session) -> None:
    pair = Pair(db_session)
    preview = _merge(db_session, pair)
    done = _merge(db_session, pair, dry_run=False)

    assert done.dry_run is False
    assert done.moved == preview.moved, "the preview and the merge walk the same steps"
    for model, column in (
        (Deal, "customer_id"),
        (Contract, "customer_id"),
        (Invoice, "customer_id"),
        (Person, "customer_id"),
        (Document, "customer_id"),
        (DueItem, "organization_id"),
        (Engagement, "freelancer_id"),
        (Engagement, "client_organization_id"),
        (Task, "customer_id"),
    ):
        assert _count(db_session, model, column, pair.loser.id) == 0, model
        assert _count(db_session, model, column, pair.survivor.id) == (
            2 if model is DueItem else 1
        ), model
    for model in (Note, EmailDraft, GmailMessageLink):
        assert _linked(db_session, model, pair.loser.id) == 0, model
        assert _linked(db_session, model, pair.survivor.id) == 1, model

    survivor = db_session.get_one(Organization, pair.survivor.id)
    loser = db_session.get_one(Organization, pair.loser.id)
    assert loser.deleted_at is not None
    assert survivor.deleted_at is None
    assert survivor.roles == ["customer", "supplier"]
    assert survivor.website == "https://acme.example"
    assert survivor.supplier_kind == "company"
    assert survivor.custom_fields == {"tier": "silver", "sector": "metal"}
    assert done.survivor is not None and done.survivor.id == survivor.id
    assert duplicate_vat_groups(db_session) == []
    assert ensure_vat_index(db_session) == "created", "the last duplicate is gone"

    kinds = {
        (row.entity_id, row.kind)
        for row in db_session.scalars(
            select(Activity).where(Activity.kind.in_(["merged", "merged_into"]))
        )
    }
    assert (survivor.id, "merged") in kinds
    assert (loser.id, "merged_into") in kinds
    trace = db_session.scalars(
        select(Activity).where(Activity.entity_id == survivor.id, Activity.kind == "merged")
    ).one()
    assert trace.payload["organization_id"] == str(loser.id)
    assert trace.payload["moved"]["deals"] == 1
    db_session.refresh(pair.activity)
    moved_activity = pair.activity
    assert moved_activity.entity_id == survivor.id

    with pytest.raises(Conflict):
        _merge(db_session, pair, dry_run=False)


def test_what_the_survivor_already_has_is_not_carried_twice(db_session: Session) -> None:
    survivor = make_customer(db_session, vat_number="01234567897", email="info@acme.example")
    loser = make_customer(db_session, vat_number="01234567897")
    loser.contact_points = points(email="INFO@acme.example", phone="+39 06 1234567")
    db_session.flush()
    loser_points = {p.kind: p for p in loser.contact_points}
    assert loser_points["email"].normalized == survivor.contact_points[0].normalized
    account = connected_account(db_session)
    messages = []
    for index in range(2):
        message = GmailMessage(
            google_account_id=account.id,
            gmail_message_id=f"g{index}{uuid4().hex[:6]}",
            gmail_thread_id="t",
            message_id_header=f"<{uuid4()}@x>",
            direction="inbound",
            from_address="a@acme.example",
            to_addresses=["me@example.test"],
            cc_addresses=[],
            subject="s",
            snippet="",
            internal_date=datetime(2026, 3, 1, tzinfo=UTC),
            body_text="b",
            attachments=[],
        )
        db_session.add(message)
        messages.append(message)
    db_session.flush()
    for message, owner in ((messages[0], survivor), (messages[0], loser), (messages[1], loser)):
        db_session.add(
            GmailMessageLink(
                gmail_message_id=message.id, entity_type="organization", entity_id=owner.id
            )
        )
    for owner in (survivor, loser):
        db_session.add(
            Task(title="Chase", customer_id=owner.id, rule="quiet_customer", origin="automation")
        )
    db_session.add(
        Task(title="Other", customer_id=loser.id, rule="another_rule", origin="automation")
    )
    db_session.flush()

    done = OrganizationService(db_session).merge(survivor.id, loser.id, ADMIN, dry_run=False)

    assert done.dropped == {"contact_points": 1, "tasks": 1, "gmail_message_links": 1}
    assert done.moved["contact_points"] == 1
    assert done.moved["gmail_message_links"] == 1
    assert done.moved["tasks"] == 2, "the archived duplicate moves too: nothing keeps pointing back"
    db_session.expire_all()
    survivor_points = db_session.scalars(
        select(ContactPoint).where(ContactPoint.customer_id == survivor.id)
    ).all()
    assert sorted((p.kind, p.is_primary) for p in survivor_points) == [
        ("email", True),
        ("phone", True),
    ]
    open_chase = db_session.scalars(
        select(Task).where(
            Task.customer_id == survivor.id,
            Task.rule == "quiet_customer",
            Task.deleted_at.is_(None),
        )
    ).all()
    assert len(open_chase) == 1


def test_a_loser_primary_never_takes_a_primary_the_survivor_has(db_session: Session) -> None:
    survivor = make_customer(db_session, vat_number="01234567897", email="a@acme.example")
    loser = make_customer(db_session, vat_number="01234567897", email="b@acme.example")
    OrganizationService(db_session).merge(survivor.id, loser.id, ADMIN, dry_run=False)
    db_session.expire_all()
    rows = db_session.scalars(
        select(ContactPoint)
        .where(ContactPoint.customer_id == survivor.id)
        .order_by(ContactPoint.position)
    ).all()
    assert [(p.value, p.is_primary) for p in rows] == [
        ("a@acme.example", True),
        ("b@acme.example", False),
    ]
    assert survivor.email == "a@acme.example"


def test_two_legal_entities_make_the_survivor_shared(db_session: Session) -> None:
    other = LegalEntity(name=f"Other {uuid4()}", legal_name="Other srl", country="IT")
    db_session.add(other)
    db_session.flush()
    survivor = make_customer(db_session, vat_number="01234567897")
    loser = make_customer(db_session, vat_number="01234567897", legal_entity_id=other.id)
    assert survivor.legal_entity_id != other.id

    preview = OrganizationService(db_session).merge(survivor.id, loser.id, ADMIN)
    assert preview.shares_survivor is True
    assert db_session.get_one(Organization, survivor.id).legal_entity_id is not None

    OrganizationService(db_session).merge(survivor.id, loser.id, ADMIN, dry_run=False)
    assert db_session.get_one(Organization, survivor.id).legal_entity_id is None


def test_the_hub_freelancer_id_passes_to_the_survivor_unless_both_have_one(
    db_session: Session,
) -> None:
    hub_id = uuid4()
    survivor = make_customer(db_session, vat_number="01234567897")
    loser = make_customer(db_session, roles=["freelancer"], hub_freelancer_id=hub_id)
    done = OrganizationService(db_session).merge(survivor.id, loser.id, ADMIN, dry_run=False)
    assert done.moves_hub_freelancer_id is True
    assert db_session.get_one(Organization, survivor.id).hub_freelancer_id == hub_id
    assert db_session.get_one(Organization, survivor.id).roles == ["customer", "freelancer"]
    assert db_session.get_one(Organization, loser.id).hub_freelancer_id is None

    first = make_customer(db_session, roles=["freelancer"], hub_freelancer_id=uuid4())
    second = make_customer(db_session, roles=["freelancer"], hub_freelancer_id=uuid4())
    with pytest.raises(Conflict):
        OrganizationService(db_session).merge(first.id, second.id, ADMIN, dry_run=False)


def test_pending_contract_proposals_follow_and_decided_ones_do_not(db_session: Session) -> None:
    pair = Pair(db_session)
    pending = Proposal(
        document_id=pair.document.id,
        contract_id=None,
        target_type="contract",
        proposed_fields={"contract": {"customer_id": str(pair.loser.id), "title": "T"}},
        excerpt="x",
        confidence=Decimal("0.50"),
    )
    decided = Proposal(
        document_id=pair.document.id,
        contract_id=None,
        target_type="contract",
        proposed_fields={"contract": {"customer_id": str(pair.loser.id), "title": "T"}},
        excerpt="x",
        confidence=Decimal("0.50"),
        status="rejected",
    )
    db_session.add_all([pending, decided])
    db_session.flush()

    assert _merge(db_session, pair).moved["proposals"] == 1
    _merge(db_session, pair, dry_run=False)
    db_session.refresh(pending)
    db_session.refresh(decided)
    assert pending.proposed_fields["contract"]["customer_id"] == str(pair.survivor.id)
    assert decided.proposed_fields["contract"]["customer_id"] == str(pair.loser.id)


def test_the_refusals(db_session: Session) -> None:
    one = make_customer(db_session, vat_number="01234567897")
    two = make_customer(db_session, vat_number="01234567897")
    service = OrganizationService(db_session)

    with pytest.raises(ValidationFailed) as same:
        service.merge(one.id, one.id, ADMIN)
    assert same.value.details["field"] == "loser_id"
    with pytest.raises(NotFound):
        service.merge(one.id, uuid4(), ADMIN)
    with pytest.raises(NotFound):
        service.merge(uuid4(), one.id, ADMIN)

    for actor in (COLLABORATOR, AGENT_ADMIN):
        with pytest.raises((PermissionDenied, AgentForbidden)):
            service.merge(one.id, two.id, actor, dry_run=False)
    with pytest.raises(ScopedAdmin):
        service.merge(one.id, two.id, SCOPED_ADMIN)
    assert db_session.get_one(Organization, two.id).deleted_at is None

    two.deleted_at = datetime.now(UTC)
    db_session.flush()
    with pytest.raises(Conflict):
        service.merge(one.id, two.id, ADMIN)
    with pytest.raises(Conflict):
        service.merge(two.id, one.id, ADMIN)


def test_an_agent_with_full_access_may_merge(db_session: Session) -> None:
    one = make_customer(db_session, vat_number="01234567897")
    two = make_customer(db_session, vat_number="01234567897")
    done = OrganizationService(db_session).merge(one.id, two.id, AGENT_ADMIN_FULL, dry_run=False)
    assert done.dry_run is False


def test_every_foreign_key_to_organizations_is_covered_by_the_merge() -> None:
    """A table that gains a foreign key to `organizations` (a purchase's supplier) has to join
    the merge the day it appears, or merging would leave rows pointing at an archived row."""
    found: set[str] = set()
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if any(fk.column.table.name == "organizations" for fk in column.foreign_keys):
                found.add(f"{table.name}.{column.name}")
    assert found == COVERED_COLUMNS


def test_a_field_the_survivor_has_is_never_overwritten_or_emptied(db_session: Session) -> None:
    survivor = make_customer(
        db_session,
        city="Milano",
        postal_code="20100",
        supplier_area="eu",
        payment_days=60,
    )
    loser = make_customer(
        db_session,
        vat_number="01234567897",
        address="Via X 1",
        supplier_kind="foreign",
        supplier_supplies="services",
        payment_days=30,
        payment_end_of_month=True,
        supplier_payment_days=45,
        supplier_payment_end_of_month=True,
        customer_kind="public_body",
        sdi_code="ABC123",
    )

    done = OrganizationService(db_session).merge(survivor.id, loser.id, ADMIN, dry_run=False)

    row = db_session.get_one(Organization, survivor.id)
    assert (row.city, row.postal_code, row.address) == ("Milano", "20100", "Via X 1")
    assert (row.supplier_kind, row.supplier_area, row.supplier_supplies) == (
        "foreign",
        "eu",
        "services",
    )
    assert (row.payment_days, row.payment_end_of_month) == (60, False), "its own terms stay"
    assert (row.supplier_payment_days, row.supplier_payment_end_of_month) == (45, True)
    assert row.vat_number == "01234567897", "the loser's number is not lost with the loser"
    assert (row.customer_kind, row.sdi_code) == ("public_body", "ABC123")
    assert "payment_days" not in done.filled_fields
