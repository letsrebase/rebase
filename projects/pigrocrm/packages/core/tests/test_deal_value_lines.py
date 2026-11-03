"""A deal's value as lines: what `DealService` validates, stores and derives from them."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.models import DealValueLine
from pigrocrm.core.deals.schemas import (
    DealCreate,
    DealListQuery,
    DealUpdate,
    DealValueLineIn,
    ValuePreviewIn,
)
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.pipeline.service import PipelineService

ADMIN = Actor(id=None, type="system", role="admin")


def line(
    unit: str = "flat",
    period: str = "one_off",
    price: str = "100",
    quantity: str = "1",
    start: date | None = None,
    months: int | None = None,
    description: str | None = None,
) -> DealValueLineIn:
    return DealValueLineIn.model_validate(
        {
            "unit": unit,
            "delivery_period": period,
            "unit_price": price,
            "quantity": quantity,
            "start": start,
            "duration_months": months,
            "description": description,
        }
    )


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    PipelineService(db_session).seed_defaults(ADMIN)
    return CustomerService(db_session).create(CustomerCreate(legal_name="ACME"), ADMIN).id


def create(db_session: Session, customer_id: UUID, *lines: DealValueLineIn, **fields):  # type: ignore[no-untyped-def]
    return DealService(db_session).create(
        DealCreate(name="Deal", customer_id=customer_id, value_lines=list(lines), **fields),
        ADMIN,
    )


def stored(db_session: Session, deal_id: UUID) -> list[DealValueLine]:
    return list(
        db_session.scalars(
            select(DealValueLine)
            .where(DealValueLine.deal_id == deal_id)
            .order_by(DealValueLine.position)
        )
    )


def test_an_upfront_and_an_open_ended_fee_give_the_total_and_the_two_figures(
    db_session: Session, customer_id: UUID
) -> None:
    deal = create(
        db_session,
        customer_id,
        line(price="20000"),
        line(period="monthly", price="500"),
    )
    assert deal.expected_value == Decimal("26000.00")
    assert deal.one_off_value == Decimal("20000.00")
    assert deal.monthly_recurring_value == Decimal("500.00")
    assert [(item.position, item.delivery_period) for item in deal.value_lines] == [
        (0, "one_off"),
        (1, "monthly"),
    ]


def test_a_deal_with_no_lines_has_no_value_and_no_figures(
    db_session: Session, customer_id: UUID
) -> None:
    deal = create(db_session, customer_id)
    assert (deal.expected_value, deal.one_off_value, deal.monthly_recurring_value) == (
        None,
        None,
        None,
    )
    assert deal.value_lines == []


def test_a_list_carries_the_figures_of_every_deal_without_the_lines(
    db_session: Session, customer_id: UUID
) -> None:
    create(db_session, customer_id, line(price="1000"))
    create(db_session, customer_id, line(period="monthly", price="50", months=12))
    create(db_session, customer_id)
    page = DealService(db_session).list(DealListQuery(), ADMIN)
    figures = sorted(
        (str(d.expected_value), str(d.one_off_value), str(d.monthly_recurring_value))
        for d in page.items
    )
    assert figures == [
        ("1000.00", "1000.00", "0.00"),
        ("600.00", "0.00", "50.00"),
        ("None", "None", "None"),
    ]
    assert not hasattr(page.items[0], "value_lines")


def test_updating_the_lines_replaces_the_whole_list_and_the_total(
    db_session: Session, customer_id: UUID
) -> None:
    deal = create(db_session, customer_id, line(price="1000"), line(period="monthly", price="10"))
    service = DealService(db_session)
    updated = service.update(deal.id, DealUpdate(value_lines=[line(price="300")]), ADMIN)
    assert updated.expected_value == Decimal("300.00")
    assert [(item.position, item.unit_price) for item in updated.value_lines] == [
        (0, Decimal("300.00"))
    ]
    assert len(stored(db_session, deal.id)) == 1


def test_clearing_the_lines_clears_the_value(db_session: Session, customer_id: UUID) -> None:
    deal = create(db_session, customer_id, line(price="1000"))
    cleared = DealService(db_session).update(deal.id, DealUpdate(value_lines=[]), ADMIN)
    assert cleared.expected_value is None
    assert cleared.one_off_value is None
    assert stored(db_session, deal.id) == []


def test_an_update_that_does_not_name_the_lines_leaves_them_and_the_total(
    db_session: Session, customer_id: UUID
) -> None:
    deal = create(db_session, customer_id, line(price="1000"))
    renamed = DealService(db_session).update(deal.id, DealUpdate(name="Altro"), ADMIN)
    assert renamed.expected_value == Decimal("1000.00")
    assert [item.id for item in renamed.value_lines] == [item.id for item in deal.value_lines]


def test_the_timeline_says_value_lines_changed_only_when_they_did(
    db_session: Session, customer_id: UUID
) -> None:
    deal = create(db_session, customer_id, line(price="1000"))
    service = DealService(db_session)
    service.update(deal.id, DealUpdate(value_lines=[line(price="1000")]), ADMIN)
    service.update(deal.id, DealUpdate(value_lines=[line(price="1500")]), ADMIN)
    payloads = [
        row.payload
        for row in db_session.scalars(
            select(Activity)
            .where(Activity.entity_id == deal.id, Activity.kind == "updated")
            .order_by(Activity.occurred_at)
        )
    ]
    assert payloads == [{"changed": []}, {"changed": ["value_lines"]}]


@pytest.mark.parametrize(
    "bad,fragment",
    [
        (line(unit="flat", price="10", quantity="2"), "quantità 1"),
        (line(unit="flat", period="one_off", months=3), "non ha durata"),
        (line(unit="day", period="monthly", price="500", quantity="4"), "data di inizio"),
    ],
)
def test_a_line_that_cannot_be_priced_is_refused_naming_it(
    db_session: Session, customer_id: UUID, bad: DealValueLineIn, fragment: str
) -> None:
    with pytest.raises(ValidationFailed) as refusal:
        create(db_session, customer_id, line(price="1"), bad)
    assert refusal.value.details["field"] == "value_lines"
    assert "riga 2" in str(refusal.value)
    assert fragment in str(refusal.value)


def test_a_refused_create_leaves_no_deal_behind(db_session: Session, customer_id: UUID) -> None:
    with pytest.raises(ValidationFailed):
        create(db_session, customer_id, line(unit="day", period="monthly", quantity="2"))
    assert DealService(db_session).list(DealListQuery(), ADMIN).items == []


def test_a_day_rate_takes_the_close_date_of_the_deal_as_its_start(
    db_session: Session, customer_id: UUID
) -> None:
    deal = create(
        db_session,
        customer_id,
        line(unit="day", period="monthly", price="450", quantity="18", months=12),
        expected_close_date=date(2026, 11, 1),
    )
    assert deal.expected_value == Decimal("97200.00")
    assert deal.value_lines[0].start is None


def test_two_day_rates_in_force_together_are_refused_and_adjacent_ones_are_not(
    db_session: Session, customer_id: UUID
) -> None:
    first = line(unit="day", period="monthly", price="500", quantity="4", start=date(2026, 1, 1))
    first = first.model_copy(update={"duration_months": 6})
    overlapping = line(
        unit="hour", period="monthly", price="80", quantity="10", start=date(2026, 6, 30)
    )
    with pytest.raises(ValidationFailed) as refusal:
        create(db_session, customer_id, first, overlapping)
    assert "riga 2 si sovrappone a riga 1" in str(refusal.value)

    after = overlapping.model_copy(update={"start": date(2026, 7, 1)})
    deal = create(db_session, customer_id, first, after)
    assert len(deal.value_lines) == 2


def test_an_open_day_rate_overlaps_any_later_one(db_session: Session, customer_id: UUID) -> None:
    open_rate = line(unit="day", period="monthly", quantity="4", start=date(2026, 1, 1))
    later = line(unit="day", period="one_off", quantity="2", start=date(2030, 1, 1))
    with pytest.raises(ValidationFailed):
        create(db_session, customer_id, open_rate, later)


def test_fees_overlap_freely_next_to_a_day_rate(db_session: Session, customer_id: UUID) -> None:
    deal = create(
        db_session,
        customer_id,
        line(unit="day", period="monthly", quantity="4", start=date(2026, 1, 1), months=12),
        line(period="monthly", price="500", start=date(2026, 1, 1)),
        line(period="monthly", price="70", start=date(2026, 3, 1)),
    )
    assert len(deal.value_lines) == 3


def test_moving_the_close_date_rechecks_lines_that_follow_it(
    db_session: Session, customer_id: UUID
) -> None:
    deal = create(
        db_session,
        customer_id,
        line(unit="day", period="monthly", quantity="4", months=3),
        line(unit="day", period="monthly", quantity="2", months=3, start=date(2026, 6, 1)),
        expected_close_date=date(2026, 1, 1),
    )
    service = DealService(db_session)
    with pytest.raises(ValidationFailed):
        service.update(deal.id, DealUpdate(expected_close_date=date(2026, 5, 1)), ADMIN)
    # Nothing was written by the refused call.
    assert service.get(deal.id, ADMIN).expected_close_date == date(2026, 1, 1)
    moved = service.update(deal.id, DealUpdate(expected_close_date=date(2026, 3, 1)), ADMIN)
    assert moved.expected_close_date == date(2026, 3, 1)


def test_clearing_the_close_date_is_refused_while_a_day_rate_follows_it(
    db_session: Session, customer_id: UUID
) -> None:
    deal = create(
        db_session,
        customer_id,
        line(unit="day", period="monthly", quantity="4", months=3),
        expected_close_date=date(2026, 1, 1),
    )
    with pytest.raises(ValidationFailed) as refusal:
        DealService(db_session).update(deal.id, DealUpdate(expected_close_date=None), ADMIN)
    assert "data di inizio" in str(refusal.value)


def test_a_line_or_a_total_beyond_the_column_is_refused_by_name_not_by_the_database(
    db_session: Session, customer_id: UUID
) -> None:
    with pytest.raises(ValidationFailed) as refusal:
        create(
            db_session,
            customer_id,
            line(price="1"),
            line(period="monthly", price="99999999.99", months=600),
        )
    assert "riga 2" in str(refusal.value)

    # Each line fits; together they do not.
    with pytest.raises(ValidationFailed) as total:
        create(db_session, customer_id, line(price="9999999999.99"), line(price="1"))
    assert "totale del deal" in str(total.value)
    # The session is still usable: nothing reached the database to poison it.
    assert create(db_session, customer_id, line(price="1")).expected_value == Decimal("1.00")


PAST_THE_CALENDAR = [
    # Past the calendar at the end of its duration, and at the end of the open-ended twelve
    # months, which is reached in `term_total` and not in `line_term`.
    line(period="monthly", start=date(9999, 6, 1), months=12),
    line(period="monthly", start=date(9999, 6, 1)),
    line(period="monthly", start=date(9999, 12, 1), months=2),
    line(period="annual", start=date(9999, 12, 31)),
]


@pytest.mark.parametrize("bad", PAST_THE_CALENDAR)
def test_a_date_past_the_calendar_is_refused_by_create_update_and_preview(
    db_session: Session, customer_id: UUID, bad: DealValueLineIn
) -> None:
    service = DealService(db_session)
    with pytest.raises(ValidationFailed) as on_create:
        create(db_session, customer_id, line(price="1"), bad)
    assert "riga 2" in str(on_create.value) and "fuori dal calendario" in str(on_create.value)

    deal = create(db_session, customer_id, line(price="1"))
    with pytest.raises(ValidationFailed):
        service.update(deal.id, DealUpdate(value_lines=[bad]), ADMIN)
    with pytest.raises(ValidationFailed):
        service.preview_value(ValuePreviewIn(value_lines=[bad]))
    assert service.get(deal.id, ADMIN).expected_value == Decimal("1.00")


def test_replacing_the_lines_locks_the_deal_row_first(
    db_session: Session, customer_id: UUID
) -> None:
    """Two writers replacing one deal's lines would otherwise both insert position 0."""
    deal = create(db_session, customer_id, line(price="1"))
    statements: list[str] = []
    connection = db_session.connection()
    event.listen(connection, "before_cursor_execute", lambda *args: statements.append(args[2]))
    DealService(db_session).update(deal.id, DealUpdate(value_lines=[line(price="2")]), ADMIN)
    lock = next(i for i, sql in enumerate(statements) if "FOR UPDATE" in sql)
    delete = next(i for i, sql in enumerate(statements) if sql.startswith("DELETE FROM deal_value"))
    assert lock < delete


