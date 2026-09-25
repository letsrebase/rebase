from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.schemas import (
    DECIMAL_PLACES,
    NOME_MAX_LENGTH,
    ORE_MAX_DIGITS,
    VALORE_MAX_DIGITS,
    DealCreate,
    DealListQuery,
    DealUpdate,
)
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.fields.schemas import FieldDefinitionCreate
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.pipeline.service import PipelineService

ADMIN = Actor(id=None, type="system", role="admin")
COLLAB = Actor(id=None, type="user", role="collaboratore")
READONLY = Actor(id=None, type="user", role="readonly")


@pytest.fixture
def customer_id(db_session: Session):
    return CustomerService(db_session).create(CustomerCreate(ragione_sociale="ACME"), ADMIN).id


@pytest.fixture
def owner_id(db_session: Session):
    return (
        UserService(db_session)
        .create(
            UserCreate(email="owner@example.it", password="supersegreta1", nome="Owner"),
            ADMIN,
        )
        .id
    )


@pytest.fixture
def stages(db_session: Session):
    return {s.nome: s for s in PipelineService(db_session).seed_defaults(ADMIN)}


# --- Brief's own tests (Step 1), unchanged --------------------------------------


def test_a_deal_requires_a_customer(db_session: Session, customer_id, stages) -> None:
    deal = DealService(db_session).create(
        DealCreate(nome="Progetto X", customer_id=customer_id), ADMIN
    )
    assert deal.customer_id == customer_id


def test_a_deal_for_a_missing_customer_is_rejected(db_session: Session, stages) -> None:
    with pytest.raises(NotFound) as exc:
        DealService(db_session).create(DealCreate(nome="X", customer_id=uuid4()), ADMIN)
    assert exc.value.details["entity"] == "customer"


def test_a_new_deal_lands_in_the_first_stage_by_default(
    db_session: Session, customer_id, stages
) -> None:
    deal = DealService(db_session).create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    assert deal.pipeline_stage_id == stages["Lead"].id


def test_a_new_deal_inherits_the_stage_default_probability(
    db_session: Session, customer_id, stages
) -> None:
    deal = DealService(db_session).create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    assert deal.probabilita == stages["Lead"].probabilita_default


def test_an_explicit_probability_wins_over_the_stage_default(
    db_session: Session, customer_id, stages
) -> None:
    deal = DealService(db_session).create(
        DealCreate(nome="X", customer_id=customer_id, probabilita=42), ADMIN
    )
    assert deal.probabilita == 42


def test_money_keeps_two_decimals_and_does_not_drift(
    db_session: Session, customer_id, stages
) -> None:
    """Float would turn 1234.56 into 1234.5599999. On an invoice that is a bug."""
    deal = DealService(db_session).create(
        DealCreate(nome="X", customer_id=customer_id, valore_previsto=Decimal("1234.56")), ADMIN
    )
    assert deal.valore_previsto == Decimal("1234.56")


def test_probability_outside_range_is_rejected(db_session: Session, customer_id, stages) -> None:
    with pytest.raises(ValidationFailed) as exc:
        DealService(db_session).create(
            DealCreate(nome="X", customer_id=customer_id, probabilita=150), ADMIN
        )
    assert exc.value.details["field"] == "probabilita"


def test_negative_value_is_rejected(db_session: Session, customer_id, stages) -> None:
    with pytest.raises(ValidationFailed):
        DealService(db_session).create(
            DealCreate(nome="X", customer_id=customer_id, valore_previsto=Decimal("-1")), ADMIN
        )


def test_move_stage_updates_the_deal_and_the_timeline(
    db_session: Session, customer_id, stages
) -> None:
    from pigrocrm.core.activities.service import ActivityService

    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    moved = service.move_stage(deal.id, stages["Offerta"].id, ADMIN)

    assert moved.pipeline_stage_id == stages["Offerta"].id
    timeline = ActivityService(db_session).timeline("deal", deal.id)
    entry = next(e for e in timeline if e.kind == "stage_changed")
    assert entry.payload["to"] == "Offerta"
    assert entry.payload["from"] == "Lead"


def test_move_to_a_missing_stage_is_rejected(db_session: Session, customer_id, stages) -> None:
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    with pytest.raises(NotFound):
        service.move_stage(deal.id, uuid4(), ADMIN)


def test_moving_to_a_won_stage_sets_probability_to_one_hundred(
    db_session: Session, customer_id, stages
) -> None:
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    assert service.move_stage(deal.id, stages["Vinto"].id, ADMIN).probabilita == 100


def test_moving_to_a_lost_stage_sets_probability_to_zero(
    db_session: Session, customer_id, stages
) -> None:
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    assert service.move_stage(deal.id, stages["Perso"].id, ADMIN).probabilita == 0


