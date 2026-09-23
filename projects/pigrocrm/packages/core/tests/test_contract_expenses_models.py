"""REB-360's own "what the implementing issues must test" for rebillable expenses
(design spec §14): `rimborsabile` computed correctly against every combination of
`pre_autorizzata` and the contract's own `politica_spese`, never rejecting the
write itself -- on insert *and* on update, mirroring the day lifecycle's own
"the redirect also fires on update, not only insert" discipline.

Writes go straight through the ORM, deliberately bypassing
`ContractExpenseService`, to prove the trigger -- not a service-level check
anything could route around -- is what enforces this: a direct SQL write obeys the
identical rule.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.contract_expenses.models import ContractExpense
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.timetracking.models import CostCategory


def _customer(db_session: Session) -> Customer:
    customer = Customer(ragione_sociale=f"ACME {uuid4()}")
    db_session.add(customer)
    db_session.flush()
    return customer


def _contract(db_session: Session, *, politica_spese: dict[str, Any]) -> Contract:
    contract = Contract(
        customer_id=_customer(db_session).id,
        titolo="Consulenza",
        inizio=date(2026, 1, 1),
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese=politica_spese,
    )
    db_session.add(contract)
    db_session.flush()
    return contract


def _category(db_session: Session) -> CostCategory:
    category = CostCategory(nome=f"Categoria {uuid4()}", posizione=0)
    db_session.add(category)
    db_session.flush()
    return category


def _expense(
    db_session: Session, contract: Contract, category: CostCategory, **overrides: object
) -> ContractExpense:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "category_id": category.id,
        "data": date(2026, 3, 5),
        "importo": Decimal("100.00"),
        "descrizione": "Biglietto treno",
        "pre_autorizzata": False,
    }
    payload.update(overrides)
    expense = ContractExpense(**payload)  # type: ignore[arg-type]
    db_session.add(expense)
    db_session.flush()
    db_session.refresh(expense)  # the trigger may rewrite `rimborsabile` in flight
    return expense


# ---- the trigger reads an unknown contract ------------------------------------------


def test_an_expense_on_an_unknown_contract_is_refused(
    db_session: Session,
) -> None:
    category = _category(db_session)
    with pytest.raises(IntegrityError):
        _expense(
            db_session,
            Contract(id=uuid4()),  # detached, never flushed: contract_id is real but absent
            category,
        )


# ---- non_rimborsabile: never reimbursable, regardless of pre-authorisation ----------


def test_non_rimborsabile_policy_is_never_reimbursable_even_when_pre_authorised(
    db_session: Session,
) -> None:
    contract = _contract(db_session, politica_spese={"kind": "non_rimborsabile"})
    category = _category(db_session)
    expense = _expense(
        db_session,
        contract,
        category,
        pre_autorizzata=True,
        riferimento_autorizzazione="email del 2026-03-01",
    )
    assert expense.rimborsabile is False


def test_non_rimborsabile_policy_is_never_reimbursable_without_pre_authorisation(
    db_session: Session,
) -> None:
    contract = _contract(db_session, politica_spese={"kind": "non_rimborsabile"})
    category = _category(db_session)
    expense = _expense(db_session, contract, category, pre_autorizzata=False)
    assert expense.rimborsabile is False


# ---- rimborsabile with no pre-authorisation requirement -----------------------------


def test_rimborsabile_with_no_preauth_requirement_is_reimbursable_unauthorised(
    db_session: Session,
) -> None:
    contract = _contract(
        db_session,
        politica_spese={"kind": "rimborsabile", "richiede_preautorizzazione": False},
    )
    category = _category(db_session)
    expense = _expense(db_session, contract, category, pre_autorizzata=False)
    assert expense.rimborsabile is True


def test_rimborsabile_with_no_preauth_requirement_absent_key_defaults_to_reimbursable(
    db_session: Session,
) -> None:
    """`richiede_preautorizzazione` absent from the JSONB (not merely `false`)
    falls back to `false` too -- mastro's own column default, ported."""
    contract = _contract(db_session, politica_spese={"kind": "rimborsabile"})
    category = _category(db_session)
    expense = _expense(db_session, contract, category, pre_autorizzata=False)
    assert expense.rimborsabile is True


# ---- rimborsabile requiring pre-authorisation: the flagged, non-blocking case -------


def test_rimborsabile_requiring_preauth_is_reimbursable_when_authorised(
    db_session: Session,
) -> None:
    contract = _contract(
        db_session,
        politica_spese={"kind": "rimborsabile", "richiede_preautorizzazione": True},
    )
    category = _category(db_session)
    expense = _expense(
        db_session,
        contract,
        category,
        pre_autorizzata=True,
        riferimento_autorizzazione="email del 2026-03-01",
    )
    assert expense.rimborsabile is True


