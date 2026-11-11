"""Due items and their settlements (REB-967, spec 2026-11-09, due items, § 1).

The service is driven against the real schema: the over-settlement and reversal rules, the
state a part-paid item is in, the four due rules and what moves when a dependency settles,
and the per-action permissions of § 5, which are the card's «done when».
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items import rules
from pigrocrm.core.due_items.models import (
    COUNTERPARTY_TYPES,
    DIRECTIONS,
    RESTRICTED_SOURCE_KINDS,
    RULE_KINDS,
    SOURCE_KINDS,
    STATES,
    DueItem,
    DueItemSettlement,
)
from pigrocrm.core.due_items.schemas import (
    DueItemCreate,
    DueItemDetail,
    DueItemListQuery,
    DueRule,
    SettlementCreate,
)
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    ImmutableField,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository

D = dt.date
ADMIN = Actor(id=uuid4(), type="user", role="admin")
COLLABORATOR = Actor(id=uuid4(), type="user", role="collaborator")
READONLY = Actor(id=uuid4(), type="user", role="readonly")
AGENT_COLLABORATOR = Actor(id=uuid4(), type="mcp", role="collaborator")
AGENT_ADMIN = Actor(id=uuid4(), type="mcp", role="admin")
AGENT_ADMIN_FULL = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)


class World:
    def __init__(self, session: Session) -> None:
        self.session = session
        default = LegalEntityRepository(session).default()
        assert default is not None
        self.entity: UUID = default.id
        self.customer = make_customer(session, legal_entity_id=self.entity)
        self.service = DueItemService(session)

    def item(self, **overrides: object) -> UUID:
        values: dict[str, object] = {
            "legal_entity_id": self.entity,
            "direction": "in",
            "organization_id": self.customer.id,
            "amount": Decimal("100.00"),
            "source_kind": "sales_invoice",
            "source_id": uuid4(),
            "own_date": D(2026, 2, 28),
        }
        values.update(overrides)
        return self.service.create(DueItemCreate(**values)).id  # type: ignore[arg-type]

    def pay(self, item_id: UUID, amount: str, on: dt.date, actor: Actor = ADMIN) -> None:
        self.service.settle(item_id, SettlementCreate(amount=Decimal(amount), settled_on=on), actor)

    def read(self, item_id: UUID) -> DueItemDetail:
        return self.service.get(item_id, ADMIN)


@pytest.fixture
def world(db_session: Session) -> World:
    return World(db_session)


# --- the vocabularies ---------------------------------------------------------------------


def test_every_source_kind_is_listed_and_the_fiscal_ones_are_a_named_subset() -> None:
    """A later project adds a kind here and in `RESTRICTED_SOURCE_KINDS` when it needs the
    fiscal gate; nothing in the schema changes. This lists them all, so a kind added without
    deciding who may read it fails here."""
    assert set(SOURCE_KINDS) == {
        "sales_invoice",
        "purchase",
        "integration_vat",
        "iva_settlement",
        "withholding",
        "tax_instalment",
        "tax_balance",
        "tax_advance",
        "chamber_fee",
        "payroll",
        "contract_occurrence",
        "recurring_expense",
        "cash_basis_vat",
        "engagement",
        "sales_draft",
        "expense_note",
    }
    assert set(SOURCE_KINDS) >= RESTRICTED_SOURCE_KINDS
    assert set(SOURCE_KINDS) - RESTRICTED_SOURCE_KINDS == {
        "sales_invoice",
        "purchase",
        "contract_occurrence",
        "recurring_expense",
        "sales_draft",
    }
    assert STATES == ("forecast", "expected", "settled", "dropped")
    assert DIRECTIONS == ("in", "out")
    assert COUNTERPARTY_TYPES == ("organization", "tax_office", "staff_member")
    assert RULE_KINDS == (
        "fixed",
        "after_settlement",
        "month_after_settlement_on_day",
        "on_settlement",
    )


def test_an_unknown_source_kind_is_refused_by_the_service_not_by_a_constraint(
    world: World,
) -> None:
    with pytest.raises(ValidationFailed) as refused:
        world.item(source_kind="lottery")
    assert refused.value.details["field"] == "source_kind"


# --- the date rules, pure -----------------------------------------------------------------


def _resolve(kind: str, anchor: rules.Anchor | None, **kw: object) -> rules.Resolved:
    values: dict[str, object] = {
        "own_date": None,
        "offset_days": 0,
        "day_of_month": None,
        "not_after": None,
    }
    values.update(kw)
    return rules.resolve(kind, anchor=anchor, **values)  # type: ignore[arg-type]


def test_a_fixed_rule_is_the_items_own_date() -> None:
    assert _resolve("fixed", None, own_date=D(2026, 5, 1)) == rules.Resolved(D(2026, 5, 1), False)
    assert _resolve("fixed", None) == rules.Resolved(None, False)


def test_after_settlement_is_the_later_of_the_settlement_plus_days_and_the_own_date() -> None:
    settled = rules.Anchor("settled", D(2026, 3, 1), D(2026, 3, 10))
    assert _resolve("after_settlement", settled, offset_days=15) == rules.Resolved(
        D(2026, 3, 25), False
    )
    # The own date is a floor.
    assert _resolve(
        "after_settlement", settled, offset_days=15, own_date=D(2026, 4, 30)
    ) == rules.Resolved(D(2026, 4, 30), False)


def test_while_the_dependency_is_open_the_date_is_projected_and_estimated() -> None:
    open_anchor = rules.Anchor("expected", D(2026, 3, 1), None)
    assert _resolve("after_settlement", open_anchor, offset_days=10) == rules.Resolved(
        D(2026, 3, 11), True
    )


def test_not_after_caps_both_a_settled_and_a_projected_date() -> None:
    cap = D(2026, 3, 20)
    settled = rules.Anchor("settled", D(2026, 3, 1), D(2026, 3, 10))
    assert _resolve("after_settlement", settled, offset_days=30, not_after=cap) == rules.Resolved(
        cap, False
    )
    open_anchor = rules.Anchor("expected", D(2026, 3, 1), None)
    assert _resolve(
        "after_settlement", open_anchor, offset_days=60, not_after=cap
    ) == rules.Resolved(cap, True)


def test_not_after_dates_an_item_with_nothing_to_project_from() -> None:
    """With no client invoice yet the item waits, and the backstop still dates it."""
    cap = D(2026, 4, 30)
    undated = rules.Anchor("expected", None, None)
    assert _resolve("after_settlement", undated, not_after=cap) == rules.Resolved(cap, True)
    assert _resolve("after_settlement", None, not_after=cap) == rules.Resolved(cap, True)
    assert _resolve("after_settlement", undated) == rules.Resolved(None, True)


def test_a_dropped_dependency_dates_nothing_but_the_backstop() -> None:
    dropped = rules.Anchor("dropped", D(2026, 3, 1), None)
    assert _resolve("on_settlement", dropped) == rules.Resolved(None, True)
    assert _resolve("on_settlement", dropped, not_after=D(2026, 5, 1)) == rules.Resolved(
        D(2026, 5, 1), True
    )


def test_the_month_after_rule_lands_on_the_day_of_the_next_month_across_a_year_end() -> None:
    settled = rules.Anchor("settled", D(2026, 12, 2), D(2026, 12, 20))
    assert _resolve("month_after_settlement_on_day", settled, day_of_month=16) == rules.Resolved(
        D(2027, 1, 16), False
    )
    open_anchor = rules.Anchor("expected", D(2026, 3, 31), None)
    assert _resolve(
        "month_after_settlement_on_day", open_anchor, day_of_month=16
    ) == rules.Resolved(D(2026, 4, 16), True)


def test_on_settlement_is_the_day_the_other_item_settled() -> None:
    settled = rules.Anchor("settled", D(2026, 3, 1), D(2026, 3, 12))
    assert _resolve("on_settlement", settled) == rules.Resolved(D(2026, 3, 12), False)


def test_the_rule_columns_agree_with_the_kind() -> None:
    with pytest.raises(ValueError):
        DueRule(kind="fixed", depends_on=uuid4())
    with pytest.raises(ValueError):
        DueRule(kind="after_settlement")
    with pytest.raises(ValueError):
        DueRule(kind="month_after_settlement_on_day", depends_on=uuid4())
    with pytest.raises(ValueError):
        DueRule(kind="on_settlement", depends_on=uuid4(), offset_days=3)
    with pytest.raises(ValueError):
        DueRule(kind="after_settlement", depends_on=uuid4(), day_of_month=16)
    DueRule(kind="month_after_settlement_on_day", depends_on=uuid4(), day_of_month=16)


# --- settling -----------------------------------------------------------------------------


def test_a_due_item_settled_in_two_instalments_reads_settled_with_both(world: World) -> None:
    """The card's «done when»."""
    item_id = world.item(amount=Decimal("100.00"))
    world.pay(item_id, "40.00", D(2026, 3, 1))
    partly = world.read(item_id)
    assert partly.state == "expected"
    assert (partly.settled, partly.remaining) == (Decimal("40.00"), Decimal("60.00"))
    world.pay(item_id, "60.00", D(2026, 3, 20))

    paid = world.read(item_id)
    assert paid.state == "settled"
    assert (paid.settled, paid.remaining) == (Decimal("100.00"), Decimal("0.00"))
    assert [(s.amount, s.settled_on) for s in paid.settlements] == [
        (Decimal("40.00"), D(2026, 3, 1)),
        (Decimal("60.00"), D(2026, 3, 20)),
    ]
    assert all(s.reversed_at is None for s in paid.settlements)
    assert {s.recorded_by_id for s in paid.settlements} == {ADMIN.id}


