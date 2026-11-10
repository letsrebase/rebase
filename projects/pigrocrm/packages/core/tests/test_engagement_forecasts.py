"""What each engagement pays the freelancer, forecast by its payment rule (REB-1003,
collaborators design § 4, «Due items» and «The payment rule»).

The arithmetic (`payments.py`) is driven without a database; the due items it writes are driven
against the real schema: a fixed term dated from the billing point, an `on_collection` item that
waits, is paired with the client's invoice, moves when that item settles and never passes the 60
days of law 81/2017 art. 3 c. 1.
"""

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes.customer_fixtures import make_customer
from fakes.invoice_fixtures import unpaid_invoice
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators import payments
from pigrocrm.core.collaborators.forecast import EngagementForecastService
from pigrocrm.core.collaborators.models import Engagement
from pigrocrm.core.collaborators.schemas import EngagementCreate, EngagementRead, EngagementUpdate
from pigrocrm.core.collaborators.service import EngagementService
from pigrocrm.core.db import today_local
from pigrocrm.core.db.clock import month_bounds
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.schemas import DueItemCreate, DueRule, SettlementCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.organizations.models import Organization

D = dt.date
ADMIN = Actor(id=None, type="user", role="admin")  # type: ignore[arg-type]
LEGAL_LIMIT = dt.timedelta(days=60)