def test_list_can_be_filtered_by_customer_and_by_stage(
    db_session: Session, customer_id, stages
) -> None:
    other = CustomerService(db_session).create(CustomerCreate(ragione_sociale="Beta"), ADMIN).id
    service = DealService(db_session)
    service.create(DealCreate(nome="Mio", customer_id=customer_id), ADMIN)
    service.create(DealCreate(nome="Altro", customer_id=other), ADMIN)

    assert [d.nome for d in service.list(DealListQuery(customer_id=customer_id), ADMIN).items] == [
        "Mio"
    ]
    assert len(service.list(DealListQuery(stage_id=stages["Lead"].id), ADMIN).items) == 2


def test_estimate_fields_exist_for_the_later_pnl_slice(
    db_session: Session, customer_id, stages
) -> None:
    deal = DealService(db_session).create(
        DealCreate(
            nome="X",
            customer_id=customer_id,
            ore_preventivate=Decimal("120.50"),
            valore_preventivato=Decimal("15000.00"),
        ),
        ADMIN,
    )
    assert deal.ore_preventivate == Decimal("120.50")
    assert deal.valore_preventivato == Decimal("15000.00")


def test_a_customer_with_active_deals_cannot_be_deleted(
    db_session: Session, customer_id, stages
) -> None:
    """Deleting must not cascade silently; the error says how many deals are in the way.

    This is one of the two Conflict paths that go live for the first time now that
    `Deal` is registered: `CustomerRepository.count_active_deals` has queried the
    `deals` table through `Base.metadata` since Task 10, always returning 0 because
    the table did not exist. It exists now.
    """
    DealService(db_session).create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    with pytest.raises(Conflict) as exc:
        CustomerService(db_session).soft_delete(customer_id, ADMIN)
    assert exc.value.details["active_deals"] == 1


def test_deleting_the_deal_first_then_the_customer_works(
    db_session: Session, customer_id, stages
) -> None:
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    service.soft_delete(deal.id, ADMIN)
    CustomerService(db_session).soft_delete(customer_id, ADMIN)

    with pytest.raises(NotFound):
        CustomerService(db_session).get(customer_id, ADMIN)


def test_readonly_cannot_move_a_deal(db_session: Session, customer_id, stages) -> None:
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    with pytest.raises(PermissionDenied) as exc:
        service.move_stage(deal.id, stages["Offerta"].id, READONLY)
    assert exc.value.details["actual_role"] == "readonly"


def test_update_changes_name_and_records_it(db_session: Session, customer_id, stages) -> None:
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="Vecchio", customer_id=customer_id), ADMIN)
    assert service.update(deal.id, DealUpdate(nome="Nuovo"), ADMIN).nome == "Nuovo"


# --- Carried-over item 1: max_length on every string field ---------------------


def test_nome_over_the_column_width_is_rejected_on_create() -> None:
    """Mirrors CustomerCreate/PersonCreate's own *_MAX_LENGTH bounds: without a
    matching Pydantic bound, an over-length value sails past validation, reaches
    flush(), and comes back as a raw sqlalchemy.exc.DataError
    (StringDataRightTruncation) -- not a subclass of IntegrityError, so nothing in
    this codebase catches it, and it poisons the session."""
    with pytest.raises(ValidationError):
        DealCreate(nome="x" * (NOME_MAX_LENGTH + 1), customer_id=uuid4())


def test_nome_over_the_column_width_is_rejected_on_update() -> None:
    with pytest.raises(ValidationError):
        DealUpdate(nome="x" * (NOME_MAX_LENGTH + 1))


# --- Carried-over item 2: escape_like on search ---------------------------------


def test_search_treats_underscore_as_a_literal_character_not_a_wildcard(
    db_session: Session, customer_id, stages
) -> None:
    """In LIKE/ILIKE, "_" means "any one character". An unescaped search term makes a
    literal underscore in the query match every row with any character in that
    position -- here, searching "a_b" would also match "axb"."""
    service = DealService(db_session)
    service.create(DealCreate(nome="A_B", customer_id=customer_id), ADMIN)
    service.create(DealCreate(nome="AXB", customer_id=customer_id), ADMIN)

    result = service.list(DealListQuery(search="a_b"), ADMIN)
    assert [d.nome for d in result.items] == ["A_B"]


def test_search_treats_percent_as_a_literal_character_not_a_wildcard(
    db_session: Session, customer_id, stages
) -> None:
    """Same bug, "%" instead of "_": unescaped, it means "any run of characters", so
    searching "50%off" would also match "50XXXoff"."""
    service = DealService(db_session)
    service.create(DealCreate(nome="50%off", customer_id=customer_id), ADMIN)
    service.create(DealCreate(nome="50XXXoff", customer_id=customer_id), ADMIN)

    result = service.list(DealListQuery(search="50%off"), ADMIN)
    assert [d.nome for d in result.items] == ["50%off"]


def test_search_term_with_a_trailing_backslash_still_matches(
    db_session: Session, customer_id, stages
) -> None:
    """Before escaping, a trailing backslash in the search term combines with the "%"
    this method appends to build the pattern, forming an accidental escape sequence
    that swallows the trailing wildcard -- the match disappears entirely, even though
    the target genuinely contains that backslash."""
    service = DealService(db_session)
    service.create(DealCreate(nome="ACME\\", customer_id=customer_id), ADMIN)

    result = service.list(DealListQuery(search="acme\\"), ADMIN)
    assert [d.nome for d in result.items] == ["ACME\\"]


