"""REB-372: `unbilled_backlog`'s own `work_units` contribution, narrowed to
approved-or-later time.

`TimeEntry` has no state machine at all (REB-352 §1.2) and keeps counting every
billable, unbilled hour unconditionally -- `test_unbilled_backlog.py` already pins
that half. `work_units` is the one source that carries an approval state
(REB-358/REB-359), so this file pins the one thing REB-372 adds: a day only
contributes to the committed backlog once it has left `proposto` for an
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
    customer = Customer(ragione_sociale=f"ACME {uuid4()}")
    db_session.add(customer)
    db_session.flush()
    payload: dict[str, object] = {
        "customer_id": customer.id,
        "titolo": "Consulenza",
        "inizio": date(2026, 1, 1),
        "tipo_rinnovo": "nessuno",
        "preavviso_disdetta_giorni": 30,
        "cadenza_fatturazione": "mensile",
        "politica_spese": {"kind": "non_rimborsabile"},
    }
    payload.update(overrides)
    contract = Contract(**payload)  # type: ignore[arg-type]
    db_session.add(contract)
    db_session.flush()
    return contract


def _rate_card(db_session: Session, contract: Contract, **overrides: object) -> RateCard:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "valido_da": date(2026, 1, 1),
        "valido_a": None,
        "tipo": "giornaliero",
        "importo": Decimal("500.00"),
        "unita": "giorno",
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
        "quantita": Decimal("1.00"),
        "descrizione": "Giornata di consulenza",
        "stato": "lavorato",
    }
    payload.update(overrides)
    work_unit = WorkUnit(**payload)  # type: ignore[arg-type]
    db_session.add(work_unit)
    db_session.flush()
    # `work_unit_enforce_state_machine` can rewrite `stato` in flight (the
    # `lavorato` -> `lavorato_senza_approvazione` redirect, spec §5) -- refresh so
    # the returned object reflects what was actually written, not merely requested.
    db_session.refresh(work_unit)
    return work_unit


def _bare_invoice_line(db_session: Session, contract: Contract) -> InvoiceLine:
    invoice = Invoice(customer_id=contract.customer_id, tipo="fattura", stato="bozza")
    db_session.add(invoice)
    db_session.flush()
    line = InvoiceLine(
        invoice_id=invoice.id,
        numero_linea=1,
        descrizione="Riga esistente",
        quantita=Decimal("1.000000"),
        prezzo_unitario=Decimal("1.000000"),
        prezzo_totale=Decimal("1.00"),
        aliquota_iva=Decimal("22.00"),
    )
    db_session.add(line)
    db_session.flush()
    return line


# ---- the Done-when, literally: approved counts, merely-logged does not -----------


def test_an_approved_day_counts_towards_the_backlog(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, stato="approvato")

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.valore_maturato == Decimal("500.00")
    assert backlog.voci == 1


def test_a_merely_proposed_day_does_not_count(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, stato="proposto")

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.valore_maturato == Decimal("0.00")
    assert backlog.voci == 0


# ---- the rest of the state machine ------------------------------------------------


def test_a_worked_day_counts_the_same_as_an_approved_one(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, stato="lavorato")

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.valore_maturato == Decimal("500.00")
    assert backlog.voci == 1


def test_a_disputed_day_still_counts(db_session: Session) -> None:
    """`contestato` is later in the lifecycle than `approvato`/`lavorato`, and mastro's
    own citation (spec §1.2) names it as one of the three qualifying states -- a
    dispute over an already-worked day does not retract the fact that the work
    happened and was billable."""
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, stato="lavorato")
    db_session.flush()
    work_unit = db_session.query(WorkUnit).filter_by(contract_id=contract.id).one()
    work_unit.stato = "contestato"
    db_session.flush()

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.valore_maturato == Decimal("500.00")
    assert backlog.voci == 1


def test_a_day_marked_fatturato_with_no_invoice_line_does_not_count(db_session: Session) -> None:
    """`fatturato` says "already billed" on its own -- `WorkUnitService.transition`
    (the generic, already-public state mover) has no coupling to `invoice_line_id`
    at all, so a day can reach this state through it while the link stays NULL.
    Relying on the `invoice_line_id IS NULL` filter alone to keep such a row out of
    the backlog would be relying on an invariant this codebase does not enforce; the
    state itself has to be excluded."""
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, stato="lavorato")
    db_session.flush()
    work_unit = db_session.query(WorkUnit).filter_by(contract_id=contract.id).one()
    work_unit.stato = "fatturato"
    db_session.flush()
    assert work_unit.invoice_line_id is None

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.valore_maturato == Decimal("0.00")
    assert backlog.voci == 0


def test_a_day_marked_pagato_with_no_invoice_line_does_not_count(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, stato="lavorato")
    db_session.flush()
    work_unit = db_session.query(WorkUnit).filter_by(contract_id=contract.id).one()
    work_unit.stato = "fatturato"
    db_session.flush()
    work_unit.stato = "pagato"
    db_session.flush()
    assert work_unit.invoice_line_id is None

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.valore_maturato == Decimal("0.00")
    assert backlog.voci == 0


def test_a_day_worked_without_the_required_approval_does_not_count(db_session: Session) -> None:
    """The trigger's own automatic redirect (REB-359 §5/§12): a `'lavorato'` write with
    no `approval_id`, on a contract that requires prior approval, is recorded as
    `lavorato_senza_approvazione` rather than rejected outright -- and that flagged,
    unapproved branch is exactly the ambiguity this issue exists to keep out of the
    committed figure."""
    contract = _contract(db_session, requires_prior_approval=True)
    _rate_card(db_session, contract)
    work_unit = _work_unit(db_session, contract, stato="lavorato", approval_id=None)
    assert work_unit.stato == "lavorato_senza_approvazione"

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.valore_maturato == Decimal("0.00")
    assert backlog.voci == 0


def test_a_rejected_day_does_not_count(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, stato="proposto")
    db_session.flush()
    work_unit = db_session.query(WorkUnit).filter_by(contract_id=contract.id).one()
    work_unit.stato = "rifiutato"
    db_session.flush()

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.valore_maturato == Decimal("0.00")
    assert backlog.voci == 0


def test_a_written_off_day_does_not_count(db_session: Session) -> None:
    """`non_fatturabile` is real, recorded work -- just declared unbillable, the
    opposite of committed revenue."""
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, stato="lavorato")
    db_session.flush()
    work_unit = db_session.query(WorkUnit).filter_by(contract_id=contract.id).one()
    work_unit.stato = "non_fatturabile"
    db_session.flush()

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.valore_maturato == Decimal("0.00")
    assert backlog.voci == 0


def test_an_already_invoiced_day_is_not_double_counted(db_session: Session) -> None:
    """`invoice_line_id IS NULL` is the same "not yet invoiced" fact the `TimeEntry`
    half of this figure already keys on -- an approved day already bound to a line
    has left the backlog, whatever its own `stato` says."""
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    existing_line = _bare_invoice_line(db_session, contract)
    _work_unit(db_session, contract, stato="approvato", invoice_line_id=existing_line.id)

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.valore_maturato == Decimal("0.00")
    assert backlog.voci == 0


# ---- pricing, missing coverage, and composing with the TimeEntry half ------------


def test_a_day_with_no_covering_rate_card_counts_but_prices_at_nothing(
    db_session: Session,
) -> None:
    """A read-only aggregate must survive a coverage gap rather than raise --
    `unbilled_work_unit_lines`'s own `ValidationFailed` is the right refusal for an
    actual invoicing attempt, not for a dashboard figure."""
    contract = _contract(db_session)
    _rate_card(db_session, contract, valido_da=date(2026, 1, 1), valido_a=date(2026, 2, 28))
    _work_unit(db_session, contract, stato="approvato", data=date(2026, 3, 5))

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.valore_maturato == Decimal("0.00")
    assert backlog.voci == 1
    assert backlog.voci_senza_tariffa == 1


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
        ore=Decimal("10.00"),
        descrizione="Ore fatturabili",
        fatturabile=True,
        tariffa_applicata=Decimal("50.000000"),
        tariffa_origine="manuale",
        costo_origine="assente",
    )
    db_session.add(entry)
    db_session.flush()

    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, stato="approvato")

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.ore_fatturabili_non_fatturate == Decimal("10.00")
    assert backlog.valore_maturato == Decimal("1000.00")
    assert backlog.voci == 2


def test_customer_id_scopes_work_units_through_their_contract(db_session: Session) -> None:
    in_scope = _contract(db_session)
    out_of_scope = _contract(db_session)
    _rate_card(db_session, in_scope)
    _rate_card(db_session, out_of_scope)
    _work_unit(db_session, in_scope, stato="approvato")
    _work_unit(db_session, out_of_scope, stato="approvato")

    ore, valore, senza_tariffa, voci = AnalyticsRepository(db_session).unbilled_backlog(
        customer_id=in_scope.customer_id
    )
    assert valore == Decimal("500.00")
    assert voci == 1
    assert ore == Decimal("0.00")
    assert senza_tariffa == 0


def test_date_window_scopes_work_units_by_their_own_data(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, stato="approvato", data=date(2026, 3, 5))
    _work_unit(db_session, contract, stato="approvato", data=date(2026, 4, 5))

    _, valore, _, voci = AnalyticsRepository(db_session).unbilled_backlog(
        da=date(2026, 3, 1), a=date(2026, 3, 31)
    )
    assert valore == Decimal("500.00")
    assert voci == 1
