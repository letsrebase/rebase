"""Matching a bank movement to the due item it pays (REB-1042, spec 2026-11-09, due items and the
cash schedule, § 3): proposed from the amount, the counterparty and the references, confirmed by
a person who settles the item with the movement as evidence, undone by a person who reopens it."""

import datetime as dt
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fakes.cash_fixtures import ADMIN, AGENT, AGENT_FULL, COLLABORATOR, CashWorld
from fakes.customer_fixtures import make_customer
from fakes.invoice_fixtures import unpaid_invoice
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.bank import match_rank as rank
from pigrocrm.core.bank.match_schemas import (
    MatchConfirm,
    MatchProposalCreate,
    MatchSelection,
    MatchUndo,
)
from pigrocrm.core.bank.matching import MovementMatchService
from pigrocrm.core.bank.models import (
    BankAccount,
    BankMovement,
    MovementMatch,
    OrganizationIban,
)
from pigrocrm.core.bank.readers import ParsedMovement, ParsedStatement
from pigrocrm.core.bank.service import BankStatementService
from pigrocrm.core.bank.statements import StatementImportService
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.schemas import DueItemCreate, SettlementCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.schemas import PaymentState
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage.local import LocalFileStorage

D = dt.date
TODAY = today_local()
CAMT = Path(__file__).parent / "fixtures" / "camt053" / "statement_001_02.xml"
CAMT_IBAN = "IT60X0542811101000000123456"


def _services(session: Session) -> tuple[MovementMatchService, DueItemService]:
    return MovementMatchService(session), DueItemService(session)


def _movement(
    session: Session,
    account: BankAccount,
    amount: str,
    *,
    description: str | None = None,
    counterparty: str | None = None,
    iban: str | None = None,
    day: dt.date | None = None,
    reference: str | None = None,
    end_to_end_id: str | None = None,
) -> BankMovement:
    """A movement read by the importer, which is the only way one is stored."""
    parsed = ParsedStatement(
        format="test",
        movements=[
            ParsedMovement(
                booking_date=day or TODAY - dt.timedelta(days=2),
                amount=Decimal(amount),
                currency="EUR",
                counterparty_name=counterparty,
                counterparty_iban=iban,
                description=description,
                bank_reference=reference or uuid4().hex,
                end_to_end_id=end_to_end_id,
            )
        ],
        period_from=TODAY - dt.timedelta(days=30),
        period_to=TODAY,
    )
    result = BankStatementService(session).import_statement(account.id, parsed)
    (movement_id,) = result.new_movement_ids
    movement = session.get(BankMovement, movement_id)
    assert movement is not None
    return movement


def _invoice_item(
    session: Session,
    world: CashWorld,
    *,
    number: int,
    total: str = "1220.00",
    year: int = 2026,
    customer: Organization | None = None,
) -> tuple[Invoice, DueItem]:
    invoice = unpaid_invoice(
        session,
        due=TODAY + dt.timedelta(days=10),
        number=number,
        year=year,
        total=Decimal(total),
        customer=customer or world.customer,
    )
    item = DueItemService(session).repo.by_source("sales_invoice", invoice.id)
    assert item is not None
    return invoice, item


@pytest.fixture
def world(db_session: Session) -> CashWorld:
    return CashWorld(db_session)


@pytest.fixture
def account(world: CashWorld) -> BankAccount:
    return world.account("1000.00")


# -- proposing: what is ranked, and what the importer proposes on its own -------------------