def _engagement(**overrides: Any) -> Any:
    """What `payments` reads of an engagement, without a row."""
    values: dict[str, Any] = {
        "id": uuid4(),
        "starts_on": D(2026, 11, 2),
        "ends_on": D(2026, 11, 29),
        "days_per_week": Decimal("3"),
        "fee": Decimal("450.00"),
        "fee_unit": "day",
        "expected_days": None,
        "payment_rule": "fixed_term",
        "payment_days": 30,
        "payment_end_of_month": False,
        "collection_days": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# ---- the billing points ---------------------------------------------------------------------


def test_a_day_letter_bills_each_month_at_month_end_for_the_days_it_commits() -> None:
    # Four weeks of three days: 12 days at 450.
    (november,) = payments.billings(_engagement(), D(2026, 11, 10))
    assert november == payments.Billing(
        D(2026, 11, 2), D(2026, 11, 29), D(2026, 11, 30), Decimal("5400.00")
    )

    # Across a month boundary the weekdays of each month count on their own.
    first, second = payments.billings(
        _engagement(starts_on=D(2026, 11, 23), ends_on=D(2026, 12, 11)), D(2026, 11, 10)
    )
    assert (first.billing_date, first.amount) == (
        D(2026, 11, 30),
        Decimal("1620.00"),
    )  # 6 weekdays of 5: 3.6 days
    assert (second.billing_date, second.amount) == (D(2026, 12, 31), Decimal("2430.00"))


def test_an_hourly_letter_bills_eight_hours_a_day() -> None:
    (november,) = payments.billings(
        _engagement(fee_unit="hour", fee=Decimal("50.00")), D(2026, 11, 10)
    )
    assert november.amount == Decimal("4800.00")  # 12 days * 8 hours * 50


def test_expected_days_cap_what_is_forecast_in_total() -> None:
    capped = payments.billings(
        _engagement(starts_on=D(2026, 11, 2), ends_on=D(2027, 1, 31), expected_days=Decimal("20")),
        D(2026, 11, 10),
    )
    assert [b.amount for b in capped] == [
        Decimal("5670.00"),
        Decimal("3330.00"),
    ]  # 12.6 then 7.4 days


def test_a_flat_fee_bills_once_at_the_end_and_needs_one() -> None:
    (flat,) = payments.billings(
        _engagement(fee_unit="flat", fee=Decimal("9000.00"), ends_on=D(2026, 12, 15)),
        D(2026, 11, 10),
    )
    assert (flat.billing_date, flat.amount) == (D(2026, 12, 31), Decimal("9000.00"))
    assert payments.billings(_engagement(fee_unit="flat", ends_on=None), D(2026, 11, 10)) == []


def test_without_days_per_week_a_day_letter_cannot_be_estimated() -> None:
    assert payments.billings(_engagement(days_per_week=None), D(2026, 11, 10)) == []


def test_an_open_ended_letter_is_forecast_up_to_the_horizon() -> None:
    points = payments.billings(_engagement(ends_on=None), D(2026, 11, 10))
    assert points[0].billing_date == D(2026, 11, 30)
    assert points[-1].billing_date == D(2027, 11, 30)
    assert len(points) == 13


def test_a_fixed_term_is_dated_from_the_billing_point_and_an_on_collection_one_is_capped() -> None:
    month_end = payments.due_terms(
        _engagement(payment_days=30, payment_end_of_month=True), D(2026, 11, 30)
    )
    assert month_end.own_date == D(2026, 12, 31)
    assert month_end.rule == DueRule(kind="fixed")

    waiting = payments.due_terms(
        _engagement(payment_rule="on_collection", payment_days=None, collection_days=15),
        D(2026, 11, 30),
    )
    assert waiting.own_date is None
    assert waiting.rule == DueRule(
        kind="after_settlement", offset_days=15, not_after=D(2027, 1, 29)
    )


def test_the_forecast_key_is_the_engagement_and_the_billing_date_and_nothing_else() -> None:
    engagement = uuid4()
    assert payments.forecast_key(engagement, D(2026, 11, 30)) == payments.forecast_key(
        engagement, D(2026, 11, 30)
    )
    assert payments.forecast_key(engagement, D(2026, 11, 30)) != payments.forecast_key(
        engagement, D(2026, 12, 31)
    )


# ---- the due items --------------------------------------------------------------------------


class World:
    def __init__(self, session: Session, deal_id: UUID) -> None:
        self.session = session
        self.engagements = EngagementService(session)
        self.forecasts = EngagementForecastService(session)
        self.items = DueItemService(session)
        self.deal_id = deal_id
        deal = session.get(Deal, deal_id)
        assert deal is not None
        self.client_id: UUID = deal.customer_id
        today = today_local()
        # The billing month is the one before this: its backstop is still ahead, and today is
        # past it, so a client that pays today is later than the cap lets the freelancer wait.
        self.period_end = month_bounds(today.year, today.month)[0] - dt.timedelta(days=1)
        self.period_start = self.period_end.replace(day=1)
        self.today = today

    def freelancer(self) -> Organization:
        return make_customer(
            self.session,
            roles=["freelancer"],
            hub_freelancer_id=uuid4(),
            supplier_kind="professional",
        )

    def engagement(self, *, active: bool = True, **overrides: Any) -> EngagementRead:
        values: dict[str, Any] = {
            "freelancer_id": self.freelancer().id,
            "role": "Backend developer",
            "starts_on": self.period_start,
            "ends_on": self.period_end,
            "days_per_week": Decimal("5"),
            "fee": Decimal("100.00"),
            "fee_unit": "day",
            "payment_rule": "on_collection",
            "collection_days": 15,
            "deal_id": self.deal_id,
        }
        values.update(overrides)
        created = self.engagements.create(EngagementCreate(**values), ADMIN)
        if active:
            self.engagements.transition(created.id, "awaiting_signature", ADMIN)
            self.engagements.transition(created.id, "active", ADMIN)
        return created

    def forecast(self, engagement_id: UUID, state: str = "forecast") -> list[DueItem]:
        self.session.expire_all()
        return list(
            self.session.scalars(
                select(DueItem)
                .where(
                    DueItem.source_kind == "engagement",
                    DueItem.source_id == engagement_id,
                    DueItem.state == state,
                )
                .order_by(DueItem.due_date)
            )
        )

    def client_invoice(self, *, total: str = "10000.00", period_covered: bool = True) -> Invoice:
        invoice = unpaid_invoice(
            self.session,
            due=self.today + dt.timedelta(days=15),
            total=Decimal(total),
            customer=self.session.get(Organization, self.client_id),
        )
        invoice.deal_id = self.deal_id
        if period_covered:
            invoice.accrual_from, invoice.accrual_to = self.period_start, self.period_end
        self.session.flush()
        return invoice

    def client_item(self, invoice: Invoice) -> DueItem:
        item = self.session.scalars(
            select(DueItem).where(
                DueItem.source_kind == "sales_invoice", DueItem.source_id == invoice.id
            )
        ).one()
        return item

    def pay(self, item: DueItem, amount: str, on: dt.date) -> None:
        self.items.settle(item.id, SettlementCreate(amount=Decimal(amount), settled_on=on), ADMIN)
        self.session.expire_all()


@pytest.fixture
def world(db_session: Session, seeded_deal_id: UUID) -> World:
    return World(db_session, seeded_deal_id)


def test_a_draft_or_a_signed_for_nothing_engagement_forecasts_nothing(world: World) -> None:
    draft = world.engagement(active=False)
    assert world.forecast(draft.id) == []
    world.engagements.transition(draft.id, "awaiting_signature", ADMIN)
    assert world.forecast(draft.id) == []


def test_a_fixed_term_engagement_forecasts_its_payment_from_the_billing_point(world: World) -> None:
    created = world.engagement(
        payment_rule="fixed_term", payment_days=30, payment_end_of_month=True, collection_days=None
    )
    (item,) = world.forecast(created.id)
    assert item.direction == "out"
    assert item.organization_id == created.freelancer_id
    assert item.source_rule == "engagement_payment"
    assert item.amount == Decimal("100.00") * Decimal(
        payments._working_days(world.period_start, world.period_end)
    )
    assert item.rule_kind == "fixed" and item.depends_on_id is None
    expected = payments.due_terms(
        _engagement(payment_days=30, payment_end_of_month=True), world.period_end
    )
    assert item.due_date == expected.own_date
    assert item.due_date_estimated is False


def test_an_on_collection_engagement_waits_dated_by_the_backstop(world: World) -> None:
    created = world.engagement()
    (item,) = world.forecast(created.id)
    assert item.rule_kind == "after_settlement"
    assert item.rule_offset_days == 15
    assert item.depends_on_id is None
    assert item.due_date == world.period_end + LEGAL_LIMIT
    assert item.due_date_estimated is True


def test_the_forecast_follows_the_clients_item_and_never_passes_sixty_days(world: World) -> None:
    created = world.engagement()
    invoice = world.client_invoice()
    EngagementForecastService(world.session).pair_invoice(invoice)
    (item,) = world.forecast(created.id)
    client = world.client_item(invoice)
    assert item.depends_on_id == client.id
    # Projected from the client's due date until the client pays.
    assert item.due_date == min(
        client.due_date + dt.timedelta(days=15), world.period_end + LEGAL_LIMIT
    )
    assert item.due_date_estimated is True

    world.pay(client, "10000.00", world.today)
    (settled,) = world.forecast(created.id)
    assert settled.due_date == min(
        world.today + dt.timedelta(days=15), world.period_end + LEGAL_LIMIT
    )
    assert settled.due_date_estimated is False
    assert settled.due_date <= world.period_end + LEGAL_LIMIT


def test_a_late_client_cannot_push_the_freelancer_past_the_backstop(world: World) -> None:
    created = world.engagement(collection_days=60)
    invoice = world.client_invoice()
    EngagementForecastService(world.session).pair_invoice(invoice)
    world.pay(world.client_item(invoice), "10000.00", world.today)
    (item,) = world.forecast(created.id)
    # The client paid today, more than the month's end ago: 60 days from today is past the limit.
    assert world.today + dt.timedelta(days=60) > world.period_end + LEGAL_LIMIT
    assert item.due_date == world.period_end + LEGAL_LIMIT


def test_a_part_paid_client_item_does_not_date_the_freelancer(world: World) -> None:
    created = world.engagement()
    invoice = world.client_invoice()
    EngagementForecastService(world.session).pair_invoice(invoice)
    world.pay(world.client_item(invoice), "100.00", world.today)
    (item,) = world.forecast(created.id)
    assert item.due_date_estimated is True
    assert item.due_date == min(
        world.client_item(invoice).due_date + dt.timedelta(days=15),
        world.period_end + LEGAL_LIMIT,
    )


def test_one_client_invoice_covering_two_engagements_dates_both(world: World) -> None:
    first = world.engagement()
    second = world.engagement()
    invoice = world.client_invoice()
    assert EngagementForecastService(world.session).pair_invoice(invoice) == 2
    client = world.client_item(invoice)
    assert world.forecast(first.id)[0].depends_on_id == client.id
    assert world.forecast(second.id)[0].depends_on_id == client.id


def test_a_client_invoice_for_another_period_or_deal_is_not_the_one_it_waits_for(
    world: World, db_session: Session
) -> None:
    created = world.engagement()
    other_period = world.client_invoice()
    other_period.accrual_from = world.period_start - dt.timedelta(days=62)
    other_period.accrual_to = world.period_start - dt.timedelta(days=32)
    other_deal = world.client_invoice()
    other_deal.deal_id = None
    db_session.flush()
    assert EngagementForecastService(db_session).pair_invoice(other_period) == 0
    assert EngagementForecastService(db_session).pair_invoice(other_deal) == 0
    assert world.forecast(created.id)[0].depends_on_id is None


def test_an_invoice_with_no_period_is_taken_when_issued_soon_after_the_billing_month(
    world: World,
) -> None:
    created = world.engagement()
    invoice = world.client_invoice(period_covered=False)
    invoice.issue_date = world.period_end + dt.timedelta(days=3)
    world.session.flush()
    assert EngagementForecastService(world.session).pair_invoice(invoice) == 1
    assert world.forecast(created.id)[0].depends_on_id == world.client_item(invoice).id


def test_syncing_again_changes_nothing_and_a_change_of_days_rewrites_the_amount(
    world: World,
) -> None:
    created = world.engagement(payment_rule="fixed_term", payment_days=30, collection_days=None)
    (before,) = world.forecast(created.id)
    assert not EngagementForecastService(world.session).sync(_row(world, created))
    assert [i.id for i in world.forecast(created.id)] == [before.id]

    world.engagements.update(created.id, EngagementUpdate(days_per_week=Decimal("2.5")), ADMIN)
    (after,) = world.forecast(created.id)
    assert after.id != before.id
    assert after.amount == before.amount / 2
    assert after.forecast_key == before.forecast_key
    assert world.forecast(created.id, "dropped") == [world.session.get(DueItem, before.id)]


def test_cancelling_drops_the_forecast_and_closing_early_drops_the_months_after(
    world: World,
) -> None:
    cancelled = world.engagement(
        starts_on=world.today + dt.timedelta(days=20),
        ends_on=world.today + dt.timedelta(days=80),
    )
    assert world.forecast(cancelled.id)
    world.engagements.cancel(cancelled.id, ADMIN)
    assert world.forecast(cancelled.id) == []

    open_ended = world.engagement(starts_on=world.today - dt.timedelta(days=40), ends_on=None)
    months = len(world.forecast(open_ended.id))
    assert months >= 12
    world.engagements.close(open_ended.id, ADMIN, ended_on=world.today)
    kept = world.forecast(open_ended.id)
    assert 0 < len(kept) < months
    assert all(item.due_date is not None for item in kept)
    assert world.engagements.get(open_ended.id, ADMIN).state == "concluded"


def test_an_elapsed_backstop_is_not_forecast_again(world: World) -> None:
    ancient = world.engagement(
        starts_on=world.today - dt.timedelta(days=200), ends_on=world.today - dt.timedelta(days=150)
    )
    assert world.forecast(ancient.id) == []


def test_refreshing_every_engagement_moves_the_horizon_and_pairs_what_was_left_waiting(
    world: World,
) -> None:
    created = world.engagement()
    invoice = world.client_invoice()
    # No hook ran for this invoice (written elsewhere): the cron's pass pairs it.
    assert world.forecast(created.id)[0].depends_on_id is None
    result = EngagementForecastService(world.session).refresh_all()
    assert result.paired == 1
    assert world.forecast(created.id)[0].depends_on_id == world.client_item(invoice).id
    assert not EngagementForecastService(world.session).refresh_all()


def test_two_live_forecasts_cannot_share_a_key(world: World, db_session: Session) -> None:
    created = world.engagement()
    (item,) = world.forecast(created.id)
    with pytest.raises(IntegrityError), db_session.begin_nested():
        DueItemService(db_session).create(
            DueItemCreate(
                legal_entity_id=item.legal_entity_id,
                direction="out",
                organization_id=created.freelancer_id,
                amount=Decimal("1.00"),
                state="forecast",
                source_kind="engagement",
                source_id=created.id,
                source_rule="engagement_payment",
                forecast_key=item.forecast_key,
                own_date=D(2026, 12, 31),
            )
        )


def _row(world: World, created: EngagementRead) -> Engagement:
    engagement = world.session.get(Engagement, created.id)
    assert engagement is not None
    return engagement


def test_issuing_the_clients_invoice_pairs_the_waiting_forecast(
    world: World, db_session: Session, tmp_path: Any
) -> None:
    """The emission's own transaction pairs it: nothing else has to run for the item to follow
    the client."""
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService
    from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
    from pigrocrm.core.invoices.service import InvoiceService
    from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
    from pigrocrm.core.legal_entities.service import LegalEntityService
    from pigrocrm.core.storage.local import LocalFileStorage

    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(
            legal_name="Studio Rossi",
            vat_number="01234567890",
            tax_code="HMCRFT00A01H501K",
            address="Via Vittorio Veneto 12",
            postal_code="20124",
            city="Milano",
            province="MI",
            country="IT",
            email="mario@example.com",
        ),
        ADMIN,
    )
    client = db_session.get(Organization, world.client_id)
    assert client is not None
    client.vat_number, client.sdi_code = "12345678901", "ABCDEFG"
    client.address, client.postal_code, client.city = "Corso Italia 5", "00100", "Roma"
    client.province, client.country = "RM", "IT"
    db_session.flush()

    created = world.engagement()
    service = InvoiceService(db_session, LocalFileStorage(tmp_path / "documents"))
    draft = service.create(
        InvoiceCreate(
            customer_id=world.client_id,
            deal_id=world.deal_id,
            accrual_from=world.period_start,
            accrual_to=world.period_end,
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("1000.00"))],
        ),
        ADMIN,
    )
    issued = service.issue(draft.id, InvoiceIssue(), ADMIN)
    (item,) = world.forecast(created.id)
    client_item = world.session.scalars(
        select(DueItem).where(
            DueItem.source_kind == "sales_invoice", DueItem.source_id == issued.id
        )
    ).one()
    assert item.depends_on_id == client_item.id


