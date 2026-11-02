import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fields.types import FIELD_TYPES, FieldSpec, FieldType
from pigrocrm.core.fields.validator import validate_custom_fields


def spec(key: str, field_type: str, **kw: Any) -> FieldSpec:
    return FieldSpec(key=key, label=key.title(), field_type=field_type, **kw)  # type: ignore[arg-type]


def test_empty_specs_and_empty_values_produce_empty_result() -> None:
    assert validate_custom_fields("customer", [], {}) == {}


def test_unknown_key_is_rejected_rather_than_silently_stored() -> None:
    """Silently accepting unknown keys turns JSONB into a junk drawer and hides typos
    from agents that guessed a field name."""
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("customer", [spec("settore", "text")], {"setore": "IT"})
    assert exc.value.details["field"] == "setore"
    assert "settore" in exc.value.details["reason"]


def test_missing_required_field_is_rejected() -> None:
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("customer", [spec("settore", "text", required=True)], {})
    assert exc.value.details["field"] == "settore"


def test_required_field_rejects_empty_string() -> None:
    with pytest.raises(ValidationFailed):
        validate_custom_fields(
            "customer", [spec("settore", "text", required=True)], {"settore": "   "}
        )


def test_optional_field_accepts_none_and_is_dropped() -> None:
    assert validate_custom_fields("customer", [spec("settore", "text")], {"settore": None}) == {}


def test_text_is_trimmed() -> None:
    result = validate_custom_fields("customer", [spec("settore", "text")], {"settore": "  IT  "})
    assert result == {"settore": "IT"}


def test_number_accepts_int_float_and_numeric_string() -> None:
    specs = [spec("n", "number")]
    assert validate_custom_fields("c", specs, {"n": 5})["n"] == 5.0
    assert validate_custom_fields("c", specs, {"n": 5.5})["n"] == 5.5
    assert validate_custom_fields("c", specs, {"n": "5.5"})["n"] == 5.5


def test_number_rejects_non_numeric() -> None:
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("c", [spec("n", "number")], {"n": "molto"})
    assert exc.value.details["expected"] == "un numero"


def test_currency_is_stored_as_a_two_decimal_string_not_a_float() -> None:
    """JSON has no decimal type. Storing money as float rounds wrong on invoices,
    so currency is serialised as a fixed-scale string."""
    result = validate_custom_fields("c", [spec("budget", "currency")], {"budget": "1234.567"})
    assert result == {"budget": "1234.57"}
    assert Decimal(result["budget"]) == Decimal("1234.57")


def test_currency_rejects_non_numeric() -> None:
    with pytest.raises(ValidationFailed):
        validate_custom_fields("c", [spec("budget", "currency")], {"budget": "gratis"})


def test_date_accepts_iso_string_and_date_and_normalises_to_iso() -> None:
    specs = [spec("scadenza", "date")]
    assert (
        validate_custom_fields("c", specs, {"scadenza": "2026-08-06"})["scadenza"] == "2026-08-06"
    )
    assert (
        validate_custom_fields("c", specs, {"scadenza": date(2026, 8, 6)})["scadenza"]
        == "2026-08-06"
    )


@pytest.mark.parametrize("bad", ["06/08/2026", "2026-13-01", "domani"])
def test_date_rejects_non_iso(bad: str) -> None:
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("c", [spec("scadenza", "date")], {"scadenza": bad})
    assert exc.value.details["expected"] == "una data ISO (YYYY-MM-DD)"


def test_select_accepts_a_declared_option() -> None:
    s = spec("stato", "select", options=["attivo", "sospeso"])
    assert validate_custom_fields("c", [s], {"stato": "attivo"})["stato"] == "attivo"


def test_select_rejects_an_undeclared_option_and_lists_the_valid_ones() -> None:
    """The error must name the allowed values: an agent that gets 'invalid' retries
    at random, one that gets the list corrects itself."""
    s = spec("stato", "select", options=["attivo", "sospeso"])
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("c", [s], {"stato": "chiuso"})
    assert "attivo" in exc.value.details["expected"]
    assert "sospeso" in exc.value.details["expected"]


