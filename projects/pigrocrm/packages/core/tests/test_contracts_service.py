from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fakes.customer_fixtures import make_customer
from frozen_clock import TODAY_IN_ITALY, freeze
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.contract_expenses.schemas import ContractExpenseCreate
from pigrocrm.core.contract_expenses.service import ContractExpenseService
from pigrocrm.core.contracts.projection_service import ContractProjectionService
from pigrocrm.core.contracts.rate_card_service import RateCardService
from pigrocrm.core.contracts.renewal_assumption_service import RenewalAssumptionService
from pigrocrm.core.contracts.repository import ContractRepository
from pigrocrm.core.contracts.schemas import (
    ContractCreate,
    ContractListQuery,
    ContractProjectionQuery,
    ContractStateChange,
    ContractUpdate,
    RateCardCreate,
    RateCardUpdate,
    RenewalAssumptionUpsert,
)
from pigrocrm.core.contracts.service import ContractService
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.schemas import FieldDefinitionCreate
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.timetracking.models import CostCategory

ADMIN = Actor(id=None, type="system", role="admin")


def _create_payload(customer_id: object, **overrides: object) -> ContractCreate:
    payload: dict[str, object] = {
        "customer_id": customer_id,
        "title": "Consulenza CTO",
        "start": date(2026, 1, 1),
        "renewal_type": "none",
        "termination_notice_days": 30,
        "billing_cadence": "monthly",
        "expense_policy": {"kind": "not_reimbursable"},
    }
    payload.update(overrides)
    return ContractCreate(**payload)  # type: ignore[arg-type]


# ---- Done-when: created, read and listed through the API's own service layer ------


def test_a_contract_can_be_created_read_and_listed(db_session: Session) -> None:
    customer = make_customer(db_session)
    service = ContractService(db_session)

    created = service.create(_create_payload(customer.id), ADMIN)
    assert created.status == "draft"

    read = service.get(created.id, ADMIN)
    assert read.id == created.id
    assert read.title == "Consulenza CTO"

    page = service.list(ContractListQuery(customer_id=customer.id), ADMIN)
    assert [c.id for c in page.items] == [created.id]


def test_create_rejects_an_unknown_customer(db_session: Session) -> None:
    service = ContractService(db_session)
    with pytest.raises(NotFound):
        service.create(_create_payload(uuid4()), ADMIN)


def test_create_rejects_half_set_payment_terms_with_a_clean_validation_error(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    service = ContractService(db_session)
    payload = _create_payload(customer.id).model_copy(update={"payment_days": 30})
    with pytest.raises(ValidationFailed) as excinfo:
        service.create(payload, ADMIN)
    assert excinfo.value.details["field"] == "payment_days"


def test_create_rejects_a_missing_renewal_notice_with_a_clean_validation_error(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    service = ContractService(db_session)
    payload = _create_payload(customer.id).model_copy(update={"renewal_type": "explicit"})
    with pytest.raises(ValidationFailed) as excinfo:
        service.create(payload, ADMIN)
    assert excinfo.value.details["field"] == "renewal_notice_days"


def test_get_of_an_unknown_contract_is_not_found(db_session: Session) -> None:
    service = ContractService(db_session)
    with pytest.raises(NotFound):
        service.get(uuid4(), ADMIN)


def test_contract_create_rejects_end_before_start() -> None:
    """An inverted validity period is never even constructed -- Greptile flagged
    this as unenforced; ck_contracts_end_ordered is the database's own backstop,
    but the schema is what gives a clean 422 instead of a raw IntegrityError."""
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="fine non può precedere inizio"):
        _create_payload(uuid4(), end=date(2025, 1, 1))


def test_rate_card_create_rejects_valid_to_before_valid_from() -> None:
    """Greptile: the service's own `except IntegrityError` cannot distinguish an
    inverted range from a genuine overlap, so this must never reach the database
    at all -- caught here, at construction, not translated into a Conflict."""
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="valid_to"):
        _rate_card_payload(valid_from=date(2026, 6, 1), valid_to=date(2026, 1, 1))


# ---- rate cards, through the service layer -----------------------------------------


def _rate_card_payload(**overrides: object) -> RateCardCreate:
    payload: dict[str, object] = {
        "valid_from": date(2026, 1, 1),
        "type": "fixed_recurring",
        "amount": Decimal("1000.00"),
        "unit": "month",
    }
    payload.update(overrides)
    return RateCardCreate(**payload)  # type: ignore[arg-type]


