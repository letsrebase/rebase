"""REB-768: the contracts, days and approvals group of the reminder catalogue (spec 2026-10-26
§3.4): `contract_expiring`, `renewal_window_open`, `worked_without_approval`,
`approval_unanswered`, `approval_requirement_unmet` and `work_unit_outside_term`.

Every test evaluates on a date it chooses (`TODAY`). The card's proof is the first one: a
contract ending in 60 days with two days worked and not approved raises both of its types in
one pass, and a contract with no approval requirement raises no approval type.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User, UserLegalEntity
from pigrocrm.core.config import Settings
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.reminders.catalogue import reminder_key
from pigrocrm.core.reminders.engine import Evaluation, ReminderEngine
from pigrocrm.core.reminders.preferences import ReminderPreferenceService
from pigrocrm.core.reminders.schemas import ReminderPreferenceWrite
from pigrocrm.core.work_units.models import Approval, WorkUnit

TODAY = date(2026, 10, 26)
NOON = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]

CONTRACT_TYPES = {"contract_expiring", "renewal_window_open"}
DAY_TYPES = {
    "worked_without_approval",
    "approval_unanswered",
    "approval_requirement_unmet",
    "work_unit_outside_term",
}


def _ago(days: int) -> date:
    return TODAY - timedelta(days=days)


def _ahead(days: int) -> date:
    return TODAY + timedelta(days=days)


def _noon(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)


@dataclass
class World:
    session: Session
    owner: User
    carol: User  # owns the customer
    customer: Customer
    legal_entity: UUID

    def contract(
        self,
        *,
        requires: bool = False,
        start: date | None = None,
        end: date | None = None,
        renewal_type: str = "none",
        notice: int | None = None,
        customer: Customer | None = None,
        status: str = "draft",
    ) -> Contract:
        row = Contract(
            customer_id=(customer or self.customer).id,
            legal_entity_id=self.legal_entity,
            title="Consulenza",
            start=start or _ago(200),
            end=end,
            renewal_type=renewal_type,
            renewal_notice_days=notice,
            termination_notice_days=30,
            billing_cadence="monthly",
            expense_policy={"kind": "reimbursable"},
            requires_prior_approval=requires,
            status=status,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def day(
        self, contract: Contract, day: date, status: str = "worked", **fields: object
    ) -> WorkUnit:
        row = WorkUnit(
            contract_id=contract.id,
            date=day,
            quantity=Decimal("1.00"),
            description="Giornata",
            status=status,
            **fields,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def approval(self, contract: Contract, received: date) -> Approval:
        document = Document(contract_id=contract.id, type="contract", title="Contratto firmato")
        self.session.add(document)
        self.session.flush()
        row = Approval(
            contract_id=contract.id,
            channel="email",
            sender="cliente@example.test",
            received_at=_noon(received),
            document_id=document.id,
            excerpt="Confermiamo.",
            origin={"kind": "manual"},
        )
        self.session.add(row)
        self.session.flush()
        return row

    def evaluate(self) -> Evaluation:
        self.session.flush()
        return ReminderEngine(self.session, SETTINGS).evaluate(today=TODAY, now=NOON)

    def raised(self, evaluation: Evaluation | None = None) -> dict[tuple[str, UUID], set[UUID]]:
        """`(type, subject)` to the people told."""
        found: dict[tuple[str, UUID], set[UUID]] = {}
        for reminder in (evaluation or self.evaluate()).reminders:
            subject = UUID(reminder.key.partition(":")[2])
            found.setdefault((reminder.type, subject), set()).add(reminder.user_id)
        return found

    def severity(self, type_key: str, subject: UUID) -> str | None:
        """What Carol, the customer's owner, is told about `subject`, or `None`."""
        for reminder in self.evaluate().reminders:
            mine = reminder.user_id == self.carol.id
            if mine and reminder.type == type_key and reminder.key.endswith(str(subject)):
                return reminder.severity
        return None


