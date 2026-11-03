from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.fields.schemas import FieldDefinitionCreate
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.schema_registry import ENTITY_TYPES, describe_entity, native_fields

ADMIN = Actor(id=None, type="system", role="admin")


def test_native_fields_come_from_the_model_not_a_hand_written_list() -> None:
    fields = native_fields("customer")
    assert "legal_name" in fields
    assert "vat_number" in fields
    assert "custom_fields" not in fields, "custom fields are described separately"


def test_every_entity_type_can_be_described(db_session: Session) -> None:
    for entity_type in ENTITY_TYPES:
        described = describe_entity(db_session, entity_type)
        assert described["entity_type"] == entity_type
        assert described["native_fields"]


def test_a_new_custom_field_shows_up_immediately(db_session: Session) -> None:
    FieldDefinitionService(db_session).create(
        FieldDefinitionCreate(
            entity_type="deal", key="rischio", label="Rischio", field_type="text"
        ),
        ADMIN,
    )
    described = describe_entity(db_session, "deal")
    assert [field["key"] for field in described["custom_fields"]] == ["rischio"]


def test_describing_an_invoice_is_a_supported_entity_type() -> None:
    from pigrocrm.core.schema_registry import ENTITY_TYPES

    assert "invoice" in ENTITY_TYPES
