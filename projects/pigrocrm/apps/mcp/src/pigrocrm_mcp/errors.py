from collections.abc import Mapping, Sequence
from difflib import get_close_matches
from typing import Any

import pydantic

from pigrocrm.core.errors import DomainError, ValidationFailed
from pigrocrm.core.validation_messages import validation_failed_from_error


def _malformed_identifier_error() -> ValidationFailed:
    return ValidationFailed(
        "input",
        "id",
        "is not a valid identifier",
        expected="a valid identifier (UUID); use the search tool to find it",
    )


def to_domain_error(exc: ValueError) -> DomainError:
    """Turns an argument-conversion failure the core layer never got a chance to
    raise a `DomainError` for into the same shape `to_agent_message` renders.

    Two distinct sources reach a tool's guard as a bare `ValueError` rather than
    a `DomainError`: `uuid.UUID(bad_string)` (every `*_id` parameter in this
    package is a plain `str`, converted by hand) and `pydantic.ValidationError`
    (raised when a tool constructs one of `pigrocrm.core`'s own Create/Update/
    ListQuery schemas from caller-supplied data -- e.g. `DealUpdate(**changes)`
    inside `tools/deals.py`, or `CustomerListQuery(limit=...)` inside
    `search_customers`).

    `pydantic.ValidationError` **is** a `ValueError` subclass -- confirmed against
    the installed pydantic, not assumed -- so it is checked first: a caller of
    this function that reversed the two branches would misreport every pydantic
    failure as a malformed identifier instead of naming the real field and reason.
    """
    if isinstance(exc, pydantic.ValidationError):
        return validation_failed_from_error(exc.errors()[0])
    return _malformed_identifier_error()


HINT_BY_CODE: dict[str, str] = {
    "not_found": "Check the identifier, or look the entity up with the search tool.",
    "validation_failed": "Fix the indicated value and try again.",
    "conflict": (
        "The current state prevents the operation: resolve the described conflict and try again."
    ),
    "permission_denied": "Different permissions are needed: ask the user to proceed manually.",
    "immutable_field": "This field cannot be modified: archive and recreate instead of updating.",
}


def to_agent_message(exc: DomainError) -> str:
    """A numeric status makes a model retry at random; a diagnosis makes it stop or fix.

    So the MCP rendering states what failed, what was expected, and what to do next —
    the same structured details the API renders as a problem document.
    """
    lines = [exc.message]

    expected = exc.details.get("expected")
    if expected:
        lines.append(f"Expected value: {expected}.")

    if exc.code == "permission_denied":
        required = ", ".join(exc.details.get("required_roles", []))
        actual = exc.details.get("actual_role", "unknown")
        lines.append(f"Current role: {actual}. Sufficient roles: {required}.")

    extras = {
        key: value
        for key, value in exc.details.items()
        if key not in {"expected", "required_roles", "actual_role", "entity", "field", "reason"}
    }
    if extras:
        lines.append("Context: " + ", ".join(f"{k}={v}" for k, v in sorted(extras.items())))

    lines.append(HINT_BY_CODE.get(exc.code, "Review the parameters and try again."))
    return " ".join(lines)


def _parameters_of(schema: Mapping[str, Any]) -> str:
    """The parameters a tool takes, as one clause an agent can copy names from."""
    properties = list(schema.get("properties", {}))
    required = set(schema.get("required", []))
    mandatory = [name for name in properties if name in required]
    optional = [name for name in properties if name not in required]
    parts = []
    if mandatory:
        parts.append("required " + ", ".join(f"`{name}`" for name in mandatory))
    if optional:
        parts.append("optional " + ", ".join(f"`{name}`" for name in optional))
    return "parameters " + "; ".join(parts) if parts else "no parameters"


PERSON_FLAT = ("email", "phone", "linkedin")
CUSTOMER_FLAT = ("email", "phone")
# Arguments a tool used to take and no longer does, with what to send instead: a misspelling
# guess cannot find `value_lines` from `expected_value`.
REMOVED_ARGUMENTS: dict[tuple[str, str], str] = {
    ("create_deal", "expected_value"): (
        "the deal's value is the total of its `value_lines`: send `value_lines` (a list of "
        "lines with `unit`, `delivery_period`, `unit_price`; `preview_deal_value` shows the "
        "total they give), never `expected_value`"
    ),
    **{
        (tool, name): (
            f"{who} are its `contact_points`: send a list of `{{kind, value, label, note, "
            f"is_primary}}`, never `{name}` (the first of a kind is its primary)"
        )
        for tool, who, names in (
            ("create_person", "a person's emails, phones and LinkedIn profiles", PERSON_FLAT),
            ("update_person", "a person's emails, phones and LinkedIn profiles", PERSON_FLAT),
            ("create_customer", "a customer's emails and phones", CUSTOMER_FLAT),
            ("update_customer", "a customer's emails and phones", CUSTOMER_FLAT),
        )
        for name in names
    },
}


def refuse_removed_changes(tool: str, changes: Mapping[str, Any]) -> None:
    """The `changes` of an `update_*` that names a field the tool no longer takes: refused
    with what to send instead, as a removed parameter is, rather than as the schema's bare
    «field not recognised»."""
    for name in changes:
        if (tool, name) in REMOVED_ARGUMENTS:
            raise ValidationFailed(
                "input",
                f"changes.{name}",
                f"field removed from `{tool}`",
                expected=REMOVED_ARGUMENTS[(tool, name)],
            )


def unknown_argument_error(
    tool: str, names: Sequence[str], schema: Mapping[str, Any]
) -> DomainError:
    """A parameter the tool does not have (REB-808). The SDK ignores it silently, so a
    misspelt `limit` on a search answered with every row, and a misspelt `stage_id` read
    as a missing one. Names the first, offers the closest real name, and lists them all."""
    name = names[0]
    if (tool, name) in REMOVED_ARGUMENTS:
        return ValidationFailed(
            "input",
            name,
            f"parameter removed from `{tool}`",
            expected=REMOVED_ARGUMENTS[(tool, name)],
        )
    close = get_close_matches(name, list(schema.get("properties", {})), n=1)
    suggestion = f" (maybe `{close[0]}`)" if close else ""
    return ValidationFailed(
        "input",
        name,
        f"parameter not recognized by `{tool}`{suggestion}",
        expected=_parameters_of(schema),
    )


def argument_error(
    tool: str, exc: pydantic.ValidationError, schema: Mapping[str, Any]
) -> DomainError:
    """What the SDK's own validation of a tool's arguments, which runs before any tool
    body and so before `_guard`, answers instead of its English dump (REB-808): the same
    `ValidationFailed` the guard renders for a bad value inside a tool, and for a
    parameter that is missing, the list of the ones the tool takes."""
    failed = validation_failed_from_error(exc.errors()[0])
    if exc.errors()[0]["type"] == "missing":
        return ValidationFailed(
            "input",
            str(failed.details["field"]),
            f"required field missing in `{tool}`",
            expected=_parameters_of(schema),
        )
    return failed
