"""A contract's cap on one customer's share of the azienda's revenue (REB-761).

The clause is a column of the contract (`tetto_quota_cliente`, a percentage), read over
the contract's own anniversary year: by `concentration_cap`, which compares the share
with it when no threshold is named, and by the «ci sta?» simulator, which adds a
synthetic amount to the customer and says whether the share would reach the cap.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pydantic
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_contracts_service import _create_payload, _customer, _invoice

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.schemas import CeilingSimulationQuery
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.contracts.schemas import ContractUpdate
from pigrocrm.core.contracts.service import ContractService
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.emitter.models import LegalEntity
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService

ADMIN = Actor(id=None, type="system", role="admin")


def _world(db_session: Session, *, tetto: str | None = "50.00") -> tuple[Customer, Contract]:
    """A customer of 300 euros out of 1,000 invoiced over the contract's first year, which
    starts on 10 March: 30% of the azienda's revenue, a 9,000 invoice before the
    anniversary left out of the window."""
    customer = _customer(db_session)
    other = Customer(ragione_sociale="Altro Cliente")
    db_session.add(other)
    db_session.flush()
    contract = ContractService(db_session).create(
        _create_payload(customer.id, inizio=date(2026, 3, 10), tetto_quota_cliente=tetto), ADMIN
    )
    _invoice(db_session, customer.id, imponibile="300.00", data_emissione=date(2026, 4, 1))
    _invoice(db_session, other.id, imponibile="700.00", data_emissione=date(2026, 4, 1))
    _invoice(db_session, customer.id, imponibile="9000.00", data_emissione=date(2026, 2, 1))
    row = db_session.get(Contract, contract.id)
    assert row is not None
    return customer, row


# ---- the clause itself -------------------------------------------------------------


def test_a_cap_is_stored_read_changed_and_cleared(db_session: Session) -> None:
    customer = _customer(db_session)
    service = ContractService(db_session)
    created = service.create(_create_payload(customer.id, tetto_quota_cliente="50.00"), ADMIN)
    assert created.tetto_quota_cliente == Decimal("50.00")

    changed = service.update(
        created.id, ContractUpdate(tetto_quota_cliente=Decimal("33.33")), ADMIN
    )
    assert changed.tetto_quota_cliente == Decimal("33.33")

    cleared = service.update(created.id, ContractUpdate(tetto_quota_cliente=None), ADMIN)
    assert cleared.tetto_quota_cliente is None
    assert service.get(created.id, ADMIN).tetto_quota_cliente is None


def test_a_contract_without_a_cap_has_none(db_session: Session) -> None:
    created = ContractService(db_session).create(_create_payload(_customer(db_session).id), ADMIN)
    assert created.tetto_quota_cliente is None


@pytest.mark.parametrize("value", ["0", "0.00", "100", "100.00", "150", "-5"])
def test_a_cap_of_zero_or_one_hundred_percent_or_beyond_is_not_a_clause(value: str) -> None:
    """Zero forbids the customer and a hundred never binds; the schema says so before the
    database's CHECK has to."""
    with pytest.raises(pydantic.ValidationError, match="tetto_quota_cliente"):
        _create_payload(uuid4(), tetto_quota_cliente=value)


def test_a_cap_with_three_decimals_is_refused() -> None:
    with pytest.raises(pydantic.ValidationError, match="tetto_quota_cliente"):
        _create_payload(uuid4(), tetto_quota_cliente="50.005")


def test_the_database_refuses_a_cap_the_schema_never_saw(db_session: Session) -> None:
    _, contract = _world(db_session)
    contract.tetto_quota_cliente = Decimal("100.00")
    with pytest.raises(IntegrityError, match="ck_contracts_tetto_quota_cliente_range"):
        db_session.flush()
    db_session.rollback()


# ---- the share against the cap -----------------------------------------------------


