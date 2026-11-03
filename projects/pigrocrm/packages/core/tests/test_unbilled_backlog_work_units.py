"""REB-372: `unbilled_backlog`'s own `work_units` contribution, narrowed to
approved-or-later time.

`TimeEntry` has no state machine at all (REB-352 §1.2) and keeps counting every
billable, unbilled hour unconditionally -- `test_unbilled_backlog.py` already pins
that half. `work_units` is the one source that carries an approval state
(REB-358/REB-359), so this file pins the one thing REB-372 adds: a day only
contributes to the committed backlog once it has left `proposed` for an
approved-or-later state, and a day worked without the approval its own contract
requires never contributes at all.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.timetracking.models import TimeEntry
from pigrocrm.core.work_units.models import WorkUnit

READONLY = Actor(id=uuid7(), type="user", role="readonly")


def _contract(db_session: Session, **overrides: object) -> Contract:
    customer = Customer(legal_name=f"ACME {uuid4()}")
    db_session.add(customer)
    db_session.flush()
    payload: dict[str, object] = {
        "customer_id": customer.id,
        "title": "Consulenza",
        "start": date(2026, 1, 1),
        "renewal_type": "none",
        "termination_notice_days": 30,
        "billing_cadence": "monthly",
        "expense_policy": {"kind": "not_reimbursable"},
    }
    payload.update(overrides)
    contract = Contract(**payload)  # type: ignore[arg-type]
    db_session.add(contract)
    db_session.flush()
    return contract


def _rate_card(db_session: Session, contract: Contract, **overrides: object) -> RateCard:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "valid_from": date(2026, 1, 1),
        "valid_to": None,
        "type": "daily",
        "amount": Decimal("500.00"),
        "unit": "day",
    }
    payload.update(overrides)
    rate_card = RateCard(**payload)  # type: ignore[arg-type]
    db_session.add(rate_card)
    db_session.flush()
    return rate_card


def _work_unit(db_session: Session, contract: Contract, **overrides: object) -> WorkUnit:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "data": date(2026, 3, 5),
        "quantity": Decimal("1.00"),
        "description": "Giornata di consulenza",
        "status": "worked",
    }
    payload.update(overrides)
    work_unit = WorkUnit(**payload)  # type: ignore[arg-type]
    db_session.add(work_unit)
    db_session.flush()
    # `work_unit_enforce_state_machine` can rewrite `status` in flight (the
    # `worked` -> `worked_without_approval` redirect, spec §5) -- refresh so
    # the returned object reflects what was actually written, not merely requested.
    db_session.refresh(work_unit)
    return work_unit


def _bare_invoice_line(db_session: Session, contract: Contract) -> InvoiceLine:
    invoice = Invoice(customer_id=contract.customer_id, type="invoice", status="draft")
    db_session.add(invoice)
    db_session.flush()
    line = InvoiceLine(
        invoice_id=invoice.id,
        line_number=1,
        description="Riga esistente",
        quantity=Decimal("1.000000"),
        unit_price=Decimal("1.000000"),
        total_price=Decimal("1.00"),
        vat_rate=Decimal("22.00"),
    )
    db_session.add(line)
    db_session.flush()
    return line


# ---- the Done-when, literally: approved counts, merely-logged does not -----------


def test_an_approved_day_counts_towards_the_backlog(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, status="approved")

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.accrued_value == Decimal("500.00")
    assert backlog.entries == 1


def test_a_merely_proposed_day_does_not_count(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, status="proposed")

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.accrued_value == Decimal("0.00")
    assert backlog.entries == 0


# ---- the rest of the state machine ------------------------------------------------


def test_a_worked_day_counts_the_same_as_an_approved_one(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, status="worked")

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.accrued_value == Decimal("500.00")
    assert backlog.entries == 1


def test_a_disputed_day_still_counts(db_session: Session) -> None:
    """`disputed` is later in the lifecycle than `approved`/`worked`, and mastro's
    own citation (spec §1.2) names it as one of the three qualifying states -- a
    dispute over an already-worked day does not retract the fact that the work
    happened and was billable."""
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, status="worked")
    db_session.flush()
    work_unit = db_session.query(WorkUnit).filter_by(contract_id=contract.id).one()
    work_unit.status = "disputed"
    db_session.flush()

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.accrued_value == Decimal("500.00")
    assert backlog.entries == 1


def test_to_day_marked_invoiced_with_no_invoice_line_does_not_count(db_session: Session) -> None:
    """`invoiced` says "already billed" on its own -- `WorkUnitService.transition`
    (the generic, already-public state mover) has no coupling to `invoice_line_id`
    at all, so a day can reach this state through it while the link stays NULL.
    Relying on the `invoice_line_id IS NULL` filter alone to keep such a row out of
    the backlog would be relying on an invariant this codebase does not enforce; the
    state itself has to be excluded."""
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, status="worked")
    db_session.flush()
    work_unit = db_session.query(WorkUnit).filter_by(contract_id=contract.id).one()
    work_unit.status = "invoiced"
    db_session.flush()
    assert work_unit.invoice_line_id is None

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.accrued_value == Decimal("0.00")
    assert backlog.entries == 0


def test_to_day_marked_paid_with_no_invoice_line_does_not_count(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, status="worked")
    db_session.flush()
    work_unit = db_session.query(WorkUnit).filter_by(contract_id=contract.id).one()
    work_unit.status = "invoiced"
    db_session.flush()
    work_unit.status = "paid"
    db_session.flush()
    assert work_unit.invoice_line_id is None

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.accrued_value == Decimal("0.00")
    assert backlog.entries == 0


def test_a_day_worked_without_the_required_approval_does_not_count(db_session: Session) -> None:
    """The trigger's own automatic redirect (REB-359 §5/§12): a `'worked'` write with
    no `approval_id`, on a contract that requires prior approval, is recorded as
    `worked_without_approval` rather than rejected outright -- and that flagged,
    unapproved branch is exactly the ambiguity this issue exists to keep out of the
    committed figure."""
    contract = _contract(db_session, requires_prior_approval=True)
    _rate_card(db_session, contract)
    work_unit = _work_unit(db_session, contract, status="worked", approval_id=None)
    assert work_unit.status == "worked_without_approval"

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.accrued_value == Decimal("0.00")
    assert backlog.entries == 0


def test_a_rejected_day_does_not_count(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, status="proposed")
    db_session.flush()
    work_unit = db_session.query(WorkUnit).filter_by(contract_id=contract.id).one()
    work_unit.status = "rejected"
    db_session.flush()

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.accrued_value == Decimal("0.00")
    assert backlog.entries == 0


def test_a_written_off_day_does_not_count(db_session: Session) -> None:
    """`non_billable` is real, recorded work -- just declared unbillable, the
    opposite of committed revenue."""
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, status="worked")
    db_session.flush()
    work_unit = db_session.query(WorkUnit).filter_by(contract_id=contract.id).one()
    work_unit.status = "non_billable"
    db_session.flush()

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.accrued_value == Decimal("0.00")
    assert backlog.entries == 0


def test_an_already_invoiced_day_is_not_double_counted(db_session: Session) -> None:
    """`invoice_line_id IS NULL` is the same "not yet invoiced" fact the `TimeEntry`
    half of this figure already keys on -- an approved day already bound to a line
    has left the backlog, whatever its own `status` says."""
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    existing_line = _bare_invoice_line(db_session, contract)
    _work_unit(db_session, contract, status="approved", invoice_line_id=existing_line.id)

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.accrued_value == Decimal("0.00")
    assert backlog.entries == 0


# ---- pricing, missing coverage, and composing with the TimeEntry half ------------


def test_a_day_with_no_covering_rate_card_counts_but_prices_at_nothing(
    db_session: Session,
) -> None:
    """A read-only aggregate must survive a coverage gap rather than raise --
    `unbilled_work_unit_lines`'s own `ValidationFailed` is the right refusal for an
    actual invoicing attempt, not for a dashboard figure."""
    contract = _contract(db_session)
    _rate_card(db_session, contract, valid_from=date(2026, 1, 1), valid_to=date(2026, 2, 28))
    _work_unit(db_session, contract, status="approved", data=date(2026, 3, 5))

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.accrued_value == Decimal("0.00")
    assert backlog.entries == 1
    assert backlog.entries_without_rate == 1


def test_work_units_and_time_entries_combine_in_the_same_figure(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """The two ledgers are structurally independent (no FK between `TimeEntry` and
    `WorkUnit`), and REB-372 narrows only the `work_units` half -- both sources are
    expected to contribute to the same total, never one at the expense of the
    other."""
    entry = TimeEntry(
        deal_id=seeded_deal_id,
        user_id=seeded_user_id,
        data=date(2026, 3, 1),
        hours=Decimal("10.00"),
        description="Ore fatturabili",
        billable=True,
        applied_rate=Decimal("50.000000"),
        rate_source="manual",
        cost_source="absent",
    )
    db_session.add(entry)
    db_session.flush()

    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, status="approved")

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.billable_uninvoiced_hours == Decimal("10.00")
    assert backlog.accrued_value == Decimal("1000.00")
    assert backlog.entries == 2


def test_customer_id_scopes_work_units_through_their_contract(db_session: Session) -> None:
    in_scope = _contract(db_session)
    out_of_scope = _contract(db_session)
    _rate_card(db_session, in_scope)
    _rate_card(db_session, out_of_scope)
    _work_unit(db_session, in_scope, status="approved")
    _work_unit(db_session, out_of_scope, status="approved")

    hours, value, without_rate, entries = AnalyticsRepository(db_session).unbilled_backlog(
        customer_id=in_scope.customer_id
    )
    assert value == Decimal("500.00")
    assert entries == 1
    assert hours == Decimal("0.00")
    assert without_rate == 0


def test_date_window_scopes_work_units_by_their_own_data(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, status="approved", data=date(2026, 3, 5))
    _work_unit(db_session, contract, status="approved", data=date(2026, 4, 5))

    _, value, _, entries = AnalyticsRepository(db_session).unbilled_backlog(
        date_from=date(2026, 3, 1), date_to=date(2026, 3, 31)
    )
    assert value == Decimal("500.00")
    assert entries == 1
