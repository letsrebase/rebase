"""A contract's cap on one customer's share of the legal entity's revenue (REB-761).

The clause is a column of the contract (`customer_share_cap`, a percentage), read over
the contract's own anniversary year: by `concentration_cap`, which compares the share
with it when no threshold is named, and by the «ci sta?» simulator, which adds a
synthetic amount to the customer and says whether the share would reach the cap.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pydantic
import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_contracts_service import _create_payload, _invoice

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.schemas import CeilingSimulationQuery
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.contracts.schemas import ContractUpdate
from pigrocrm.core.contracts.service import ContractService
from pigrocrm.core.deals.models import Deal, DealValueLine
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.pipeline.service import PipelineService

ADMIN = Actor(id=None, type="system", role="admin")


def _world(db_session: Session, *, cap: str | None = "50.00") -> tuple[Organization, Contract]:
    """A customer of 300 euros out of 1,000 invoiced over the contract's first year, which
    starts on 10 March: 30% of the legal entity's revenue, a 9,000 invoice before the
    anniversary left out of the window."""
    customer = make_customer(db_session)
    other = Organization(legal_name="Altro Cliente")
    db_session.add(other)
    db_session.flush()
    contract = ContractService(db_session).create(
        _create_payload(customer.id, start=date(2026, 3, 10), customer_share_cap=cap), ADMIN
    )
    _invoice(db_session, customer.id, taxable_amount="300.00", issue_date=date(2026, 4, 1))
    _invoice(db_session, other.id, taxable_amount="700.00", issue_date=date(2026, 4, 1))
    _invoice(db_session, customer.id, taxable_amount="9000.00", issue_date=date(2026, 2, 1))
    row = db_session.get(Contract, contract.id)
    assert row is not None
    return customer, row


# ---- the clause itself -------------------------------------------------------------


def test_a_cap_is_stored_read_changed_and_cleared(db_session: Session) -> None:
    customer = make_customer(db_session)
    service = ContractService(db_session)
    created = service.create(_create_payload(customer.id, customer_share_cap="50.00"), ADMIN)
    assert created.customer_share_cap == Decimal("50.00")

    changed = service.update(created.id, ContractUpdate(customer_share_cap=Decimal("33.33")), ADMIN)
    assert changed.customer_share_cap == Decimal("33.33")

    cleared = service.update(created.id, ContractUpdate(customer_share_cap=None), ADMIN)
    assert cleared.customer_share_cap is None
    assert service.get(created.id, ADMIN).customer_share_cap is None


def test_a_contract_without_a_cap_has_none(db_session: Session) -> None:
    created = ContractService(db_session).create(
        _create_payload(make_customer(db_session).id), ADMIN
    )
    assert created.customer_share_cap is None


@pytest.mark.parametrize("value", ["0", "0.00", "100", "100.00", "150", "-5"])
def test_a_cap_of_zero_or_one_hundred_percent_or_beyond_is_not_a_clause(value: str) -> None:
    """Zero forbids the customer and a hundred never binds; the schema says so before the
    database's CHECK has to."""
    with pytest.raises(pydantic.ValidationError, match="customer_share_cap"):
        _create_payload(uuid4(), customer_share_cap=value)


def test_a_cap_with_three_decimals_is_refused() -> None:
    with pytest.raises(pydantic.ValidationError, match="customer_share_cap"):
        _create_payload(uuid4(), customer_share_cap="50.005")


def test_the_database_refuses_a_cap_the_schema_never_saw(db_session: Session) -> None:
    _, contract = _world(db_session)
    contract.customer_share_cap = Decimal("100.00")
    with pytest.raises(IntegrityError, match="ck_contracts_customer_share_cap_range"):
        db_session.flush()
    db_session.rollback()


# ---- the share against the cap -----------------------------------------------------


def test_the_concentration_is_compared_with_the_contracts_own_cap(db_session: Session) -> None:
    _, contract = _world(db_session, cap="25.00")

    cap = ContractService(db_session).concentration_cap(contract.id, ADMIN, as_of=date(2026, 6, 1))

    assert cap.threshold == pytest.approx(0.25)
    assert cap.threshold_from_contract is True
    [eur] = cap.currencies
    assert eur.share == pytest.approx(0.3)
    assert eur.exceeded is True


def test_a_share_under_the_cap_is_not_over_it(db_session: Session) -> None:
    _, contract = _world(db_session, cap="50.00")

    [eur] = (
        ContractService(db_session)
        .concentration_cap(contract.id, ADMIN, as_of=date(2026, 6, 1))
        .currencies
    )

    assert eur.exceeded is False