@pytest.fixture
def world(db_session: Session) -> World:
    legal_entity = db_session.scalars(
        select(LegalEntity.id).where(LegalEntity.is_default.is_(True))
    ).one()
    users = {}
    for name, role in (("owner", "admin"), ("carol", "collaborator")):
        user = User(
            email=f"{name}-{uuid4()}@example.test",
            password_hash="x",
            name=name.title(),
            role=role,
        )
        db_session.add(user)
        users[name] = user
    db_session.flush()
    customer = Customer(
        legal_name="ACME Srl", legal_entity_id=legal_entity, owner_id=users["carol"].id
    )
    db_session.add(customer)
    db_session.flush()
    return World(db_session, users["owner"], users["carol"], customer, legal_entity)


def _actor(user: User) -> Actor:
    return Actor(id=user.id, type="user", role=user.role)  # type: ignore[arg-type]


# -- the card's proof ---------------------------------------------------------------------


def test_a_contract_ending_in_sixty_days_with_two_days_worked_unapproved_raises_both(
    world: World,
) -> None:
    contract = world.contract(requires=True, end=_ahead(60))
    first = world.day(contract, _ago(2))
    second = world.day(contract, _ago(1))
    free = world.contract(requires=False)  # no approval type at all
    free_day = world.day(free, _ago(5))

    raised = world.raised()

    assert raised[("contract_expiring", contract.id)] == {world.carol.id}
    assert raised[("worked_without_approval", first.id)] == {world.carol.id}
    assert raised[("worked_without_approval", second.id)] == {world.carol.id}
    assert world.severity("contract_expiring", contract.id) == "warning"
    assert world.severity("worked_without_approval", first.id) == "serious"
    assert {subject for (_, subject) in raised} == {contract.id, first.id, second.id}, (
        "nothing for the contract that requires no approval, nor for its worked day"
    )
    assert free.id not in {subject for (_, subject) in raised}
    assert free_day.id not in {subject for (_, subject) in raised}


def test_a_contract_with_no_approval_requirement_raises_no_approval_type(world: World) -> None:
    contract = world.contract(requires=False, end=_ahead(60))
    world.day(contract, _ago(3))
    world.day(contract, _ago(2), "proposed")

    evaluation = world.evaluate()

    assert {reminder.type for reminder in evaluation.reminders} == {"contract_expiring"}
    assert evaluation.ran.isdisjoint(
        {"worked_without_approval", "approval_unanswered", "approval_requirement_unmet"}
    ), "the approval types do not apply to a space with no requiring contract"


# -- contract_expiring --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "severity"),
    [
        (61, None),
        (60, "warning"),
        (31, "warning"),
        (30, "serious"),
        (15, "serious"),
        (14, "critical"),
        (0, "critical"),
        (-1, None),
    ],
)
def test_contract_expiring_climbs_the_ladder_on_both_sides_of_each_boundary(
    world: World, days: int, severity: str | None
) -> None:
    contract = world.contract(end=_ahead(days), start=_ago(400))

    assert world.severity("contract_expiring", contract.id) == severity


def test_a_contract_without_an_end_date_or_archived_is_not_expiring(world: World) -> None:
    world.contract(end=None)
    gone = world.contract(end=_ahead(10))
    gone.deleted_at = NOON
    world.session.flush()

    evaluation = world.evaluate()

    assert evaluation.reminders == ()


def test_a_renewed_contract_is_neither_expiring_nor_in_its_renewal_window(world: World) -> None:
    """REB-763: once a successor exists the renewal is decided, so neither the end nor the
    deadline of the contract it replaces is a thing to be reminded of; its successor, which
    ends a year later, raises nothing yet."""
    renewing = _renewing(world, 20)
    assert world.severity("contract_expiring", renewing.id) is not None
    assert world.severity("renewal_window_open", renewing.id) is not None

    successor = world.contract(start=renewing.end + timedelta(days=1), end=_ahead(500))
    successor.previous_contract_id = renewing.id
    world.session.flush()

    assert world.severity("contract_expiring", renewing.id) is None
    assert world.severity("renewal_window_open", renewing.id) is None
    assert world.severity("contract_expiring", successor.id) is None