def test_a_payment_beyond_what_remains_is_refused_and_leaves_nothing(world: World) -> None:
    item_id = world.item(amount=Decimal("100.00"))
    world.pay(item_id, "70.00", D(2026, 3, 1))
    with pytest.raises(Conflict) as over:
        world.pay(item_id, "30.01", D(2026, 3, 2))
    assert over.value.details["remaining"] == "30.00"
    after = world.read(item_id)
    assert (after.state, after.remaining, len(after.settlements)) == (
        "expected",
        Decimal("30.00"),
        1,
    )
    world.pay(item_id, "30.00", D(2026, 3, 2))  # exactly what remains is fine
    assert world.read(item_id).state == "settled"


def test_a_settled_item_takes_no_more_money(world: World) -> None:
    item_id = world.item()
    world.pay(item_id, "100.00", D(2026, 3, 1))
    with pytest.raises(Conflict):
        world.pay(item_id, "0.01", D(2026, 3, 2))


def test_a_payment_in_the_future_or_of_the_wrong_sign_is_refused(world: World) -> None:
    item_id = world.item()
    with pytest.raises(ValidationFailed) as future:
        world.pay(item_id, "10.00", today_local() + dt.timedelta(days=1))
    assert future.value.details["field"] == "settled_on"
    with pytest.raises(ValidationFailed) as sign:
        world.pay(item_id, "-10.00", D(2026, 3, 1))
    assert sign.value.details["field"] == "amount"
    for bad in (
        {"amount": Decimal("0")},
        {"amount": Decimal("10"), "account_amount": Decimal("-9"), "account_currency": "EUR"},
        {"amount": Decimal("10"), "account_amount": Decimal("0"), "account_currency": "EUR"},
    ):
        with pytest.raises(ValueError):
            SettlementCreate(settled_on=D(2026, 3, 1), **bad)