def test_a_threshold_named_by_the_caller_wins_and_is_not_the_contracts(
    db_session: Session,
) -> None:
    _, contract = _world(db_session, cap="50.00")

    cap = ContractService(db_session).concentration_cap(
        contract.id, ADMIN, as_of=date(2026, 6, 1), threshold=0.2
    )

    assert cap.threshold == 0.2
    assert cap.threshold_from_contract is False
    assert [v.exceeded for v in cap.currencies] == [True]


def test_a_contract_with_no_cap_and_no_threshold_says_nothing_about_crossing(
    db_session: Session,
) -> None:
    _, contract = _world(db_session, cap=None)

    cap = ContractService(db_session).concentration_cap(contract.id, ADMIN, as_of=date(2026, 6, 1))

    assert cap.threshold is None and cap.threshold_from_contract is False
    assert [v.exceeded for v in cap.currencies] == [None]


# ---- the simulator -----------------------------------------------------------------


def test_the_simulator_warns_when_the_addition_takes_the_customer_past_the_cap(
    db_session: Session,
) -> None:
    """Done when: a contract with a 50% cap warns when the simulator would take the
    customer past it. 300 of 1,000 is 30%; 500 more to this customer is 800 of 1,500,
    53.3%."""
    _, contract = _world(db_session, cap="50.00")

    result = ContractService(db_session).simulate_cap(
        contract.id, Decimal("500.00"), date(2026, 6, 1), ADMIN
    )

    assert result.cap == pytest.approx(0.5)
    assert (result.current_customer_revenue, result.current_total_revenue) == (
        Decimal("300.00"),
        Decimal("1000.00"),
    )
    assert result.current_share == pytest.approx(0.3)
    assert (result.simulated_customer_revenue, result.simulated_total_revenue) == (
        Decimal("800.00"),
        Decimal("1500.00"),
    )
    assert result.simulated_share == pytest.approx(800 / 1500)
    assert result.falls_within is False
    assert (result.period_from, result.period_to) == (date(2026, 3, 10), date(2027, 3, 9))
    assert result.currency == "EUR"


def test_the_simulator_says_it_fits_when_the_share_stays_under_the_cap(
    db_session: Session,
) -> None:
    _, contract = _world(db_session, cap="50.00")

    result = ContractService(db_session).simulate_cap(
        contract.id, Decimal("100.00"), date(2026, 6, 1), ADMIN
    )

    assert result.simulated_share == pytest.approx(400 / 1100)
    assert result.falls_within is True


def test_the_simulator_reads_the_year_the_money_is_expected_in(db_session: Session) -> None:
    """The anniversary year of 10 March 2027 holds none of this year's invoices, so the
    customer is alone in it: the addition alone is 100% of that year's revenue."""
    _, contract = _world(db_session, cap="50.00")

    result = ContractService(db_session).simulate_cap(
        contract.id, Decimal("100.00"), date(2027, 4, 1), ADMIN
    )

    assert (result.period_from, result.period_to) == (date(2027, 3, 10), date(2028, 3, 9))
    assert result.current_total_revenue == Decimal("0.00")
    assert result.simulated_share == pytest.approx(1.0)
    assert result.falls_within is False


def test_the_simulator_counts_only_the_contracts_own_currency(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = ContractService(db_session).create(
        _create_payload(customer.id, start=date(2026, 3, 10), customer_share_cap="50.00"),
        ADMIN,
    )
    _invoice(
        db_session,
        customer.id,
        taxable_amount="900.00",
        issue_date=date(2026, 4, 1),
        currency="USD",
    )

    result = ContractService(db_session).simulate_cap(
        contract.id, Decimal("100.00"), date(2026, 6, 1), ADMIN
    )

    assert result.current_total_revenue == Decimal("0.00")
    assert result.currency == "EUR"


def test_a_contract_without_a_cap_cannot_be_simulated(db_session: Session) -> None:
    _, contract = _world(db_session, cap=None)
    with pytest.raises(ValidationFailed) as excinfo:
        ContractService(db_session).simulate_cap(
            contract.id, Decimal("100.00"), date(2026, 6, 1), ADMIN
        )
    assert excinfo.value.details["field"] == "customer_share_cap"


def test_a_contract_that_has_not_started_is_simulated_on_its_first_year(
    db_session: Session,
) -> None:
    """A fresh renewal carries its cap and is offered by the picker: asking about a day
    before it starts answers on its first anniversary year instead of failing the whole
    simulation."""
    _, contract = _world(db_session)

    result = ContractService(db_session).simulate_cap(
        contract.id, Decimal("1.00"), date(2026, 1, 1), ADMIN
    )

    assert (result.period_from, result.period_to) == (date(2026, 3, 10), date(2027, 3, 9))


def test_an_unknown_contract_is_not_simulated(db_session: Session) -> None:
    with pytest.raises(NotFound):
        ContractService(db_session).simulate_cap(uuid4(), Decimal("1.00"), None, ADMIN)


# ---- through the ceiling simulator ---------------------------------------------------


def test_the_ceiling_simulator_answers_the_contracts_cap_beside_the_packs_ceilings(
    db_session: Session,
) -> None:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    _, contract = _world(db_session, cap="50.00")

    simulation = AnalyticsService(db_session).simulate_ceiling(
        2026,
        CeilingSimulationQuery(
            budgeted_value=Decimal("500.00"),
            contract_id=contract.id,
            expected_date=date(2026, 6, 1),
        ),
        ADMIN,
    )

    assert [s.id for s in simulation.thresholds] == [
        "revenue_ceiling",
        "immediate_exit_ceiling",
    ]
    assert simulation.contract_cap is not None
    assert simulation.contract_cap.falls_within is False
    assert simulation.contract_cap.contract_id == contract.id


def test_the_postal_code_is_answered_for_an_legal_entity_with_no_ceiling_at_all(
    db_session: Session,
) -> None:
    """A cap is a clause of the contract, not of the regime: an ordinary legal entity has no
    thresholds and still hears that the addition takes the customer past 50%."""
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(pack_id="it-standard"), ADMIN)
    _, contract = _world(db_session, cap="50.00")

    simulation = AnalyticsService(db_session).simulate_ceiling(
        2026,
        CeilingSimulationQuery(
            budgeted_value=Decimal("500.00"),
            contract_id=contract.id,
            expected_date=date(2026, 6, 1),
        ),
        ADMIN,
    )

    assert simulation.thresholds == []
    assert simulation.contract_cap is not None
    assert simulation.contract_cap.falls_within is False