def test_contract_expiring_reads_the_end_date_each_pass(world: World) -> None:
    contract = world.contract(end=_ahead(20))
    assert world.severity("contract_expiring", contract.id) == "serious"

    contract.end = _ahead(400)
    world.session.flush()

    assert world.severity("contract_expiring", contract.id) is None, "the cause was fixed"


def test_contract_expiring_goes_to_the_customers_owner_scoped_to_the_legal_entity(
    world: World,
) -> None:
    contract = world.contract(end=_ahead(20))
    other = LegalEntity(name="Altra", legal_name="Altra Srl", country="IT")
    world.session.add(other)
    world.session.flush()
    scoped = User(
        email=f"s-{uuid4()}@example.test", password_hash="x", name="Scoped", role="collaborator"
    )
    scoped.limited_scope = True
    world.session.add(scoped)
    world.session.flush()
    scoped.scopes = [UserLegalEntity(user_id=scoped.id, legal_entity_id=other.id)]
    world.customer.owner_id = scoped.id
    world.session.flush()

    assert world.raised()[("contract_expiring", contract.id)] == {world.owner.id}, (
        "the owner cannot read this azienda, so the reminder goes to the space's owner"
    )


def test_contract_expiring_names_what_the_web_opens(world: World) -> None:
    contract = world.contract(end=_ahead(20))

    [reminder] = [r for r in world.evaluate().reminders if r.user_id == world.carol.id]

    assert (reminder.entity, reminder.entity_id) == ("contract", contract.id)
    assert reminder.customer_id == world.customer.id
    assert reminder.title == "Contratto in scadenza: Consulenza"
    assert reminder.detail.endswith("fra 20 giorni · ACME Srl")


# -- renewal_window_open ------------------------------------------------------------------


def _renewing(world: World, left: int, *, type: str = "tacit", notice: int = 10) -> Contract:
    """A contract whose renewal deadline is `left` days away."""
    return world.contract(
        renewal_type=type, notice=notice, end=_ahead(left + notice), start=_ago(400)
    )


@pytest.mark.parametrize(
    ("left", "severity"),
    [
        (31, None),
        (30, "warning"),
        (15, "warning"),
        (14, "serious"),
        (8, "serious"),
        (7, "critical"),
        (0, "critical"),
        (-1, None),
    ],
)
def test_the_renewal_window_climbs_as_it_closes(
    world: World, left: int, severity: str | None
) -> None:
    contract = _renewing(world, left)

    assert world.severity("renewal_window_open", contract.id) == severity


@pytest.mark.parametrize("type", ["explicit", "counterparty_option", "tacit"])
def test_every_renewal_type_with_a_notice_has_a_window(world: World, type: str) -> None:
    contract = _renewing(world, 5, type=type)

    assert world.severity("renewal_window_open", contract.id) == "critical"


def test_a_contract_with_no_renewal_clause_or_no_end_has_no_window(world: World) -> None:
    world.contract(renewal_type="none", end=_ahead(5))
    world.contract(renewal_type="tacit", notice=10, end=None)

    assert "renewal_window_open" not in {r.type for r in world.evaluate().reminders}


def test_the_renewal_window_is_measured_to_the_deadline_not_to_the_end(world: World) -> None:
    contract = _renewing(world, 20, notice=40)

    [reminder] = [
        r
        for r in world.evaluate().reminders
        if r.type == "renewal_window_open" and r.user_id == world.carol.id
    ]

    assert reminder.measure == 20
    assert reminder.entity == "contract" and reminder.entity_id == contract.id


def test_a_renewal_whose_deadline_passed_is_not_open_but_expiring_still_is(world: World) -> None:
    contract = world.contract(renewal_type="tacit", notice=30, end=_ahead(10))

    types = {r.type for r in world.evaluate().reminders}

    assert types == {"contract_expiring"}
    assert contract.id


# -- worked_without_approval --------------------------------------------------------------


