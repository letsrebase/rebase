"""`space_is_empty` (spec 2026-09-16 §4.1, REB-222): one live row in any of the four
tables is work, a deleted one is not.

Every assertion compares against what the probe answered before the row was added,
rather than against `True`: the session-scoped database is shared by every file on this
worker, and a test here must not depend on another file having cleaned up after itself.
A live row always makes the answer `False`; a deleted one leaves it as it was.

A deal, a document and a time entry all need a customer (a foreign key, and a document's
one-owner check), so each hangs off a customer that is already deleted: that way the only
live row is the one the test is about.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from pigrocrm.core.auth.models import User
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.first_steps import space_is_empty
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.timetracking.models import TimeEntry

_NOW = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)


def _deleted_customer(session: Session) -> Customer:
    customer = Customer(
        legal_name="Cliente cancellato", country="IT", custom_fields={}, deleted_at=_NOW
    )
    session.add(customer)
    session.flush()
    return customer


def _deal(session: Session, customer: Customer, *, deleted: bool = False) -> Deal:
    stage = PipelineStage(name=f"Aperto {uuid4()}", position=0, default_probability=20, type="open")
    session.add(stage)
    session.flush()
    deal = Deal(
        name="Un deal",
        customer_id=customer.id,
        pipeline_stage_id=stage.id,
        probability=20,
        expected_value=Decimal("1000.00"),
        custom_fields={},
        deleted_at=_NOW if deleted else None,
    )
    session.add(deal)
    session.flush()
    return deal


def test_a_deleted_customer_is_not_work(db_session: Session) -> None:
    before = space_is_empty(db_session)
    _deleted_customer(db_session)
    assert space_is_empty(db_session) is before


def test_a_live_customer_is_work(db_session: Session) -> None:
    db_session.add(Customer(legal_name="Acme S.r.l.", country="IT", custom_fields={}))
    db_session.flush()
    assert space_is_empty(db_session) is False


def test_a_live_deal_is_work(db_session: Session) -> None:
    _deal(db_session, _deleted_customer(db_session))
    assert space_is_empty(db_session) is False


def test_a_live_document_is_work(db_session: Session) -> None:
    customer = _deleted_customer(db_session)
    db_session.add(
        Document(customer_id=customer.id, type="document", title="Un PDF", custom_fields={})
    )
    db_session.flush()
    assert space_is_empty(db_session) is False


def test_a_live_time_entry_is_work(db_session: Session) -> None:
    deal = _deal(db_session, _deleted_customer(db_session), deleted=True)
    user = User(
        email=f"ore-{uuid4()}@example.test", password_hash="x", name="Ore", role="collaborator"
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        TimeEntry(
            deal_id=deal.id,
            user_id=user.id,
            date=date(2026, 9, 22),
            hours=Decimal("2.00"),
            description="Lavoro",
            billable=True,
            applied_rate=Decimal("50.000000"),
            rate_source="manual",
            applied_cost=None,
            cost_source="absent",
            invoice_line_id=None,
            custom_fields={},
        )
    )
    db_session.flush()
    assert space_is_empty(db_session) is False
