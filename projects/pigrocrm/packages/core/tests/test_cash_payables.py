"""The payables mirror (REB-1025, spec 2026-11-09, due items and the cash schedule, § 4): what we
owe in the receivables' six bands, and what is late.

The mirror is pinned to the receivables it copies: the same codes, labels and bounds, and the
same figures for the same amounts at the same dates.
"""

import datetime as dt
import itertools
from decimal import Decimal

import pytest
from fakes.cash_fixtures import ADMIN, AGENT, COLLABORATOR, CashWorld
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.dashboard.service import _BAND_LABELS
from pigrocrm.core.due_items.payables import OVERDUE_SHOWN
from pigrocrm.core.due_items.schedule import CashScheduleService
from pigrocrm.core.due_items.schedule_schemas import PayablesDashboard
from pigrocrm.core.due_items.schemas import SettlementCreate
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.receivables import BANDS_RECEIVABLES
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.modules.registry import Profile

_NUMBERS = itertools.count(1)

# One row per band, amounts chosen so no two sums coincide: 100 + 200 + ... + 600 = 2100.
OFFSETS: list[tuple[str, int | None]] = [
    ("100.00", -40),  # expired
    ("200.00", 0),  # due today: «entro 30», not expired
    ("300.00", 45),
    ("400.00", 90),
    ("500.00", 91),
    ("600.00", None),
]


@pytest.fixture
def world(db_session: Session) -> CashWorld:
    return CashWorld(db_session)


def payables(
    world: CashWorld,
    actor: Actor = ADMIN,
    *,
    profile: Profile | None = "company",
    entity: object = None,
) -> PayablesDashboard:
    return CashScheduleService(world.session).get_payables(
        entity,  # type: ignore[arg-type]
        actor,
        profile=profile,
    )


def _owe(world: CashWorld) -> None:
    for amount, offset in OFFSETS:
        due = None if offset is None else world.today + dt.timedelta(days=offset)
        world.item(amount, due, direction="out")


def test_the_six_bands_add_up_to_the_total_to_the_cent(world: CashWorld) -> None:
    _owe(world)

    page = payables(world)

    assert [band.code for band in page.bands] == list(BANDS_RECEIVABLES)
    assert {band.code: str(band.amount) for band in page.bands} == {
        "expired": "100.00",
        "entro_30": "200.00",
        "da_31_a_60": "300.00",
        "da_61_a_90": "400.00",
        "oltre_90": "500.00",
        "without_due_date": "600.00",
    }
    assert sum(band.amount for band in page.bands) == page.total == Decimal("2100.00")
    assert all(band.number == 1 for band in page.bands)
    assert page.today == world.today and page.legal_entity_id is None
    assert max(band.share for band in page.bands) == 1.0
    assert [band.link for band in page.bands] == [None] * 6


def test_only_what_is_owed_counts(world: CashWorld) -> None:
    world.item("100.00", world.today + dt.timedelta(days=3), direction="out")
    world.item("900.00", world.today + dt.timedelta(days=3), direction="out", state="forecast")
    world.item("800.00", world.today + dt.timedelta(days=3), direction="out", currency="USD")
    world.item("700.00", world.today + dt.timedelta(days=3))  # money in
    settled = world.item("600.00", world.today - dt.timedelta(days=3), direction="out")
    world.service.settle(
        settled, SettlementCreate(amount=Decimal("600.00"), settled_on=world.today), ADMIN
    )
    part = world.item("50.00", world.today - dt.timedelta(days=3), direction="out")
    world.service.settle(
        part, SettlementCreate(amount=Decimal("20.00"), settled_on=world.today), ADMIN
    )

    page = payables(world)

    assert page.total == Decimal("130.00")
    assert {band.code: str(band.amount) for band in page.bands}["expired"] == "30.00"
    assert (page.total_overdue, [row.amount for row in page.overdue]) == (1, [Decimal("30.00")])