def test_multiselect_accepts_a_list_and_deduplicates_preserving_order() -> None:
    s = spec("tag", "multiselect", options=["a", "b", "c"])
    assert validate_custom_fields("c", [s], {"tag": ["b", "a", "b"]})["tag"] == ["b", "a"]


def test_multiselect_rejects_a_bare_string() -> None:
    s = spec("tag", "multiselect", options=["a"])
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("c", [s], {"tag": "a"})
    assert exc.value.details["expected"] == "una lista di valori"


def test_multiselect_rejects_an_undeclared_member() -> None:
    s = spec("tag", "multiselect", options=["a", "b"])
    with pytest.raises(ValidationFailed):
        validate_custom_fields("c", [s], {"tag": ["a", "z"]})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), (False, False), ("true", True), ("false", False), (1, True), (0, False)],
)
def test_checkbox_coerces_common_truthy_representations(raw: Any, expected: bool) -> None:
    assert validate_custom_fields("c", [spec("ok", "checkbox")], {"ok": raw})["ok"] is expected


@pytest.mark.parametrize("raw", ["forse", 1.0, 2, -1, 0.0])
def test_checkbox_rejects_ambiguous_values(raw: Any) -> None:
    with pytest.raises(ValidationFailed):
        validate_custom_fields("c", [spec("ok", "checkbox")], {"ok": raw})


@pytest.mark.parametrize("raw", [True, False])
def test_a_number_field_refuses_a_boolean(raw: bool) -> None:
    """`float(True)` is 1.0: without the check a checkbox's answer is stored as a quantity."""
    with pytest.raises(ValidationFailed):
        validate_custom_fields("c", [spec("n", "number")], {"n": raw})


@pytest.mark.parametrize("url", ["https://example.com", "http://localhost:5173/x?y=1"])
def test_url_accepts_http_and_https(url: str) -> None:
    assert validate_custom_fields("c", [spec("sito", "url")], {"sito": url})["sito"] == url


@pytest.mark.parametrize("bad", ["example.com", "javascript:alert(1)", "ftp://x.it"])
def test_url_rejects_anything_that_is_not_http_or_https(bad: str) -> None:
    """javascript: in particular would become a stored-XSS vector the moment the UI
    renders a custom field as a link."""
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("c", [spec("sito", "url")], {"sito": bad})
    assert exc.value.details["expected"] == "un URL http:// o https://"


def test_archived_specs_are_simply_not_passed_in_so_their_values_are_rejected() -> None:
    with pytest.raises(ValidationFailed):
        validate_custom_fields("c", [], {"vecchio": "valore"})


def test_all_errors_name_the_entity() -> None:
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("deal", [spec("n", "number")], {"n": "x"})
    assert exc.value.details["entity"] == "deal"


# --- Fix round 1: reviewer-found gaps in the "never a raw exception, never an
# unsafe value" contract. Each of C1-I3 below was reproduced against the
# pre-fix code (see task-6-report.md for the red output); the cross-cutting
# test at the end is the net that would have caught all four Criticals at once.


def test_checkbox_rejects_unhashable_values_instead_of_raising_typeerror() -> None:
    """An MCP client sends arbitrary JSON, so a dict landing on a checkbox field is
    entirely plausible. a membership test must not leak a raw TypeError for an
    unhashable value - it must fail the same way any other non-boolean value does."""
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("c", [spec("ok", "checkbox")], {"ok": {"a": 1}})
    assert exc.value.details["expected"] == "true oppure false"