def test_a_later_invoice_for_the_same_period_takes_over_the_pairing(world: World) -> None:
    created = world.engagement()
    first = world.client_invoice()
    EngagementForecastService(world.session).pair_invoice(first)
    second = world.client_invoice()
    second.issue_date = first.issue_date + dt.timedelta(days=5)
    second.due_date = first.due_date + dt.timedelta(days=5)
    world.session.flush()
    assert EngagementForecastService(world.session).pair_invoice(second) == 1
    assert world.forecast(created.id)[0].depends_on_id == world.client_item(second).id


def test_relinking_the_engagement_to_another_client_forgets_the_old_clients_invoice(
    world: World, db_session: Session, seeded_open_stage_id: UUID
) -> None:
    created = world.engagement()
    invoice = world.client_invoice()
    EngagementForecastService(db_session).pair_invoice(invoice)
    (paired,) = world.forecast(created.id)
    assert paired.depends_on_id == world.client_item(invoice).id

    other_client = make_customer(db_session)
    other_deal = Deal(
        name="Altro progetto",
        customer_id=other_client.id,
        pipeline_stage_id=seeded_open_stage_id,
        probability=10,
    )
    db_session.add(other_deal)
    db_session.flush()
    world.engagements.update(created.id, EngagementUpdate(deal_id=other_deal.id), ADMIN)
    (waiting,) = world.forecast(created.id)
    assert waiting.id != paired.id
    assert waiting.depends_on_id is None
    assert waiting.due_date == world.period_end + LEGAL_LIMIT