def test_a_forecast_and_a_dropped_item_cannot_be_settled(world: World) -> None:
    forecast = world.item(state="forecast", source_rule="contract_recurring_fee", source_id=None)
    with pytest.raises(Conflict):
        world.pay(forecast, "10.00", D(2026, 3, 1))
    dropped = world.item()
    world.service.drop(dropped, "credit note")
    with pytest.raises(Conflict):
        world.pay(dropped, "10.00", D(2026, 3, 1))


def test_a_credit_is_a_negative_item_settled_by_negative_payments(world: World) -> None:
    credit = world.item(
        direction="out",
        counterparty_type="tax_office",
        organization_id=None,
        tax="vat",
        source_kind="iva_settlement",
        amount=Decimal("-300.00"),
    )
    world.service.settle(
        credit, SettlementCreate(amount=Decimal("-100.00"), settled_on=D(2026, 3, 1)), ADMIN
    )
    assert world.read(credit).remaining == Decimal("-200.00")
    with pytest.raises(Conflict):
        world.service.settle(
            credit, SettlementCreate(amount=Decimal("-200.01"), settled_on=D(2026, 3, 2)), ADMIN
        )
    world.service.settle(
        credit, SettlementCreate(amount=Decimal("-200.00"), settled_on=D(2026, 3, 2)), ADMIN
    )
    assert world.read(credit).state == "settled"


def test_a_payment_in_another_currency_keeps_both_amounts(world: World) -> None:
    item_id = world.item(currency="USD", amount=Decimal("100.00"))
    world.service.settle(
        item_id,
        SettlementCreate(
            amount=Decimal("100.00"),
            settled_on=D(2026, 3, 1),
            account_amount=Decimal("92.10"),
            account_currency="EUR",
        ),
        ADMIN,
    )
    settlement = world.read(item_id).settlements[0]
    assert (settlement.amount, settlement.account_amount, settlement.account_currency) == (
        Decimal("100.00"),
        Decimal("92.10"),
        "EUR",
    )
    with pytest.raises(ValueError):
        SettlementCreate(amount=Decimal("1"), settled_on=D(2026, 3, 1), account_amount=Decimal("1"))


