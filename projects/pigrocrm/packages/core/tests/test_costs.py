"""A cost is money that actually left, towards somebody else, with a receipt to prove
it -- as distinct from an hour's cost, which is an internal, notional figure derived
from a rate you chose yourself (§4.2)."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.timetracking.categories import CostCategoryService
from pigrocrm.core.timetracking.costs import CostService
from pigrocrm.core.timetracking.locks import PeriodLockService
from pigrocrm.core.timetracking.schemas import (
    CostCreate,
    CostListQuery,
    CostUpdate,
    PeriodLockCreate,
)

WRITER = Actor(id=None, type="user", role="collaboratore")
READER = Actor(id=None, type="user", role="readonly")


def _create(service, category_id, **overrides):
    payload = {
        "category_id": category_id,
        "data": date(2026, 3, 5),
        "importo": Decimal("120.00"),
        "descrizione": "Licenza annuale",
    }
    payload.update(overrides)
    return service.create(CostCreate(**payload), WRITER)


def test_a_cost_with_no_deal_is_a_general_expense(
    db_session: Session, seeded_category_id: UUID, seeded_deal_id: UUID
) -> None:
    """§7.4. It enters the period P&L in a row of its own and is never apportioned onto
    any deal: any apportionment key would make a deal's margin move when a *different*
    deal was invoiced."""
    service = CostService(db_session)
    general = _create(service, seeded_category_id, deal_id=None)
    attributed = _create(service, seeded_category_id, deal_id=seeded_deal_id)
    assert general.deal_id is None

    only_general = service.list(CostListQuery(solo_generali=True), READER)
    assert [c.id for c in only_general.items] == [general.id]
    per_deal = service.list(CostListQuery(deal_id=seeded_deal_id), READER)
    assert [c.id for c in per_deal.items] == [attributed.id]


def test_a_negative_amount_is_a_refund_and_zero_is_refused(
    db_session: Session, seeded_category_id: UUID
) -> None:
    """§4.4, the same choice slice 3 §6.1 rule 6 makes for a discount: represent the
    correction with the instrument that already exists, instead of a `tipo` column that
    multiplies the cases in every sum."""
    service = CostService(db_session)
    assert _create(service, seeded_category_id, importo=Decimal("-45.50")).importo == Decimal(
        "-45.50"
    )
    with pytest.raises(ValidationFailed) as excinfo:
        _create(service, seeded_category_id, importo=Decimal("0.00"))
    assert excinfo.value.details["field"] == "importo"


def test_an_archived_category_cannot_be_chosen_for_a_new_cost(
    db_session: Session, seeded_category_id: UUID
) -> None:
    admin = Actor(id=None, type="system", role="admin")
    CostCategoryService(db_session).archive_cost_category(seeded_category_id, admin)
    with pytest.raises(ValidationFailed) as excinfo:
        _create(CostService(db_session), seeded_category_id)
    assert excinfo.value.details["field"] == "category_id"


def test_a_write_into_a_closed_period_is_refused_both_ways(
    db_session: Session, seeded_category_id: UUID, seeded_user_id: UUID
) -> None:
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    service = CostService(db_session)
    cost = _create(service, seeded_category_id, data=date(2026, 4, 5))
    PeriodLockService(db_session).close_period(PeriodLockCreate(anno=2026, mese=3), admin)

    with pytest.raises(Conflict):
        _create(service, seeded_category_id, data=date(2026, 3, 5))
    with pytest.raises(Conflict):
        service.update(cost.id, CostUpdate(data=date(2026, 3, 5)), WRITER)

    PeriodLockService(db_session).close_period(PeriodLockCreate(anno=2026, mese=4), admin)
    with pytest.raises(Conflict):
        service.soft_delete(cost.id, WRITER)


def test_the_receipt_is_a_document_reference_not_bytes(
    db_session: Session, seeded_category_id: UUID
) -> None:
    """The previous system kept the attachment as base64 inside the costs JSON
    (`parseBase64Payload`). The document store already exists, with pluggable storage,
    versioning and a hash, and is not reinvented (§10.3). An id that resolves to
    nothing is `NotFound`, not a raw `ForeignKeyViolation`."""
    with pytest.raises(NotFound):
        _create(CostService(db_session), seeded_category_id, document_id=uuid4())


def test_soft_delete_is_reversible_and_a_reader_cannot_write(
    db_session: Session, seeded_category_id: UUID
) -> None:
    service = CostService(db_session)
    cost = _create(service, seeded_category_id)
    service.soft_delete(cost.id, WRITER)
    assert service.list(CostListQuery(), READER).items == []
    # `CostRead` carries no `deleted_at` -- no other entity's Read schema does, and
    # exposing it would invite a client to branch on soft-delete state when list
    # queries already filter it out. Reversibility is proven by visibility instead.
    restored = service.restore(cost.id, WRITER)
    assert restored.id == cost.id
    assert [c.id for c in service.list(CostListQuery(), READER).items] == [cost.id]
    with pytest.raises(PermissionDenied):
        service.soft_delete(cost.id, READER)


def test_the_list_is_ordered_by_date_descending(
    db_session: Session, seeded_category_id: UUID
) -> None:
    service = CostService(db_session)
    for day in (5, 20, 12):
        _create(service, seeded_category_id, data=date(2026, 3, day))
    assert [c.data.day for c in service.list(CostListQuery(), READER).items] == [20, 12, 5]


def test_a_cursor_that_names_no_cost_is_refused_not_read_as_the_first_page(
    db_session: Session,
) -> None:
    with pytest.raises(ValidationFailed) as refused:
        CostService(db_session).list(CostListQuery(cursor=uuid4()), READER)

    assert refused.value.details["field"] == "cursor"