# --- Carried-over item 4: DealListQuery.limit bounded ---------------------------


def test_list_query_limit_is_bounded() -> None:
    with pytest.raises(ValidationError):
        DealListQuery(limit=0)
    with pytest.raises(ValidationError):
        DealListQuery(limit=201)


# --- Carried-over item 5: custom fields on update -------------------------------


def test_custom_fields_are_validated_against_the_definitions(
    db_session: Session, customer_id, stages
) -> None:
    FieldDefinitionService(db_session).create(
        FieldDefinitionCreate(
            entity_type="deal",
            key="fonte",
            label="Fonte",
            field_type="select",
            options=["referral", "outbound"],
        ),
        ADMIN,
    )
    service = DealService(db_session)

    ok = service.create(
        DealCreate(nome="X", customer_id=customer_id, custom_fields={"fonte": "referral"}), ADMIN
    )
    assert ok.custom_fields == {"fonte": "referral"}

    with pytest.raises(ValidationFailed):
        service.create(
            DealCreate(nome="Y", customer_id=customer_id, custom_fields={"fonte": "boh"}), ADMIN
        )


def test_undefined_custom_field_is_rejected(db_session: Session, customer_id, stages) -> None:
    with pytest.raises(ValidationFailed):
        DealService(db_session).create(
            DealCreate(nome="X", customer_id=customer_id, custom_fields={"inventato": "v"}), ADMIN
        )


def test_archived_custom_field_value_survives_unrelated_updates_and_can_still_be_cleared(
    db_session: Session, customer_id, stages
) -> None:
    """Task 7's contract for archiving a field definition is 'hide it, keep the data
    readable'. Validating the *union* of a row's stored custom_fields and the caller's
    incoming values against only the active definitions breaks that contract: an
    archived key still sitting in custom_fields would make every future update -- even
    one that never mentions that key -- fail with "campo non definito". update() must
    validate only the keys the caller actually supplies, never the union with what is
    already stored. Mirrors the identical fix on Customers and People exactly."""
    fields = FieldDefinitionService(db_session)
    settore = fields.create(
        FieldDefinitionCreate(
            entity_type="deal", key="settore", label="Settore", field_type="text"
        ),
        ADMIN,
    )
    fields.create(
        FieldDefinitionCreate(
            entity_type="deal", key="priorita", label="Priorita", field_type="text"
        ),
        ADMIN,
    )
    service = DealService(db_session)
    deal = service.create(
        DealCreate(nome="X", customer_id=customer_id, custom_fields={"settore": "IT"}), ADMIN
    )
    fields.archive(settore.id, ADMIN)

    updated = service.update(deal.id, DealUpdate(custom_fields={"priorita": "alta"}), ADMIN)
    assert updated.custom_fields == {"settore": "IT", "priorita": "alta"}

    cleared = service.update(deal.id, DealUpdate(custom_fields={"settore": None}), ADMIN)
    assert cleared.custom_fields == {"priorita": "alta"}

    fields.create(
        FieldDefinitionCreate(
            entity_type="deal",
            key="stato_deal",
            label="Stato",
            field_type="select",
            options=["aperto", "chiuso"],
        ),
        ADMIN,
    )
    with pytest.raises(ValidationFailed):
        service.update(deal.id, DealUpdate(custom_fields={"stato_deal": "sconosciuto"}), ADMIN)


def test_required_active_custom_field_set_to_none_is_rejected_like_empty_string(
    db_session: Session, customer_id, stages
) -> None:
    """`None` and `""` are two spellings of the same intent -- "this field has no
    value" -- and must be rejected identically on a currently active, required
    field."""
    fields = FieldDefinitionService(db_session)
    fields.create(
        FieldDefinitionCreate(
            entity_type="deal",
            key="settore",
            label="Settore",
            field_type="text",
            required=True,
        ),
        ADMIN,
    )
    service = DealService(db_session)
    deal = service.create(
        DealCreate(nome="X", customer_id=customer_id, custom_fields={"settore": "IT"}), ADMIN
    )

    with pytest.raises(ValidationFailed) as via_none:
        service.update(deal.id, DealUpdate(custom_fields={"settore": None}), ADMIN)
    assert via_none.value.details["field"] == "settore"
    assert via_none.value.details["reason"] == "campo obbligatorio"

    with pytest.raises(ValidationFailed) as via_empty:
        service.update(deal.id, DealUpdate(custom_fields={"settore": ""}), ADMIN)
    assert via_empty.value.details["reason"] == via_none.value.details["reason"]


def test_non_required_active_custom_field_set_to_none_is_removed(
    db_session: Session, customer_id, stages
) -> None:
    fields = FieldDefinitionService(db_session)
    fields.create(
        FieldDefinitionCreate(
            entity_type="deal", key="settore", label="Settore", field_type="text"
        ),
        ADMIN,
    )
    service = DealService(db_session)
    deal = service.create(
        DealCreate(nome="X", customer_id=customer_id, custom_fields={"settore": "IT"}), ADMIN
    )

    updated = service.update(deal.id, DealUpdate(custom_fields={"settore": None}), ADMIN)
    assert updated.custom_fields == {}