# --- reversal -----------------------------------------------------------------------------


def test_reversing_a_settlement_reopens_the_item_and_keeps_the_row(world: World) -> None:
    item_id = world.item()
    world.pay(item_id, "40.00", D(2026, 3, 1))
    world.pay(item_id, "60.00", D(2026, 3, 20))
    second = world.read(item_id).settlements[1]

    reopened = world.service.reverse_settlement(second.id, "wrong account", ADMIN)
    assert reopened.state == "expected"
    assert (reopened.settled, reopened.remaining) == (Decimal("40.00"), Decimal("60.00"))
    assert len(reopened.settlements) == 2
    stamped = reopened.settlements[1]
    assert stamped.reversed_at is not None and stamped.reversal_reason == "wrong account"
    assert (stamped.reversed_by_id, stamped.reversed_by_type) == (ADMIN.id, "user")

    with pytest.raises(Conflict):
        world.service.reverse_settlement(second.id, None, ADMIN)
    # The freed amount can be paid again, and the reversed row does not count twice.
    world.pay(item_id, "60.00", D(2026, 3, 25))
    assert world.read(item_id).state == "settled"
    with pytest.raises(NotFound):
        world.service.reverse_settlement(uuid4(), None, ADMIN)


def test_a_dropped_item_with_a_payment_is_refused_until_the_payment_is_reversed(
    world: World,
) -> None:
    item_id = world.item()
    world.pay(item_id, "10.00", D(2026, 3, 1))
    with pytest.raises(Conflict):
        world.service.drop(item_id, "credit note")
    settlement = world.read(item_id).settlements[0]
    world.service.reverse_settlement(settlement.id, None, ADMIN)
    dropped = world.service.drop(item_id, "credit note")
    assert (dropped.state, dropped.remaining, dropped.dropped_reason) == (
        "dropped",
        Decimal("0.00"),
        "credit note",
    )
    assert world.service.drop(item_id).state == "dropped"  # idempotent


# --- rescheduling -------------------------------------------------------------------------


def test_a_sales_items_date_never_moves_but_an_outgoing_item_and_a_forecast_do(
    world: World,
) -> None:
    sales = world.item()
    with pytest.raises(ImmutableField):
        world.service.reschedule(sales, D(2026, 6, 30))
    outgoing = world.item(direction="out", source_kind="purchase")
    assert world.service.reschedule(outgoing, D(2026, 6, 30)).due_date == D(2026, 6, 30)
    forecast = world.item(state="forecast", source_rule="recurring_fee", source_id=None)
    assert world.service.reschedule(forecast, None).due_date is None
    world.pay(sales, "100.00", D(2026, 3, 1))
    with pytest.raises(Conflict):
        world.service.reschedule(sales, D(2026, 6, 30))


# --- the due rules, through the service ---------------------------------------------------


def test_a_freelancer_waits_for_the_client_and_is_projected_meanwhile(world: World) -> None:
    """Paid when the client pays, 10 days after, never later than the legal backstop."""
    client = world.item(own_date=D(2026, 3, 1))
    freelancer = world.item(
        direction="out",
        source_kind="engagement",
        own_date=D(2026, 3, 5),
        rule=DueRule(
            kind="after_settlement",
            depends_on=client,
            offset_days=10,
            not_after=D(2026, 3, 31),
        ),
    )
    waiting = world.read(freelancer)
    assert (waiting.due_date, waiting.due_date_estimated) == (D(2026, 3, 11), True)

    # A part-paid client does not date the freelancer: the date still follows the due date.
    world.pay(client, "40.00", D(2026, 3, 20))
    still = world.read(freelancer)
    assert (still.due_date, still.due_date_estimated) == (D(2026, 3, 11), True)

    # Settled late, the backstop caps what the client's payment would have meant.
    world.pay(client, "60.00", D(2026, 4, 2))
    capped = world.read(freelancer)
    assert (capped.due_date, capped.due_date_estimated) == (D(2026, 3, 31), False)

    # Reversing the client's last payment puts the freelancer back on the projection.
    last = world.read(client).settlements[-1]
    world.service.reverse_settlement(last.id, None, ADMIN)
    back = world.read(freelancer)
    assert (back.due_date, back.due_date_estimated) == (D(2026, 3, 11), True)


