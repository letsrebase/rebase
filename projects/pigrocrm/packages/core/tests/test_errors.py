import pytest
from pydantic import ValidationError

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import (
    Conflict,
    DomainError,
    ImmutableField,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)


def test_errors_carry_structured_details_not_formatted_strings() -> None:
    """The same exception becomes an HTTP problem detail AND an LLM-readable message.
    That is only possible if the data stays structured."""
    err = ValidationFailed("customer", "vat_number", "must be 11 digits", expected="11 digits")

    assert isinstance(err, DomainError)
    assert err.code == "validation_failed"
    assert err.details == {
        "entity": "customer",
        "field": "vat_number",
        "reason": "must be 11 digits",
        "expected": "11 digits",
    }


def test_validation_failed_without_expected() -> None:
    """When expected is not provided, it should not be in details."""
    err = ValidationFailed("customer", "vat_number", "must be 11 digits")
    assert err.code == "validation_failed"
    assert err.details == {
        "entity": "customer",
        "field": "vat_number",
        "reason": "must be 11 digits",
    }
    assert "expected" not in err.details


def test_not_found_records_which_entity_and_which_id() -> None:
    err = NotFound("deal", "0199-abc")
    assert err.code == "not_found"
    assert err.details["entity"] == "deal"
    assert err.details["identifier"] == "0199-abc"


def test_not_found_message_and_str() -> None:
    """Error message is available on .message and str(exc)."""
    err = NotFound("deal", "0199-abc")
    assert err.message == "deal 0199-abc not found"
    assert str(err) == "deal 0199-abc not found"


def test_validation_failed_message_and_str() -> None:
    """Error message is available on .message and str(exc)."""
    err = ValidationFailed("customer", "vat_number", "must be 11 digits")
    assert err.message == "customer.partita_iva: must be 11 digits"
    assert str(err) == "customer.partita_iva: must be 11 digits"


def test_permission_denied_message_and_str() -> None:
    """Error message is available on .message and str(exc)."""
    err = PermissionDenied("update_deal", ["admin", "collaborator"], "readonly")
    expected_msg = "update_deal requires one of ['admin', 'collaboratore'], actor has readonly"
    assert err.message == expected_msg
    assert str(err) == expected_msg


def test_conflict_accepts_arbitrary_context() -> None:
    err = Conflict("customer", "has active deals", active_deals=3)
    assert err.code == "conflict"
    assert err.details["active_deals"] == 3


def test_permission_denied_states_what_role_would_be_enough() -> None:
    err = PermissionDenied("update_deal", ["admin", "collaborator"], "readonly")
    assert err.details["required_roles"] == ["admin", "collaborator"]
    assert err.details["actual_role"] == "readonly"


def test_immutable_field_is_its_own_error() -> None:
    err = ImmutableField("field_definition", "field_type", "would orphan existing values")
    assert err.code == "immutable_field"


@pytest.mark.parametrize(
    ("role", "can_write", "can_administer"),
    [("admin", True, True), ("collaborator", True, False), ("readonly", False, False)],
)
def test_actor_permissions_by_role(role: str, can_write: bool, can_administer: bool) -> None:
    actor = Actor(id=None, type="user", role=role)  # type: ignore[arg-type]
    assert actor.can_write is can_write
    assert actor.can_administer is can_administer


def test_system_actor_is_an_admin_with_no_user_id() -> None:
    actor = Actor.system()
    assert actor.type == "system"
    assert actor.id is None
    assert actor.can_administer is True


def test_actor_is_immutable() -> None:
    """Actor is a frozen value object; assignment should raise."""
    actor = Actor(id=None, type="user", role="readonly")
    with pytest.raises(ValidationError):
        actor.role = "admin"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("role", "should_raise_write", "should_raise_admin"),
    [
        ("admin", False, False),
        ("collaborator", False, True),
        ("readonly", True, True),
    ],
)
def test_require_write_and_admin_by_role(
    role: str, should_raise_write: bool, should_raise_admin: bool
) -> None:
    """require_write and require_admin enforce role-based permissions."""
    actor = Actor(id=None, type="user", role=role)  # type: ignore[arg-type]

    if should_raise_write:
        with pytest.raises(PermissionDenied) as exc_info:
            actor.require_write("do_something")
        assert exc_info.value.details["required_roles"] == ["admin", "collaborator"]
        assert exc_info.value.details["actual_role"] == role
    else:
        actor.require_write("do_something")  # should not raise

    if should_raise_admin:
        with pytest.raises(PermissionDenied) as exc_info:
            actor.require_admin("do_admin_thing")
        assert exc_info.value.details["required_roles"] == ["admin"]
        assert exc_info.value.details["actual_role"] == role
    else:
        actor.require_admin("do_admin_thing")  # should not raise


def test_collaborator_passes_write_but_not_admin() -> None:
    """Demonstrates role distinction: collaboratore can write but not administer."""
    actor = Actor(id=None, type="user", role="collaborator")
    actor.require_write("update_field")  # should not raise
    with pytest.raises(PermissionDenied):
        actor.require_admin("delete_entity")


def test_permission_denied_details_are_not_aliased_to_global_state() -> None:
    """Mutating err.details["required_roles"] should not affect subsequent calls.
    This test catches the aliasing bug where global WRITE_ROLES was passed by reference."""
    actor = Actor(id=None, type="user", role="readonly")

    # First call: capture the error
    with pytest.raises(PermissionDenied) as exc_info:
        actor.require_write("action1")
    err1 = exc_info.value
    original_roles = list(err1.details["required_roles"])

    # Mutate the details (simulates a bug in downstream code)
    err1.details["required_roles"].append("corrupted")

    # Second call: should have fresh, uncorrupted details
    with pytest.raises(PermissionDenied) as exc_info:
        actor.require_write("action2")
    err2 = exc_info.value

    assert err2.details["required_roles"] == original_roles
    assert err2.details["required_roles"] == ["admin", "collaborator"]