def test_archived_custom_field_set_to_none_is_removed_even_if_it_was_required(
    db_session: Session, customer_id, stages
) -> None:
    """Requiredness only ever blocks removal for a *currently active* definition:
    clearing an archived field's stored value must stay possible even if the
    definition was required back when it was active."""
    fields = FieldDefinitionService(db_session)
    settore = fields.create(
        FieldDefinitionCreate(
            entity_type="deal",
            key="settore",
            label="Settore",
            field_type="text",
            required=True,
        ),
        ADMIN,
    )
    service = DealService(db_session)
    deal = service.create(
        DealCreate(nome="X", customer_id=customer_id, custom_fields={"settore": "IT"}), ADMIN
    )
    fields.archive(settore.id, ADMIN)

    updated = service.update(deal.id, DealUpdate(custom_fields={"settore": None}), ADMIN)
    assert updated.custom_fields == {}


def test_required_active_custom_field_not_mentioned_in_a_partial_update_is_unaffected(
    db_session: Session, customer_id, stages
) -> None:
    """A required, active field the caller never mentions in this update is neither
    an omission (create-time semantics) nor a removal (a None was never sent) -- it
    must be left exactly as stored, with no error."""
    fields = FieldDefinitionService(db_session)
    fields.create(
        FieldDefinitionCreate(
            entity_type="deal",
            key="settore",
            label="Settore",
            field_type="text",
            required=True,
        ),
        ADMIN,
    )
    fields.create(
        FieldDefinitionCreate(
            entity_type="deal", key="priorita", label="Priorita", field_type="text"
        ),
        ADMIN,
    )
    service = DealService(db_session)
    deal = service.create(
        DealCreate(nome="X", customer_id=customer_id, custom_fields={"settore": "IT"}), ADMIN
    )

    updated = service.update(deal.id, DealUpdate(custom_fields={"priorita": "alta"}), ADMIN)
    assert updated.custom_fields == {"settore": "IT", "priorita": "alta"}


# --- Carried-over item 6: restore() logs only when actually deleted ------------


def test_soft_delete_then_restore(db_session: Session, customer_id, stages) -> None:
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    service.soft_delete(deal.id, ADMIN)

    with pytest.raises(NotFound):
        service.get(deal.id, ADMIN)
    assert service.restore(deal.id, ADMIN).nome == "X"


def test_restore_on_a_deal_that_was_never_deleted_does_not_log_a_restored_entry(
    db_session: Session, customer_id, stages
) -> None:
    """Unconditionally logging "restored" -- even for a deal that was never
    soft-deleted -- would write a timeline entry claiming a recovery that never
    happened. Mirrors the identical guard on CustomerService.restore and
    PersonService.restore."""
    from pigrocrm.core.activities.service import ActivityService

    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)

    service.restore(deal.id, ADMIN)

    kinds = [e.kind for e in ActivityService(db_session).timeline("deal", deal.id)]
    assert "restored" not in kinds


# --- Carried-over item 7: pagination boundary and JSONB containment ------------


def test_pagination_returns_a_cursor_and_does_not_repeat_rows(
    db_session: Session, customer_id, stages
) -> None:
    service = DealService(db_session)
    for index in range(5):
        service.create(DealCreate(nome=f"Deal {index:02d}", customer_id=customer_id), ADMIN)

    first = service.list(DealListQuery(limit=2), ADMIN)
    assert len(first.items) == 2
    assert first.next_cursor is not None

    second = service.list(DealListQuery(limit=2, cursor=first.next_cursor), ADMIN)
    assert {d.id for d in first.items}.isdisjoint({d.id for d in second.items})


def test_last_page_has_no_cursor(db_session: Session, customer_id, stages) -> None:
    service = DealService(db_session)
    service.create(DealCreate(nome="Solo", customer_id=customer_id), ADMIN)
    assert service.list(DealListQuery(limit=10), ADMIN).next_cursor is None


def test_filter_by_custom_field_uses_jsonb_containment(
    db_session: Session, customer_id, stages
) -> None:
    FieldDefinitionService(db_session).create(
        FieldDefinitionCreate(
            entity_type="deal", key="settore", label="Settore", field_type="text"
        ),
        ADMIN,
    )
    service = DealService(db_session)
    service.create(
        DealCreate(nome="A", customer_id=customer_id, custom_fields={"settore": "IT"}), ADMIN
    )
    service.create(
        DealCreate(nome="B", customer_id=customer_id, custom_fields={"settore": "Retail"}), ADMIN
    )

    page = service.list(DealListQuery(custom={"settore": "IT"}), ADMIN)
    assert [d.nome for d in page.items] == ["A"]


# --- New Conflict paths: count_active_deals / count_deals_in_stage go live -----