def test_an_unsettled_client_with_no_date_leaves_only_the_backstop(world: World) -> None:
    client = world.item(own_date=None)
    freelancer = world.item(
        direction="out",
        source_kind="engagement",
        own_date=None,
        rule=DueRule(kind="after_settlement", depends_on=client, not_after=D(2026, 5, 1)),
    )
    assert (world.read(freelancer).due_date, world.read(freelancer).due_date_estimated) == (
        D(2026, 5, 1),
        True,
    )


def test_a_withholding_falls_on_the_sixteenth_of_the_month_after_the_payment(
    world: World,
) -> None:
    supplier_payment = world.item(direction="out", source_kind="purchase", own_date=D(2026, 3, 15))
    withholding = world.item(
        direction="out",
        counterparty_type="tax_office",
        organization_id=None,
        tax="withholding",
        source_kind="withholding",
        amount=Decimal("20.00"),
        own_date=None,
        rule=DueRule(
            kind="month_after_settlement_on_day", depends_on=supplier_payment, day_of_month=16
        ),
    )
    assert world.read(withholding).due_date == D(2026, 4, 16)
    world.pay(supplier_payment, "100.00", D(2026, 5, 29))
    settled = world.read(withholding)
    assert (settled.due_date, settled.due_date_estimated) == (D(2026, 6, 16), False)


def test_on_settlement_follows_the_chain_down_two_levels(world: World) -> None:
    first = world.item(own_date=D(2026, 3, 1))
    second = world.item(
        direction="out",
        source_kind="engagement",
        own_date=None,
        rule=DueRule(kind="on_settlement", depends_on=first),
    )
    third = world.item(
        direction="out",
        source_kind="engagement",
        own_date=None,
        rule=DueRule(kind="after_settlement", depends_on=second, offset_days=5),
    )
    assert world.read(third).due_date == D(2026, 3, 6)
    world.pay(first, "100.00", D(2026, 3, 12))
    assert world.read(second).due_date == D(2026, 3, 12)
    assert world.read(third).due_date == D(2026, 3, 17)


def test_a_dropped_dependency_stops_dating_its_dependents(world: World) -> None:
    client = world.item(own_date=D(2026, 3, 1))
    freelancer = world.item(
        direction="out",
        source_kind="engagement",
        own_date=None,
        rule=DueRule(kind="on_settlement", depends_on=client),
    )
    assert world.read(freelancer).due_date == D(2026, 3, 1)
    world.service.drop(client, "contract ended")
    assert world.read(freelancer).due_date is None


def test_a_dependent_must_share_the_legal_entity_of_its_dependency(
    world: World, db_session: Session
) -> None:
    other = LegalEntity(name=f"Altra {uuid4()}", legal_name="Altra srl", country="IT")
    db_session.add(other)
    db_session.flush()
    elsewhere = make_customer(db_session, legal_entity_id=other.id)
    client = world.item()
    with pytest.raises(ValidationFailed) as refused:
        world.item(
            legal_entity_id=other.id,
            organization_id=elsewhere.id,
            direction="out",
            source_kind="engagement",
            own_date=None,
            rule=DueRule(kind="on_settlement", depends_on=client),
        )
    assert refused.value.details["field"] == "rule.depends_on"
    with pytest.raises(NotFound):
        world.item(own_date=None, rule=DueRule(kind="on_settlement", depends_on=uuid4()))
    # And the database says the same for a row built by hand.
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            DueItem(
                legal_entity_id=other.id,
                direction="out",
                state="expected",
                counterparty_type="organization",
                organization_id=elsewhere.id,
                amount=Decimal("1.00"),
                source_kind="engagement",
                rule_kind="on_settlement",
                depends_on_id=client,
            )
        )


# --- the counterparty ---------------------------------------------------------------------


