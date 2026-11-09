"""Two payments racing for the same remainder never both land (REB-967).

`DueItemService.settle` takes the item under `FOR UPDATE`, so the second payment reads what
the first left and the over-settlement check holds. Committed rows and one connection per
thread: a savepoint test cannot express two transactions.
"""

import datetime as dt
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID, uuid4

from fakes.customer_fixtures import make_customer
from fakes.legal_entity_fixtures import committed_default_legal_entity, remove_legal_entity
from sqlalchemy import Engine, delete, func, select

from pigrocrm.core.actor import Actor
from pigrocrm.core.db import session_factory
from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.due_items.schemas import DueItemCreate, SettlementCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import Conflict
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.organizations.models import Organization

TAG = "DUERACE"
ADMIN = Actor(id=uuid4(), type="user", role="admin")


def test_two_payments_for_the_same_remainder_land_once(db_engine: Engine) -> None:
    factory = session_factory(db_engine)
    with factory() as session:
        inserted = committed_default_legal_entity(session)
        entity = LegalEntityRepository(session).default()
        assert entity is not None
        customer = make_customer(session, legal_name=f"{TAG} cliente", legal_entity_id=entity.id)
        item_id: UUID = (
            DueItemService(session)
            .create(
                DueItemCreate(
                    legal_entity_id=entity.id,
                    direction="in",
                    organization_id=customer.id,
                    amount=Decimal("100.00"),
                    source_kind="sales_invoice",
                    own_date=dt.date(2026, 2, 28),
                )
            )
            .id
        )
        session.commit()
    try:
        barrier = threading.Barrier(2)

        def pay() -> str:
            with factory() as own:
                barrier.wait()
                try:
                    DueItemService(own).settle(
                        item_id,
                        SettlementCreate(amount=Decimal("60.00"), settled_on=dt.date(2026, 3, 1)),
                        ADMIN,
                    )
                except Conflict:
                    own.rollback()
                    return "refused"
                return "paid"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = sorted(pool.map(lambda _: pay(), range(2)))

        assert outcomes == ["paid", "refused"]
        with factory() as check:
            settled = check.scalar(
                select(func.sum(DueItemSettlement.amount)).where(
                    DueItemSettlement.due_item_id == item_id
                )
            )
            assert settled == Decimal("60.00")
    finally:
        with factory() as cleanup:
            cleanup.execute(
                delete(DueItemSettlement).where(DueItemSettlement.due_item_id == item_id)
            )
            cleanup.execute(delete(DueItem).where(DueItem.id == item_id))
            cleanup.execute(delete(Organization).where(Organization.legal_name.like(f"{TAG} %")))
            remove_legal_entity(cleanup, inserted)
            cleanup.commit()