def test_the_import_of_a_statement_proposes_the_invoice_its_movement_pays(
    db_session: Session, world: CashWorld
) -> None:
    """The card's acceptance: a statement whose movement pays an open sales invoice proposes
    that invoice (amount, counterparty and `12/2026` in the description), and nothing is paid."""
    account = world.account("1000.00")
    account.iban = CAMT_IBAN
    db_session.flush()
    invoice, item = _invoice_item(db_session, world, number=12)

    result = StatementImportService(db_session).import_file(account.id, CAMT.read_bytes(), ADMIN)

    assert result.proposed == 1
    movement = db_session.scalars(
        select(BankMovement).where(BankMovement.amount == Decimal("1220.00"))
    ).one()
    detail = MovementMatchService(db_session).get_movement(movement.id, ADMIN)
    match = detail.movement.match
    assert match is not None and match.state == "proposed" and match.proposed_by_type == "system"
    assert match.is_current
    assert [row.due_item_id for row in match.items] == [item.id]
    assert match.items[0].document_number == "2026/12"
    assert match.items[0].allocated == Decimal("1220.00")
    assert set(match.reasons) == {"exact_amount", "document_number", "counterparty_name"}
    assert detail.movement.state == "unmatched"
    db_session.refresh(item)
    db_session.refresh(invoice)
    assert item.state == "expected" and invoice.payment_status == "unpaid"
    assert DueItemService(db_session).repo.live_settlements(item.id) == []