@pytest.mark.parametrize(
    ("ago", "severity"), [(0, "serious"), (2, "serious"), (3, "critical"), (10, "critical")]
)
def test_a_day_worked_without_approval_is_serious_at_once_and_critical_after_three_days(
    world: World, ago: int, severity: str
) -> None:
    contract = world.contract(requires=True)
    unit = world.day(contract, _ago(ago))

    assert world.severity("worked_without_approval", unit.id) == severity


def test_an_approved_day_or_a_day_on_a_contract_that_stopped_requiring_is_not_raised(
    world: World,
) -> None:
    contract = world.contract(requires=True)
    approved = world.day(contract, _ago(5), approval_id=world.approval(contract, _ago(6)).id)
    flagged = world.day(contract, _ago(1))
    assert world.severity("worked_without_approval", approved.id) is None
    assert world.severity("worked_without_approval", flagged.id) == "serious"

    contract.requires_prior_approval = False
    world.session.flush()

    assert world.severity("worked_without_approval", flagged.id) is None


def test_linking_an_approval_or_revoking_the_day_clears_it(world: World) -> None:
    contract = world.contract(requires=True)
    linked = world.day(contract, _ago(5))
    revoked = world.day(contract, _ago(4))
    assert world.severity("worked_without_approval", linked.id) == "critical"

    linked.approval_id = world.approval(contract, _ago(1)).id
    revoked.status = "revoked"
    world.session.flush()

    assert world.severity("worked_without_approval", linked.id) is None
    assert world.severity("worked_without_approval", revoked.id) is None


def test_a_day_worked_without_approval_opens_its_contract(world: World) -> None:
    contract = world.contract(requires=True)
    world.day(contract, _ago(1))

    [reminder] = [
        r
        for r in world.evaluate().reminders
        if r.type == "worked_without_approval" and r.user_id == world.carol.id
    ]

    assert (reminder.entity, reminder.entity_id) == ("contract", contract.id)
    assert reminder.title.startswith("Giornata senza approvazione: Consulenza")
    assert reminder.detail.startswith("Lavorata ieri")


def test_an_archived_contracts_days_are_not_raised(world: World) -> None:
    contract = world.contract(requires=True)
    world.day(contract, _ago(1))
    contract.deleted_at = NOON
    world.session.flush()

    assert world.raised() == {}


# -- approval_unanswered ------------------------------------------------------------------


def _proposed(world: World, contract: Contract, days: int) -> WorkUnit:
    unit = world.day(contract, _ago(days + 1), "proposed")
    unit.created_at = _noon(_ago(days))
    world.session.flush()
    return unit


@pytest.mark.parametrize(
    ("days", "severity"),
    [
        (4, None),
        (5, "warning"),
        (9, "warning"),
        (10, "serious"),
        (19, "serious"),
        (20, "critical"),
    ],
)
def test_an_approval_asked_and_unanswered_climbs_with_the_days_waited(
    world: World, days: int, severity: str | None
) -> None:
    contract = world.contract(requires=True)
    unit = _proposed(world, contract, days)

    assert world.severity("approval_unanswered", unit.id) == severity


def test_an_answered_or_not_required_approval_is_not_unanswered(world: World) -> None:
    requiring = world.contract(requires=True)
    answered = _proposed(world, requiring, 12)
    answered.approval_id = world.approval(requiring, _ago(1)).id
    answered.status = "approved"
    free = world.contract(requires=False)
    not_needed = _proposed(world, free, 12)
    worked = world.day(requiring, _ago(30))
    worked.created_at = _noon(_ago(30))
    world.session.flush()

    assert world.severity("approval_unanswered", answered.id) is None
    assert world.severity("approval_unanswered", not_needed.id) is None
    assert world.severity("approval_unanswered", worked.id) is None, "worked, not asked"


def test_an_unanswered_approval_clears_when_the_day_is_refused(world: World) -> None:
    contract = world.contract(requires=True)
    unit = _proposed(world, contract, 12)
    assert world.severity("approval_unanswered", unit.id) == "serious"

    unit.status = "rejected"
    world.session.flush()

    assert world.severity("approval_unanswered", unit.id) is None