def test_a_pipeline_stage_with_deals_cannot_be_deleted(
    db_session: Session, customer_id, stages
) -> None:
    """The second of the two Conflict paths that go live for the first time now that
    `Deal` is registered: `PipelineRepository.count_deals_in_stage` has queried the
    `deals` table through `Base.metadata` since Task 9, always returning 0 because the
    table did not exist. It exists now, and `PipelineService.delete` refuses to remove
    a stage some deal still points at."""
    DealService(db_session).create(DealCreate(nome="X", customer_id=customer_id), ADMIN)

    with pytest.raises(Conflict) as exc:
        PipelineService(db_session).delete(stages["Lead"].id, ADMIN)
    assert exc.value.details["deals"] == 1
    # Fix round 1, item 4: the message must be actionable on its own, not leave an
    # administrator to work out from a bare count that archived deals count too and
    # that moving them (not soft-deleting again) is what frees the stage.
    assert "archiviat" in exc.value.details["reason"]
    assert "sposta" in exc.value.details["reason"]


def test_soft_deleting_the_deal_does_not_free_its_pipeline_stage_for_deletion(
    db_session: Session, customer_id, stages
) -> None:
    """Unlike `CustomerRepository.count_active_deals` -- `soft_delete` on a customer
    is itself a soft operation on the *customer* row, so excluding already-archived
    deals from that count is correct -- `PipelineService.delete` performs a real,
    hard `session.delete()` on the stage row. `pipeline_stage_id` is NOT NULL with no
    ON DELETE rule, so a deal row still references its stage regardless of its own
    `deleted_at`: soft-deleting the deal does not change that foreign key, and the row
    would still violate referential integrity if the stage vanished under it.
    `count_deals_in_stage` deliberately counts every deal in the stage, deleted or
    not, for exactly this reason -- filtering it to "active only" here would swap a
    clean, catchable `Conflict` for a raw, unhandled `IntegrityError` the moment
    Postgres enforces the constraint on the actual DELETE."""
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    service.soft_delete(deal.id, ADMIN)

    with pytest.raises(Conflict) as exc:
        PipelineService(db_session).delete(stages["Lead"].id, ADMIN)
    assert exc.value.details["deals"] == 1


def test_moving_the_deal_out_of_the_stage_then_deleting_it_works(
    db_session: Session, customer_id, stages
) -> None:
    """The only way to free a stage that still has deals in it: move them somewhere
    else. `move_stage` is the operation that actually changes `pipeline_stage_id`;
    soft-deleting does not (see the test above)."""
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    service.move_stage(deal.id, stages["Offerta"].id, ADMIN)

    PipelineService(db_session).delete(stages["Lead"].id, ADMIN)

    assert stages["Lead"].id not in {s.id for s in PipelineService(db_session).list()}


# --- Parity with Customers/People's shipped shape -------------------------------


def test_get_missing_deal_raises_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        DealService(db_session).get(uuid4(), ADMIN)


def test_readonly_cannot_create_a_deal(db_session: Session, customer_id, stages) -> None:
    with pytest.raises(PermissionDenied):
        DealService(db_session).create(DealCreate(nome="X", customer_id=customer_id), READONLY)


def test_readonly_can_read_a_deal(db_session: Session, customer_id, stages) -> None:
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    assert service.get(deal.id, READONLY).id == deal.id


def test_collaborator_can_create_a_deal(db_session: Session, customer_id, stages) -> None:
    assert DealService(db_session).create(DealCreate(nome="X", customer_id=customer_id), COLLAB)


def test_update_records_only_the_changed_fields(db_session: Session, customer_id, stages) -> None:
    from pigrocrm.core.activities.service import ActivityService

    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    service.update(deal.id, DealUpdate(note="Richiamare la prossima settimana"), ADMIN)

    updates = [
        e for e in ActivityService(db_session).timeline("deal", deal.id) if e.kind == "updated"
    ]
    assert updates[0].payload["changed"] == ["note"]


def test_a_new_deal_can_specify_an_explicit_stage(db_session: Session, customer_id, stages) -> None:
    deal = DealService(db_session).create(
        DealCreate(nome="X", customer_id=customer_id, pipeline_stage_id=stages["Offerta"].id),
        ADMIN,
    )
    assert deal.pipeline_stage_id == stages["Offerta"].id
    assert deal.probabilita == stages["Offerta"].probabilita_default


# --- Fix round 1 -----------------------------------------------------------------

# (field, at_limit, over_limit) for every Numeric(p, s) column: Numeric(12, 2) for
# valore_previsto/valore_preventivato, Numeric(8, 2) for ore_preventivate. Derived
# from the schema's own constants rather than hardcoded twice, so a future change to
# a column's width changes these test values along with it.
_MONEY_INTEGER_DIGITS = VALORE_MAX_DIGITS - DECIMAL_PLACES
_HOURS_INTEGER_DIGITS = ORE_MAX_DIGITS - DECIMAL_PLACES
_MONEY_AT_LIMIT = Decimal("9" * _MONEY_INTEGER_DIGITS + "." + "9" * DECIMAL_PLACES)
_MONEY_OVER_LIMIT = Decimal("9" * (_MONEY_INTEGER_DIGITS + 1) + "." + "9" * DECIMAL_PLACES)
_HOURS_AT_LIMIT = Decimal("9" * _HOURS_INTEGER_DIGITS + "." + "9" * DECIMAL_PLACES)
_HOURS_OVER_LIMIT = Decimal("9" * (_HOURS_INTEGER_DIGITS + 1) + "." + "9" * DECIMAL_PLACES)

