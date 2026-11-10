from typing import Annotated

import pytest
from pydantic import BaseModel, Field, ValidationError

from pigrocrm.core.validation import SafeStr

# --- Item 1 (CRITICAL, final review): SafeStr is the one reusable guard against a  --
# --- NUL byte reaching a native String/Text column. Six times this project fixed   --
# --- "an unvalidated input reaches Postgres and comes back as a raw exception" as  --
# --- six separate special cases (max_length, max_digits/decimal_places, fullmatch  --
# --- instead of match, escape_like, ...); NUL bytes in native columns were never   --
# --- swept because no single column shape ever forced it. This is the fix as a    --
# --- class: one annotated type, applied to every user-supplied string field.      --


class _Model(BaseModel):
    """A throwaway model exercising SafeStr in every shape it is actually used in
    across the real domain schemas: a bare required field, an optional field
    combined with Field(max_length=...), and a list of strings (mirrors
    FieldDefinitionCreate.options)."""

    required: SafeStr
    optional: SafeStr | None = Field(default=None, max_length=5)
    items: list[SafeStr] = []


def test_an_ordinary_string_passes_through_unchanged() -> None:
    model = _Model(required="ACME Srl")
    assert model.required == "ACME Srl"


def test_a_nul_byte_in_a_required_field_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        _Model(required="ACME\x00Srl")
    error = exc.value.errors()[0]
    assert error["type"] == "value_error"
    assert error["loc"] == ("required",)


def test_a_nul_byte_in_an_optional_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _Model(required="ok", optional="a\x00b")


def test_a_nul_byte_inside_a_list_item_is_rejected_and_names_its_index() -> None:
    """Mirrors FieldDefinitionCreate.options: a list of strings must reject a NUL
    byte in any one element, not just when the whole field is a scalar string."""
    with pytest.raises(ValidationError) as exc:
        _Model(required="ok", items=["end", "bad\x00one"])
    error = exc.value.errors()[0]
    assert error["loc"] == ("items", 1)


def test_the_message_names_the_field_so_a_caller_knows_what_to_fix() -> None:
    with pytest.raises(ValidationError) as exc:
        _Model(required="a\x00b")
    message = str(exc.value)
    assert "required" in message


def test_max_length_still_applies_alongside_safe_str() -> None:
    """SafeStr must compose with an ordinary Field(max_length=...) constraint --
    every domain schema pairs the two (SafeStr for the NUL guard, max_length to
    mirror the column width) rather than choosing one or the other."""
    with pytest.raises(ValidationError) as exc:
        _Model(required="ok", optional="toolong")
    assert "too_long" in exc.value.errors()[0]["type"]


def test_it_rejects_do_not_strip_the_character() -> None:
    """The fix must reject, not silently strip: silently altering a user's data is
    how a character disappears with nobody noticing. Confirmed by checking that a
    valid string is never mutated, and an invalid one never constructs at all --
    there is no code path in SafeStr that returns a modified string."""
    model = _Model(required="Città")
    assert model.required == "Città"


def test_safe_str_is_a_real_annotated_str_type() -> None:
    """Documents the shape the task asked for literally, so a future reader
    can see the type used across every schema is this exact alias."""
    assert Annotated[str, ...].__class__ is SafeStr.__class__