# -- approval_requirement_unmet -----------------------------------------------------------


def test_an_approval_received_after_the_day_does_not_meet_a_prior_requirement(
    world: World,
) -> None:
    contract = world.contract(requires=True)
    late = world.day(contract, _ago(5), approval_id=world.approval(contract, _ago(2)).id)
    same_day = world.day(contract, _ago(4), approval_id=world.approval(contract, _ago(4)).id)
    before = world.day(contract, _ago(3), approval_id=world.approval(contract, _ago(9)).id)

    assert world.severity("approval_requirement_unmet", late.id) == "serious"
    assert world.severity("approval_requirement_unmet", same_day.id) is None
    assert world.severity("approval_requirement_unmet", before.id) is None


def test_an_approval_of_another_contract_does_not_meet_the_requirement(world: World) -> None:
    contract = world.contract(requires=True)
    other = world.contract(requires=True)
    unit = world.day(contract, _ago(3), approval_id=world.approval(other, _ago(9)).id)

    assert world.severity("approval_requirement_unmet", unit.id) == "serious"
    [reminder] = [
        r
        for r in world.evaluate().reminders
        if r.type == "approval_requirement_unmet" and r.user_id == world.carol.id
    ]
    assert reminder.detail.startswith("La giornata poggia su un'approvazione data per un altro")


def test_the_requirement_applies_only_to_a_requiring_contract_and_a_live_day(
    world: World,
) -> None:
    free = world.contract(requires=False)
    free_unit = world.day(free, _ago(5), approval_id=world.approval(free, _ago(1)).id)
    contract = world.contract(requires=True)
    refused = world.day(contract, _ago(5), approval_id=world.approval(contract, _ago(1)).id)
    refused.status = "revoked"
    world.session.flush()

    assert world.severity("approval_requirement_unmet", free_unit.id) is None
    assert world.severity("approval_requirement_unmet", refused.id) is None


# -- work_unit_outside_term ---------------------------------------------------------------


def test_a_day_outside_the_contract_term_is_serious_at_once(world: World) -> None:
    contract = world.contract(start=_ago(30), end=_ago(10))
    before = world.day(contract, _ago(31))
    after = world.day(contract, _ago(9))
    first = world.day(contract, _ago(30))
    last = world.day(contract, _ago(10))

    assert world.severity("work_unit_outside_term", before.id) == "serious"
    assert world.severity("work_unit_outside_term", after.id) == "serious"
    assert world.severity("work_unit_outside_term", first.id) is None, "the first day is inside"
    assert world.severity("work_unit_outside_term", last.id) is None, "the last day is inside"


def test_an_open_ended_contract_has_no_end_to_be_outside_of(world: World) -> None:
    contract = world.contract(start=_ago(30), end=None)
    inside = world.day(contract, _ago(1))
    before = world.day(contract, _ago(31))

    assert world.severity("work_unit_outside_term", inside.id) is None
    assert world.severity("work_unit_outside_term", before.id) == "serious"


def test_correcting_the_term_clears_the_reminder(world: World) -> None:
    contract = world.contract(start=_ago(30), end=_ago(10))
    unit = world.day(contract, _ago(5))
    assert world.severity("work_unit_outside_term", unit.id) == "serious"

    contract.end = _ahead(30)
    world.session.flush()

    assert world.severity("work_unit_outside_term", unit.id) is None


def test_a_withdrawn_or_archived_day_outside_the_term_is_not_raised(world: World) -> None:
    contract = world.contract(start=_ago(30), end=_ago(10))
    revoked = world.day(contract, _ago(5))
    revoked.status = "revoked"
    unwanted = world.contract(start=_ago(30), end=_ago(10))
    world.day(unwanted, _ago(5))
    unwanted.deleted_at = NOON
    world.session.flush()

    assert world.raised() == {} and revoked.id