def test_the_concentration_is_compared_with_the_contracts_own_cap(db_session: Session) -> None:
    _, contract = _world(db_session, tetto="25.00")

    cap = ContractService(db_session).concentration_cap(contract.id, ADMIN, as_of=date(2026, 6, 1))

    assert cap.soglia == pytest.approx(0.25)
    assert cap.soglia_da_contratto is True
    [eur] = cap.valute
    assert eur.quota == pytest.approx(0.3)
    assert eur.superata is True


def test_a_share_under_the_cap_is_not_over_it(db_session: Session) -> None:
    _, contract = _world(db_session, tetto="50.00")

    [eur] = (
        ContractService(db_session)
        .concentration_cap(contract.id, ADMIN, as_of=date(2026, 6, 1))
        .valute
    )

    assert eur.superata is False


def test_a_threshold_named_by_the_caller_wins_and_is_not_the_contracts(
    db_session: Session,
) -> None:
    _, contract = _world(db_session, tetto="50.00")

    cap = ContractService(db_session).concentration_cap(
        contract.id, ADMIN, as_of=date(2026, 6, 1), soglia=0.2
    )

    assert cap.soglia == 0.2
    assert cap.soglia_da_contratto is False
    assert [v.superata for v in cap.valute] == [True]


def test_a_contract_with_no_cap_and_no_threshold_says_nothing_about_crossing(
    db_session: Session,
) -> None:
    _, contract = _world(db_session, tetto=None)

    cap = ContractService(db_session).concentration_cap(contract.id, ADMIN, as_of=date(2026, 6, 1))

    assert cap.soglia is None and cap.soglia_da_contratto is False
    assert [v.superata for v in cap.valute] == [None]


# ---- the simulator -----------------------------------------------------------------


def test_the_simulator_warns_when_the_addition_takes_the_customer_past_the_cap(
    db_session: Session,
) -> None:
    """Done when: a contract with a 50% cap warns when the simulator would take the
    customer past it. 300 of 1,000 is 30%; 500 more to this customer is 800 of 1,500,
    53.3%."""
    _, contract = _world(db_session, tetto="50.00")

    result = ContractService(db_session).simulate_cap(
        contract.id, Decimal("500.00"), date(2026, 6, 1), ADMIN
    )

    assert result.tetto == pytest.approx(0.5)
    assert (result.ricavi_cliente_attuali, result.ricavi_totali_attuali) == (
        Decimal("300.00"),
        Decimal("1000.00"),
    )
    assert result.quota_attuale == pytest.approx(0.3)
    assert (result.ricavi_cliente_simulati, result.ricavi_totali_simulati) == (
        Decimal("800.00"),
        Decimal("1500.00"),
    )
    assert result.quota_simulata == pytest.approx(800 / 1500)
    assert result.rientra is False
    assert (result.periodo_da, result.periodo_a) == (date(2026, 3, 10), date(2027, 3, 9))
    assert result.divisa == "EUR"


def test_the_simulator_says_it_fits_when_the_share_stays_under_the_cap(
    db_session: Session,
) -> None:
    _, contract = _world(db_session, tetto="50.00")

    result = ContractService(db_session).simulate_cap(
        contract.id, Decimal("100.00"), date(2026, 6, 1), ADMIN
    )

    assert result.quota_simulata == pytest.approx(400 / 1100)
    assert result.rientra is True


def test_the_simulator_reads_the_year_the_money_is_expected_in(db_session: Session) -> None:
    """The anniversary year of 10 March 2027 holds none of this year's invoices, so the
    customer is alone in it: the addition alone is 100% of that year's revenue."""
    _, contract = _world(db_session, tetto="50.00")

    result = ContractService(db_session).simulate_cap(
        contract.id, Decimal("100.00"), date(2027, 4, 1), ADMIN
    )

    assert (result.periodo_da, result.periodo_a) == (date(2027, 3, 10), date(2028, 3, 9))
    assert result.ricavi_totali_attuali == Decimal("0.00")
    assert result.quota_simulata == pytest.approx(1.0)
    assert result.rientra is False