NUMERIC_FIELD_LIMITS = [
    ("valore_previsto", _MONEY_AT_LIMIT, _MONEY_OVER_LIMIT),
    ("valore_preventivato", _MONEY_AT_LIMIT, _MONEY_OVER_LIMIT),
    ("ore_preventivate", _HOURS_AT_LIMIT, _HOURS_OVER_LIMIT),
]


# --- Item 1 (CRITICAL): Numeric columns need max_digits/decimal_places too -------


@pytest.mark.parametrize("field,at_limit,over_limit", NUMERIC_FIELD_LIMITS)
def test_value_at_the_numeric_column_limit_is_accepted_on_create(
    field: str, at_limit: Decimal, over_limit: Decimal
) -> None:
    """Numeric(12,2)/Numeric(8,2) in models.py allow exactly this many significant
    digits. The fix for the over-limit case below must not also reject the boundary
    value itself."""
    deal = DealCreate(nome="X", customer_id=uuid4(), **{field: at_limit})
    assert getattr(deal, field) == at_limit


@pytest.mark.parametrize("field,at_limit,over_limit", NUMERIC_FIELD_LIMITS)
def test_value_beyond_the_numeric_column_capacity_is_rejected_on_create(
    field: str, at_limit: Decimal, over_limit: Decimal
) -> None:
    """Before max_digits/decimal_places were declared, DealCreate(valore_previsto=
    Decimal("99999999999.99")) sailed past Pydantic, reached flush(), and came back
    as a raw sqlalchemy.exc.DataError (NumericValueOutOfRange) -- not a subclass of
    IntegrityError, so nothing in this codebase caught it, and it poisoned the
    session. This is the sixth time this project has hit this class of bug -- the
    first five were String columns, closed with max_length; Deal is the first
    entity with Numeric columns, so there was no template to copy for this one."""
    with pytest.raises(ValidationError):
        DealCreate(nome="X", customer_id=uuid4(), **{field: over_limit})


@pytest.mark.parametrize("field,at_limit,over_limit", NUMERIC_FIELD_LIMITS)
def test_value_beyond_the_numeric_column_capacity_is_rejected_on_update(
    field: str, at_limit: Decimal, over_limit: Decimal
) -> None:
    with pytest.raises(ValidationError):
        DealUpdate(**{field: over_limit})


def test_value_at_the_numeric_column_limit_round_trips_through_the_database(
    db_session: Session, customer_id, stages
) -> None:
    """Schema-level acceptance alone would not catch a mismatch between Pydantic's
    bound and the column's real capacity: this proves the exact boundary value
    survives create() -> Postgres -> DealRead with no truncation or rejection."""
    deal = DealService(db_session).create(
        DealCreate(nome="X", customer_id=customer_id, valore_previsto=_MONEY_AT_LIMIT),
        ADMIN,
    )
    assert deal.valore_previsto == _MONEY_AT_LIMIT


# --- Item 3 (IMPORTANT): a sub-cent value is rejected, not silently rounded ------


def test_a_sub_cent_value_is_rejected_instead_of_silently_rounded() -> None:
    """Before decimal_places=2 was declared, create(valore_previsto=Decimal("0.005"))
    reached Postgres, which stored 0.01 -- but expire_on_commit=False (db/session.py)
    meant the in-memory object, and the DealRead built straight from it, kept
    reporting 0.005: the immediate response lied about what was actually written.
    Deciding which cent the caller meant is not this service's job, so the value is
    rejected outright instead of being rounded silently. Fixed by the same
    decimal_places=2 declaration as item 1 above -- no separate code path exists to
    round money, so closing item 1 already closes this."""
    with pytest.raises(ValidationError):
        DealCreate(nome="X", customer_id=uuid4(), valore_previsto=Decimal("0.005"))


# --- Item 2 (IMPORTANT): "won at 60%" must be unreachable through every gate -----


def test_creating_a_deal_directly_in_a_won_stage_ignores_an_explicit_low_probability(
    db_session: Session, customer_id, stages
) -> None:
    """'Won at 60%' must not be reachable through create() either, not only through
    move_stage(): a deal created straight into a terminal stage with an explicit,
    contradicting probability must still land at the stage's settled value."""
    deal = DealService(db_session).create(
        DealCreate(
            nome="X", customer_id=customer_id, pipeline_stage_id=stages["Vinto"].id, probabilita=60
        ),
        ADMIN,
    )
    assert deal.probabilita == 100


def test_creating_a_deal_directly_in_a_lost_stage_ignores_an_explicit_nonzero_probability(
    db_session: Session, customer_id, stages
) -> None:
    deal = DealService(db_session).create(
        DealCreate(
            nome="X", customer_id=customer_id, pipeline_stage_id=stages["Perso"].id, probabilita=60
        ),
        ADMIN,
    )
    assert deal.probabilita == 0