def test_the_mirror_has_the_receivables_codes_labels_bounds_and_shares(world: CashWorld) -> None:
    """The same amounts at the same dates, owed and receivable: every band row is the same."""
    _owe(world)
    for amount, offset in OFFSETS:
        due = None if offset is None else world.today + dt.timedelta(days=offset)
        world.session.add(
            Invoice(
                customer_id=world.customer.id,
                type="invoice",
                status="issued",
                year=2026,
                number=next(_NUMBERS),
                payment_status="unpaid",
                taxable_amount=Decimal(amount),
                vat_amount=Decimal("0.00"),
                stamp_duty=Decimal("0.00"),
                total=Decimal(amount),
                issue_date=world.today - dt.timedelta(days=1),
                due_date=due,
                sdi_document_type="TD01",
                currency="EUR",
                custom_fields={},
            )
        )
    world.session.flush()
    receivable = InvoiceRepository(world.session).ageing_receivables(world.today)

    page = payables(world)

    assert [
        (b.code, b.label, b.date_from, b.date_to, b.amount, b.number, b.share) for b in page.bands
    ] == [
        (r.code, _BAND_LABELS[r.code], r.date_from, r.date_to, r.amount, r.number, r.share)
        for r in receivable
    ]
    assert page.total == InvoiceRepository(world.session).sum_unpaid()
    assert page.total_overdue == InvoiceRepository(world.session).count_overdue_not_paid()


def test_an_empty_band_is_a_row_at_zero(world: CashWorld) -> None:
    page = payables(world)

    assert len(page.bands) == 6
    assert page.total == Decimal("0.00") and page.overdue == [] and page.total_overdue == 0
    assert all(
        (band.amount, band.number, band.share) == (Decimal("0.00"), 0, 0.0) for band in page.bands
    )
    assert page.bands[1].date_from == world.today
    assert page.bands[1].date_to == world.today + dt.timedelta(days=30)


def test_the_overdue_rows_name_who_is_owed_and_how_late(world: CashWorld) -> None:
    older = world.item("40.00", world.today - dt.timedelta(days=30), direction="out")
    tax = world.item(
        "25.00",
        world.today - dt.timedelta(days=5),
        direction="out",
        source_kind="iva_settlement",
        tax="vat",
    )
    world.item("90.00", world.today + dt.timedelta(days=5), direction="out")

    page = payables(world)

    assert page.total_overdue == 2
    first, second = page.overdue
    assert (first.due_item_id, first.counterparty, first.days_of_delay, first.amount) == (
        older,
        "Acme srl",
        30,
        Decimal("40.00"),
    )
    assert (second.due_item_id, second.counterparty, second.days_of_delay) == (tax, "vat", 5)
    assert first.due_date == world.today - dt.timedelta(days=30)


def test_the_list_is_capped_and_the_count_is_not(world: CashWorld) -> None:
    for index in range(OVERDUE_SHOWN + 3):
        world.item("1.00", world.today - dt.timedelta(days=1 + index), direction="out")

    page = payables(world)

    assert len(page.overdue) == OVERDUE_SHOWN
    assert page.total_overdue == OVERDUE_SHOWN + 3
    # The oldest first.
    assert page.overdue[0].days_of_delay == OVERDUE_SHOWN + 3


def test_the_restricted_kinds_are_an_administrators(world: CashWorld) -> None:
    world.item("100.00", world.today - dt.timedelta(days=4), direction="out")
    world.item(
        "40.00",
        world.today - dt.timedelta(days=4),
        direction="out",
        source_kind="iva_settlement",
        tax="vat",
    )
    world.item("900.00", world.today + dt.timedelta(days=4), direction="out", source_kind="payroll")

    assert payables(world, ADMIN).total == Decimal("1040.00")
    assert payables(world, ADMIN).total_overdue == 2
    for actor in (COLLABORATOR, AGENT):
        page = payables(world, actor)
        assert (page.total, page.total_overdue) == (Decimal("100.00"), 1), actor


def test_the_legal_entity_filter_scopes_it(world: CashWorld) -> None:
    other = world.second_entity()
    world.item("100.00", world.today + dt.timedelta(days=4), direction="out")
    world.item("7.00", world.today - dt.timedelta(days=4), direction="out", entity=other)

    assert payables(world).total == Decimal("107.00")
    scoped = payables(world, entity=other)
    assert (scoped.total, scoped.total_overdue, scoped.legal_entity_id) == (
        Decimal("7.00"),
        1,
        other,
    )


def test_a_solo_space_answers_zeros_in_the_same_shape(world: CashWorld) -> None:
    _owe(world)

    page = payables(world, profile="solo")

    assert [band.code for band in page.bands] == list(BANDS_RECEIVABLES)
    assert page.total == Decimal("0.00") and page.overdue == [] and page.total_overdue == 0
    assert all(band.amount == Decimal("0.00") for band in page.bands)
    assert page.bands[1].date_to == world.today + dt.timedelta(days=30)