def test_an_amount_alone_is_a_candidate_for_the_panel_and_never_a_proposal(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    _, item = _invoice_item(db_session, world, number=1)
    movement = _movement(
        db_session, account, "1220.00", description="Bonifico", counterparty="Zeta"
    )
    service, _ = _services(db_session)

    assert service.propose_for_import([movement.id]) == 0
    detail = service.get_movement(movement.id, ADMIN)
    assert detail.movement.match is None
    assert [c.items[0].due_item_id for c in detail.candidates] == [item.id]
    assert detail.candidates[0].reasons == ["exact_amount"]
    assert detail.candidates[0].difference == Decimal("0.00")


def test_two_invoices_that_fit_equally_are_left_for_a_person(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    _invoice_item(db_session, world, number=1)
    _invoice_item(db_session, world, number=2)
    movement = _movement(
        db_session, account, "1220.00", description="Pagamento", counterparty="Acme S.r.l."
    )
    service, _ = _services(db_session)

    assert service.propose_for_import([movement.id]) == 0
    assert len(service.get_movement(movement.id, ADMIN).candidates) == 2


def test_the_document_number_decides_between_two_equal_invoices(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    _invoice_item(db_session, world, number=1)
    _, second = _invoice_item(db_session, world, number=2)
    movement = _movement(
        db_session, account, "1220.00", description="Saldo fatt. 2/2026", counterparty="Acme"
    )
    service, _ = _services(db_session)

    assert service.propose_for_import([movement.id]) == 1
    match = service.get_movement(movement.id, ADMIN).movement.match
    assert match is not None and [row.due_item_id for row in match.items] == [second.id]


def test_a_known_iban_ranks_and_never_settles(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    other = make_customer(db_session, legal_entity_id=world.entity, legal_name="Beta spa")
    _invoice_item(db_session, world, number=1)
    _, beta = _invoice_item(db_session, world, number=2, customer=other)
    db_session.add(
        OrganizationIban(
            organization_id=other.id,
            iban="DE89370400440532013000",
            source="manual",
            state="accepted",
        )
    )
    db_session.flush()
    movement = _movement(
        db_session,
        account,
        "1220.00",
        description="Bonifico",
        counterparty="Mario Rossi",
        iban="DE89370400440532013000",
    )
    service, _ = _services(db_session)

    detail = service.get_movement(movement.id, ADMIN)
    assert detail.candidates[0].items[0].due_item_id == beta.id
    assert "known_iban" in detail.candidates[0].reasons
    assert service.propose_for_import([movement.id]) == 1
    db_session.refresh(beta)
    assert beta.state == "expected"


def test_a_proposed_iban_does_not_rank(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    other = make_customer(db_session, legal_entity_id=world.entity, legal_name="Beta spa")
    _invoice_item(db_session, world, number=2, customer=other)
    db_session.add(
        OrganizationIban(
            organization_id=other.id,
            iban="DE89370400440532013000",
            source="movement",
            state="proposed",
        )
    )
    db_session.flush()
    movement = _movement(
        db_session, account, "1220.00", description="x", iban="DE89370400440532013000"
    )
    candidates = MovementMatchService(db_session).get_movement(movement.id, ADMIN).candidates
    assert candidates[0].reasons == ["exact_amount"]


def test_two_invoices_of_one_customer_that_add_up_are_a_split_candidate(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    _, first = _invoice_item(db_session, world, number=35, total="600.00")
    _, second = _invoice_item(db_session, world, number=36, total="620.00")
    _invoice_item(db_session, world, number=37, total="333.00")
    movement = _movement(
        db_session, account, "1220.00", description="Fatture 2026/35 e 2026/36", counterparty="Acme"
    )
    service, _ = _services(db_session)

    detail = service.get_movement(movement.id, ADMIN)
    best = detail.candidates[0]
    assert {row.due_item_id for row in best.items} == {first.id, second.id}
    assert "items_total" in best.reasons and "document_number" in best.reasons
    assert best.allocated == Decimal("1220.00") and best.difference == Decimal("0.00")
    assert service.propose_for_import([movement.id]) == 1


def test_a_movement_in_another_currency_direction_or_company_is_not_a_candidate(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    world.item("1220.00", TODAY, direction="out", source_kind="purchase")
    world.item("1220.00", TODAY, currency="USD")
    world.item("1220.00", TODAY, entity=world.second_entity())
    movement = _movement(db_session, account, "1220.00", description="x", counterparty="Acme")
    assert MovementMatchService(db_session).get_movement(movement.id, ADMIN).candidates == []


def test_the_open_items_are_searched_by_counterparty_and_by_number(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    _invoice_item(db_session, world, number=7, total="50.00")
    _invoice_item(db_session, world, number=8, total="60.00")
    movement = _movement(db_session, account, "1220.00", description="x")
    service, _ = _services(db_session)

    assert len(service.list_open_items(movement.id, ADMIN)) == 2
    assert [
        i.document_number for i in service.list_open_items(movement.id, ADMIN, search="2026/8")
    ] == ["2026/8"]
    assert service.list_open_items(movement.id, ADMIN, search="nobody") == []
    with pytest.raises(ValidationFailed):
        service.list_open_items(movement.id, ADMIN, limit=0)


# -- confirming: the item is settled with the movement as evidence --------------------------


def test_confirming_settles_the_invoice_with_the_movement_attached(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    invoice, item = _invoice_item(db_session, world, number=12)
    movement = _movement(
        db_session,
        account,
        "1220.00",
        description="Fattura 12/2026",
        counterparty="Acme",
        day=TODAY - dt.timedelta(days=3),
    )
    service, due = _services(db_session)
    service.propose_for_import([movement.id])

    detail = service.confirm(movement.id, MatchConfirm(due_item_ids=[item.id]), ADMIN)

    assert detail.movement.state == "matched"
    assert detail.movement.match is not None and detail.movement.match.state == "confirmed"
    assert detail.movement.match.confirmed_by_type == "user"
    assert detail.candidates == []
    db_session.refresh(item)
    db_session.refresh(invoice)
    assert item.state == "settled"
    assert invoice.payment_status == "paid" and invoice.payment_date == movement.booking_date
    (settlement,) = due.repo.live_settlements(item.id)
    assert settlement.bank_movement_id == movement.id
    assert settlement.settled_on == movement.booking_date
    assert settlement.amount == Decimal("1220.00")
    read = due.get(item.id, ADMIN)
    assert read.settlements[0].bank_movement_id == movement.id
    # The proposal the importer wrote became the confirmed match, one live row.
    assert db_session.scalars(select(MovementMatch)).all()[0].proposed_by_type == "system"
    assert len(db_session.scalars(select(MovementMatch)).all()) == 1


def test_undoing_a_confirmed_match_reopens_the_item_and_keeps_the_trace(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    invoice, item = _invoice_item(db_session, world, number=12)
    movement = _movement(db_session, account, "1220.00", description="Fattura 12/2026")
    service, due = _services(db_session)
    service.confirm(movement.id, MatchConfirm(due_item_ids=[item.id]), ADMIN)

    detail = service.undo(movement.id, MatchUndo(reason="era un altro cliente"), ADMIN)

    assert detail.movement.state == "unmatched" and detail.movement.match is None
    assert [m.state for m in detail.history] == ["undone"]
    assert detail.history[0].close_reason == "era un altro cliente"
    db_session.refresh(item)
    db_session.refresh(invoice)
    assert item.state == "expected" and invoice.payment_status == "unpaid"
    assert due.repo.live_settlements(item.id) == []
    (settlement,) = due.repo.settlements(item.id)
    assert settlement.reversed_at is not None and settlement.bank_movement_id == movement.id
    # And it can be matched again, to the same item.
    again = service.confirm(movement.id, MatchConfirm(due_item_ids=[item.id]), ADMIN)
    assert again.movement.state == "matched"
    assert len(due.repo.live_settlements(item.id)) == 1


def test_an_agent_proposes_a_match_and_only_a_person_confirms_or_undoes_it(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    _, item = _invoice_item(db_session, world, number=12)
    movement = _movement(db_session, account, "1220.00", description="Bonifico")
    service, _ = _services(db_session)

    detail = service.propose_match(
        movement.id, MatchProposalCreate(due_item_ids=[item.id], note="stesso importo"), AGENT_FULL
    )
    match = detail.movement.match
    assert match is not None and match.state == "proposed" and match.proposed_by_type == "mcp"
    assert match.note == "stesso importo"
    db_session.refresh(item)
    assert item.state == "expected"

    with pytest.raises(AgentForbidden):
        service.confirm(movement.id, MatchConfirm(due_item_ids=[item.id]), AGENT_FULL)
    with pytest.raises(AgentForbidden):
        service.propose_match(movement.id, MatchProposalCreate(due_item_ids=[item.id]), AGENT)
    with pytest.raises(AgentForbidden):
        service.get_movement(movement.id, AGENT)
    with pytest.raises(PermissionDenied):
        service.confirm(movement.id, MatchConfirm(due_item_ids=[item.id]), COLLABORATOR)
    service.confirm(movement.id, MatchConfirm(due_item_ids=[item.id]), ADMIN)
    with pytest.raises(AgentForbidden):
        service.undo(movement.id, MatchUndo(), AGENT_FULL)
    with pytest.raises(PermissionDenied):
        service.undo(movement.id, MatchUndo(), COLLABORATOR)


def test_a_new_proposal_replaces_the_waiting_one_and_a_matched_movement_takes_none(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    _, first = _invoice_item(db_session, world, number=1)
    _, second = _invoice_item(db_session, world, number=2)
    movement = _movement(db_session, account, "1220.00", description="Bonifico")
    service, _ = _services(db_session)
    service.propose_match(movement.id, MatchProposalCreate(due_item_ids=[first.id]), AGENT_FULL)

    detail = service.propose_match(
        movement.id, MatchProposalCreate(due_item_ids=[second.id]), AGENT_FULL
    )

    assert detail.movement.match is not None
    assert [row.due_item_id for row in detail.movement.match.items] == [second.id]
    assert [m.state for m in detail.history] == ["dismissed"]
    # A person's choice of the other item dismisses the waiting proposal too.
    service.confirm(movement.id, MatchConfirm(due_item_ids=[first.id]), ADMIN)
    with pytest.raises(Conflict):
        service.propose_match(
            movement.id, MatchProposalCreate(due_item_ids=[second.id]), AGENT_FULL
        )
    with pytest.raises(Conflict):
        service.confirm(movement.id, MatchConfirm(due_item_ids=[second.id]), ADMIN)
    with pytest.raises(Conflict):
        service.undo(_movement(db_session, account, "5.00").id, MatchUndo(), ADMIN)


def test_two_invoices_are_settled_by_one_movement(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    _, first = _invoice_item(db_session, world, number=35, total="600.00")
    _, second = _invoice_item(db_session, world, number=36, total="620.00")
    movement = _movement(db_session, account, "1220.00", description="Fatture 2026/35 e 36")
    service, due = _services(db_session)

    detail = service.confirm(movement.id, MatchConfirm(due_item_ids=[second.id, first.id]), ADMIN)

    assert detail.movement.state == "matched"
    for item in (first, second):
        db_session.refresh(item)
        assert item.state == "settled"
        assert due.repo.live_settlements(item.id)[0].bank_movement_id == movement.id
    service.undo(movement.id, MatchUndo(), ADMIN)
    for item in (first, second):
        db_session.refresh(item)
        assert item.state == "expected"


def test_a_selection_must_cover_the_movement_and_a_partial_payment_is_confirmed_on_purpose(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    _, item = _invoice_item(db_session, world, number=12)
    service, due = _services(db_session)

    short = _movement(db_session, account, "1500.00", description="Bonifico")
    preview = service.preview(short.id, MatchSelection(due_item_ids=[item.id]), ADMIN)
    assert (preview.allocated, preview.difference, preview.can_confirm) == (
        Decimal("1220.00"),
        Decimal("280.00"),
        False,
    )
    with pytest.raises(ValidationFailed):
        service.confirm(short.id, MatchConfirm(due_item_ids=[item.id]), ADMIN)

    part = _movement(db_session, account, "1000.00", description="Acconto 2026/12")
    preview = service.preview(part.id, MatchSelection(due_item_ids=[item.id]), ADMIN)
    assert preview.partial and preview.can_confirm and preview.difference == Decimal("0.00")
    assert preview.items[0].allocated == Decimal("1000.00")
    assert preview.items[0].remaining == Decimal("1220.00")
    with pytest.raises(ValidationFailed):
        service.confirm(part.id, MatchConfirm(due_item_ids=[item.id]), ADMIN)
    service.confirm(part.id, MatchConfirm(due_item_ids=[item.id], accept_partial=True), ADMIN)

    db_session.refresh(item)
    assert item.state == "expected"
    assert due.repo.totals(item.id).settled == Decimal("1000.00")
    # What remains is 220.00: a movement of 220.00 pays it in full.
    rest = _movement(db_session, account, "220.00", description="Saldo 2026/12")
    assert service.get_movement(rest.id, ADMIN).candidates[0].items[0].remaining == Decimal(
        "220.00"
    )
    service.confirm(rest.id, MatchConfirm(due_item_ids=[item.id]), ADMIN)
    db_session.refresh(item)
    assert item.state == "settled"
    # Undoing the first reverses only its own payment.
    service.undo(part.id, MatchUndo(), ADMIN)
    assert due.repo.totals(item.id).settled == Decimal("220.00")


def test_an_item_that_cannot_take_the_movement_is_refused(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    out = world.item("1220.00", TODAY, direction="out", source_kind="purchase")
    usd = world.item("1220.00", TODAY, currency="USD")
    foreign = world.item("1220.00", TODAY, entity=world.second_entity())
    forecast = world.item("1220.00", TODAY, state="forecast")
    _, ok = _invoice_item(db_session, world, number=1)
    movement = _movement(db_session, account, "1220.00", description="Bonifico")
    service, _ = _services(db_session)

    for bad in (out, usd, foreign):
        with pytest.raises(ValidationFailed):
            service.preview(movement.id, MatchSelection(due_item_ids=[bad]), ADMIN)
    with pytest.raises(Conflict):
        service.preview(movement.id, MatchSelection(due_item_ids=[forecast]), ADMIN)
    with pytest.raises(NotFound):
        service.preview(movement.id, MatchSelection(due_item_ids=[uuid4()]), ADMIN)
    with pytest.raises(ValidationFailed):
        # The movement is already covered by the first item, so a second one has nothing to take.
        _, another = _invoice_item(db_session, world, number=2)
        service.preview(movement.id, MatchSelection(due_item_ids=[ok.id, another.id]), ADMIN)
    with pytest.raises(ValidationError):
        MatchSelection(due_item_ids=[ok.id, ok.id])
    with pytest.raises(NotFound):
        service.get_movement(uuid4(), ADMIN)


def test_a_module_that_is_off_leaves_its_items_out_of_the_candidates(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    world.item("30.00", TODAY, direction="out", source_kind="purchase")
    movement = _movement(db_session, account, "-30.00", description="Saldo")
    service, _ = _services(db_session)

    assert (
        len(service.get_movement(movement.id, ADMIN, modules={"cash"} | {"purchases"}).candidates)
        == 1
    )
    assert service.get_movement(movement.id, ADMIN, modules={"cash"}).candidates == []


def test_money_out_pays_an_outgoing_item_and_is_an_administrators_act(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    supplier = make_customer(db_session, legal_entity_id=world.entity, legal_name="Fornitore SpA")
    item_id = (
        DueItemService(db_session)
        .create(
            DueItemCreate(
                legal_entity_id=world.entity,
                direction="out",
                organization_id=supplier.id,
                amount=Decimal("30.00"),
                source_kind="purchase",
                source_id=uuid4(),
                own_date=TODAY,
            ),
            check_organization=False,
        )
        .id
    )
    movement = _movement(
        db_session,
        account,
        "-30.00",
        description="Saldo fattura F-77",
        counterparty="Fornitore SpA",
    )
    service, due = _services(db_session)
    assert service.propose_for_import([movement.id]) == 1

    with pytest.raises(PermissionDenied):
        service.confirm(movement.id, MatchConfirm(due_item_ids=[item_id]), COLLABORATOR)
    service.confirm(movement.id, MatchConfirm(due_item_ids=[item_id]), ADMIN)

    item = db_session.get(DueItem, item_id)
    assert item is not None and item.state == "settled"
    (settlement,) = due.repo.live_settlements(item_id)
    assert settlement.amount == Decimal("30.00") and settlement.bank_movement_id == movement.id
    service.undo(movement.id, MatchUndo(), ADMIN)
    db_session.refresh(item)
    assert item.state == "expected"


# -- a payment marked by hand is adopted, not doubled --------------------------------------


def test_a_movement_attaches_to_the_payment_already_marked_by_hand(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    invoice, item = _invoice_item(db_session, world, number=12)
    service, due = _services(db_session)
    due.settle(
        item.id,
        SettlementCreate(amount=Decimal("1220.00"), settled_on=TODAY - dt.timedelta(days=4)),
        COLLABORATOR,
    )
    movement = _movement(
        db_session, account, "1220.00", description="Fattura 12/2026", counterparty="Acme"
    )

    detail = service.get_movement(movement.id, ADMIN)
    assert "already_marked_paid" in detail.candidates[0].reasons
    assert detail.candidates[0].items[0].adopts_payment
    assert service.propose_for_import([movement.id]) == 1

    confirmed = service.confirm(movement.id, MatchConfirm(due_item_ids=[item.id]), ADMIN)

    assert confirmed.movement.match is not None and confirmed.movement.match.items[0].adopts_payment
    (settlement,) = due.repo.settlements(item.id)
    assert settlement.bank_movement_id == movement.id and settlement.reversed_at is None
    assert settlement.recorded_by_type == "user"
    # Undoing lets go of the evidence and leaves the person's payment as it was.
    service.undo(movement.id, MatchUndo(), ADMIN)
    db_session.refresh(settlement)
    db_session.refresh(item)
    assert settlement.bank_movement_id is None and settlement.reversed_at is None
    assert item.state == "settled"


def test_a_payment_marked_by_hand_a_year_away_is_not_adopted(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    _, item = _invoice_item(db_session, world, number=12)
    service, due = _services(db_session)
    due.settle(
        item.id,
        SettlementCreate(amount=Decimal("1220.00"), settled_on=TODAY - dt.timedelta(days=200)),
        COLLABORATOR,
    )
    movement = _movement(db_session, account, "1220.00", description="Fattura 12/2026")
    # Not suggested: a movement months away from a payment of the same amount is a coincidence.
    assert service.get_movement(movement.id, ADMIN).candidates == []


# -- the evidence cannot be taken back from the side -------------------------------------------


def test_a_payment_a_movement_proves_is_taken_back_only_through_its_match(
    db_session: Session, world: CashWorld, account: BankAccount, local_storage: LocalFileStorage
) -> None:
    invoice, item = _invoice_item(db_session, world, number=12)
    movement = _movement(db_session, account, "1220.00", description="Fattura 12/2026")
    service, due = _services(db_session)
    service.confirm(movement.id, MatchConfirm(due_item_ids=[item.id]), ADMIN)
    (settlement,) = due.repo.live_settlements(item.id)

    with pytest.raises(Conflict):
        due.reverse_settlement(settlement.id, None, COLLABORATOR)
    with pytest.raises(Conflict):
        InvoiceService(db_session, local_storage).set_payment_state(
            invoice.id, PaymentState(payment_status="unpaid"), ADMIN
        )
    db_session.refresh(item)
    assert item.state == "settled"


def test_every_settlement_read_names_its_movement_or_none(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    _, item = _invoice_item(db_session, world, number=12)
    service, due = _services(db_session)
    due.settle(item.id, SettlementCreate(amount=Decimal("20.00"), settled_on=TODAY), COLLABORATOR)
    assert due.get(item.id, ADMIN).settlements[0].bank_movement_id is None


# -- reverting a statement --------------------------------------------------------------------


def test_reverting_a_statement_drops_its_proposals_and_a_matched_movement_blocks_it(
    db_session: Session, world: CashWorld
) -> None:
    account = world.account("1000.00")
    account.iban = CAMT_IBAN
    db_session.flush()
    _, item = _invoice_item(db_session, world, number=12)
    imports = StatementImportService(db_session)
    first = imports.import_file(account.id, CAMT.read_bytes(), ADMIN)
    assert first.proposed == 1
    assert db_session.scalars(select(MovementMatch)).all()

    imports.revert_statement(first.statement.id, ADMIN)
    assert db_session.scalars(select(MovementMatch)).all() == []

    second = imports.import_file(account.id, CAMT.read_bytes(), ADMIN)
    movement = db_session.scalars(
        select(BankMovement).where(BankMovement.amount == Decimal("1220.00"))
    ).one()
    service = MovementMatchService(db_session)
    service.confirm(movement.id, MatchConfirm(due_item_ids=[item.id]), ADMIN)
    with pytest.raises(Conflict):
        imports.revert_statement(second.statement.id, ADMIN)
    # Unmatched again, the movement still carries the trace of its payment and still blocks it.
    service.undo(movement.id, MatchUndo(), ADMIN)
    with pytest.raises(Conflict):
        imports.revert_statement(second.statement.id, ADMIN)


def test_a_movement_of_a_second_import_is_not_proposed_twice(
    db_session: Session, world: CashWorld
) -> None:
    account = world.account("1000.00")
    account.iban = CAMT_IBAN
    db_session.flush()
    _invoice_item(db_session, world, number=12)
    imports = StatementImportService(db_session)
    imports.import_file(account.id, CAMT.read_bytes(), ADMIN)
    again = imports.import_file(account.id, CAMT.read_bytes(), ADMIN)
    assert again.added == 0 and again.proposed == 0
    assert len(db_session.scalars(select(MovementMatch)).all()) == 1


def test_a_proposal_goes_stale_when_its_item_is_paid_by_hand(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    _, item = _invoice_item(db_session, world, number=12)
    movement = _movement(db_session, account, "1220.00", description="Fattura 12/2026")
    service, due = _services(db_session)
    service.propose_match(movement.id, MatchProposalCreate(due_item_ids=[item.id]), AGENT_FULL)
    # Paid in part by hand: the proposal no longer fits what is owed.
    due.settle(
        item.id,
        SettlementCreate(amount=Decimal("1000.00"), settled_on=TODAY - dt.timedelta(days=30)),
        COLLABORATOR,
    )
    assert service.get_movement(movement.id, ADMIN).movement.match is not None
    assert service.get_movement(movement.id, ADMIN).movement.match.is_current is False  # type: ignore[union-attr]


def test_the_actor_of_a_confirmation_is_on_the_match_and_the_timeline(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    from pigrocrm.core.activities.models import Activity

    person = Actor(id=uuid4(), type="user", role="admin")
    _, item = _invoice_item(db_session, world, number=12)
    movement = _movement(db_session, account, "1220.00", description="Fattura 12/2026")
    service, _ = _services(db_session)
    service.confirm(movement.id, MatchConfirm(due_item_ids=[item.id]), person)
    service.undo(movement.id, MatchUndo(), person)

    kinds = db_session.scalars(
        select(Activity.kind).where(Activity.entity_type == "bank_movement")
    ).all()
    assert sorted(kinds) == ["matched", "unmatched"]
    match = db_session.scalars(select(MovementMatch)).one()
    assert match.confirmed_by_id == person.id and match.closed_by_id == person.id


# -- the pure ranking --------------------------------------------------------------------------


def test_names_are_compared_by_their_identifying_words() -> None:
    assert rank.names_match("ACME SRL", "Acme S.r.l.")
    assert rank.names_match("Alfa Servizi", "Alfa Servizi S.r.l.")
    assert not rank.names_match("Alfa", "Beta Servizi")
    assert not rank.names_match(None, "Acme")
    assert rank.name_in_text("Bonifico a favore di Alfa Servizi", "Alfa Servizi S.r.l.")
    assert not rank.name_in_text("alfa", "Alfa Servizi")


def test_an_invoice_number_is_found_in_the_ways_a_payer_writes_it() -> None:
    for text in ("Fattura 2026/35", "FT 35/2026", "n. 0035-2026", "2026 / 35 saldo", "2026.35"):
        assert rank.invoice_mentioned(text, 2026, 35), text
    for text in ("Fattura 2026/350", "135/2026", "2026/3", "12026/35"):
        assert not rank.invoice_mentioned(text, 2026, 35), text


def test_a_date_in_the_bank_text_is_not_an_invoice_number() -> None:
    assert not rank.invoice_mentioned("Bonifico 10/05/2026", 2026, 5)
    assert not rank.invoice_mentioned("del 31.12.2026 saldo", 2026, 12)
    assert not rank.invoice_mentioned("data 2026-05-10", 2026, 5)
    assert rank.invoice_mentioned("Bonifico 10/05/2026 fattura 5/2026", 2026, 5)
    assert rank.invoice_mentioned("Fattura 2026/5 del 10/05/2026", 2026, 5)


def test_a_suppliers_own_number_is_evidence_only_when_it_is_long_enough() -> None:
    assert rank.text_mentions("Saldo fattura F-77 del mese", "F-77")
    assert rank.text_mentions("ft 1042/a", "FT-1042/A")
    assert not rank.text_mentions("Fattura 1", "1")
    assert not rank.text_mentions("Pagato il 21/04/2026", "104")
    assert not rank.text_mentions("FT123 saldo", "FT12")
    assert not rank.text_mentions("fattura 104", "104")
    assert not rank.text_mentions("x", None)


def test_a_split_is_searched_among_one_counterpartys_items() -> None:
    org = uuid4()
    fits = [
        rank.Fit(uuid4(), Decimal(amount), None, org, False, False, False)
        for amount in ("600.00", "620.00", "333.00", "10.00")
    ]
    found = rank.split_combinations(fits, Decimal("1220.00"))
    assert [sorted(f.remaining for f in combo) for combo in found] == [
        [Decimal("600.00"), Decimal("620.00")]
    ]
    assert rank.split_combinations(fits, Decimal("7.00")) == []
    assert rank.score(["exact_amount", "document_number"]) == 9


def test_one_import_proposes_an_item_to_one_movement_only(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    _, item = _invoice_item(db_session, world, number=12)
    first = _movement(
        db_session, account, "1220.00", description="Fattura 12/2026", counterparty="Acme"
    )
    second = _movement(
        db_session, account, "1220.00", description="Fattura 12/2026", counterparty="Acme"
    )
    service, _ = _services(db_session)

    assert service.propose_for_import([first.id, second.id]) == 1
    proposed = db_session.scalars(select(MovementMatch)).all()
    assert len(proposed) == 1 and proposed[0].state == "proposed"
    assert {row.due_item_id for row in service._items(proposed[0])} == {item.id}


def test_the_movement_attaches_to_the_hand_payment_nearest_its_booking(
    db_session: Session, world: CashWorld, account: BankAccount
) -> None:
    _, item = _invoice_item(db_session, world, number=12, total="2440.00")
    service, due = _services(db_session)
    for days in (60, 3):
        due.settle(
            item.id,
            SettlementCreate(amount=Decimal("1220.00"), settled_on=TODAY - dt.timedelta(days=days)),
            COLLABORATOR,
        )
    movement = _movement(db_session, account, "1220.00", description="Fattura 12/2026")
    service.confirm(movement.id, MatchConfirm(due_item_ids=[item.id]), ADMIN)
    evidenced = [s for s in due.repo.settlements(item.id) if s.bank_movement_id == movement.id]
    assert [s.settled_on for s in evidenced] == [TODAY - dt.timedelta(days=3)]
