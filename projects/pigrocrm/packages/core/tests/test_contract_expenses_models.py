"""REB-360's own "what the implementing issues must test" for rebillable expenses
(design spec §14): `reimbursable` computed correctly against every combination of
`pre_authorized` and the contract's own `expense_policy`, never rejecting the
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
from fakes.customer_fixtures import make_customer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.contract_expenses.models import ContractExpense
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.timetracking.models import CostCategory


def _contract(db_session: Session, *, expense_policy: dict[str, Any]) -> Contract:
    contract = Contract(
        customer_id=make_customer(db_session).id,
        title="Consulenza",
        start=date(2026, 1, 1),
        renewal_type="none",
        termination_notice_days=30,
        billing_cadence="monthly",
        expense_policy=expense_policy,
    )
    db_session.add(contract)
    db_session.flush()
    return contract


def _category(db_session: Session) -> CostCategory:
    category = CostCategory(nome=f"Categoria {uuid4()}", position=0)
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
        "amount": Decimal("100.00"),
        "description": "Biglietto treno",
        "pre_authorized": False,
    }
    payload.update(overrides)
    expense = ContractExpense(**payload)  # type: ignore[arg-type]
    db_session.add(expense)
    db_session.flush()
    db_session.refresh(expense)  # the trigger may rewrite `reimbursable` in flight
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


def test_not_reimbursable_policy_is_never_reimbursable_even_when_pre_authorised(
    db_session: Session,
) -> None:
    contract = _contract(db_session, expense_policy={"kind": "not_reimbursable"})
    category = _category(db_session)
    expense = _expense(
        db_session,
        contract,
        category,
        pre_authorized=True,
        authorization_reference="email del 2026-03-01",
    )
    assert expense.reimbursable is False


def test_not_reimbursable_policy_is_never_reimbursable_without_pre_authorisation(
    db_session: Session,
) -> None:
    contract = _contract(db_session, expense_policy={"kind": "not_reimbursable"})
    category = _category(db_session)
    expense = _expense(db_session, contract, category, pre_authorized=False)
    assert expense.reimbursable is False


# ---- rimborsabile with no pre-authorisation requirement -----------------------------


def test_reimbursable_with_no_preauth_requirement_is_reimbursable_unauthorised(
    db_session: Session,
) -> None:
    contract = _contract(
        db_session,
        expense_policy={"kind": "reimbursable", "richiede_preautorizzazione": False},
    )
    category = _category(db_session)
    expense = _expense(db_session, contract, category, pre_authorized=False)
    assert expense.reimbursable is True


def test_reimbursable_with_no_preauth_requirement_absent_key_defaults_to_reimbursable(
    db_session: Session,
) -> None:
    """`richiede_preautorizzazione` absent from the JSONB (not merely `false`)
    falls back to `false` too -- mastro's own column default, ported."""
    contract = _contract(db_session, expense_policy={"kind": "reimbursable"})
    category = _category(db_session)
    expense = _expense(db_session, contract, category, pre_authorized=False)
    assert expense.reimbursable is True


# ---- rimborsabile requiring pre-authorisation: the flagged, non-blocking case -------


def test_reimbursable_requiring_preauth_is_reimbursable_when_authorised(
    db_session: Session,
) -> None:
    contract = _contract(
        db_session,
        expense_policy={"kind": "reimbursable", "richiede_preautorizzazione": True},
    )
    category = _category(db_session)
    expense = _expense(
        db_session,
        contract,
        category,
        pre_authorized=True,
        authorization_reference="email del 2026-03-01",
    )
    assert expense.reimbursable is True


def test_reimbursable_requiring_preauth_is_flagged_not_reimbursable_when_missing_it(
    db_session: Session,
) -> None:
    """The non-blocking philosophy: the write succeeds, the expense is just
    flagged -- the same treatment `worked_without_approval` gives an
    unapproved day (spec §5, §7)."""
    contract = _contract(
        db_session,
        expense_policy={"kind": "reimbursable", "richiede_preautorizzazione": True},
    )
    category = _category(db_session)
    expense = _expense(db_session, contract, category, pre_authorized=False)
    assert expense.reimbursable is False