def test_the_counterparty_is_an_organization_of_the_same_legal_entity_or_the_tax_office(
    world: World, db_session: Session
) -> None:
    other = LegalEntity(name=f"Altra {uuid4()}", legal_name="Altra srl", country="IT")
    db_session.add(other)
    db_session.flush()
    foreign_customer = make_customer(db_session, legal_entity_id=other.id)
    with pytest.raises(ValidationFailed) as refused:
        world.item(organization_id=foreign_customer.id)
    assert refused.value.details["field"] == "organization_id"
    with pytest.raises(NotFound):
        world.item(organization_id=uuid4())
    # An organization shared across the legal entities (REB-970) is every legal entity's.
    shared = make_customer(db_session, legal_entity_id=other.id)
    shared.legal_entity_id = None
    db_session.flush()
    assert world.item(organization_id=shared.id) is not None
    with pytest.raises(ValueError):
        DueItemCreate(
            legal_entity_id=world.entity,
            direction="out",
            counterparty_type="tax_office",
            organization_id=world.customer.id,
            tax="vat",
            amount=Decimal("1.00"),
            source_kind="iva_settlement",
        )
    tax = world.service.create(
        DueItemCreate(
            legal_entity_id=world.entity,
            direction="out",
            counterparty_type="tax_office",
            tax="vat",
            amount=Decimal("1.00"),
            source_kind="iva_settlement",
        )
    )
    assert (tax.organization_id, tax.organization_name, tax.tax) == (None, None, "vat")
    named = world.service.get(world.item(), ADMIN)
    assert named.organization_name == world.customer.legal_name


def test_a_forecast_names_its_rule_and_only_a_forecast_does(world: World) -> None:
    with pytest.raises(ValueError):
        DueItemCreate(
            legal_entity_id=world.entity,
            direction="in",
            organization_id=world.customer.id,
            amount=Decimal("1.00"),
            source_kind="contract_occurrence",
            state="forecast",
        )
    with pytest.raises(ValueError):
        DueItemCreate(
            legal_entity_id=world.entity,
            direction="in",
            organization_id=world.customer.id,
            amount=Decimal("1.00"),
            source_kind="sales_invoice",
            source_rule="recurring_fee",
        )
    with pytest.raises(ValueError):
        DueItemCreate(
            legal_entity_id=world.entity,
            direction="in",
            organization_id=world.customer.id,
            amount=Decimal("0.00"),
            source_kind="sales_invoice",
        )


# --- permissions (spec § 5) ---------------------------------------------------------------


def test_an_incoming_item_is_settled_by_a_collaborator_and_an_agent_and_not_by_a_reader(
    world: World,
) -> None:
    item_id = world.item(amount=Decimal("100.00"))
    with pytest.raises(PermissionDenied):
        world.pay(item_id, "10.00", D(2026, 3, 1), READONLY)
    world.pay(item_id, "10.00", D(2026, 3, 1), COLLABORATOR)
    world.pay(item_id, "10.00", D(2026, 3, 2), AGENT_COLLABORATOR)
    settlements = world.read(item_id).settlements
    assert [(s.recorded_by_id, s.recorded_by_type) for s in settlements] == [
        (COLLABORATOR.id, "user"),
        (AGENT_COLLABORATOR.id, "mcp"),
    ]
    # Undoing a client's payment is the same permission, in the same hands.
    with pytest.raises(PermissionDenied):
        world.service.reverse_settlement(settlements[0].id, None, READONLY)
    world.service.reverse_settlement(settlements[0].id, None, AGENT_COLLABORATOR)


def test_an_outgoing_item_is_refused_to_a_token_without_the_right_permission(
    world: World,
) -> None:
    """The card's «done when»: a token without the right permission is refused on an outgoing
    item. A collaborator is refused by role, an agent by the class of the action, and an
    agent's admin token only with `full_access`."""
    item_id = world.item(direction="out", source_kind="purchase", amount=Decimal("100.00"))
    with pytest.raises(PermissionDenied):
        world.pay(item_id, "10.00", D(2026, 3, 1), COLLABORATOR)
    with pytest.raises(AgentForbidden):
        world.pay(item_id, "10.00", D(2026, 3, 1), AGENT_COLLABORATOR)
    with pytest.raises(AgentForbidden):
        world.pay(item_id, "10.00", D(2026, 3, 1), AGENT_ADMIN)
    assert world.read(item_id).settlements == []

    world.pay(item_id, "10.00", D(2026, 3, 1), ADMIN)
    world.pay(item_id, "10.00", D(2026, 3, 2), AGENT_ADMIN_FULL)
    settlements = world.read(item_id).settlements
    assert len(settlements) == 2

    # Undoing an outgoing payment follows the same line.
    with pytest.raises(PermissionDenied):
        world.service.reverse_settlement(settlements[0].id, None, COLLABORATOR)
    with pytest.raises(AgentForbidden):
        world.service.reverse_settlement(settlements[0].id, None, AGENT_ADMIN)
    world.service.reverse_settlement(settlements[0].id, None, AGENT_ADMIN_FULL)


