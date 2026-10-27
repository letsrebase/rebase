"""Renewing a contract into its successor (REB-763)."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_contracts_service import _create_payload, _customer

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.contracts.renewal import add_months, renewed_term
from pigrocrm.core.contracts.schemas import ContractRenew, RateCardCreate, RenewalDuration
from pigrocrm.core.contracts.service import ClauseNoteService, ContractService, RateCardService
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed

ADMIN = Actor(id=None, type="system", role="admin")


def _year_contract(db_session: Session, **overrides: object):  # type: ignore[no-untyped-def]
    payload = {
        "inizio": date(2026, 1, 1),
        "fine": date(2026, 12, 31),
        "tipo_rinnovo": "esplicito",
        "preavviso_rinnovo_giorni": 60,
        "applies_social_charge": True,
        "giorni_pagamento": 45,
        "pagamento_fine_mese": True,
        "tetto_quota_cliente": "40.00",
        "note": "Condizioni 2026",
        **overrides,
    }
    return ContractService(db_session).create(
        _create_payload(_customer(db_session).id, **payload), ADMIN
    )


# ---- the term, pure ---------------------------------------------------------------


def test_a_renewal_of_a_year_ending_in_december_starts_on_the_first_of_january() -> None:
    assert renewed_term(inizio=date(2026, 1, 1), fine=date(2026, 12, 31)) == (
        date(2027, 1, 1),
        date(2027, 12, 31),
    )


def test_whole_months_stay_whole_months_and_other_spans_stay_days() -> None:
    # February is 28 days, the renewed month is March's 31: months, not days.
    assert renewed_term(inizio=date(2026, 2, 1), fine=date(2026, 2, 28)) == (
        date(2026, 3, 1),
        date(2026, 3, 31),
    )
    # A term that is not whole months lasts as many days.
    assert renewed_term(inizio=date(2026, 3, 10), fine=date(2026, 4, 9)) == (
        date(2026, 4, 10),
        date(2026, 5, 10),
    )


def test_a_stated_duration_or_end_overrides_the_default() -> None:
    base = {"inizio": date(2026, 1, 1), "fine": date(2026, 12, 31)}
    assert renewed_term(**base, durata=(6, "mesi"))[1] == date(2027, 6, 30)
    assert renewed_term(**base, durata=(2, "anni"))[1] == date(2028, 12, 31)
    assert renewed_term(**base, durata=(10, "giorni"))[1] == date(2027, 1, 10)
    assert renewed_term(**base, durata=(2, "settimane"))[1] == date(2027, 1, 14)
    assert renewed_term(**base, nuova_fine=date(2027, 3, 15))[1] == date(2027, 3, 15)


def test_month_arithmetic_clamps_to_the_last_day() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 12, 15), 2) == date(2027, 2, 15)


def test_the_term_is_refused_without_an_end_or_when_it_overlaps() -> None:
    from pigrocrm.core.contracts.renewal import RenewalRefused

    with pytest.raises(RenewalRefused) as open_ended:
        renewed_term(inizio=date(2026, 1, 1), fine=None)
    assert open_ended.value.field == "fine"
    with pytest.raises(RenewalRefused) as overlap:
        renewed_term(
            inizio=date(2026, 1, 1), fine=date(2026, 12, 31), nuovo_inizio=date(2026, 12, 31)
        )
    assert overlap.value.field == "inizio"
    with pytest.raises(RenewalRefused) as backwards:
        renewed_term(
            inizio=date(2026, 1, 1), fine=date(2026, 12, 31), nuova_fine=date(2026, 12, 31)
        )
    assert backwards.value.field == "fine"


def test_fine_and_durata_together_are_refused() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ContractRenew(fine=date(2027, 6, 1), durata=RenewalDuration(conteggio=1, unita="mesi"))


# ---- the successor ----------------------------------------------------------------


def test_renewing_a_contract_ending_in_december_links_a_successor_starting_on_1_january(
    db_session: Session,
) -> None:
    """Done when."""
    contract = _year_contract(db_session)

    successor = ContractService(db_session).renew(contract.id, ContractRenew(), ADMIN)

    assert (successor.inizio, successor.fine) == (date(2027, 1, 1), date(2027, 12, 31))
    assert successor.contratto_precedente_id == contract.id
    assert successor.stato == "attivo"
    assert successor.id != contract.id
    detail = ContractService(db_session).detail(contract.id, ADMIN)
    assert detail.contratto_successivo_id == successor.id
    assert ContractService(db_session).get(contract.id, ADMIN).stato == "bozza"


def test_the_successor_has_the_same_terms(db_session: Session) -> None:
    contract = _year_contract(db_session)

    successor = ContractService(db_session).renew(contract.id, ContractRenew(), ADMIN)

    for field in (
        "customer_id",
        "azienda_id",
        "titolo",
        "tipo_rinnovo",
        "preavviso_rinnovo_giorni",
        "preavviso_disdetta_giorni",
        "giorni_pagamento",
        "pagamento_fine_mese",
        "cadenza_fatturazione",
        "divisa",
        "requires_prior_approval",
        "applies_social_charge",
        "tetto_quota_cliente",
        "politica_spese",
        "note",
        "custom_fields",
    ):
        assert getattr(successor, field) == getattr(contract, field), field


def test_a_title_and_dates_can_be_stated(db_session: Session) -> None:
    contract = _year_contract(db_session)

    successor = ContractService(db_session).renew(
        contract.id,
        ContractRenew(
            inizio=date(2027, 2, 1),
            durata=RenewalDuration(conteggio=6, unita="mesi"),
            titolo="Consulenza CTO 2027",
        ),
        ADMIN,
    )

    assert (successor.inizio, successor.fine) == (date(2027, 2, 1), date(2027, 7, 31))
    assert successor.titolo == "Consulenza CTO 2027"


def test_the_rate_cards_that_reach_the_new_term_go_across_and_the_old_ones_stay(
    db_session: Session,
) -> None:
    contract = _year_contract(db_session)
    cards = RateCardService(db_session)
    base = {"tipo": "giornaliero", "unita": "giorno"}
    cards.create(
        contract.id,
        RateCardCreate(
            valido_da=date(2026, 1, 1), valido_a=date(2026, 6, 30), importo=Decimal("400"), **base
        ),
        ADMIN,
    )
    cards.create(
        contract.id,
        RateCardCreate(
            valido_da=date(2026, 7, 1), valido_a=date(2027, 2, 28), importo=Decimal("450"), **base
        ),
        ADMIN,
    )
    cards.create(
        contract.id,
        RateCardCreate(valido_da=date(2027, 3, 1), importo=Decimal("500"), **base),
        ADMIN,
    )

    successor = ContractService(db_session).renew(contract.id, ContractRenew(), ADMIN)

    carried = sorted(cards.list_for_contract(successor.id, ADMIN), key=lambda c: c.valido_da)
    # The card that closed in June is a period that is over; the July one is carried from
    # the term's first day; the one recorded for March keeps its own date.
    assert [(c.valido_da, c.valido_a, c.importo) for c in carried] == [
        (date(2027, 1, 1), date(2027, 2, 28), Decimal("450.00")),
        (date(2027, 3, 1), None, Decimal("500.00")),
    ]
    kept = cards.list_for_contract(contract.id, ADMIN)
    assert [(c.valido_da, c.valido_a) for c in kept] == [
        (date(2026, 1, 1), date(2026, 6, 30)),
        (date(2026, 7, 1), date(2027, 2, 28)),
        (date(2027, 3, 1), None),
    ]


def test_notes_expenses_and_assumptions_do_not_cross(db_session: Session) -> None:
    from pigrocrm.core.contracts.schemas import ClauseNoteCreate

    contract = _year_contract(db_session)
    ClauseNoteService(db_session).create(
        contract.id,
        ClauseNoteCreate(
            riferimento_clausola="Art. 8", testo_letterale="x", interpretazione_adottata="y"
        ),
        ADMIN,
    )

    successor = ContractService(db_session).renew(contract.id, ContractRenew(), ADMIN)

    assert ContractService(db_session).detail(successor.id, ADMIN).note_clausole == []


def test_a_contract_is_renewed_once(db_session: Session) -> None:
    contract = _year_contract(db_session)
    first = ContractService(db_session).renew(contract.id, ContractRenew(), ADMIN)

    with pytest.raises(Conflict) as caught:
        ContractService(db_session).renew(contract.id, ContractRenew(), ADMIN)
    assert caught.value.details["successor_id"] == str(first.id)
    # The chain continues from the successor.
    second = ContractService(db_session).renew(first.id, ContractRenew(), ADMIN)
    assert (second.inizio, second.fine) == (date(2028, 1, 1), date(2028, 12, 31))


def test_an_open_ended_contract_and_an_unknown_one_are_refused(db_session: Session) -> None:
    open_ended = _year_contract(db_session, fine=None)
    with pytest.raises(ValidationFailed) as caught:
        ContractService(db_session).renew(open_ended.id, ContractRenew(), ADMIN)
    assert caught.value.details["field"] == "fine"
    with pytest.raises(NotFound):
        ContractService(db_session).renew(uuid4(), ContractRenew(), ADMIN)


def test_only_a_writer_renews(db_session: Session) -> None:
    contract = _year_contract(db_session)
    with pytest.raises(PermissionDenied):
        ContractService(db_session).renew(
            contract.id, ContractRenew(), Actor(id=None, type="user", role="readonly")
        )


def test_the_renewal_is_on_both_timelines(db_session: Session) -> None:
    contract = _year_contract(db_session)
    successor = ContractService(db_session).renew(contract.id, ContractRenew(), ADMIN)

    old = ActivityService(db_session).timeline("contract", contract.id)
    new = ActivityService(db_session).timeline("contract", successor.id)

    assert old[0].kind == "renewed" and old[0].payload["successore"] == str(successor.id)
    assert new[0].kind == "created" and new[0].payload["rinnova"] == str(contract.id)
    assert (
        db_session.scalars(select(RateCard).where(RateCard.contract_id == successor.id)).all() == []
    )
    assert db_session.get(Contract, successor.id) is not None
