"""Expense notes from submission to reimbursement, against the real schema (REB-1046, spec
2026-11-09, staff, payroll and expenses, § 3 and § 4).

The card's «done when»: a staff member submits a note, another administrator approves it, it is
a purchase and is reimbursed once; an administrator is refused approval of their own note; a
note reimbursed in a payslip is settled by it and adds no second due item.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tax_period import TODAY

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.schemas import SettlementCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    OwnExpenseNote,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.expense_notes.schemas import (
    ExpenseNoteCreate,
    ExpenseNoteListQuery,
    ExpenseNoteReimburse,
    ExpenseNoteReject,
)
from pigrocrm.core.expense_notes.service import ExpenseNoteService
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.purchases.schemas import PurchaseCreate, PurchaseUpdate
from pigrocrm.core.purchases.service import PurchaseService
from pigrocrm.core.staff.estimate import add_months, first_of
from pigrocrm.core.staff.schemas import EmployeeCreate, PayDayWrite, PayslipFigures
from pigrocrm.core.staff.service import StaffService
from pigrocrm.core.timetracking.models import CostCategory

D = dt.date
THIS_MONTH = first_of(TODAY)
NEXT_MONTH = add_months(THIS_MONTH, 1)


def _user(session: Session, role: str, name: str) -> User:
    user = User(
        email=f"{name.lower()}-{uuid4()}@example.test", password_hash="x", name=name, role=role
    )
    session.add(user)
    session.flush()
    return user


def _actor(user: User, **extra: object) -> Actor:
    return Actor(id=user.id, type="user", role=user.role, **extra)  # type: ignore[arg-type]


class World:
    """One legal entity with a staff member linked to a collaborator, two administrators and a
    cost category."""

    def __init__(self, session: Session) -> None:
        self.session = session
        # The default legal entity every space starts with has no staff of its own here.
        entity = LegalEntity(name="notes", legal_name="Note S.r.l.", country="IT")
        session.add(entity)
        session.flush()
        session.add(
            FiscalProfile(legal_entity_id=entity.id, pack_id="it-standard", regime_code="RF01")
        )
        category = CostCategory(name=f"Pranzi {uuid4()}", position=0, code=None)
        session.add(category)
        session.flush()
        self.entity: UUID = entity.id
        self.category: UUID = category.id
        self.admin_user = _user(session, "admin", "Anna")
        self.other_admin_user = _user(session, "admin", "Bruno")
        self.member_user = _user(session, "collaborator", "Carla")
        self.admin = _actor(self.admin_user)
        self.other_admin = _actor(self.other_admin_user)
        self.person = _actor(self.member_user)
        staff = StaffService(session)
        staff.set_payroll_pay_day(
            self.entity, PayDayWrite(pay_day=27, pay_month_offset=0), self.admin
        )
        self.member = staff.create_staff_member(
            self._employee(user_id=self.member_user.id), self.admin
        )
        # A person who is not a user: an administrator enters their notes.
        self.no_user = staff.create_staff_member(self._employee(name="Dario Neri"), self.admin)

    def _employee(self, **overrides: object) -> EmployeeCreate:
        values: dict[str, object] = {
            "legal_entity_id": self.entity,
            "name": "Carla Rossi",
            "start_date": D(2025, 1, 1),
            "gross_annual_pay": Decimal("30000.00"),
            "monthly_payments": 14,
            "part_time_percent": Decimal("100"),
            "employer_inps_rate": Decimal("30.00"),
            "employee_inps_rate": Decimal("9.19"),
            "employer_inail_rate": Decimal("1.00"),
            "thirteenth_month": 12,
            "fourteenth_month": 6,
        }
        values.update(overrides)
        return EmployeeCreate(**values)  # type: ignore[arg-type]

    def note(self, **overrides: object) -> ExpenseNoteCreate:
        values: dict[str, object] = {
            "date": TODAY,
            "description": "Pranzo con il cliente",
            "category_id": self.category,
            "amount": Decimal("42.50"),
        }
        values.update(overrides)
        return ExpenseNoteCreate(**values)  # type: ignore[arg-type]

    def submit(self, **overrides: object) -> UUID:
        return (
            ExpenseNoteService(self.session)
            .submit_expense_note(self.note(**overrides), self.person)
            .id
        )


@pytest.fixture
def world(db_session: Session) -> World:
    return World(db_session)


def _items(session: Session, purchase_id: UUID) -> list[DueItem]:
    return list(
        session.execute(
            select(DueItem).where(
                DueItem.source_kind == "purchase", DueItem.source_id == purchase_id
            )
        ).scalars()
    )


# ---- submit -------------------------------------------------------------------------------


def test_a_staff_member_submits_their_own_note_and_reads_only_their_own(world: World) -> None:
    service = ExpenseNoteService(world.session)
    read = service.submit_expense_note(world.note(), world.person)
    assert read.status == "submitted"
    assert read.staff_member_id == world.member.id
    assert read.staff_member_name == "Carla Rossi"
    assert read.legal_entity_id == world.entity
    assert read.amount == Decimal("42.50")
    assert read.own is True and read.can_decide is False
    # Somebody else's note is a note of another staff member.
    other = service.submit_expense_note(
        world.note(staff_member_id=world.no_user.id, description="Taxi"), world.admin
    )
    page = service.list_expense_notes(ExpenseNoteListQuery(), world.person)
    assert [item.id for item in page.items] == [read.id]
    assert page.total_count == 1 and page.total_amount == Decimal("42.50")
    with pytest.raises(NotFound):
        service.get_expense_note(other.id, world.person)
    # The administrator reads both, newest first.
    both = service.list_expense_notes(ExpenseNoteListQuery(), world.other_admin)
    assert [item.id for item in both.items] == [other.id, read.id]
    assert both.items[0].can_decide is True
    # The administrator who typed it in does not decide on it.
    assert service.get_expense_note(other.id, world.admin).can_decide is False


def test_a_note_is_for_the_callers_own_staff_member_and_nobody_elses(world: World) -> None:
    service = ExpenseNoteService(world.session)
    with pytest.raises(PermissionDenied):
        service.submit_expense_note(world.note(staff_member_id=world.no_user.id), world.person)
    # A collaborator who is not a staff member has nobody to submit for.
    stranger = _actor(_user(world.session, "collaborator", "Estranea"))
    with pytest.raises(Conflict, match="Non risulti tra le persone dell'azienda"):
        service.submit_expense_note(world.note(), stranger)
    summary = service.summarize_expense_notes(stranger)
    assert summary.staff_member_id is None and summary.can_submit is False
    assert service.list_expense_notes(ExpenseNoteListQuery(), stranger).items == []


def test_a_readonly_member_reads_their_own_notes_and_cannot_submit(world: World) -> None:
    service = ExpenseNoteService(world.session)
    note_id = world.submit()
    world.member_user.role = "readonly"
    world.session.flush()
    reader = _actor(world.member_user)
    with pytest.raises(PermissionDenied):
        service.submit_expense_note(world.note(), reader)
    assert [
        item.id for item in service.list_expense_notes(ExpenseNoteListQuery(), reader).items
    ] == [note_id]
    assert service.summarize_expense_notes(reader).can_submit is False


def test_an_administrator_enters_a_note_for_a_person_who_is_not_a_user(world: World) -> None:
    read = ExpenseNoteService(world.session).submit_expense_note(
        world.note(staff_member_id=world.no_user.id), world.admin
    )
    assert read.staff_member_id == world.no_user.id
    assert read.submitted_by_id == world.admin_user.id and read.own is True


def test_a_note_dated_after_the_end_of_the_contract_or_in_the_future_is_refused(
    world: World,
) -> None:
    service = ExpenseNoteService(world.session)
    with pytest.raises(ValidationFailed, match="giorno che non"):
        service.submit_expense_note(world.note(date=TODAY + dt.timedelta(days=1)), world.person)
    with pytest.raises(ValidationFailed, match="in servizio"):
        service.submit_expense_note(world.note(date=D(2024, 12, 31)), world.person)


def test_a_mileage_note_is_kilometres_times_the_rate_the_person_types(world: World) -> None:
    read = ExpenseNoteService(world.session).submit_expense_note(
        world.note(
            kind="mileage",
            amount=None,
            kilometres=Decimal("120.5"),
            rate_per_km=Decimal("0.4125"),
            route_from="Milano",
            route_to="Torino",
            description="Visita al cliente",
        ),
        world.person,
    )
    assert read.kind == "mileage"
    # 120.5 * 0.4125 = 49.70625
    assert read.amount == Decimal("49.71")
    assert read.rate_per_km == Decimal("0.4125")
    with pytest.raises(ValueError, match="chilometri per tariffa"):
        world.note(
            kind="mileage",
            amount=Decimal("10"),
            kilometres=Decimal("1"),
            rate_per_km=Decimal("0.4"),
            route_from="A",
            route_to="B",
        )
    with pytest.raises(ValueError, match="nota chilometrica"):
        world.note(kilometres=Decimal("3"))


def test_a_receipt_backs_one_live_note_and_a_rejected_note_frees_it(world: World) -> None:
    item = InboxItem(
        original_name="scontrino.pdf",
        file_type="pdf",
        size_bytes=10,
        sha256="1" * 64,
        content_sha256="1" * 64,
        uploaded_by_id=world.member_user.id,
    )
    world.session.add(item)
    world.session.flush()
    service = ExpenseNoteService(world.session)
    first = service.submit_expense_note(world.note(inbox_item_id=item.id), world.person)
    assert first.receipt_name == "scontrino.pdf"
    # The item took the staff member's legal entity, so the administrators of it can open it.
    assert item.legal_entity_id == world.entity
    with pytest.raises(Conflict, match="gia' in un'altra nota"):
        service.submit_expense_note(world.note(inbox_item_id=item.id), world.person)
    service.reject_expense_note(first.id, ExpenseNoteReject(reason="Illeggibile"), world.admin)
    again = service.submit_expense_note(world.note(inbox_item_id=item.id), world.person)
    assert again.id != first.id


def test_a_receipt_the_submitter_cannot_see_is_not_there(world: World) -> None:
    item = InboxItem(
        original_name="altrui.pdf",
        file_type="pdf",
        size_bytes=10,
        sha256="2" * 64,
        content_sha256="2" * 64,
        uploaded_by_id=_user(world.session, "collaborator", "Altro").id,
    )
    world.session.add(item)
    world.session.flush()
    with pytest.raises(NotFound):
        ExpenseNoteService(world.session).submit_expense_note(
            world.note(inbox_item_id=item.id), world.person
        )


# ---- approve ------------------------------------------------------------------------------


def test_another_administrator_approves_and_the_note_is_a_purchase_with_one_due_item(
    world: World,
) -> None:
    service = ExpenseNoteService(world.session)
    note_id = world.submit()
    read = service.approve_expense_note(note_id, world.admin)
    assert read.status == "approved" and read.self_approved is False
    assert read.decided_by_id == world.admin_user.id and read.decided_by_name == "Anna"
    assert read.decided_at is not None and read.reimbursement_method is None

    purchase = world.session.get(Purchase, read.purchase_id)
    assert purchase is not None
    assert purchase.kind == "expense_note"
    assert purchase.staff_member_id == world.member.id
    assert purchase.legal_entity_id == world.entity
    assert purchase.amount == Decimal("42.50") and purchase.cost_amount == Decimal("42.50")
    assert purchase.category_id == world.category and purchase.date == TODAY
    assert purchase.vat_lines == [] and purchase.supplier_id is None

    (item,) = _items(world.session, purchase.id)
    assert item.id == read.due_item_id
    assert (item.direction, item.state, item.counterparty_type) == (
        "out",
        "expected",
        "staff_member",
    )
    assert item.staff_member_id == world.member.id and item.amount == Decimal("42.50")
    # It shows in the purchases list as one of kind `expense_note`.
    from pigrocrm.core.purchases.schemas import PurchaseListQuery

    listed = PurchaseService(world.session).list(
        PurchaseListQuery(kind="expense_note"), world.admin
    )
    assert [row.id for row in listed.items] == [purchase.id]
    assert listed.total_amount == Decimal("42.50")
    # Approving twice is a 409 and writes nothing more.
    with pytest.raises(Conflict, match="gia' stata decisa"):
        service.approve_expense_note(note_id, world.other_admin)
    assert len(_items(world.session, purchase.id)) == 1


def test_an_administrator_is_refused_approval_of_their_own_note(world: World) -> None:
    service = ExpenseNoteService(world.session)
    # The staff member is the administrator's own: a user linked to the staff member.
    world.member_user.role = "admin"
    world.session.flush()
    own_admin = _actor(world.member_user)
    note_id = service.submit_expense_note(world.note(), own_admin).id
    for decide in (
        lambda: service.approve_expense_note(note_id, own_admin),
        lambda: service.reject_expense_note(note_id, ExpenseNoteReject(reason="No"), own_admin),
    ):
        with pytest.raises(OwnExpenseNote, match="Non puoi approvare una tua nota"):
            decide()
    read = service.get_expense_note(note_id, own_admin)
    assert read.status == "submitted" and read.own is True and read.can_decide is False
    assert (
        world.session.execute(select(Purchase).where(Purchase.kind == "expense_note")).first()
        is None
    )
    # Another administrator approves it.
    assert service.approve_expense_note(note_id, world.admin).status == "approved"


def test_an_administrator_who_typed_the_note_for_somebody_else_does_not_approve_it(
    world: World,
) -> None:
    service = ExpenseNoteService(world.session)
    note = service.submit_expense_note(world.note(staff_member_id=world.no_user.id), world.admin)
    with pytest.raises(OwnExpenseNote):
        service.approve_expense_note(note.id, world.admin)
    assert service.approve_expense_note(note.id, world.other_admin).status == "approved"


def test_the_only_administrator_approves_their_own_note_and_it_is_flagged(
    world: World,
) -> None:
    service = ExpenseNoteService(world.session)
    world.other_admin_user.active = False
    world.session.flush()
    note = service.submit_expense_note(world.note(staff_member_id=world.no_user.id), world.admin)
    assert service.summarize_expense_notes(world.admin).only_administrator is True
    assert service.get_expense_note(note.id, world.admin).can_decide is True
    read = service.approve_expense_note(note.id, world.admin)
    assert read.status == "approved" and read.self_approved is True
    # An agent of that administrator never approves their own note, whatever the space has.
    agent = _actor(world.admin_user)
    agent = Actor(id=agent.id, type="mcp", role="admin", full_access=True)
    own = service.submit_expense_note(world.note(staff_member_id=world.no_user.id), world.admin)
    with pytest.raises(OwnExpenseNote):
        service.approve_expense_note(own.id, agent)


def test_rejecting_keeps_the_reason_and_the_decision_is_final(world: World) -> None:
    service = ExpenseNoteService(world.session)
    note_id = world.submit()
    read = service.reject_expense_note(
        note_id, ExpenseNoteReject(reason="Manca lo scontrino"), world.admin
    )
    assert read.status == "rejected" and read.rejection_reason == "Manca lo scontrino"
    assert read.purchase_id is None and read.due_item_id is None
    # The person reads the reason on their own note.
    assert service.get_expense_note(note_id, world.person).rejection_reason == "Manca lo scontrino"
    with pytest.raises(Conflict):
        service.approve_expense_note(note_id, world.other_admin)
    with pytest.raises(Conflict):
        service.reject_expense_note(note_id, ExpenseNoteReject(reason="Ancora"), world.admin)
    assert (
        world.session.execute(select(Purchase).where(Purchase.kind == "expense_note")).first()
        is None
    )


# ---- permissions --------------------------------------------------------------------------


def test_only_an_administrator_decides_and_a_token_needs_full_access(world: World) -> None:
    service = ExpenseNoteService(world.session)
    note_id = world.submit()
    for actor in (world.person, Actor(id=uuid4(), type="user", role="readonly")):
        with pytest.raises(PermissionDenied):
            service.approve_expense_note(note_id, actor)
        with pytest.raises(PermissionDenied):
            service.reject_expense_note(note_id, ExpenseNoteReject(reason="No"), actor)
        with pytest.raises(PermissionDenied):
            service.reimburse_expense_note(
                note_id, ExpenseNoteReimburse(method="transfer", date=TODAY), actor
            )
    token = Actor(id=uuid4(), type="mcp", role="admin")
    with pytest.raises(AgentForbidden):
        service.approve_expense_note(note_id, token)
    with pytest.raises(AgentForbidden):
        service.reject_expense_note(note_id, ExpenseNoteReject(reason="No"), token)
    with pytest.raises(AgentForbidden):
        service.reimburse_expense_note(
            note_id, ExpenseNoteReimburse(method="transfer", date=TODAY), token
        )
    full = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
    assert service.approve_expense_note(note_id, full).status == "approved"


def test_an_agent_submits_only_for_the_staff_member_its_owner_is(world: World) -> None:
    service = ExpenseNoteService(world.session)
    token = Actor(id=world.member_user.id, type="mcp", role="collaborator")
    own = service.submit_expense_note(world.note(), token)
    assert own.staff_member_id == world.member.id and own.submitted_by_type == "mcp"
    # An administrator's token is no wider: another person's note is not the owner's own.
    admin_token = Actor(id=world.admin_user.id, type="mcp", role="admin", full_access=True)
    with pytest.raises(Conflict, match="Non risulti"):
        service.submit_expense_note(world.note(), admin_token)
    with pytest.raises(PermissionDenied):
        service.submit_expense_note(world.note(staff_member_id=world.no_user.id), admin_token)
    # Without `full_access` the token reads only what is its owner's own.
    assert [i.id for i in service.list_expense_notes(ExpenseNoteListQuery(), token).items] == [
        own.id
    ]
    other = world.submit(description="Altra")
    ids = {i.id for i in service.list_expense_notes(ExpenseNoteListQuery(), admin_token).items}
    assert ids == {own.id, other}


def test_a_purchase_of_an_expense_note_cannot_be_registered_or_changed_as_a_purchase(
    world: World,
) -> None:
    read = ExpenseNoteService(world.session).approve_expense_note(world.submit(), world.admin)
    assert read.purchase_id is not None
    purchases = PurchaseService(world.session)
    with pytest.raises(ValueError):
        PurchaseCreate(
            kind="expense_note",  # type: ignore[arg-type]
            category_id=world.category,
            date=TODAY,
            description="x",
            amount=Decimal("1.00"),
        )
    with pytest.raises(Conflict, match="non si cambia da qui"):
        purchases.update(read.purchase_id, PurchaseUpdate(description="x"), world.admin)
    with pytest.raises(Conflict, match="non si cambia da qui"):
        purchases.cancel(read.purchase_id, world.admin)
    with pytest.raises(Conflict, match="non si cambia da qui"):
        purchases.soft_delete(read.purchase_id, world.admin)


# ---- reimburse ----------------------------------------------------------------------------


def test_a_transfer_is_dated_settled_like_any_due_item_and_reimburses_once(world: World) -> None:
    service = ExpenseNoteService(world.session)
    approved = service.approve_expense_note(world.submit(), world.admin)
    chosen = service.reimburse_expense_note(
        approved.id, ExpenseNoteReimburse(method="transfer", date=NEXT_MONTH), world.other_admin
    )
    # The way is chosen and the item dated; nothing was paid, so the note is still approved.
    assert chosen.status == "approved" and chosen.reimbursement_method == "transfer"
    assert chosen.reimbursement_date == NEXT_MONTH and chosen.reimbursed_on is None
    assert world.session.get(DueItem, approved.due_item_id).due_date == NEXT_MONTH  # type: ignore[union-attr]

    # Paid like any other outgoing item: the person's mark, an administrator's act.
    detail = DueItemService(world.session).settle(
        approved.due_item_id,  # type: ignore[arg-type]
        SettlementCreate(amount=Decimal("42.50"), settled_on=TODAY),
        world.admin,
    )
    assert detail.state == "settled"
    paid = service.get_expense_note(approved.id, world.person)
    assert paid.status == "reimbursed" and paid.reimbursed_on == TODAY
    assert paid.reimbursement_method == "transfer"

    # Reimbursed once: a second reimbursement is refused and no second item is written.
    with pytest.raises(Conflict, match="gia' rimborsata"):
        service.reimburse_expense_note(
            approved.id, ExpenseNoteReimburse(method="transfer", date=TODAY), world.admin
        )
    assert len(_items(world.session, approved.purchase_id)) == 1  # type: ignore[arg-type]

    # Taking the payment back reopens the note, with its way.
    settlement = DueItemService(world.session).get(approved.due_item_id, world.admin).settlements[0]  # type: ignore[arg-type]
    DueItemService(world.session).reverse_settlement(settlement.id, "errore", world.admin)
    reopened = service.get_expense_note(approved.id, world.admin)
    assert reopened.status == "approved" and reopened.reimbursed_on is None
    assert reopened.reimbursement_method == "transfer"


def test_a_transfer_that_already_left_is_settled_in_the_same_act(world: World) -> None:
    service = ExpenseNoteService(world.session)
    approved = service.approve_expense_note(world.submit(), world.admin)
    read = service.reimburse_expense_note(
        approved.id,
        ExpenseNoteReimburse(method="transfer", date=TODAY, paid=True),
        world.other_admin,
    )
    assert read.status == "reimbursed" and read.reimbursed_on == TODAY
    item = world.session.get(DueItem, approved.due_item_id)
    assert item is not None and item.state == "settled"
    with pytest.raises(ValidationFailed, match="non e' ancora partito"):
        other = service.approve_expense_note(world.submit(description="Altra"), world.admin)
        service.reimburse_expense_note(
            other.id,
            ExpenseNoteReimburse(method="transfer", date=NEXT_MONTH, paid=True),
            world.admin,
        )


def test_a_note_paid_before_its_way_was_chosen_reads_as_a_transfer(world: World) -> None:
    service = ExpenseNoteService(world.session)
    approved = service.approve_expense_note(world.submit(), world.admin)
    DueItemService(world.session).settle(
        approved.due_item_id,  # type: ignore[arg-type]
        SettlementCreate(amount=Decimal("42.50"), settled_on=TODAY),
        world.admin,
    )
    read = service.get_expense_note(approved.id, world.admin)
    assert read.status == "reimbursed" and read.reimbursement_method == "transfer"
    assert read.reimbursement_date == TODAY


def test_a_way_is_chosen_for_an_approved_note_only_and_can_change_until_paid(
    world: World,
) -> None:
    service = ExpenseNoteService(world.session)
    note_id = world.submit()
    with pytest.raises(Conflict, match="si rimborsa una nota approvata"):
        service.reimburse_expense_note(
            note_id, ExpenseNoteReimburse(method="transfer", date=TODAY), world.admin
        )
    service.approve_expense_note(note_id, world.admin)
    service.reimburse_expense_note(
        note_id, ExpenseNoteReimburse(method="transfer", date=NEXT_MONTH), world.other_admin
    )
    again = service.reimburse_expense_note(
        note_id, ExpenseNoteReimburse(method="payslip"), world.other_admin
    )
    assert again.reimbursement_method == "payslip" and again.payroll_month is not None
    assert again.status == "approved"
    # Once a payment was recorded the way no longer changes.
    DueItemService(world.session).settle(
        again.due_item_id,  # type: ignore[arg-type]
        SettlementCreate(amount=Decimal("10.00"), settled_on=TODAY),
        world.admin,
    )
    with pytest.raises(Conflict, match="il modo non si cambia"):
        service.reimburse_expense_note(
            note_id, ExpenseNoteReimburse(method="transfer", date=TODAY), world.other_admin
        )


def test_an_administrator_does_not_reimburse_their_own_note_unless_they_are_the_only_one(
    world: World,
) -> None:
    service = ExpenseNoteService(world.session)
    note = service.submit_expense_note(world.note(staff_member_id=world.no_user.id), world.admin)
    service.approve_expense_note(note.id, world.other_admin)
    with pytest.raises(OwnExpenseNote):
        service.reimburse_expense_note(
            note.id, ExpenseNoteReimburse(method="transfer", date=TODAY), world.admin
        )
    read = service.reimburse_expense_note(
        note.id, ExpenseNoteReimburse(method="transfer", date=TODAY), world.other_admin
    )
    assert read.reimbursement_method == "transfer"


def test_a_payslip_settles_the_notes_item_and_adds_no_second_due_item(world: World) -> None:
    service = ExpenseNoteService(world.session)
    approved = service.approve_expense_note(world.submit(), world.admin)
    # The way: the person's next month with no payslip, dated by the pay day (the 27th).
    chosen = service.reimburse_expense_note(
        approved.id, ExpenseNoteReimburse(method="payslip"), world.other_admin
    )
    assert chosen.reimbursement_method == "payslip"
    assert chosen.payroll_month == THIS_MONTH
    assert chosen.reimbursement_date == THIS_MONTH.replace(day=27)
    assert chosen.status == "approved"
    assert world.session.get(DueItem, approved.due_item_id).due_date == chosen.reimbursement_date  # type: ignore[union-attr]
    # Choosing it again for a month that already has its payslip is a refusal below; first the
    # payslip arrives and is recorded by a person.
    purchase_items_before = world.session.execute(select(DueItem.id)).scalars().all()
    StaffService(world.session).record_staff_payslip(
        world.member.id,
        PayslipFigures(
            period=THIS_MONTH,
            gross=Decimal("2142.86"),
            net=Decimal("1607.15"),
            withholding=Decimal("321.43"),
        ),
        world.admin,
    )
    read = service.get_expense_note(approved.id, world.person)
    assert read.status == "reimbursed" and read.reimbursement_method == "payslip"
    assert read.reimbursed_on is not None and read.reimbursed_on <= TODAY
    item = world.session.get(DueItem, approved.due_item_id)
    assert item is not None and item.state == "settled"
    # No second due item for the reimbursement: the purchase still owns exactly one, and the
    # payslip's own items are the net pay and the withholding, never a reimbursement.
    assert len(_items(world.session, approved.purchase_id)) == 1  # type: ignore[arg-type]
    written = set(world.session.execute(select(DueItem.id)).scalars().all()) - set(
        purchase_items_before
    )
    kinds = {
        row.source_kind
        for row in world.session.execute(select(DueItem).where(DueItem.id.in_(written))).scalars()
    }
    assert "purchase" not in kinds
    # And the note cannot be reimbursed again.
    with pytest.raises(Conflict, match="gia' rimborsata"):
        service.reimburse_expense_note(
            approved.id, ExpenseNoteReimburse(method="payslip"), world.admin
        )


def test_a_payslip_month_with_its_payslip_already_recorded_is_not_chosen(world: World) -> None:
    service = ExpenseNoteService(world.session)
    approved = service.approve_expense_note(world.submit(), world.admin)
    StaffService(world.session).record_staff_payslip(
        world.member.id,
        PayslipFigures(
            period=THIS_MONTH,
            gross=Decimal("2142.86"),
            net=Decimal("1607.15"),
            withholding=Decimal("321.43"),
        ),
        world.admin,
    )
    with pytest.raises(Conflict, match="cedolino di quel mese"):
        service.reimburse_expense_note(
            approved.id,
            ExpenseNoteReimburse(method="payslip", payroll_month=THIS_MONTH),
            world.other_admin,
        )
    # Left out, it is the next month with no payslip.
    read = service.reimburse_expense_note(
        approved.id, ExpenseNoteReimburse(method="payslip"), world.other_admin
    )
    assert read.payroll_month == NEXT_MONTH


def test_the_summary_counts_each_tab_and_the_money_still_to_be_paid_back(world: World) -> None:
    service = ExpenseNoteService(world.session)
    waiting = world.submit(amount=Decimal("10.00"))
    approved = service.approve_expense_note(world.submit(amount=Decimal("20.00")), world.admin)
    rejected = world.submit(amount=Decimal("30.00"))
    service.reject_expense_note(rejected, ExpenseNoteReject(reason="No"), world.admin)
    summary = service.summarize_expense_notes(world.person)
    assert (summary.to_approve, summary.to_reimburse, summary.closed) == (1, 1, 1)
    assert summary.awaiting_reimbursement == Decimal("30.00")
    assert summary.staff_member_id == world.member.id and summary.can_submit is True
    assert summary.only_administrator is False
    bucket = service.list_expense_notes(ExpenseNoteListQuery(bucket="to_reimburse"), world.admin)
    assert [i.id for i in bucket.items] == [approved.id]
    assert service.get_expense_note(waiting, world.admin).status == "submitted"


# ---- privacy and the other doors ------------------------------------------------------------


def test_the_purchase_of_a_note_is_read_by_its_staff_member_and_administrators_only(
    world: World,
) -> None:
    from pigrocrm.core.purchases.schemas import PurchaseListQuery

    read = ExpenseNoteService(world.session).approve_expense_note(world.submit(), world.admin)
    purchases = PurchaseService(world.session)
    assert read.purchase_id is not None
    # The person it pays back and the administrators read it; every other member does not.
    for actor in (world.person, world.admin):
        assert [p.id for p in purchases.list(PurchaseListQuery(), actor).items] == [
            read.purchase_id
        ]
        assert purchases.get(read.purchase_id, actor).staff_member_id == world.member.id
    other = _actor(_user(world.session, "collaborator", "Altro"))
    readonly = _actor(_user(world.session, "readonly", "Lettore"))
    for actor in (other, readonly):
        page = purchases.list(PurchaseListQuery(), actor)
        assert page.items == [] and page.total_count == 0
        with pytest.raises(NotFound):
            purchases.get(read.purchase_id, actor)
    token = Actor(id=world.admin_user.id, type="mcp", role="admin")
    assert purchases.list(PurchaseListQuery(), token).items == []


def test_the_author_cannot_pay_their_own_notes_due_item_through_the_generic_settle(
    world: World,
) -> None:
    service = ExpenseNoteService(world.session)
    note = service.submit_expense_note(world.note(staff_member_id=world.no_user.id), world.admin)
    approved = service.approve_expense_note(note.id, world.other_admin)
    payment = SettlementCreate(amount=Decimal("42.50"), settled_on=TODAY)
    with pytest.raises(OwnExpenseNote):
        DueItemService(world.session).settle(approved.due_item_id, payment, world.admin)  # type: ignore[arg-type]
    DueItemService(world.session).settle(approved.due_item_id, payment, world.other_admin)  # type: ignore[arg-type]
    assert service.get_expense_note(note.id, world.other_admin).status == "reimbursed"