def test_an_incoming_fiscal_item_is_settled_like_an_outgoing_one(world: World) -> None:
    """An IVA credit reaching the account is a fiscal fact, whatever the direction."""
    refund = world.item(
        direction="in",
        counterparty_type="tax_office",
        organization_id=None,
        tax="vat",
        source_kind="iva_settlement",
        amount=Decimal("50.00"),
    )
    with pytest.raises(PermissionDenied):
        world.pay(refund, "50.00", D(2026, 3, 1), COLLABORATOR)
    world.pay(refund, "50.00", D(2026, 3, 1), ADMIN)


def test_the_fiscal_kinds_are_read_by_an_administrator_and_a_full_access_agent_alone(
    world: World,
) -> None:
    ordinary = world.item(source_kind="sales_invoice")
    tax = world.item(
        direction="out",
        counterparty_type="tax_office",
        organization_id=None,
        tax="vat",
        source_kind="iva_settlement",
        amount=Decimal("10.00"),
    )
    payroll = world.item(direction="out", source_kind="payroll")
    # What an engagement owes a freelancer: REB-1034.
    engagement = world.item(direction="out", source_kind="engagement")
    every = {ordinary, tax, payroll, engagement}
    query = DueItemListQuery()

    def listed(actor: Actor) -> set[UUID]:
        return {item.id for item in world.service.list(query, actor).items} & every

    assert listed(ADMIN) == every
    assert listed(AGENT_ADMIN_FULL) == every
    for reader in (COLLABORATOR, READONLY, AGENT_COLLABORATOR, AGENT_ADMIN):
        assert listed(reader) == {ordinary}, reader
        with pytest.raises((PermissionDenied, AgentForbidden)):
            world.service.get(tax, reader)
        with pytest.raises((PermissionDenied, AgentForbidden)):
            world.service.get(engagement, reader)
        with pytest.raises((PermissionDenied, AgentForbidden)):
            world.service.list(DueItemListQuery(source_kind="payroll"), reader)
        with pytest.raises((PermissionDenied, AgentForbidden)):
            world.service.list(DueItemListQuery(source_kind="engagement"), reader)
        with pytest.raises(ValidationFailed):
            world.service.list(DueItemListQuery(cursor=engagement), reader)
        assert world.service.get(ordinary, reader).id == ordinary


# --- listing ------------------------------------------------------------------------------


def test_the_list_answers_open_items_in_date_order_with_the_undated_last_and_pages(
    world: World,
) -> None:
    late = world.item(own_date=D(2026, 9, 1))
    early = world.item(own_date=D(2026, 1, 5))
    undated = world.item(own_date=None)
    same_day = world.item(own_date=D(2026, 1, 5))
    paid = world.item(own_date=D(2026, 1, 1))
    world.pay(paid, "100.00", D(2026, 1, 2))
    mine = {late, early, undated, same_day}

    first = world.service.list(
        DueItemListQuery(legal_entity_id=world.entity, organization_id=world.customer.id, limit=2),
        ADMIN,
    )
    assert first.next_cursor is not None
    second = world.service.list(
        DueItemListQuery(
            legal_entity_id=world.entity,
            organization_id=world.customer.id,
            limit=2,
            cursor=first.next_cursor,
        ),
        ADMIN,
    )
    assert second.next_cursor is None
    ordered = [item.id for item in first.items + second.items]
    assert set(ordered) == mine  # the settled one is not open
    assert ordered[:2] == sorted([early, same_day], key=lambda i: str(i))
    assert ordered[2:] == [late, undated]

    only_settled = world.service.list(DueItemListQuery(state="settled"), ADMIN)
    assert paid in {item.id for item in only_settled.items}
    window = world.service.list(
        DueItemListQuery(due_from=D(2026, 1, 1), due_to=D(2026, 1, 31), direction="in"), ADMIN
    )
    assert {early, same_day} <= {item.id for item in window.items}
    assert late not in {item.id for item in window.items}
    out = world.service.list(DueItemListQuery(direction="out"), ADMIN)
    assert not mine & {item.id for item in out.items}


def test_a_list_cursor_nobody_issued_is_refused(world: World) -> None:
    with pytest.raises(ValidationFailed):
        world.service.list(DueItemListQuery(cursor=uuid4()), ADMIN)


def test_an_unknown_item_is_not_found(world: World) -> None:
    with pytest.raises(NotFound):
        world.service.get(uuid4(), ADMIN)
    with pytest.raises(NotFound):
        world.service.settle(
            uuid4(), SettlementCreate(amount=Decimal("1.00"), settled_on=D(2026, 3, 1)), ADMIN
        )


# --- the table's own invariants -----------------------------------------------------------