def test_rimborsabile_requiring_preauth_is_flagged_not_reimbursable_when_missing_it(
    db_session: Session,
) -> None:
    """The non-blocking philosophy: the write succeeds, the expense is just
    flagged -- the same treatment `lavorato_senza_approvazione` gives an
    unapproved day (spec §5, §7)."""
    contract = _contract(
        db_session,
        politica_spese={"kind": "rimborsabile", "richiede_preautorizzazione": True},
    )
    category = _category(db_session)
    expense = _expense(db_session, contract, category, pre_autorizzata=False)
    assert expense.rimborsabile is False


# ---- rimborsabile_con_tetto: the cap does not enter the reimbursable computation ----


def test_rimborsabile_con_tetto_ignores_the_cap_amount_itself(db_session: Session) -> None:
    contract = _contract(
        db_session,
        politica_spese={
            "kind": "rimborsabile_con_tetto",
            "importo_tetto": "50.00",
            "richiede_preautorizzazione": False,
        },
    )
    category = _category(db_session)
    # Well above the cap -- still reimbursable, and still recorded at its own
    # amount: enforcing the cap is explicitly out of this issue's scope (models.py).
    expense = _expense(db_session, contract, category, importo=Decimal("500.00"))
    assert expense.rimborsabile is True
    assert expense.importo == Decimal("500.00")


# ---- the redirect fires on update too, not only on insert ---------------------------


def test_rimborsabile_is_recomputed_on_update_not_only_on_insert(db_session: Session) -> None:
    contract = _contract(
        db_session,
        politica_spese={"kind": "rimborsabile", "richiede_preautorizzazione": True},
    )
    category = _category(db_session)
    expense = _expense(db_session, contract, category, pre_autorizzata=False)
    assert expense.rimborsabile is False

    expense.pre_autorizzata = True
    expense.riferimento_autorizzazione = "email del 2026-03-02"
    db_session.flush()
    db_session.refresh(expense)
    assert expense.rimborsabile is True


def test_rimborsabile_flips_back_to_false_if_pre_autorizzata_is_unset(
    db_session: Session,
) -> None:
    contract = _contract(
        db_session,
        politica_spese={"kind": "rimborsabile", "richiede_preautorizzazione": True},
    )
    category = _category(db_session)
    expense = _expense(
        db_session,
        contract,
        category,
        pre_autorizzata=True,
        riferimento_autorizzazione="email del 2026-03-02",
    )
    assert expense.rimborsabile is True

    expense.pre_autorizzata = False
    expense.riferimento_autorizzazione = None
    db_session.flush()
    db_session.refresh(expense)
    assert expense.rimborsabile is False


# ---- a malformed politica_spese fails loudly, not silently --------------------------


def test_a_politica_spese_with_no_kind_tag_is_refused(db_session: Session) -> None:
    contract = _contract(db_session, politica_spese={})
    category = _category(db_session)
    with pytest.raises(IntegrityError):
        _expense(db_session, contract, category)


# ---- CHECK constraints ---------------------------------------------------------------


def test_importo_must_be_positive(db_session: Session) -> None:
    contract = _contract(db_session, politica_spese={"kind": "rimborsabile"})
    category = _category(db_session)
    with pytest.raises(IntegrityError):
        _expense(db_session, contract, category, importo=Decimal("0.00"))


def test_riferimento_required_when_pre_autorizzata_is_true(db_session: Session) -> None:
    contract = _contract(db_session, politica_spese={"kind": "rimborsabile"})
    category = _category(db_session)
    with pytest.raises(IntegrityError):
        _expense(
            db_session,
            contract,
            category,
            pre_autorizzata=True,
            riferimento_autorizzazione=None,
        )


def test_riferimento_forbidden_when_pre_autorizzata_is_false(db_session: Session) -> None:
    contract = _contract(db_session, politica_spese={"kind": "rimborsabile"})
    category = _category(db_session)
    with pytest.raises(IntegrityError):
        _expense(
            db_session,
            contract,
            category,
            pre_autorizzata=False,
            riferimento_autorizzazione="email del 2026-03-01",
        )


def test_contract_expenses_has_indexes_on_its_foreign_keys(db_session: Session) -> None:
    from sqlalchemy import inspect

    indexes = {
        idx["name"] for idx in inspect(db_session.get_bind()).get_indexes("contract_expenses")
    }
    assert "ix_contract_expenses_contract_id" in indexes
    assert "ix_contract_expenses_category_id" in indexes
    assert "ix_contract_expenses_invoice_line_id" in indexes