# ---- rimborsabile_con_tetto: the cap does not enter the reimbursable computation ----


def test_reimbursable_with_cap_ignores_the_postal_code_amount_itself(db_session: Session) -> None:
    contract = _contract(
        db_session,
        expense_policy={
            "kind": "reimbursable_with_cap",
            "amount_cap": "50.00",
            "richiede_preautorizzazione": False,
        },
    )
    category = _category(db_session)
    # Well above the cap -- still reimbursable, and still recorded at its own
    # amount: enforcing the cap is explicitly out of this issue's scope (models.py).
    expense = _expense(db_session, contract, category, amount=Decimal("500.00"))
    assert expense.reimbursable is True
    assert expense.amount == Decimal("500.00")


# ---- the redirect fires on update too, not only on insert ---------------------------


def test_reimbursable_is_recomputed_on_update_not_only_on_insert(db_session: Session) -> None:
    contract = _contract(
        db_session,
        expense_policy={"kind": "reimbursable", "richiede_preautorizzazione": True},
    )
    category = _category(db_session)
    expense = _expense(db_session, contract, category, pre_authorized=False)
    assert expense.reimbursable is False

    expense.pre_authorized = True
    expense.authorization_reference = "email del 2026-03-02"
    db_session.flush()
    db_session.refresh(expense)
    assert expense.reimbursable is True


def test_reimbursable_flips_back_to_false_if_pre_authorized_is_unset(
    db_session: Session,
) -> None:
    contract = _contract(
        db_session,
        expense_policy={"kind": "reimbursable", "richiede_preautorizzazione": True},
    )
    category = _category(db_session)
    expense = _expense(
        db_session,
        contract,
        category,
        pre_authorized=True,
        authorization_reference="email del 2026-03-02",
    )
    assert expense.reimbursable is True

    expense.pre_authorized = False
    expense.authorization_reference = None
    db_session.flush()
    db_session.refresh(expense)
    assert expense.reimbursable is False


# ---- a malformed politica_spese fails loudly, not silently --------------------------


def test_to_expense_policy_with_no_kind_tag_is_refused(db_session: Session) -> None:
    contract = _contract(db_session, expense_policy={})
    category = _category(db_session)
    with pytest.raises(IntegrityError):
        _expense(db_session, contract, category)


# ---- CHECK constraints ---------------------------------------------------------------


def test_amount_must_be_positive(db_session: Session) -> None:
    contract = _contract(db_session, expense_policy={"kind": "reimbursable"})
    category = _category(db_session)
    with pytest.raises(IntegrityError):
        _expense(db_session, contract, category, amount=Decimal("0.00"))


def test_reference_required_when_pre_authorized_is_true(db_session: Session) -> None:
    contract = _contract(db_session, expense_policy={"kind": "reimbursable"})
    category = _category(db_session)
    with pytest.raises(IntegrityError):
        _expense(
            db_session,
            contract,
            category,
            pre_authorized=True,
            authorization_reference=None,
        )


def test_reference_forbidden_when_pre_authorized_is_false(db_session: Session) -> None:
    contract = _contract(db_session, expense_policy={"kind": "reimbursable"})
    category = _category(db_session)
    with pytest.raises(IntegrityError):
        _expense(
            db_session,
            contract,
            category,
            pre_authorized=False,
            authorization_reference="email del 2026-03-01",
        )


def test_contract_expenses_has_indexes_on_its_foreign_keys(db_session: Session) -> None:
    from sqlalchemy import inspect

    indexes = {
        idx["name"] for idx in inspect(db_session.get_bind()).get_indexes("contract_expenses")
    }
    assert "ix_contract_expenses_contract_id" in indexes
    assert "ix_contract_expenses_category_id" in indexes
    assert "ix_contract_expenses_invoice_line_id" in indexes