def test_a_rate_card_can_be_created_and_listed_for_its_contract(db_session: Session) -> None:
    customer = make_customer(db_session)
    contracts = ContractService(db_session)
    rate_cards = RateCardService(db_session)
    contract = contracts.create(_create_payload(customer.id), ADMIN)

    created = rate_cards.create(contract.id, _rate_card_payload(), ADMIN)
    assert created.contract_id == contract.id

    listed = rate_cards.list_for_contract(contract.id, ADMIN)
    assert [c.id for c in listed] == [created.id]


def test_rate_card_create_rejects_an_unknown_contract(db_session: Session) -> None:
    rate_cards = RateCardService(db_session)
    with pytest.raises(NotFound):
        rate_cards.create(uuid4(), _rate_card_payload(), ADMIN)


def test_rate_card_create_turns_an_overlap_into_a_clean_conflict_not_a_raw_db_error(
    db_session: Session,
) -> None:
    """Done-when: "a second rate card overlapping an existing one on the same
    contract is refused by the database, not by a service-level check" -- the
    service still owes the caller a clean domain error, not a raw IntegrityError."""
    customer = make_customer(db_session)
    contracts = ContractService(db_session)
    rate_cards = RateCardService(db_session)
    contract = contracts.create(_create_payload(customer.id), ADMIN)

    rate_cards.create(
        contract.id,
        _rate_card_payload(valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 30)),
        ADMIN,
    )
    with pytest.raises(Conflict):
        rate_cards.create(
            contract.id,
            _rate_card_payload(valid_from=date(2026, 6, 1), valid_to=date(2026, 12, 31)),
            ADMIN,
        )

    # The session must still be usable after the rollback -- the same guarantee
    # LegalEntityService.upsert/FiscalProfileService.upsert already give their
    # own callers -- and only the first, non-overlapping card actually exists.
    surviving = rate_cards.list_for_contract(contract.id, ADMIN)
    assert [c.valid_to for c in surviving] == [date(2026, 6, 30)]


def test_an_overlap_conflict_leaves_the_callers_other_pending_work_alone(
    db_session: Session,
) -> None:
    """The refusal confines itself to the rate card's own savepoint: a write the caller
    made earlier in the same session and has not committed is still there afterwards."""
    customer = make_customer(db_session)
    contract = ContractService(db_session).create(_create_payload(customer.id), ADMIN)
    rate_cards = RateCardService(db_session)
    rate_cards.create(
        contract.id,
        _rate_card_payload(valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 30)),
        ADMIN,
    )
    pending = Customer(legal_name="Pending S.r.l.")
    db_session.add(pending)

    with pytest.raises(Conflict):
        rate_cards.create(
            contract.id,
            _rate_card_payload(valid_from=date(2026, 6, 1), valid_to=date(2026, 12, 31)),
            ADMIN,
        )

    assert pending in db_session
    db_session.flush()
    assert db_session.get(Customer, pending.id) is not None


