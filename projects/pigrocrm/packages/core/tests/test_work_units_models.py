"""REB-359's own "what the implementing issues must test" for the day lifecycle
(design spec §14): every legal edge accepted, every illegal edge rejected, the
automatic redirect and recovery, the append-only log surviving a run of transitions
without losing an intermediate state, approval immutability, and the partial index.

Writes go straight through the ORM/raw SQL, deliberately bypassing
`WorkUnitService`, to prove the Done-when's own claim: "a direct SQL UPDATE outside
the service layer obeys the same rules as the API" -- the trigger is what enforces
this, not a service-level check anything could route around.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document
from pigrocrm.core.work_units.models import (
    WORK_UNIT_ENTRY_STATI,
    WORK_UNIT_STATI,
    WORK_UNIT_TRANSITIONS,
    Approval,
    WorkUnit,
    WorkUnitTransition,
)


def _customer(db_session: Session) -> Customer:
    customer = Customer(ragione_sociale=f"ACME {uuid4()}")
    db_session.add(customer)
    db_session.flush()
    return customer


def _contract(db_session: Session, *, requires_prior_approval: bool = False) -> Contract:
    contract = Contract(
        customer_id=_customer(db_session).id,
        titolo="Consulenza",
        inizio=date(2026, 1, 1),
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese={"kind": "non_rimborsabile"},
        requires_prior_approval=requires_prior_approval,
    )
    db_session.add(contract)
    db_session.flush()
    return contract


def _document(db_session: Session, contract: Contract) -> Document:
    document = Document(contract_id=contract.id, tipo="contratto", titolo="Contratto firmato")
    db_session.add(document)
    db_session.flush()
    return document


def _approval(db_session: Session, contract: Contract) -> Approval:
    approval = Approval(
        contract_id=contract.id,
        canale="email",
        mittente="cliente@example.test",
        ricevuto_il=datetime(2026, 3, 1, 9, 0),
        document_id=_document(db_session, contract).id,
        estratto="Confermiamo le giornate di marzo.",
        origine={"kind": "manuale"},
    )
    db_session.add(approval)
    db_session.flush()
    return approval


def _work_unit(db_session: Session, contract: Contract, **overrides: object) -> WorkUnit:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "data": date(2026, 3, 5),
        "quantita": Decimal("1.00"),
        "descrizione": "Giornata di consulenza",
        "stato": "lavorato",
    }
    payload.update(overrides)
    work_unit = WorkUnit(**payload)  # type: ignore[arg-type]
    db_session.add(work_unit)
    db_session.flush()
    # `work_unit_enforce_state_machine` can rewrite `stato` in flight (the redirect,
    # spec §5); refresh so the in-memory object reflects what was actually written,
    # not merely what was requested.
    db_session.refresh(work_unit)
    return work_unit


def _transitions(db_session: Session, work_unit_id: UUID) -> list[WorkUnitTransition]:
    return list(
        db_session.execute(
            text(
                "SELECT id, work_unit_id, stato_precedente, stato_nuovo, attore, motivo, seq "
                "FROM work_unit_transitions WHERE work_unit_id = :wid ORDER BY seq"
            ),
            {"wid": work_unit_id},
        ).mappings()
    )


# ---- entry states -------------------------------------------------------------------


@pytest.mark.parametrize("stato", sorted(WORK_UNIT_ENTRY_STATI))
def test_every_legal_entry_state_is_accepted_on_insert(db_session: Session, stato: str) -> None:
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, stato=stato)
    assert work_unit.stato == stato


@pytest.mark.parametrize(
    "stato",
    sorted(set(WORK_UNIT_TRANSITIONS) - WORK_UNIT_ENTRY_STATI - {"lavorato_senza_approvazione"}),
)
def test_every_non_entry_state_is_refused_on_insert(db_session: Session, stato: str) -> None:
    contract = _contract(db_session)
    with pytest.raises(IntegrityError):
        _work_unit(db_session, contract, stato=stato)


def test_lavorato_senza_approvazione_cannot_be_named_directly_on_insert(
    db_session: Session,
) -> None:
    """The flagged state is reachable only through the redirect -- a caller can never
    manufacture it by naming it outright."""
    contract = _contract(db_session, requires_prior_approval=True)
    with pytest.raises(IntegrityError):
        _work_unit(db_session, contract, stato="lavorato_senza_approvazione")


# ---- the state graph, edge by edge ---------------------------------------------------

# A legal chain of stato-hops from an entry state that reaches each state at least
# once -- used to *set up* a test at the right origin before exercising the one edge
# under test. `lavorato_senza_approvazione` is reached by inserting straight into
# `'lavorato'` on a contract that requires prior approval, with no approval linked
# (the redirect); its own edge back to `'lavorato'` is the recovery, which needs an
# `approval_id` write rather than a plain `stato` assignment, so it is exercised only
# by the dedicated recovery tests above, never through this generic path.
_PATH_TO: dict[str, tuple[str, ...]] = {
    "proposto": ("proposto",),
    "approvato": ("proposto", "approvato"),
    "lavorato": ("proposto", "lavorato"),
    "lavorato_senza_approvazione": ("lavorato",),
    "fatturato": ("proposto", "lavorato", "fatturato"),
    "pagato": ("proposto", "lavorato", "fatturato", "pagato"),
    "contestato": ("proposto", "lavorato", "contestato"),
    "revocato": ("proposto", "revocato"),
    "rifiutato": ("proposto", "rifiutato"),
    "non_fatturabile": ("proposto", "lavorato", "non_fatturabile"),
}


def _drive_to(
    db_session: Session, contract: Contract, target_stato: str, *, giorno: date
) -> WorkUnit:
    path = _PATH_TO[target_stato]
    work_unit = _work_unit(db_session, contract, stato=path[0], data=giorno)
    for step in path[1:]:
        work_unit.stato = step
        db_session.flush()
    db_session.refresh(work_unit)
    assert work_unit.stato == target_stato, f"expected {target_stato}, landed on {work_unit.stato}"
    return work_unit


def test_the_transition_graph_names_every_state_as_an_origin() -> None:
    """Independent of the parametrized sweeps below, which derive their cases from
    `WORK_UNIT_TRANSITIONS` itself and so cannot catch a state missing as a *key*
    entirely (a terminal state still needs an empty `frozenset()` entry, not
    absence) -- this is what actually caught `contestato` being dropped from the
    dict during review, silently making every disputed day permanently stuck."""
    assert set(WORK_UNIT_TRANSITIONS) == set(WORK_UNIT_STATI)


# The one edge excluded everywhere below: reached only by linking an approval, not by
# assigning `stato` directly (`test_linking_an_approval_recovers_...` above covers it).
_RECOVERY_EDGE = ("lavorato_senza_approvazione", "lavorato")

LEGAL_EDGES = sorted(
    (origin, target)
    for origin, targets in WORK_UNIT_TRANSITIONS.items()
    for target in targets
    if (origin, target) != _RECOVERY_EDGE
)


@pytest.mark.parametrize("origin,target", LEGAL_EDGES)
def test_every_legal_edge_is_accepted(db_session: Session, origin: str, target: str) -> None:
    contract = _contract(
        db_session, requires_prior_approval=(origin == "lavorato_senza_approvazione")
    )
    work_unit = _drive_to(db_session, contract, origin, giorno=date(2026, 3, 10))
    work_unit.stato = target
    db_session.flush()
    db_session.refresh(work_unit)
    assert work_unit.stato == target


ILLEGAL_EDGES = sorted(
    (origin, target)
    for origin in WORK_UNIT_TRANSITIONS
    for target in WORK_UNIT_TRANSITIONS
    if origin != target
    and target not in WORK_UNIT_TRANSITIONS[origin]
    and (origin, target) != _RECOVERY_EDGE
)


@pytest.mark.parametrize("origin,target", ILLEGAL_EDGES)
def test_every_illegal_edge_is_refused(db_session: Session, origin: str, target: str) -> None:
    contract = _contract(
        db_session, requires_prior_approval=(origin == "lavorato_senza_approvazione")
    )
    work_unit = _drive_to(db_session, contract, origin, giorno=date(2026, 3, 11))
    with pytest.raises(IntegrityError):
        work_unit.stato = target
        db_session.flush()


# ---- the automatic redirect and recovery (0013) --------------------------------------


def test_lavorato_with_no_approval_on_a_requiring_contract_is_redirected(
    db_session: Session,
) -> None:
    contract = _contract(db_session, requires_prior_approval=True)
    work_unit = _work_unit(db_session, contract, stato="lavorato", approval_id=None)
    assert work_unit.stato == "lavorato_senza_approvazione"


def test_lavorato_with_an_approval_on_a_requiring_contract_is_not_redirected(
    db_session: Session,
) -> None:
    contract = _contract(db_session, requires_prior_approval=True)
    approval = _approval(db_session, contract)
    work_unit = _work_unit(db_session, contract, stato="lavorato", approval_id=approval.id)
    assert work_unit.stato == "lavorato"


def test_lavorato_on_a_non_requiring_contract_is_never_redirected(db_session: Session) -> None:
    contract = _contract(db_session, requires_prior_approval=False)
    work_unit = _work_unit(db_session, contract, stato="lavorato", approval_id=None)
    assert work_unit.stato == "lavorato"


def test_linking_an_approval_recovers_the_flagged_state_automatically(
    db_session: Session,
) -> None:
    """The recovery half: the caller only sets `approval_id`, never `stato` -- the
    trigger itself flips it back to 'lavorato' (spec §5)."""
    contract = _contract(db_session, requires_prior_approval=True)
    work_unit = _work_unit(db_session, contract, stato="lavorato", approval_id=None)
    assert work_unit.stato == "lavorato_senza_approvazione"

    approval = _approval(db_session, contract)
    work_unit.approval_id = approval.id
    db_session.flush()
    db_session.refresh(work_unit)

    assert work_unit.stato == "lavorato"


def test_the_redirect_also_fires_on_update_not_only_insert(db_session: Session) -> None:
    contract = _contract(db_session, requires_prior_approval=True)
    work_unit = _work_unit(db_session, contract, stato="proposto")
    work_unit.stato = "lavorato"
    db_session.flush()
    db_session.refresh(work_unit)
    assert work_unit.stato == "lavorato_senza_approvazione"


# ---- the partial unique index --------------------------------------------------------


def test_two_live_work_units_on_the_same_contract_and_date_are_refused(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    _work_unit(db_session, contract, data=date(2026, 5, 1), stato="proposto")
    with pytest.raises(IntegrityError):
        _work_unit(db_session, contract, data=date(2026, 5, 1), stato="proposto")


def test_two_work_units_on_different_dates_are_both_accepted(db_session: Session) -> None:
    contract = _contract(db_session)
    first = _work_unit(db_session, contract, data=date(2026, 5, 1), stato="proposto")
    second = _work_unit(db_session, contract, data=date(2026, 5, 2), stato="proposto")
    assert first.id != second.id


def test_revoking_a_day_frees_its_date_for_a_fresh_proposal(db_session: Session) -> None:
    contract = _contract(db_session)
    first = _work_unit(db_session, contract, data=date(2026, 5, 1), stato="proposto")
    first.stato = "revocato"
    db_session.flush()
    second = _work_unit(db_session, contract, data=date(2026, 5, 1), stato="proposto")
    assert second.id != first.id


def test_a_non_fatturabile_day_still_occupies_its_date(db_session: Session) -> None:
    """`non_fatturabile` is excluded from nothing: it is still real, recorded work."""
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, data=date(2026, 5, 1), stato="lavorato")
    work_unit.stato = "non_fatturabile"
    db_session.flush()
    with pytest.raises(IntegrityError):
        _work_unit(db_session, contract, data=date(2026, 5, 1), stato="proposto")


# ---- the append-only transition log ---------------------------------------------------


def test_insert_writes_exactly_one_transition_with_a_null_predecessor(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, stato="proposto")
    rows = _transitions(db_session, work_unit.id)
    assert len(rows) == 1
    assert rows[0]["stato_precedente"] is None
    assert rows[0]["stato_nuovo"] == "proposto"
    # `seq` is a real Postgres identity column, global to the table and -- like any
    # sequence -- exempt from transactional rollback, so its absolute value drifts
    # across this file's other tests; only that it exists and orders correctly
    # (proven below, across several transitions of one day) is this row's own to show.
    assert isinstance(rows[0]["seq"], int)


def test_a_run_of_transitions_logs_every_intermediate_state_in_order(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, stato="proposto")
    for stato in ("approvato", "lavorato", "fatturato", "pagato"):
        work_unit.stato = stato
        db_session.flush()

    rows = _transitions(db_session, work_unit.id)
    assert [r["stato_nuovo"] for r in rows] == [
        "proposto",
        "approvato",
        "lavorato",
        "fatturato",
        "pagato",
    ]
    assert [r["stato_precedente"] for r in rows] == [
        None,
        "proposto",
        "approvato",
        "lavorato",
        "fatturato",
    ]
    assert [r["seq"] for r in rows] == sorted(r["seq"] for r in rows)


def test_editing_a_field_other_than_stato_writes_no_transition(db_session: Session) -> None:
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, stato="proposto")
    work_unit.note = "aggiornamento"
    db_session.flush()
    rows = _transitions(db_session, work_unit.id)
    assert len(rows) == 1  # only the INSERT


def test_actor_and_motivo_reach_the_log_through_session_local_settings(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, stato="proposto")
    db_session.execute(
        text("SELECT set_config('pigrocrm.actor', :a, true)"),
        {"a": '{"kind": "human", "id": "' + str(uuid4()) + '"}'},
    )
    db_session.execute(
        text("SELECT set_config('pigrocrm.motivo', :m, true)"),
        {"m": "il cliente ha confermato via email"},
    )
    work_unit.stato = "approvato"
    db_session.flush()

    rows = _transitions(db_session, work_unit.id)
    latest = rows[-1]
    assert latest["attore"]["kind"] == "human"
    assert latest["motivo"] == "il cliente ha confermato via email"


def test_unset_actor_and_motivo_fall_back_rather_than_failing(db_session: Session) -> None:
    """The log must never go silently incomplete (spec §5, `0012...sql:97-105`)."""
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, stato="proposto")
    rows = _transitions(db_session, work_unit.id)
    assert rows[0]["attore"] == {"kind": "system"}
    assert rows[0]["motivo"] == "nessun motivo fornito"


# ---- immutability ---------------------------------------------------------------------


def test_an_approval_cannot_be_updated(db_session: Session) -> None:
    contract = _contract(db_session)
    approval = _approval(db_session, contract)
    approval.estratto = "versione corretta"
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_a_transition_row_cannot_be_updated(db_session: Session) -> None:
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, stato="proposto")
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE work_unit_transitions SET motivo = 'riscritto' WHERE work_unit_id = :wid"),
            {"wid": work_unit.id},
        )


def test_a_transition_row_cannot_be_deleted(db_session: Session) -> None:
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, stato="proposto")
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("DELETE FROM work_unit_transitions WHERE work_unit_id = :wid"),
            {"wid": work_unit.id},
        )


def test_a_correction_to_an_approval_is_a_new_row_not_an_edit(db_session: Session) -> None:
    contract = _contract(db_session)
    original = _approval(db_session, contract)
    corrected = Approval(
        contract_id=contract.id,
        canale=original.canale,
        mittente=original.mittente,
        ricevuto_il=original.ricevuto_il,
        document_id=original.document_id,
        estratto="testo corretto",
        origine={"kind": "manuale"},
    )
    db_session.add(corrected)
    db_session.flush()
    assert corrected.id != original.id
    assert original.estratto != corrected.estratto