def test_the_database_holds_the_shape_the_schema_promises(
    db_session: Session, customer_id: UUID
) -> None:
    deal = create(db_session, customer_id, line(price="10"))
    rows = stored(db_session, deal.id)
    assert (rows[0].unit, rows[0].delivery_period, rows[0].quantity) == ("flat", "one_off", 1)


def test_the_preview_answers_the_figures_and_writes_nothing(
    db_session: Session, customer_id: UUID
) -> None:
    service = DealService(db_session)
    preview = service.preview_value(
        ValuePreviewIn(
            value_lines=[line(price="20000"), line(period="monthly", price="500")],
            expected_close_date=None,
        )
    )
    assert (preview.expected_value, preview.one_off_value, preview.monthly_recurring_value) == (
        Decimal("26000.00"),
        Decimal("20000.00"),
        Decimal("500.00"),
    )
    assert db_session.scalars(select(DealValueLine)).all() == []


def test_the_preview_refuses_what_a_write_would(db_session: Session) -> None:
    with pytest.raises(ValidationFailed):
        DealService(db_session).preview_value(
            ValuePreviewIn(value_lines=[line(unit="day", period="monthly")])
        )
    empty = DealService(db_session).preview_value(ValuePreviewIn(value_lines=[]))
    assert empty.expected_value is None