def test_a_day_outside_the_term_is_told_to_the_customers_owner(world: World) -> None:
    contract = world.contract(start=_ago(30), end=_ago(10))
    unit = world.day(contract, _ago(5))

    assert world.raised()[("work_unit_outside_term", unit.id)] == {world.carol.id}


# -- switches -----------------------------------------------------------------------------


def test_a_type_the_space_switched_off_is_not_raised(world: World) -> None:
    contract = world.contract(end=_ahead(20))
    ReminderPreferenceService(world.session).write(
        _actor(world.owner), "space", "contract_expiring", ReminderPreferenceWrite(enabled=False)
    )

    evaluation = world.evaluate()

    assert "contract_expiring" not in evaluation.ran
    assert ("contract_expiring", contract.id) not in world.raised(evaluation)


# -- what is over, and what is done ---------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "counts"),
    [("draft", True), ("active", True), ("terminated", False), ("expired", False)],
)
def test_a_terminated_or_lapsed_contract_is_neither_expiring_nor_renewing(
    world: World, status: str, counts: bool
) -> None:
    expiring = world.contract(end=_ahead(20), status=status)
    renewing = world.contract(
        renewal_type="tacit", notice=10, end=_ahead(15), start=_ago(400), status=status
    )

    assert (world.severity("contract_expiring", expiring.id) is not None) is counts
    assert (world.severity("renewal_window_open", renewing.id) is not None) is counts


def test_the_expiring_types_do_not_apply_when_every_contract_is_over(world: World) -> None:
    world.contract(end=_ahead(20), status="terminated", renewal_type="tacit", notice=5)

    evaluation = world.evaluate()

    assert evaluation.ran.isdisjoint({"contract_expiring", "renewal_window_open"})


def _settle(world: World, unit: WorkUnit, status: str) -> None:
    unit.status = "invoiced"
    world.session.flush()
    if status == "paid":
        unit.status = "paid"
        world.session.flush()


@pytest.mark.parametrize("status", ["invoiced", "paid"])
def test_an_invoiced_or_paid_day_is_done_and_raises_nothing(world: World, status: str) -> None:
    contract = world.contract(requires=True, start=_ago(30), end=_ago(10))
    flagged = world.day(contract, _ago(5))
    late = world.day(contract, _ago(6), approval_id=world.approval(contract, _ago(1)).id)
    outside = world.day(contract, _ago(7), approval_id=world.approval(contract, _ago(9)).id)
    assert world.severity("worked_without_approval", flagged.id) is not None
    assert world.severity("approval_requirement_unmet", late.id) is not None
    assert world.severity("work_unit_outside_term", outside.id) is not None

    for unit in (flagged, late, outside):
        _settle(world, unit, status)

    assert world.raised() == {}


def test_a_day_proposed_and_then_settled_is_not_unanswered(world: World) -> None:
    contract = world.contract(requires=True)
    unit = _proposed(world, contract, 12)
    unit.status = "worked"  # no approval on a requiring contract: flagged, then billed
    world.session.flush()
    _settle(world, unit, "paid")

    assert world.severity("approval_unanswered", unit.id) is None


# -- Italian ----------------------------------------------------------------------------------


def test_one_day_is_never_written_as_one_days(world: World) -> None:
    contract = world.contract(requires=True, end=_ahead(1), renewal_type="tacit", notice=0)
    unit = world.day(contract, _ago(1))
    other = world.day(contract, _ago(2))

    details = {r.key: r.detail for r in world.evaluate().reminders if r.user_id == world.carol.id}

    assert details[reminder_key("contract_expiring", contract.id)].startswith("Finisce")
    assert "fra 1 giorno" in details[reminder_key("contract_expiring", contract.id)]
    assert "fra 1 giorno" in details[reminder_key("renewal_window_open", contract.id)]
    assert details[reminder_key("worked_without_approval", unit.id)].startswith("Lavorata ieri")
    assert details[reminder_key("worked_without_approval", other.id)].startswith(
        "Lavorata 2 giorni fa"
    )
    assert not any("1 giorni" in detail for detail in details.values())