def test_the_simulator_counts_only_the_contracts_own_currency(db_session: Session) -> None:
    customer = _customer(db_session)
    contract = ContractService(db_session).create(
        _create_payload(customer.id, inizio=date(2026, 3, 10), tetto_quota_cliente="50.00"),
        ADMIN,
    )
    _invoice(
        db_session, customer.id, imponibile="900.00", data_emissione=date(2026, 4, 1), divisa="USD"
    )

    result = ContractService(db_session).simulate_cap(
        contract.id, Decimal("100.00"), date(2026, 6, 1), ADMIN
    )

    assert result.ricavi_totali_attuali == Decimal("0.00")
    assert result.divisa == "EUR"


def test_a_contract_without_a_cap_cannot_be_simulated(db_session: Session) -> None:
    _, contract = _world(db_session, tetto=None)
    with pytest.raises(ValidationFailed) as excinfo:
        ContractService(db_session).simulate_cap(
            contract.id, Decimal("100.00"), date(2026, 6, 1), ADMIN
        )
    assert excinfo.value.details["field"] == "tetto_quota_cliente"


def test_the_simulator_refuses_a_day_before_the_contract_and_an_unknown_contract(
    db_session: Session,
) -> None:
    _, contract = _world(db_session)
    with pytest.raises(ValidationFailed) as excinfo:
        ContractService(db_session).simulate_cap(
            contract.id, Decimal("1.00"), date(2026, 1, 1), ADMIN
        )
    assert excinfo.value.details["field"] == "data_prevista"
    with pytest.raises(NotFound):
        ContractService(db_session).simulate_cap(uuid4(), Decimal("1.00"), None, ADMIN)


# ---- through the ceiling simulator ---------------------------------------------------


def test_the_ceiling_simulator_answers_the_contracts_cap_beside_the_packs_ceilings(
    db_session: Session,
) -> None:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    _, contract = _world(db_session, tetto="50.00")

    simulation = AnalyticsService(db_session).simulate_ceiling(
        2026,
        CeilingSimulationQuery(
            valore_preventivato=Decimal("500.00"),
            contract_id=contract.id,
            data_prevista=date(2026, 6, 1),
        ),
        ADMIN,
    )

    assert [s.id for s in simulation.soglie] == ["soglia_ricavi", "soglia_fuoriuscita_immediata"]
    assert simulation.tetto_contratto is not None
    assert simulation.tetto_contratto.rientra is False
    assert simulation.tetto_contratto.contract_id == contract.id


def test_the_cap_is_answered_for_an_azienda_with_no_ceiling_at_all(db_session: Session) -> None:
    """A cap is a clause of the contract, not of the regime: an ordinary azienda has no
    soglie and still hears that the addition takes the customer past 50%."""
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(pack_id="it-standard"), ADMIN)
    _, contract = _world(db_session, tetto="50.00")

    simulation = AnalyticsService(db_session).simulate_ceiling(
        2026,
        CeilingSimulationQuery(
            valore_preventivato=Decimal("500.00"),
            contract_id=contract.id,
            data_prevista=date(2026, 6, 1),
        ),
        ADMIN,
    )

    assert simulation.soglie == []
    assert simulation.tetto_contratto is not None
    assert simulation.tetto_contratto.rientra is False


def test_a_simulation_without_a_contract_has_no_cap_answer(db_session: Session) -> None:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)

    simulation = AnalyticsService(db_session).simulate_ceiling(
        2026, CeilingSimulationQuery(valore_preventivato=Decimal("500.00")), ADMIN
    )

    assert simulation.tetto_contratto is None


def test_a_contract_of_another_azienda_is_not_simulated_against_this_one(
    db_session: Session,
) -> None:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    _, contract = _world(db_session)
    other = LegalEntity(nome="altra", ragione_sociale="Altra S.r.l.", nazione="IT")
    db_session.add(other)
    db_session.flush()
    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(codice_regime="RF19"), ADMIN, azienda_id=other.id
    )

    with pytest.raises(ValidationFailed) as excinfo:
        AnalyticsService(db_session).simulate_ceiling(
            2026,
            CeilingSimulationQuery(valore_preventivato=Decimal("1.00"), contract_id=contract.id),
            ADMIN,
            other.id,
        )
    assert excinfo.value.details["field"] == "contract_id"
