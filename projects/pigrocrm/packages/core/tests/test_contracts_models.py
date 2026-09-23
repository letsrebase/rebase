"""REB-358's own "what the implementing issues must test" for the foundations
issue: the renewal-notice CHECK, the payment-terms "together" CHECK, and the
non-overlapping-validity exclusion constraint -- refused by the database, not by a
service-level check.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer


def _customer(db_session: Session) -> Customer:
    customer = Customer(ragione_sociale="ACME")
    db_session.add(customer)
    db_session.flush()
    return customer


def _contract(db_session: Session, customer: Customer, **overrides: object) -> Contract:
    payload: dict[str, object] = {
        "customer_id": customer.id,
        "titolo": "Consulenza CTO",
        "inizio": date(2026, 1, 1),
        "tipo_rinnovo": "nessuno",
        "preavviso_disdetta_giorni": 30,
        "cadenza_fatturazione": "mensile",
        "politica_spese": {"tipo": "non_rimborsabile"},
    }
    payload.update(overrides)
    contract = Contract(**payload)  # type: ignore[arg-type]
    db_session.add(contract)
    db_session.flush()
    return contract


def test_a_contract_can_be_created_read_and_carries_its_defaults(db_session: Session) -> None:
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    assert contract.stato == "bozza"
    assert contract.divisa == "EUR"
    assert contract.requires_prior_approval is False
    assert contract.applies_social_charge is False
    assert contract.custom_fields == {}
    assert contract.contratto_precedente_id is None


# ---- ck_contracts_tipo_rinnovo -----------------------------------------------------


def test_tipo_rinnovo_is_constrained_to_the_closed_set(db_session: Session) -> None:
    customer = _customer(db_session)
    with pytest.raises(IntegrityError):
        _contract(db_session, customer, tipo_rinnovo="rinnovo_magico")


# ---- ck_contracts_preavviso_rinnovo_required ---------------------------------------


def test_preavviso_rinnovo_giorni_required_unless_renewal_type_is_nessuno(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    with pytest.raises(IntegrityError):
        _contract(
            db_session,
            customer,
            tipo_rinnovo="esplicito",
            preavviso_rinnovo_giorni=None,
        )


def test_preavviso_rinnovo_giorni_supplied_for_a_real_renewal_type_is_accepted(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    contract = _contract(
        db_session,
        customer,
        tipo_rinnovo="esplicito",
        preavviso_rinnovo_giorni=60,
    )
    assert contract.preavviso_rinnovo_giorni == 60


def test_preavviso_rinnovo_giorni_absent_is_fine_when_renewal_type_is_nessuno(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    contract = _contract(db_session, customer, tipo_rinnovo="nessuno")
    assert contract.preavviso_rinnovo_giorni is None


# ---- ck_contracts_payment_terms_together -------------------------------------------


def test_payment_terms_half_set_is_refused(db_session: Session) -> None:
    customer = _customer(db_session)
    with pytest.raises(IntegrityError):
        _contract(db_session, customer, giorni_pagamento=30, pagamento_fine_mese=None)


def test_payment_terms_the_other_half_set_is_also_refused(db_session: Session) -> None:
    customer = _customer(db_session)
    with pytest.raises(IntegrityError):
        _contract(db_session, customer, giorni_pagamento=None, pagamento_fine_mese=True)


def test_payment_terms_fully_null_inherits_the_customers_own_term(db_session: Session) -> None:
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    assert contract.giorni_pagamento is None
    assert contract.pagamento_fine_mese is None


def test_payment_terms_fully_set_is_accepted(db_session: Session) -> None:
    customer = _customer(db_session)
    contract = _contract(db_session, customer, giorni_pagamento=30, pagamento_fine_mese=True)
    assert contract.giorni_pagamento == 30
    assert contract.pagamento_fine_mese is True


def test_contracts_has_a_gin_index_on_custom_fields(db_session: Session) -> None:
    indexes = inspect(db_session.get_bind()).get_indexes("contracts")
    assert any(index["name"] == "ix_contracts_custom_fields" for index in indexes)


# ---- rate_cards ---------------------------------------------------------------------


def _rate_card(db_session: Session, contract: Contract, **overrides: object) -> RateCard:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "valido_da": date(2026, 1, 1),
        "tipo": "ricorrente_fisso",
        "importo": Decimal("1000.00"),
        "unita": "mese",
    }
    payload.update(overrides)
    rate_card = RateCard(**payload)  # type: ignore[arg-type]
    db_session.add(rate_card)
    db_session.flush()
    return rate_card


def test_a_rate_card_can_be_created_with_its_own_defaults(db_session: Session) -> None:
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    rate_card = _rate_card(db_session, contract)
    assert rate_card.valido_a is None
    assert rate_card.frazioni_ammesse == [Decimal("1.00")]


# ---- ck_rate_cards_no_overlap (btree_gist exclusion constraint) --------------------


def test_two_overlapping_rate_cards_on_one_contract_are_refused(db_session: Session) -> None:
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    _rate_card(db_session, contract, valido_da=date(2026, 1, 1), valido_a=date(2026, 6, 30))
    with pytest.raises(IntegrityError):
        _rate_card(db_session, contract, valido_da=date(2026, 6, 1), valido_a=date(2026, 12, 31))


def test_two_adjacent_rate_cards_on_one_contract_are_accepted(db_session: Session) -> None:
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    _rate_card(db_session, contract, valido_da=date(2026, 1, 1), valido_a=date(2026, 6, 30))
    second = _rate_card(
        db_session, contract, valido_da=date(2026, 7, 1), valido_a=date(2026, 12, 31)
    )
    assert second.valido_da == date(2026, 7, 1)


def test_two_open_ended_rate_cards_on_one_contract_overlap_and_are_refused(
    db_session: Session,
) -> None:
    """Mastro's own convention: `valido_a IS NULL` is the open, current card. Two
    of them on the same contract both claim "now onward", which the exclusion
    constraint must refuse -- `daterange(..., NULL, '[]')` is Postgres's own
    unbounded-on-this-side range, not a range with no members."""
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    _rate_card(db_session, contract, valido_da=date(2026, 1, 1), valido_a=None)
    with pytest.raises(IntegrityError):
        _rate_card(db_session, contract, valido_da=date(2026, 6, 1), valido_a=None)


def test_overlapping_rate_cards_on_different_contracts_are_both_accepted(
    db_session: Session,
) -> None:
    """The exclusion constraint's own `contract_id WITH =` clause: overlap is
    refused only within one contract, never across two."""
    customer = _customer(db_session)
    contract_a = _contract(db_session, customer)
    contract_b = _contract(db_session, customer)
    _rate_card(db_session, contract_a, valido_da=date(2026, 1, 1), valido_a=date(2026, 12, 31))
    second = _rate_card(
        db_session, contract_b, valido_da=date(2026, 1, 1), valido_a=date(2026, 12, 31)
    )
    assert second.contract_id == contract_b.id


# ---- ck_rate_cards_ore_minime_only_orario / ck_rate_cards_periodo_only_ricorrente --


def test_ore_minime_is_refused_outside_a_tipo_orario_card(db_session: Session) -> None:
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    with pytest.raises(IntegrityError):
        _rate_card(db_session, contract, tipo="giornaliero", ore_minime=Decimal("4.00"))


def test_ore_minime_is_accepted_on_a_tipo_orario_card(db_session: Session) -> None:
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    rate_card = _rate_card(
        db_session, contract, tipo="orario", unita="ora", ore_minime=Decimal("4.00")
    )
    assert rate_card.ore_minime == Decimal("4.00")


def test_periodo_erogazione_is_refused_outside_a_tipo_ricorrente_fisso_card(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    with pytest.raises(IntegrityError):
        _rate_card(db_session, contract, tipo="una_tantum", periodo_erogazione="mensile")


def test_periodo_erogazione_is_accepted_on_a_tipo_ricorrente_fisso_card(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    rate_card = _rate_card(
        db_session, contract, tipo="ricorrente_fisso", periodo_erogazione="mensile"
    )
    assert rate_card.periodo_erogazione == "mensile"


def test_rate_cards_has_an_index_on_contract_id(db_session: Session) -> None:
    indexes = inspect(db_session.get_bind()).get_indexes("rate_cards")
    assert any(index["name"] == "ix_rate_cards_contract_id" for index in indexes)