def test_updating_probability_after_a_move_to_a_won_stage_cannot_reopen_it(
    db_session: Session, customer_id, stages
) -> None:
    """The third, previously-unguarded path: an ordinary update() after move_stage()
    settled the deal at 100% must not be able to walk it back down."""
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    service.move_stage(deal.id, stages["Vinto"].id, ADMIN)

    updated = service.update(deal.id, DealUpdate(probabilita=60), ADMIN)
    assert updated.probabilita == 100


def test_updating_probability_after_a_move_to_a_lost_stage_cannot_reopen_it(
    db_session: Session, customer_id, stages
) -> None:
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    service.move_stage(deal.id, stages["Perso"].id, ADMIN)

    updated = service.update(deal.id, DealUpdate(probabilita=60), ADMIN)
    assert updated.probabilita == 0


def test_updating_an_open_deals_probability_still_works_normally(
    db_session: Session, customer_id, stages
) -> None:
    """The settling logic must not fire for a non-terminal stage: an ordinary
    probability edit on a deal still sitting in "Lead" is untouched."""
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)

    updated = service.update(deal.id, DealUpdate(probabilita=33), ADMIN)
    assert updated.probabilita == 33


# --- Item 4 (IMPORTANT): the real blocking case is a soft-deleted deal ----------


def test_freeing_a_stage_with_a_soft_deleted_deal_requires_restore_move_then_delete_again(
    db_session: Session, customer_id, stages
) -> None:
    """test_moving_the_deal_out_of_the_stage_then_deleting_it_works (above) only
    exercised an *active* deal -- but an active deal never actually blocks a stage
    delete for long in practice, since nothing stops it from being active in a
    different stage already. The case that genuinely blocks is a *soft-deleted*
    deal, and move_stage raises NotFound on one: DealRepository.get filters
    deleted_at by default, same as every other write method. The only real sequence
    is restore() -> move_stage() -> soft_delete() again -- previously neither tested
    nor documented."""
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    service.soft_delete(deal.id, ADMIN)

    with pytest.raises(Conflict):
        PipelineService(db_session).delete(stages["Lead"].id, ADMIN)
    with pytest.raises(NotFound):
        service.move_stage(deal.id, stages["Offerta"].id, ADMIN)

    service.restore(deal.id, ADMIN)
    service.move_stage(deal.id, stages["Offerta"].id, ADMIN)
    service.soft_delete(deal.id, ADMIN)

    PipelineService(db_session).delete(stages["Lead"].id, ADMIN)
    assert stages["Lead"].id not in {s.id for s in PipelineService(db_session).list()}


# --- Final review item 3 (CRITICAL): owner_id is a real FK, never validated -------
#
# `deals.owner_id` is `ForeignKey("users.id")` (models.py), but neither `create` nor
# `update` ever checked it -- `grep -rn owner_id packages/core/tests apps/*/tests`
# returned nothing before this section existed. Any syntactically valid UUID reached
# `flush()` and came back as a raw, uncaught `ForeignKeyViolation`, exactly the
# "unvalidated input reaches Postgres" family this project has already closed for
# every other shape. `customer_id` gets exactly this treatment already
# (`test_a_deal_for_a_missing_customer_is_rejected` above); `owner_id` never did,
# because it is optional and easy to never pass in a test.


def test_owner_id_for_a_nonexistent_user_is_rejected_on_create(
    db_session: Session, customer_id, stages
) -> None:
    with pytest.raises(NotFound) as exc:
        DealService(db_session).create(
            DealCreate(nome="X", customer_id=customer_id, owner_id=uuid4()), ADMIN
        )
    assert exc.value.details["entity"] == "user"


def test_a_valid_owner_id_is_accepted_on_create(
    db_session: Session, customer_id, stages, owner_id
) -> None:
    deal = DealService(db_session).create(
        DealCreate(nome="X", customer_id=customer_id, owner_id=owner_id), ADMIN
    )
    assert deal.owner_id == owner_id


def test_a_deal_with_no_owner_is_still_allowed(db_session: Session, customer_id, stages) -> None:
    """owner_id is nullable -- a deal may be unassigned. The fix must not turn
    "not provided" into a validation failure."""
    deal = DealService(db_session).create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    assert deal.owner_id is None


def test_owner_id_for_a_nonexistent_user_is_rejected_on_update(
    db_session: Session, customer_id, stages
) -> None:
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    with pytest.raises(NotFound) as exc:
        service.update(deal.id, DealUpdate(owner_id=uuid4()), ADMIN)
    assert exc.value.details["entity"] == "user"


def test_a_valid_owner_id_is_accepted_on_update(
    db_session: Session, customer_id, stages, owner_id
) -> None:
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    updated = service.update(deal.id, DealUpdate(owner_id=owner_id), ADMIN)
    assert updated.owner_id == owner_id


# --- Final review item 8 (IMPORTANT): restore() must not undo the invariant -----
# --- soft_delete() protects: no active deal on an archived customer. -----------


