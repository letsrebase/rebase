"""A13. The residual document's 2026-08-20 update makes this blocking for slice 4
because this slice's native columns are named `hours`, `data`, `amount` and
`description` -- the first labels anyone would type defining a custom field on an
hours entry. Before this guard, a field labelled "Hours" on `time_entry` slugified to
`hours`, the API answered 201, and every later write landed in `custom_fields.hours`
instead of the real column, silently.

Every `FieldDefinitionCreate(...)` call below passes `key=label`: `key` has no
default derived from `label` at the schema level (it is a required field of its
own, always present in this service's real callers -- the API and MCP layers send
whatever raw text the user typed as `key`, and `_slugify` normalizes it), so a
test exercising "what a user types" must send the raw label-shaped text as `key`
too, exactly as `FieldDefinitionCreate`'s own `_slugify` validator expects it to
arrive.
"""

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fields.schemas import FieldDefinitionCreate
from pigrocrm.core.fields.service import FieldDefinitionService


@pytest.fixture
def admin() -> Actor:
    return Actor.system()


@pytest.mark.parametrize(
    ("entity_type", "label", "colliding_key"),
    [
        ("time_entry", "Hours", "hours"),
        ("time_entry", "Date", "date"),
        ("time_entry", "Description", "description"),
        ("cost", "Amount", "amount"),
        ("cost", "Date", "date"),
        ("deal", "Name", "name"),
        ("organization", "Legal name", "legal_name"),
        # `note` is no column since the notes thread (REB-879), but a Create takes it as the
        # first note, so a custom field may not take the name.
        ("deal", "Note", "note"),
        ("organization", "Note", "note"),
        ("person", "Note", "note"),
        # Slugification is what makes this reachable by accident: nobody types
        # `budgeted_hours`, they type "Budgeted hours".
        ("deal", "Budgeted  hours", "budgeted_hours"),
        # Accent folding too -- slugify_key transliterates before stripping.
        ("deal", "Probabílity", "probability"),
    ],
)
def test_a_label_that_slugifies_onto_a_native_column_is_refused(
    db_session: Session, admin: Actor, entity_type: str, label: str, colliding_key: str
) -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        FieldDefinitionService(db_session).create(
            FieldDefinitionCreate(
                entity_type=entity_type, key=label, label=label, field_type="text"
            ),
            admin,
        )
    assert excinfo.value.details["field"] == "key"
    assert colliding_key in excinfo.value.details["expected"]


def test_a_non_colliding_label_is_still_accepted(db_session: Session, admin: Actor) -> None:
    field = FieldDefinitionService(db_session).create(
        FieldDefinitionCreate(
            entity_type="time_entry",
            key="Ore approvate",
            label="Ore approvate",
            field_type="number",
        ),
        admin,
    )
    assert field.key == "ore_approvate"


def test_nothing_is_written_when_the_guard_fires(db_session: Session, admin: Actor) -> None:
    """The guard must run before `repo.add`, or a refused definition still occupies
    the key: `get_by_key` does not filter on `archived`, so a half-written row would
    make even the corrected label unusable."""
    service = FieldDefinitionService(db_session)
    with pytest.raises(ValidationFailed):
        service.create(
            FieldDefinitionCreate(
                entity_type="cost", key="Amount", label="Amount", field_type="currency"
            ),
            admin,
        )
    db_session.rollback()
    assert service.repo.get_by_key("cost", "amount") is None


def test_field_definition_update_has_no_key_to_collide(db_session: Session) -> None:
    """Documented rather than asserted in prose: there is no update path to guard,
    because the key is immutable by omission from the Update schema."""
    from pigrocrm.core.fields.schemas import FieldDefinitionUpdate

    assert "key" not in FieldDefinitionUpdate.model_fields
    assert "entity_type" not in FieldDefinitionUpdate.model_fields