def test_rate_card_create_rejects_minimum_hours_outside_hourly_with_to_clean_validation_error(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    contracts = ContractService(db_session)
    rate_cards = RateCardService(db_session)
    contract = contracts.create(_create_payload(customer.id), ADMIN)

    with pytest.raises(ValidationFailed) as excinfo:
        rate_cards.create(
            contract.id,
            _rate_card_payload(type="daily", minimum_hours=Decimal("4.00")),
            ADMIN,
        )
    assert excinfo.value.details["field"] == "minimum_hours"


def test_rate_card_list_for_an_unknown_contract_is_not_found(db_session: Session) -> None:
    rate_cards = RateCardService(db_session)
    with pytest.raises(NotFound):
        rate_cards.list_for_contract(uuid4(), ADMIN)


# ---- concentration_cap (REB-352 §1.5) ----------------------------------------------


def _invoice(
    db_session: Session,
    customer_id: UUID,
    *,
    taxable_amount: str,
    issue_date: date,
    currency: str = "EUR",
) -> None:
    row = Invoice(
        customer_id=customer_id,
        type="invoice",
        status="issued",
        payment_status="unpaid",
        taxable_amount=Decimal(taxable_amount),
        vat_amount=Decimal("0.00"),
        stamp_duty=Decimal("0.00"),
        total=Decimal(taxable_amount),
        issue_date=issue_date,
        sdi_document_type="TD01",
        currency=currency,
        custom_fields={},
    )
    db_session.add(row)
    db_session.flush()


def test_concentration_cap_computes_the_clients_share_of_the_anniversary_year(
    db_session: Session,
) -> None:
    """The window anchors to the contract's own `start` (10 March), not the calendar
    year: an invoice dated before that anniversary is out of the window even though
    it falls inside the same `issue_date.year`."""
    customer = make_customer(db_session)
    other = Customer(legal_name="Altro Cliente")
    db_session.add(other)
    db_session.flush()
    contract = ContractService(db_session).create(
        _create_payload(customer.id, start=date(2026, 3, 10)), ADMIN
    )

    _invoice(db_session, customer.id, taxable_amount="300.00", issue_date=date(2026, 4, 1))
    _invoice(db_session, other.id, taxable_amount="700.00", issue_date=date(2026, 4, 1))
    # Before the anniversary: excluded from the window even though it is the same year.
    _invoice(db_session, customer.id, taxable_amount="9000.00", issue_date=date(2026, 2, 1))

    cap = ContractService(db_session).concentration_cap(contract.id, ADMIN, as_of=date(2026, 6, 1))

    assert cap.contract_id == contract.id
    assert cap.customer_id == customer.id
    assert (cap.period_from, cap.period_to) == (date(2026, 3, 10), date(2027, 3, 9))
    [eur] = cap.currencies
    assert eur.currency == "EUR"
    assert eur.customer_revenue == Decimal("300.00")
    assert eur.total_revenue == Decimal("1000.00")
    assert eur.share == pytest.approx(0.3)
    assert cap.threshold is None
    assert eur.exceeded is None


def test_concentration_cap_is_zero_when_nothing_has_been_invoiced_in_the_window(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    contract = ContractService(db_session).create(_create_payload(customer.id), ADMIN)

    cap = ContractService(db_session).concentration_cap(contract.id, ADMIN, as_of=date(2026, 6, 1))

    # One row in the contract's own currency, all zero: a quiet year is not an empty list.
    [row] = cap.currencies
    assert row.currency == "EUR"
    assert row.customer_revenue == Decimal("0.00")
    assert row.total_revenue == Decimal("0.00")
    assert row.share == 0.0


def test_concentration_cap_reports_the_currency_the_invoices_are_in_not_the_contracts(
    db_session: Session,
) -> None:
    """REB-776: a USD contract of a customer billed in euros answers euro totals, so the
    page cannot label them as dollars. Invoices in another currency are their own row
    and never summed into the euro figure."""
    customer = make_customer(db_session)
    other = Customer(legal_name="Altro Cliente")
    db_session.add(other)
    db_session.flush()
    contract = ContractService(db_session).create(
        _create_payload(customer.id, currency="USD"), ADMIN
    )
    _invoice(db_session, customer.id, taxable_amount="300.00", issue_date=date(2026, 4, 1))
    _invoice(db_session, other.id, taxable_amount="700.00", issue_date=date(2026, 4, 1))
    _invoice(
        db_session,
        customer.id,
        taxable_amount="50.00",
        issue_date=date(2026, 4, 2),
        currency="USD",
    )

    cap = ContractService(db_session).concentration_cap(contract.id, ADMIN, as_of=date(2026, 6, 1))

    assert [(v.currency, v.customer_revenue, v.total_revenue) for v in cap.currencies] == [
        ("EUR", Decimal("300.00"), Decimal("1000.00")),
        ("USD", Decimal("50.00"), Decimal("50.00")),
    ]


def test_concentration_cap_reports_whether_a_threshold_is_exceeded(db_session: Session) -> None:
    customer = make_customer(db_session)
    other = Customer(legal_name="Altro Cliente")
    db_session.add(other)
    db_session.flush()
    contract = ContractService(db_session).create(_create_payload(customer.id), ADMIN)
    _invoice(db_session, customer.id, taxable_amount="300.00", issue_date=date(2026, 4, 1))
    _invoice(db_session, other.id, taxable_amount="700.00", issue_date=date(2026, 4, 1))

    below = ContractService(db_session).concentration_cap(
        contract.id, ADMIN, as_of=date(2026, 6, 1), threshold=0.5
    )
    above = ContractService(db_session).concentration_cap(
        contract.id, ADMIN, as_of=date(2026, 6, 1), threshold=0.2
    )

    assert below.threshold == 0.5 and [v.exceeded for v in below.currencies] == [False]
    assert above.threshold == 0.2 and [v.exceeded for v in above.currencies] == [True]


def test_concentration_cap_defaults_as_of_to_today(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze(monkeypatch)
    customer = make_customer(db_session)
    contract = ContractService(db_session).create(
        _create_payload(customer.id, start=date(2025, 6, 1)), ADMIN
    )

    cap = ContractService(db_session).concentration_cap(contract.id, ADMIN)

    assert cap.period_from == date(2025, 6, 1)
    assert TODAY_IN_ITALY.year == 2026  # sanity: the frozen "today" is inside this window


def test_concentration_cap_rejects_an_as_of_before_the_contracts_own_start(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    contract = ContractService(db_session).create(
        _create_payload(customer.id, start=date(2026, 3, 10)), ADMIN
    )

    with pytest.raises(ValidationFailed) as excinfo:
        ContractService(db_session).concentration_cap(contract.id, ADMIN, as_of=date(2026, 1, 1))
    assert excinfo.value.details["field"] == "as_of"


def test_concentration_cap_of_an_unknown_contract_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        ContractService(db_session).concentration_cap(uuid4(), ADMIN, as_of=date(2026, 1, 1))


# ---- renewal assumptions, through the service layer (REB-375) ----------------------


def _assumption_payload(**overrides: object) -> RenewalAssumptionUpsert:
    payload: dict[str, object] = {
        "probability": 50,
        "expected_volume": Decimal("36500.00"),
        "horizon_until": date(2027, 12, 31),
    }
    payload.update(overrides)
    return RenewalAssumptionUpsert(**payload)  # type: ignore[arg-type]


def test_a_renewal_assumption_can_be_set_and_read(db_session: Session) -> None:
    customer = make_customer(db_session)
    contracts = ContractService(db_session)
    assumptions = RenewalAssumptionService(db_session)
    contract = contracts.create(_create_payload(customer.id), ADMIN)

    created = assumptions.upsert(contract.id, _assumption_payload(), ADMIN)
    assert created.contract_id == contract.id
    assert created.probability == 50

    read = assumptions.get(contract.id, ADMIN)
    assert read.id == created.id
    assert read.expected_volume == Decimal("36500.00")


def test_a_second_upsert_revises_the_same_row_in_place(db_session: Session) -> None:
    """Done-when: a human's belief is revised, not accumulated into a history."""
    customer = make_customer(db_session)
    contracts = ContractService(db_session)
    assumptions = RenewalAssumptionService(db_session)
    contract = contracts.create(_create_payload(customer.id), ADMIN)

    first = assumptions.upsert(contract.id, _assumption_payload(probability=50), ADMIN)
    second = assumptions.upsert(contract.id, _assumption_payload(probability=80), ADMIN)

    assert second.id == first.id
    read = assumptions.get(contract.id, ADMIN)
    assert read.probability == 80


def test_renewal_assumption_upsert_rejects_an_unknown_contract(db_session: Session) -> None:
    assumptions = RenewalAssumptionService(db_session)
    with pytest.raises(NotFound):
        assumptions.upsert(uuid4(), _assumption_payload(), ADMIN)


def test_renewal_assumption_get_of_an_unknown_contract_is_not_found(db_session: Session) -> None:
    assumptions = RenewalAssumptionService(db_session)
    with pytest.raises(NotFound):
        assumptions.get(uuid4(), ADMIN)


def test_renewal_assumption_get_before_any_upsert_is_not_found(db_session: Session) -> None:
    """A contract with no recorded belief yet -- the honest default this slice's own
    design insists on (never a guessed pace standing in for a missing one)."""
    customer = make_customer(db_session)
    contract = ContractService(db_session).create(_create_payload(customer.id), ADMIN)
    with pytest.raises(NotFound):
        RenewalAssumptionService(db_session).get(contract.id, ADMIN)


# ---- the projected figure, through the service layer (REB-375) ---------------------


def test_project_rejects_an_unknown_contract(db_session: Session) -> None:
    projections = ContractProjectionService(db_session)
    query = ContractProjectionQuery(date_from=date(2026, 1, 1), date_to=date(2027, 1, 1))
    with pytest.raises(NotFound):
        projections.project(uuid4(), query, ADMIN)


def test_the_projected_figure_combines_the_schedule_and_the_renewal_assumption(
    db_session: Session,
) -> None:
    """Done-when: a contract's own recorded pace or assumption contributes to a
    genuine "projected" figure -- exercised end to end through the real services,
    with numbers chosen to divide evenly so the assertion is exact, not a tolerance.
    """
    customer = make_customer(db_session)
    contracts = ContractService(db_session)
    rate_cards = RateCardService(db_session)
    assumptions = RenewalAssumptionService(db_session)
    projections = ContractProjectionService(db_session)

    contract = contracts.create(
        _create_payload(
            customer.id,
            end=date(2026, 12, 31),
            termination_notice_days=30,
        ),
        ADMIN,
    )
    rate_cards.create(
        contract.id,
        _rate_card_payload(
            valid_from=date(2026, 1, 1),
            type="fixed_recurring",
            delivery_period="monthly",
            amount=Decimal("1000.00"),
        ),
        ADMIN,
    )
    assumptions.upsert(
        contract.id,
        _assumption_payload(
            probability=50, expected_volume=Decimal("36500.00"), horizon_until=date(2027, 12, 31)
        ),
        ADMIN,
    )

    result = projections.project(
        contract.id,
        ContractProjectionQuery(
            date_from=date(2026, 1, 1), date_to=date(2027, 6, 1), as_of=date(2026, 6, 1)
        ),
        ADMIN,
    )

    assert result.irrevocability_window_until_to == date(2026, 7, 1)
    # Occurrences beyond the window (2026-08-01..2026-12-01) and within the contract's
    # own term: five monthly occurrences at 1000.00 each.
    assert result.scheduled == Decimal("5000.00")
    # The renewal assumption's own share of 2027-01-01..2027-06-01, prorated across
    # its full 2027-01-01..2027-12-31 horizon.
    assert result.from_renewal == Decimal("7550.00")
    assert result.total == Decimal("12550.00")


def test_the_projected_figure_is_independent_of_cash_overviews_own_projected(
    db_session: Session,
) -> None:
    """The Done-when's other half, stated explicitly by the issue: this is a new
    figure *alongside* `CashOverview.projected`, never a replacement or an input to
    it. A contract's own schedule and assumption contribute nothing to the
    draft-based figure, and an empty ledger's `projected` stays exactly zero
    whether or not a contract projects real money."""
    customer = make_customer(db_session)
    contracts = ContractService(db_session)
    rate_cards = RateCardService(db_session)
    assumptions = RenewalAssumptionService(db_session)
    projections = ContractProjectionService(db_session)

    contract = contracts.create(
        _create_payload(customer.id, end=date(2026, 12, 31), termination_notice_days=30),
        ADMIN,
    )
    rate_cards.create(
        contract.id,
        _rate_card_payload(
            valid_from=date(2026, 1, 1), type="fixed_recurring", delivery_period="monthly"
        ),
        ADMIN,
    )
    assumptions.upsert(contract.id, _assumption_payload(), ADMIN)

    result = projections.project(
        contract.id,
        ContractProjectionQuery(
            date_from=date(2026, 1, 1), date_to=date(2027, 6, 1), as_of=date(2026, 6, 1)
        ),
        ADMIN,
    )
    assert result.total > Decimal("0.00")

    overview = AnalyticsService(db_session).cash_overview(2026, ADMIN)
    assert overview.projected == Decimal("0.00")


# ---- REB-741: editing a contract and moving it through its states ------------------


def test_update_changes_only_the_fields_it_names(db_session: Session) -> None:
    customer = make_customer(db_session)
    service = ContractService(db_session)
    created = service.create(_create_payload(customer.id), ADMIN)

    updated = service.update(created.id, ContractUpdate(termination_notice_days=60), ADMIN)

    assert updated.termination_notice_days == 60
    assert updated.title == created.title
    assert updated.billing_cadence == created.billing_cadence


def test_update_can_clear_the_end_date_and_the_payment_terms_together(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    service = ContractService(db_session)
    created = service.create(
        _create_payload(
            customer.id,
            end=date(2026, 12, 31),
            payment_days=30,
            payment_end_of_month=True,
        ),
        ADMIN,
    )

    updated = service.update(
        created.id,
        ContractUpdate(end=None, payment_days=None, payment_end_of_month=None),
        ADMIN,
    )

    assert updated.end is None
    assert updated.payment_days is None
    assert updated.payment_end_of_month is None


def test_update_refuses_half_set_payment_terms_on_the_merged_row(db_session: Session) -> None:
    customer = make_customer(db_session)
    service = ContractService(db_session)
    created = service.create(_create_payload(customer.id), ADMIN)

    with pytest.raises(ValidationFailed) as excinfo:
        service.update(created.id, ContractUpdate(payment_days=30), ADMIN)
    assert excinfo.value.details["field"] == "payment_days"


def test_update_refuses_a_renewal_type_without_its_notice(db_session: Session) -> None:
    customer = make_customer(db_session)
    service = ContractService(db_session)
    created = service.create(_create_payload(customer.id), ADMIN)

    with pytest.raises(ValidationFailed) as excinfo:
        service.update(created.id, ContractUpdate(renewal_type="tacit"), ADMIN)
    assert excinfo.value.details["field"] == "renewal_notice_days"

    updated = service.update(
        created.id,
        ContractUpdate(renewal_type="tacit", renewal_notice_days=45),
        ADMIN,
    )
    assert updated.renewal_type == "tacit"


def test_update_refuses_an_end_before_the_start_and_a_cleared_required_field(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    service = ContractService(db_session)
    created = service.create(_create_payload(customer.id), ADMIN)

    with pytest.raises(ValidationFailed) as ended:
        service.update(created.id, ContractUpdate(end=date(2025, 12, 31)), ADMIN)
    assert ended.value.details["field"] == "end"

    with pytest.raises(ValidationFailed) as cleared:
        service.update(created.id, ContractUpdate(title=None), ADMIN)
    assert cleared.value.details["field"] == "title"


def test_update_merges_custom_fields_and_removes_a_key_supplied_as_null(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    fields = FieldDefinitionService(db_session)
    for key in ("contact_person", "codice_po"):
        fields.create(
            FieldDefinitionCreate(
                entity_type="contract", key=key, label=key.capitalize(), field_type="text"
            ),
            ADMIN,
        )
    service = ContractService(db_session)
    created = service.create(
        _create_payload(
            customer.id, custom_fields={"contact_person": "Rossi", "codice_po": "PO-1"}
        ),
        ADMIN,
    )

    updated = service.update(
        created.id,
        ContractUpdate(custom_fields={"codice_po": None, "contact_person": "Bianchi"}),
        ADMIN,
    )

    assert updated.custom_fields == {"contact_person": "Bianchi"}


def test_update_that_changes_nothing_writes_no_timeline_row(db_session: Session) -> None:
    customer = make_customer(db_session)
    service = ContractService(db_session)
    created = service.create(_create_payload(customer.id), ADMIN)
    before = len(ActivityService(db_session).timeline("contract", created.id, 20))

    service.update(created.id, ContractUpdate(), ADMIN)

    assert len(ActivityService(db_session).timeline("contract", created.id, 20)) == before


def test_changing_the_expense_policy_reevaluates_the_expenses_not_yet_billed(
    db_session: Session,
) -> None:
    """`reimbursable` is written by a trigger when an expense is written. A policy that
    changes later must not leave an expense «Da fatturare» under the old verdict."""
    customer = make_customer(db_session)
    category = CostCategory(name="Trasferte", position=0)
    db_session.add(category)
    db_session.flush()
    contracts = ContractService(db_session)
    created = contracts.create(
        _create_payload(customer.id, expense_policy={"kind": "reimbursable"}), ADMIN
    )
    expenses = ContractExpenseService(db_session)
    expenses.create(
        created.id,
        ContractExpenseCreate(
            category_id=category.id,
            date=date(2026, 3, 5),
            amount=Decimal("42.00"),
            description="Biglietto treno",
        ),
        ADMIN,
    )
    assert [e.reimbursable for e in expenses.list_for_contract(created.id, ADMIN)] == [True]

    contracts.update(created.id, ContractUpdate(expense_policy={"kind": "not_reimbursable"}), ADMIN)
    assert [e.reimbursable for e in expenses.list_for_contract(created.id, ADMIN)] == [False]

    contracts.update(
        created.id,
        ContractUpdate(
            expense_policy={"kind": "reimbursable", "requires_pre_authorization": False}
        ),
        ADMIN,
    )
    assert [e.reimbursable for e in expenses.list_for_contract(created.id, ADMIN)] == [True]


@pytest.mark.parametrize(
    "policy",
    [
        {},
        {"type": "not_reimbursable"},
        {"kind": "reimbursable_with_cap"},
        {"kind": "reimbursable_with_cap", "amount_cap": "molto"},
        {"kind": "reimbursable", "amount_cap": "500.00"},
        {"kind": "reimbursable", "requires_pre_authorization": "yes"},
    ],
)
def test_an_expense_policy_the_trigger_cannot_read_is_refused_on_update(
    policy: dict[str, object],
) -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="expense_policy"):
        ContractUpdate(expense_policy=policy)


def test_the_three_expense_policy_shapes_are_accepted_on_update() -> None:
    for policy in (
        {"kind": "not_reimbursable"},
        {"kind": "reimbursable", "requires_pre_authorization": True},
        {"kind": "reimbursable_with_cap", "amount_cap": "500.00"},
    ):
        assert ContractUpdate(expense_policy=policy).expense_policy == policy


def test_update_of_an_unknown_contract_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        ContractService(db_session).update(uuid4(), ContractUpdate(title="x"), ADMIN)


def test_set_state_moves_to_contract_out_of_draft_and_records_it(db_session: Session) -> None:
    customer = make_customer(db_session)
    service = ContractService(db_session)
    created = service.create(_create_payload(customer.id), ADMIN)

    active = service.set_state(created.id, ContractStateChange(status="active"), ADMIN)
    assert active.status == "active"
    assert service.get(created.id, ADMIN).status == "active"

    closed = service.set_state(created.id, ContractStateChange(status="terminated"), ADMIN)
    assert closed.status == "terminated"


def test_set_state_refuses_the_state_the_contract_already_has(db_session: Session) -> None:
    customer = make_customer(db_session)
    service = ContractService(db_session)
    created = service.create(_create_payload(customer.id), ADMIN)

    with pytest.raises(Conflict):
        service.set_state(created.id, ContractStateChange(status="draft"), ADMIN)


def test_contract_state_rejects_a_value_outside_the_four() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ContractStateChange(status="sospeso")  # type: ignore[arg-type]


# ---- REB-742: editing a rate card and closing the one a new rate replaces ----------


def test_a_new_rate_can_close_the_open_one_the_day_before_in_the_same_call(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    contract = ContractService(db_session).create(_create_payload(customer.id), ADMIN)
    rate_cards = RateCardService(db_session)
    first = rate_cards.create(contract.id, _rate_card_payload(valid_from=date(2026, 1, 1)), ADMIN)
    assert first.valid_to is None

    second = rate_cards.create(
        contract.id,
        _rate_card_payload(valid_from=date(2027, 1, 1), amount=Decimal("1200.00")),
        ADMIN,
        close_previous=True,
    )

    cards = rate_cards.list_for_contract(contract.id, ADMIN)
    assert [(c.id, c.valid_to) for c in cards] == [
        (first.id, date(2026, 12, 31)),
        (second.id, None),
    ]


def test_without_close_previous_the_overlap_is_still_refused_and_nothing_moves(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    contract = ContractService(db_session).create(_create_payload(customer.id), ADMIN)
    rate_cards = RateCardService(db_session)
    rate_cards.create(contract.id, _rate_card_payload(valid_from=date(2026, 1, 1)), ADMIN)

    with pytest.raises(Conflict):
        rate_cards.create(contract.id, _rate_card_payload(valid_from=date(2027, 1, 1)), ADMIN)

    assert [c.valid_to for c in rate_cards.list_for_contract(contract.id, ADMIN)] == [None]


def test_a_close_that_ends_in_an_overlap_is_rolled_back_with_the_insert(
    db_session: Session,
) -> None:
    """The earlier card is closed and the new one then collides with a later card: the
    refusal takes the close with it, so a rejected rate never shortens another."""
    customer = make_customer(db_session)
    contract = ContractService(db_session).create(_create_payload(customer.id), ADMIN)
    rate_cards = RateCardService(db_session)
    earlier = rate_cards.create(
        contract.id,
        _rate_card_payload(valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 30)),
        ADMIN,
    )
    rate_cards.create(contract.id, _rate_card_payload(valid_from=date(2026, 9, 1)), ADMIN)

    with pytest.raises(Conflict):
        rate_cards.create(
            contract.id,
            _rate_card_payload(valid_from=date(2026, 5, 1), valid_to=date(2026, 10, 1)),
            ADMIN,
            close_previous=True,
        )

    cards = rate_cards.list_for_contract(contract.id, ADMIN)
    assert [(c.id, c.valid_to) for c in cards][0] == (earlier.id, date(2026, 6, 30))
    assert len(cards) == 2


def test_closing_the_previous_rate_says_so_in_the_timeline(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = ContractService(db_session).create(_create_payload(customer.id), ADMIN)
    rate_cards = RateCardService(db_session)
    rate_cards.create(contract.id, _rate_card_payload(valid_from=date(2026, 1, 1)), ADMIN)
    rate_cards.create(
        contract.id, _rate_card_payload(valid_from=date(2027, 1, 1)), ADMIN, close_previous=True
    )

    added = [
        row
        for row in ActivityService(db_session).timeline("contract", contract.id, 20)
        if row.kind == "rate_card_added"
    ]
    # Newest first: the second card names the end it gave the first.
    assert added[0].payload["previous_valid_from"] == "2026-01-01"
    assert added[0].payload["previous_valid_to"] == "2026-12-31"
    assert "previous_valid_to" not in added[1].payload


def test_a_rate_card_can_be_edited_and_its_validity_reopened(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = ContractService(db_session).create(_create_payload(customer.id), ADMIN)
    rate_cards = RateCardService(db_session)
    card = rate_cards.create(
        contract.id,
        _rate_card_payload(valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 30)),
        ADMIN,
    )

    edited = rate_cards.update(
        contract.id, card.id, RateCardUpdate(amount=Decimal("1500.00"), valid_to=None), ADMIN
    )

    assert edited.amount == Decimal("1500.00")
    assert edited.valid_to is None


def test_a_rate_card_edit_that_overlaps_another_is_a_clean_conflict(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = ContractService(db_session).create(_create_payload(customer.id), ADMIN)
    rate_cards = RateCardService(db_session)
    first = rate_cards.create(
        contract.id,
        _rate_card_payload(valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 30)),
        ADMIN,
    )
    rate_cards.create(contract.id, _rate_card_payload(valid_from=date(2026, 7, 1)), ADMIN)

    with pytest.raises(Conflict):
        rate_cards.update(contract.id, first.id, RateCardUpdate(valid_to=date(2026, 8, 31)), ADMIN)

    # The session survives the refusal and the stored card is unchanged.
    assert rate_cards.list_for_contract(contract.id, ADMIN)[0].valid_to == date(2026, 6, 30)


def test_a_rate_card_edit_checks_the_merged_shape(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = ContractService(db_session).create(_create_payload(customer.id), ADMIN)
    rate_cards = RateCardService(db_session)
    card = rate_cards.create(contract.id, _rate_card_payload(), ADMIN)

    with pytest.raises(ValidationFailed) as hours:
        rate_cards.update(
            contract.id, card.id, RateCardUpdate(minimum_hours=Decimal("4.00")), ADMIN
        )
    assert hours.value.details["field"] == "minimum_hours"

    with pytest.raises(ValidationFailed) as order:
        rate_cards.update(contract.id, card.id, RateCardUpdate(valid_to=date(2025, 12, 31)), ADMIN)
    assert order.value.details["field"] == "valid_to"


def test_a_rate_card_of_another_contract_is_not_found(db_session: Session) -> None:
    customer = make_customer(db_session)
    contracts = ContractService(db_session)
    one = contracts.create(_create_payload(customer.id), ADMIN)
    other = contracts.create(_create_payload(customer.id, title="Altro"), ADMIN)
    rate_cards = RateCardService(db_session)
    card = rate_cards.create(one.id, _rate_card_payload(), ADMIN)

    with pytest.raises(NotFound):
        rate_cards.update(other.id, card.id, RateCardUpdate(amount=Decimal("1.00")), ADMIN)


@pytest.mark.parametrize(
    "policy",
    [
        {},
        {"mode": "reimbursable"},
        {"type": "not_reimbursable"},
        {"kind": "reimbursable_with_cap"},
        {"kind": "reimbursable", "amount_cap": "500.00"},
    ],
)
def test_an_expense_policy_the_trigger_cannot_read_is_refused_on_create(
    db_session: Session, policy: dict[str, object]
) -> None:
    import pydantic

    customer = make_customer(db_session)
    with pytest.raises(pydantic.ValidationError, match="expense_policy"):
        _create_payload(customer.id, expense_policy=policy)


def test_a_customers_and_a_legal_entities_contracts_are_filtered_in_sql(
    db_session: Session,
) -> None:
    first = make_customer(db_session)
    second = make_customer(db_session)
    contracts = ContractService(db_session)
    mine = contracts.create(_create_payload(first.id), ADMIN)
    other = contracts.create(_create_payload(second.id), ADMIN)

    repo = ContractRepository(db_session)
    assert [c.id for c in repo.list_for_customer(first.id)] == [mine.id]
    assert repo.list_for_customer(uuid4()) == []
    assert {c.id for c in repo.list_active(legal_entity_id=mine.legal_entity_id)} >= {
        mine.id,
        other.id,
    }
    assert repo.list_active(legal_entity_id=uuid4()) == []