def test_number_rejects_an_integer_too_large_for_float_instead_of_overflowerror() -> None:
    """JSON has no size limit on integers; json.loads happily produces a value that
    float() cannot represent, raising OverflowError rather than ValueError."""
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("c", [spec("n", "number")], {"n": 10**400})
    assert exc.value.details["expected"] == "un numero"


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), float("-inf"), "1e1000"])
def test_number_rejects_non_finite_values(raw: Any) -> None:
    """Postgres rejects NaN/Infinity in a JSONB column outright, and float("1e1000")
    becomes inf without float() raising anything - the finiteness check has to run
    after conversion, it cannot be inferred from the conversion failing."""
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("c", [spec("n", "number")], {"n": raw})
    assert exc.value.details["expected"] == "un numero finito"


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), "NaN", "Infinity", "-Infinity"])
def test_currency_rejects_non_finite_values_including_the_silent_nan_case(raw: Any) -> None:
    """Decimal("NaN").quantize(...) does not raise - it silently propagates NaN - while
    Decimal("Infinity").quantize(...) does. Relying on quantize alone lets NaN through,
    so finiteness must be checked explicitly before quantize is ever called."""
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("c", [spec("budget", "currency")], {"budget": raw})
    assert exc.value.details["expected"] == "un importo finito"


def test_text_rejects_a_null_byte_instead_of_letting_it_reach_postgres() -> None:
    """Postgres rejects \\x00 in a text column outright; a stray control character
    from a dirty import or paste is a mundane way to trigger it on the most common
    field type. Silently stripping it would change the user's data without saying so."""
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("c", [spec("note", "text")], {"note": "abc\x00def"})
    assert exc.value.details["expected"] == "testo senza caratteri di controllo"


def test_text_rejects_a_dict_instead_of_stringifying_it_into_garbage() -> None:
    """str({"a": 1}) silently produces "{'a': 1}": a garbage value with no signal to
    the agent that sent the wrong shape for the field."""
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("c", [spec("note", "text")], {"note": {"a": 1}})
    assert exc.value.details["expected"] == "un valore testuale"


def test_required_multiselect_rejects_an_empty_list_as_still_missing() -> None:
    """is_blank only special-cased None and blank strings, so a required multiselect
    could be "satisfied" by an empty list - present in the payload, absent in
    substance."""
    s = spec("tag", "multiselect", options=["a", "b"], required=True)
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("c", [s], {"tag": []})
    assert exc.value.details["field"] == "tag"


def test_checkbox_false_and_number_zero_are_not_treated_as_blank() -> None:
    """Guards the caution in the is_blank fix: extending blankness to empty
    sequences must not start swallowing other falsy-but-legitimate values."""
    specs = [spec("ok", "checkbox", required=True), spec("n", "number", required=True)]
    result = validate_custom_fields("c", specs, {"ok": False, "n": 0})
    assert result == {"ok": False, "n": 0.0}


def test_field_spec_rejects_select_with_no_options_as_an_impossible_state() -> None:
    """options=[] on a select degrades every future rejection message to "uno tra: "
    with nothing listed after it - exactly the useless "invalid" this design exists
    to avoid. Refusing to construct the spec is better than emitting that message."""
    with pytest.raises(ValidationError):
        FieldSpec(key="stato", label="Stato", field_type="select", options=[])


def test_field_spec_rejects_multiselect_with_no_options() -> None:
    with pytest.raises(ValidationError):
        FieldSpec(key="tag", label="Tag", field_type="multiselect", options=[])


def _deeply_nested_list(depth: int) -> list[Any]:
    """A list nested `depth` levels deep, built iteratively so the test itself
    never recurses - only the *value* it produces is deeply nested."""
    value: list[Any] = []
    for _ in range(depth):
        value = [value]
    return value


_DEEPLY_NESTED_LIST = _deeply_nested_list(20_000)
_VERY_LONG_STRING = "x" * 100_000