def test_a_settlement_names_the_legal_entity_of_its_item(world: World, db_session: Session) -> None:
    item_id = world.item()
    other = LegalEntity(name=f"Altra {uuid4()}", legal_name="Altra srl", country="IT")
    db_session.add(other)
    db_session.flush()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            DueItemSettlement(
                due_item_id=item_id,
                legal_entity_id=other.id,
                amount=Decimal("1.00"),
                settled_on=D(2026, 3, 1),
                recorded_by_type="user",
            )
        )


def test_the_schema_refuses_what_the_service_would(world: World, db_session: Session) -> None:
    item_id = world.item()
    for bad in (
        {"amount": Decimal("0.00")},
        {"currency": "eur"},
        {"state": "paid"},
        {"direction": "xyz"},
        {"rule_kind": "fixed", "rule_offset_days": 3},
        {"rule_kind": "after_settlement"},
        {"tax": "vat"},
    ):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            db_session.execute(
                DueItem.__table__.update().where(DueItem.id == item_id).values(**bad)
            )
    assert world.read(item_id).state == "expected"


def test_a_list_cursor_cannot_be_a_fiscal_item_for_a_caller_who_may_not_read_it(
    world: World,
) -> None:
    """Paging from a fiscal item's position would say that it exists and where it falls."""
    tax = world.item(
        direction="out",
        counterparty_type="tax_office",
        organization_id=None,
        tax="vat",
        source_kind="iva_settlement",
        amount=Decimal("10.00"),
    )
    with pytest.raises(ValidationFailed):
        world.service.list(DueItemListQuery(cursor=tax), COLLABORATOR)
    with pytest.raises(ValidationFailed):
        world.service.list(DueItemListQuery(cursor=tax), AGENT_ADMIN)
    world.service.list(DueItemListQuery(cursor=tax), ADMIN)


def test_an_item_waiting_for_a_dependency_not_recorded_yet_is_dated_by_its_backstop(
    world: World,
) -> None:
    """A freelancer's item with no client invoice yet: it waits, and the backstop dates it
    until the dependency exists, when the projection takes over."""
    freelancer = world.item(
        direction="out",
        source_kind="engagement",
        own_date=None,
        rule=DueRule(kind="after_settlement", not_after=D(2026, 5, 1)),
    )
    waiting = world.read(freelancer)
    assert (waiting.due_date, waiting.due_date_estimated) == (D(2026, 5, 1), True)
    assert waiting.rule.depends_on_id is None

    client = world.item(own_date=D(2026, 3, 1))
    attached = world.service.attach_dependency(freelancer, client)
    assert (attached.due_date, attached.due_date_estimated) == (D(2026, 3, 1), True)
    world.pay(client, "100.00", D(2026, 3, 12))
    assert world.read(freelancer).due_date == D(2026, 3, 12)
    with pytest.raises(Conflict):
        world.service.attach_dependency(freelancer, client)
    with pytest.raises(ValueError):
        DueRule(kind="after_settlement")
    with pytest.raises(ValueError):
        DueRule(kind="on_settlement", not_after=D(2026, 5, 1))


def test_an_item_following_one_payment_can_be_pointed_at_another(world: World) -> None:
    freelancer = world.item(
        direction="out",
        source_kind="engagement",
        own_date=None,
        rule=DueRule(kind="after_settlement", offset_days=10, not_after=D(2026, 5, 1)),
    )
    first = world.item(own_date=D(2026, 3, 1))
    second = world.item(own_date=D(2026, 3, 20))
    with pytest.raises(Conflict):
        world.service.repoint_dependency(freelancer, first)
    assert world.service.attach_dependency(freelancer, first).due_date == D(2026, 3, 11)
    assert world.service.repoint_dependency(freelancer, first).due_date == D(2026, 3, 11)
    moved = world.service.repoint_dependency(freelancer, second)
    assert (moved.rule.depends_on_id, moved.due_date) == (second, D(2026, 3, 30))


def test_a_dependency_cannot_close_a_loop(world: World) -> None:
    first = world.item(
        direction="out",
        source_kind="engagement",
        own_date=None,
        rule=DueRule(kind="after_settlement", not_after=D(2026, 5, 1)),
    )
    second = world.item(
        direction="out",
        source_kind="engagement",
        own_date=None,
        rule=DueRule(kind="on_settlement", depends_on=first),
    )
    with pytest.raises(ValidationFailed):
        world.service.attach_dependency(first, second)
    with pytest.raises(ValidationFailed):
        world.service.attach_dependency(first, first)
