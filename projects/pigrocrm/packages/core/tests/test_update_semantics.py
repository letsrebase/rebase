"""A14, closed. Three spellings, three distinct outcomes, on every service.

Before this change `model_dump(exclude_none=True)` collapsed "supplied as null" into
"omitted", so no `Update` schema could clear a numeric or date column. On
`deals.ore_preventivate` that is not an inconvenience but a correctness defect: `NULL`
means "nobody estimated" and `0` means "estimated zero hours", the budget report treats
them as opposite, and only `0` was writable -- so a wrong estimate stayed stuck and read
as an infinite overrun.

The frontend contract is unchanged and stays as plan 1B fixed it: a native text column
clears on `""`, a custom field clears on `null`, an omitted key clears nothing. What
changes is that a native *numeric* or *date* column now clears on `null`, which nothing
could express before.

The fourth outcome is a refusal, and it is new here too: a `null` aimed at a `NOT NULL`
column used to be silently discarded by `exclude_none`, and would now reach `flush()` as
a raw `IntegrityError` that poisons the caller's session. `reject_cleared_columns` turns
it back into this project's own `ValidationFailed`, before any assignment happens.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.schemas import CustomerCreate, CustomerUpdate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.schemas import DealUpdate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.timetracking.costs import CostService
from pigrocrm.core.timetracking.schemas import CostCreate, CostUpdate, TimeEntryUpdate
from pigrocrm.core.timetracking.service import TimeEntryService

WRITER = Actor(id=None, type="user", role="collaboratore")


def test_an_omitted_key_changes_nothing(db_session: Session, seeded_deal_id: UUID) -> None:
    service = DealService(db_session)
    service.update(
        seeded_deal_id,
        DealUpdate(ore_preventivate=Decimal("100.00"), valore_preventivato=Decimal("10000.00")),
        WRITER,
    )
    after = service.update(seeded_deal_id, DealUpdate(nome="Rinominato"), WRITER)
    assert after.ore_preventivate == Decimal("100.00")
    assert after.valore_preventivato == Decimal("10000.00")


def test_a_key_supplied_as_null_clears_a_numeric_column(
    db_session: Session, seeded_deal_id: UUID
) -> None:
    """The state that was unreachable. `model_fields_set` is what distinguishes an
    explicit `null` from an absent key -- `exclude_none` cannot, because by the time it
    runs both are `None`."""
    service = DealService(db_session)
    service.update(seeded_deal_id, DealUpdate(ore_preventivate=Decimal("100.00")), WRITER)
    cleared = service.update(
        seeded_deal_id, DealUpdate.model_validate({"ore_preventivate": None}), WRITER
    )
    assert cleared.ore_preventivate is None


def test_a_key_supplied_as_null_clears_a_date_column(
    db_session: Session, seeded_deal_id: UUID
) -> None:
    service = DealService(db_session)
    service.update(seeded_deal_id, DealUpdate(data_chiusura_prevista=date(2026, 12, 31)), WRITER)
    cleared = service.update(
        seeded_deal_id, DealUpdate.model_validate({"data_chiusura_prevista": None}), WRITER
    )
    assert cleared.data_chiusura_prevista is None


def test_empty_string_still_clears_a_text_column(db_session: Session, seeded_deal_id: UUID) -> None:
    """Unchanged, and it must stay unchanged: plan 1B's form contract is built on it and
    every existing form sends `""` for a cleared native text field."""
    service = CustomerService(db_session)
    customer = service.create(CustomerCreate(ragione_sociale="ACME", telefono="02 1234567"), WRITER)
    assert service.update(customer.id, CustomerUpdate(telefono=""), WRITER).telefono == ""


def test_a_nullable_foreign_key_is_still_validated_when_supplied(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """The trap this change opens if applied carelessly: with `exclude_none`, an
    explicit `owner_id: null` never reached `_check_owner`. Now it does, and it must be
    treated as "clear it" rather than sent to the repository as a lookup for `None` --
    which would raise `NotFound("user", None)`."""
    service = DealService(db_session)
    service.update(seeded_deal_id, DealUpdate(owner_id=seeded_user_id), WRITER)
    cleared = service.update(seeded_deal_id, DealUpdate.model_validate({"owner_id": None}), WRITER)
    assert cleared.owner_id is None
    with pytest.raises(NotFound):
        service.update(seeded_deal_id, DealUpdate(owner_id=uuid4()), WRITER)


def test_custom_fields_semantics_are_untouched(db_session: Session, seeded_deal_id: UUID) -> None:
    """`custom_fields` is excluded from the dump in every service and handled
    separately, exactly as before: a `None` *inside* the dict removes that key, and that
    contract must not shift under this change."""
    service = DealService(db_session)
    assert (
        service.update(
            seeded_deal_id, DealUpdate.model_validate({"custom_fields": None}), WRITER
        ).custom_fields
        == {}
    )


def test_a_null_aimed_at_a_not_null_column_is_refused(
    db_session: Session, seeded_deal_id: UUID
) -> None:
    """The consequence `exclude_none` was accidentally hiding. `deals.nome` is `NOT
    NULL`; `{"nome": null}` used to be dropped on the floor, and with `exclude_unset` it
    would instead reach `flush()` as a raw `IntegrityError` -- which does not merely fail
    the call, it poisons the caller's session for every statement after it. The refusal
    happens before any assignment, and names the field."""
    service = DealService(db_session)
    with pytest.raises(ValidationFailed) as exc:
        service.update(seeded_deal_id, DealUpdate.model_validate({"nome": None}), WRITER)
    assert exc.value.details["field"] == "nome"


def test_a_cleared_rate_is_manual_absence_and_never_re_resolves(
    db_session: Session, seeded_entry_id: UUID
) -> None:
    """Clearing a rate is a deliberate act ("this hour has no price"), so the origin
    becomes `assente` and stays frozen. Silently re-resolving it to the deal's rate would
    be the "a report re-reads a rate column" failure §5 exists to prevent."""
    service = TimeEntryService(db_session)
    cleared = service.update(
        seeded_entry_id, TimeEntryUpdate.model_validate({"tariffa_applicata": None}), WRITER
    )
    assert cleared.tariffa_applicata is None
    assert cleared.tariffa_origine == "assente"


def test_a_supplied_rate_is_still_frozen_as_manual(
    db_session: Session, seeded_entry_id: UUID
) -> None:
    service = TimeEntryService(db_session)
    updated = service.update(
        seeded_entry_id, TimeEntryUpdate(tariffa_applicata=Decimal("120.000000")), WRITER
    )
    assert updated.tariffa_applicata == Decimal("120.000000")
    assert updated.tariffa_origine == "manuale"


def test_an_emptied_cost_amount_is_refused_by_name(
    db_session: Session, seeded_deal_id: UUID, seeded_category_id: UUID
) -> None:
    """`costs.importo` is `NOT NULL`, and a cleared amount is not a zero amount: both are
    refused, but with the message that names the amount rather than the generic one."""
    service = CostService(db_session)
    cost = service.create(
        CostCreate(
            deal_id=seeded_deal_id,
            category_id=seeded_category_id,
            data=date(2026, 3, 10),
            importo=Decimal("100.00"),
            descrizione="Hosting",
        ),
        WRITER,
    )
    with pytest.raises(ValidationFailed) as exc:
        service.update(cost.id, CostUpdate.model_validate({"importo": None}), WRITER)
    assert exc.value.details["reason"] == "l'importo non può essere svuotato"


def test_a_nullable_cost_reference_clears_without_a_lookup(
    db_session: Session, seeded_deal_id: UUID, seeded_category_id: UUID
) -> None:
    """`_check_refs` already took values rather than keys, so an explicit `deal_id: null`
    passes straight through it instead of becoming `NotFound("deal", None)`."""
    service = CostService(db_session)
    cost = service.create(
        CostCreate(
            deal_id=seeded_deal_id,
            category_id=seeded_category_id,
            data=date(2026, 3, 10),
            importo=Decimal("100.00"),
            descrizione="Hosting",
        ),
        WRITER,
    )
    assert (
        service.update(cost.id, CostUpdate.model_validate({"deal_id": None}), WRITER).deal_id
        is None
    )