_HOSTILE_VALUES: list[Any] = [
    [],
    {},
    {"a": 1},
    10**400,
    float("nan"),
    float("inf"),
    "1e1000",
    "abc\x00def",
    None,
    True,
    _DEEPLY_NESTED_LIST,
    _VERY_LONG_STRING,
]
_HOSTILE_IDS = [
    "empty_list",
    "empty_dict",
    "nonempty_dict",
    "huge_int",
    "nan",
    "inf",
    "1e1000_str",
    "null_byte",
    "none",
    "true",
    "deeply_nested_list",
    "very_long_string",
]


def _spec_for_hostile_test(field_type: FieldType) -> FieldSpec:
    options = ["a", "b"] if field_type in ("select", "multiselect") else []
    return spec("f", field_type, options=options)


@pytest.mark.parametrize("field_type", FIELD_TYPES)
@pytest.mark.parametrize("value", _HOSTILE_VALUES, ids=_HOSTILE_IDS)
def test_hostile_values_never_escape_as_a_raw_exception_or_an_unsafe_value(
    field_type: FieldType, value: Any
) -> None:
    """The one property that actually protects three interfaces: whatever comes in,
    either a ValidationFailed comes out, or a value that survives json.dumps. Any
    other exception propagating out means some field type shipped without handling
    one of these shapes - this is the net that would have caught all four Criticals
    in one run instead of one at a time."""
    field_spec = _spec_for_hostile_test(field_type)
    try:
        result = validate_custom_fields("c", [field_spec], {"f": value})
    except ValidationFailed:
        return
    # allow_nan=False, not the lenient default: Python's json module happily emits
    # a bare NaN/Infinity token, which is not valid JSON and is exactly what a
    # strict JSONB parser like Postgres's rejects. The default allow_nan=True would
    # let the silent-corruption case (C3) through this net unnoticed.
    json.dumps(result, allow_nan=False)


# --- Fix round 2: a deeply nested or oversized value must not exhaust the stack,
# or produce a message so large it ends up whole in an HTTP problem document and
# in an agent's context, just to report that the value is rejected.


_MAX_REASONABLE_MESSAGE_LENGTH = 500


def test_validation_failed_message_stays_bounded_for_a_huge_input() -> None:
    """A 10 MB numeric-looking string in a number field must not produce a 10 MB
    ValidationFailed message - that string would end up whole in an HTTP problem
    document and in an agent's context, just to report that it is rejected."""
    huge = "9" * 10_000_000
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("c", [spec("n", "number")], {"n": huge})
    assert len(exc.value.details["reason"]) < _MAX_REASONABLE_MESSAGE_LENGTH
    assert len(str(exc.value)) < _MAX_REASONABLE_MESSAGE_LENGTH


def test_validation_failed_message_stays_bounded_for_a_deeply_nested_input() -> None:
    """A deeply nested value must not exhaust the stack while the module is still
    trying to explain why it is invalid, and the resulting message must still be
    short - not the value's own (enormous) formatted representation."""
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("c", [spec("n", "number")], {"n": _DEEPLY_NESTED_LIST})
    assert len(exc.value.details["reason"]) < _MAX_REASONABLE_MESSAGE_LENGTH
    assert len(str(exc.value)) < _MAX_REASONABLE_MESSAGE_LENGTH


# --- Fix round 3: the unknown-key name itself, not just field values, must stay
# bounded. It comes straight from the user's payload and becomes ValidationFailed's
# `field`, formatted into `message` inside errors.py - a different file, so a grep
# over validator.py's f-strings alone cannot see this site.


def test_unknown_key_error_stays_bounded_when_the_key_itself_is_huge() -> None:
    """A 10 MB dict key must not produce a 10 MB message or a 10 MB details["field"]
    - the same failure mode fix round 2 closed for values, entering through the key
    instead."""
    huge_key = "k" * 10_000_000
    with pytest.raises(ValidationFailed) as exc:
        validate_custom_fields("c", [], {huge_key: "value"})
    assert len(str(exc.value)) < _MAX_REASONABLE_MESSAGE_LENGTH
    assert len(exc.value.details["field"]) < _MAX_REASONABLE_MESSAGE_LENGTH