def test_an_invoice_with_no_period_belongs_to_one_billing_period_only(
    world: World, db_session: Session
) -> None:
    created = world.engagement()
    row = db_session.get(Engagement, created.id)
    assert row is not None
    service = EngagementForecastService(db_session)
    january = payments.Billing(D(2026, 1, 1), D(2026, 1, 31), D(2026, 1, 31), Decimal("1.00"))
    february = payments.Billing(D(2026, 2, 1), D(2026, 2, 28), D(2026, 2, 28), Decimal("1.00"))
    entity = service._legal_entity(row)
    invoice = world.client_invoice(period_covered=False)
    for issued, january_item, february_item in (
        (D(2026, 2, 5), True, False),
        (D(2026, 2, 28), False, True),
        (D(2026, 1, 31), True, False),
    ):
        invoice.issue_date = issued
        db_session.flush()
        assert (service._client_item(row, entity, january) is not None) is january_item
        assert (service._client_item(row, entity, february) is not None) is february_item


def test_a_contract_engagement_follows_the_invoice_its_work_units_are_billed_on(
    world: World, db_session: Session
) -> None:
    from pigrocrm.core.contracts.models import Contract
    from pigrocrm.core.invoices.models import InvoiceLine
    from pigrocrm.core.work_units.models import WorkUnit

    contract = Contract(
        customer_id=world.client_id,
        title="Consulenza",
        start=world.period_start,
        renewal_type="none",
        termination_notice_days=30,
        billing_cadence="monthly",
        expense_policy={"kind": "not_reimbursable"},
        requires_prior_approval=False,
    )
    db_session.add(contract)
    db_session.flush()
    created = world.engagement(deal_id=None, contract_id=contract.id)
    invoice = world.client_invoice()
    invoice.deal_id = None
    line = InvoiceLine(
        invoice_id=invoice.id,
        line_number=1,
        description="Giornate",
        quantity=Decimal("1.000000"),
        unit_price=Decimal("1.000000"),
        total_price=Decimal("1.00"),
        vat_rate=Decimal("22.00"),
    )
    db_session.add(line)
    db_session.flush()
    assert EngagementForecastService(db_session).pair_invoice(invoice) == 0
    db_session.add(
        WorkUnit(
            contract_id=contract.id,
            date=world.period_start,
            quantity=Decimal("1"),
            description="Giornata",
            status="worked",
            invoice_line_id=line.id,
        )
    )
    db_session.flush()
    assert EngagementForecastService(db_session).pair_invoice(invoice) == 1
    assert world.forecast(created.id)[0].depends_on_id == world.client_item(invoice).id