def test_restoring_a_deal_whose_customer_is_now_archived_is_refused(
    db_session: Session, customer_id, stages
) -> None:
    """The only way to reach this state at all: archive the deal (soft_delete's own
    active-deals count only counts non-archived deals, so this is what unblocks
    archiving the customer next), then archive the now deal-free customer. Without
    this fix, restoring the deal afterward produces exactly the state soft_delete
    exists to prevent -- an active deal on an archived customer, which GET
    /api/deals would list and the MCP resource deal://{id} would then fail to
    render because it cannot load the customer."""
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    service.soft_delete(deal.id, ADMIN)
    CustomerService(db_session).soft_delete(customer_id, ADMIN)

    with pytest.raises(Conflict) as exc:
        service.restore(deal.id, ADMIN)
    assert exc.value.details["entity"] == "deal"
    assert "cliente" in exc.value.message


def test_restoring_the_customer_first_then_the_deal_works(
    db_session: Session, customer_id, stages
) -> None:
    service = DealService(db_session)
    deal = service.create(DealCreate(nome="X", customer_id=customer_id), ADMIN)
    service.soft_delete(deal.id, ADMIN)
    CustomerService(db_session).soft_delete(customer_id, ADMIN)

    CustomerService(db_session).restore(customer_id, ADMIN)
    service.restore(deal.id, ADMIN)

    assert service.get(deal.id, ADMIN).id == deal.id


def test_every_read_path_names_the_deal_s_customer(
    db_session: Session, customer_id, stages
) -> None:
    """The deal list's first column is the deal; the line under it is who it is for.

    `customer_id` alone made that a second lookup per row that no screen did -- the same
    gap `PersonRead.customer_ragione_sociale` closed for Persone. Asserted on all four
    read paths at once because they must agree: a deal's customer cannot depend on which
    endpoint asked for the deal.
    """
    service = DealService(db_session)

    created = service.create(DealCreate(nome="Progetto X", customer_id=customer_id), ADMIN)
    assert created.customer_ragione_sociale == "ACME"
    assert service.get(created.id, ADMIN).customer_ragione_sociale == "ACME"

    updated = service.update(created.id, DealUpdate(note="una nota"), ADMIN)
    assert updated.customer_ragione_sociale == "ACME"

    moved = service.move_stage(created.id, stages["Vinto"].id, ADMIN)
    assert moved.customer_ragione_sociale == "ACME"

    page = service.list(DealListQuery(), ADMIN)
    assert [deal.customer_ragione_sociale for deal in page.items] == ["ACME"]


def test_the_customer_name_costs_one_query_for_the_whole_page(db_session: Session, stages) -> None:
    """The reason the name is resolved in one batched lookup and not per row: a page of
    50 deals across 50 customers must not become 51 queries. Counted, not reasoned
    about -- an N+1 reintroduced by a later refactor is invisible to every other
    assertion in this file. Same shape as `test_people.py`'s own counting test."""
    customers = CustomerService(db_session)
    service = DealService(db_session)
    for index in range(3):
        customer = customers.create(CustomerCreate(ragione_sociale=f"ACME {index}"), ADMIN)
        service.create(DealCreate(nome=f"Progetto {index}", customer_id=customer.id), ADMIN)

    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    connection = db_session.connection()
    event.listen(connection, "after_cursor_execute", record)
    try:
        page = service.list(DealListQuery(), ADMIN)
    finally:
        event.remove(connection, "after_cursor_execute", record)

    assert len(page.items) == 3
    # The list itself, plus exactly one lookup for the three customer names.
    assert len(statements) == 2, statements
    assert sorted(d.customer_ragione_sociale or "" for d in page.items) == [
        "ACME 0",
        "ACME 1",
        "ACME 2",
    ]


def test_find_by_marker_is_the_live_deal_of_that_customer_carrying_it(
    db_session: Session, customer_id, stages
) -> None:
    """How the engagements door finds again a deal it created and failed to record
    (spec 2026-09-25 § 2.3 step 5): the marker in the note, as an exact substring, on a
    live deal of that customer. A deal with the same name and no marker is not it."""
    marker = f"rebase:match={uuid4()}"
    service = DealService(db_session)
    repo = DealRepository(db_session)
    other = CustomerService(db_session).create(CustomerCreate(ragione_sociale="Beta"), ADMIN)
    service.create(DealCreate(nome="Lettera n. 3/2026", customer_id=customer_id), ADMIN)
    service.create(DealCreate(nome="Altro", customer_id=other.id, note=f"x\n{marker}"), ADMIN)
    service.create(
        DealCreate(nome="Maiuscolo", customer_id=customer_id, note=marker.upper()), ADMIN
    )
    archived = service.create(
        DealCreate(nome="Archiviato", customer_id=customer_id, note=marker), ADMIN
    )
    service.soft_delete(archived.id, ADMIN)
    assert repo.find_by_marker(customer_id, marker) is None

    ours = service.create(
        DealCreate(
            nome="Lettera n. 3/2026", customer_id=customer_id, note=f"Creato da rebase.\n{marker}"
        ),
        ADMIN,
    )
    found = repo.find_by_marker(customer_id, marker)
    assert found is not None and found.id == ours.id