def test_a_simulation_without_a_contract_has_no_cap_answer(db_session: Session) -> None:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)

    simulation = AnalyticsService(db_session).simulate_ceiling(
        2026, CeilingSimulationQuery(budgeted_value=Decimal("500.00")), ADMIN
    )

    assert simulation.contract_cap is None


def test_to_contract_of_another_legal_entity_is_not_simulated_against_this_one(
    db_session: Session,
) -> None:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    _, contract = _world(db_session)
    other = LegalEntity(name="altra", legal_name="Altra S.r.l.", country="IT")
    db_session.add(other)
    db_session.flush()
    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(regime_code="RF19"), ADMIN, legal_entity_id=other.id
    )

    with pytest.raises(ValidationFailed) as excinfo:
        AnalyticsService(db_session).simulate_ceiling(
            2026,
            CeilingSimulationQuery(budgeted_value=Decimal("1.00"), contract_id=contract.id),
            ADMIN,
            other.id,
        )
    assert excinfo.value.details["field"] == "contract_id"


def test_a_contract_in_another_currency_is_not_simulated_with_an_amount_in_euros(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    contract = ContractService(db_session).create(
        _create_payload(customer.id, currency="USD", customer_share_cap="50.00"), ADMIN
    )
    with pytest.raises(ValidationFailed) as excinfo:
        ContractService(db_session).simulate_cap(contract.id, Decimal("1.00"), None, ADMIN)
    assert excinfo.value.details["field"] == "currency"


def test_a_deals_addition_to_the_cap_is_its_share_in_the_contracts_anniversary_year(
    db_session: Session,
) -> None:
    """A fee of 1.000 a month from 1 June, counted at 12 months, has ten of its payments
    (June to March) before the anniversary on 10 March 2027: that is what the cap reads, where
    the calendar year 2026 holds seven. The pack's ceilings still read the calendar year."""
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    _, contract = _world(db_session, cap="50.00")
    PipelineService(db_session).seed_defaults(ADMIN)
    stage = next(s for s in PipelineService(db_session).list() if s.code == "lead")
    deal = Deal(
        name="Manutenzione",
        customer_id=contract.customer_id,
        pipeline_stage_id=stage.id,
        probability=50,
        custom_fields={},
    )
    db_session.add(deal)
    db_session.flush()
    db_session.add(
        DealValueLine(
            deal_id=deal.id,
            position=0,
            unit="flat",
            delivery_period="monthly",
            unit_price=Decimal("1000.00"),
            quantity=Decimal(1),
            start=date(2026, 6, 1),
        )
    )
    db_session.flush()

    simulation = AnalyticsService(db_session).simulate_ceiling(
        2026,
        CeilingSimulationQuery(
            deal_id=deal.id, contract_id=contract.id, expected_date=date(2026, 6, 1)
        ),
        ADMIN,
    )

    assert simulation.brief_addition == Decimal("7000.00")
    assert simulation.contract_cap is not None
    assert simulation.contract_cap.simulated_customer_revenue == Decimal("10300.00")
    assert simulation.contract_cap.simulated_total_revenue == Decimal("11000.00")
    assert simulation.contract_cap.falls_within is False