def test_a_flat_fee_is_forecast_once_at_the_end_of_its_last_month(world: World) -> None:
    created = world.engagement(
        fee_unit="flat",
        fee=Decimal("9000.00"),
        payment_rule="fixed_term",
        payment_days=30,
        collection_days=None,
    )
    (item,) = world.forecast(created.id)
    assert item.amount == Decimal("9000.00")
    assert (
        item.due_date == payments.due_terms(_engagement(payment_days=30), world.period_end).own_date
    )


def test_one_engagement_that_fails_does_not_stop_the_others_from_being_refreshed(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = world.engagement(payment_rule="fixed_term", payment_days=30, collection_days=None)
    healthy = world.engagement(
        payment_rule="fixed_term", payment_days=30, collection_days=None, days_per_week=Decimal("1")
    )
    for engagement_id in (broken.id, healthy.id):
        for item in world.forecast(engagement_id):
            world.items.drop(item.id)
    original = EngagementForecastService.sync

    def sync(self: EngagementForecastService, engagement: Engagement, **kwargs: Any) -> Any:
        if engagement.id == broken.id:
            raise RuntimeError("boom")
        return original(self, engagement, **kwargs)

    monkeypatch.setattr(EngagementForecastService, "sync", sync)
    result = EngagementForecastService(world.session).refresh_all()
    assert (result.created, result.failed) == (1, 1)
    assert len(world.forecast(healthy.id)) == 1
    assert world.forecast(broken.id) == []
