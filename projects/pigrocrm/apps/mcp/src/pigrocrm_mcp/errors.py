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
        "non è un identificativo valido",
        expected="un identificativo valido (UUID); usa lo strumento di ricerca per trovarlo",
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
    "not_found": "Verifica l'identificativo, oppure cerca l'entità con lo strumento di ricerca.",
    "validation_failed": "Correggi il valore indicato e riprova.",
    "conflict": (
        "Lo stato attuale impedisce l'operazione: risolvi il conflitto descritto e riprova."
    ),
    "permission_denied": "Servono permessi diversi: chiedi all'utente di procedere manualmente.",
    "immutable_field": "Questo campo non è modificabile: archivia e ricrea invece di aggiornare.",
}


def to_agent_message(exc: DomainError) -> str:
    """A numeric status makes a model retry at random; a diagnosis makes it stop or fix.

    So the MCP rendering states what failed, what was expected, and what to do next —
    the same structured details the API renders as a problem document.
    """
    lines = [exc.message]

    expected = exc.details.get("expected")
    if expected:
        lines.append(f"Valore atteso: {expected}.")

    if exc.code == "permission_denied":
        required = ", ".join(exc.details.get("required_roles", []))
        actual = exc.details.get("actual_role", "sconosciuto")
        lines.append(f"Ruolo attuale: {actual}. Ruoli sufficienti: {required}.")

    extras = {
        key: value
        for key, value in exc.details.items()
        if key not in {"expected", "required_roles", "actual_role", "entity", "field", "reason"}
    }
    if extras:
        lines.append("Contesto: " + ", ".join(f"{k}={v}" for k, v in sorted(extras.items())))

    lines.append(HINT_BY_CODE.get(exc.code, "Rivedi i parametri e riprova."))
    return " ".join(lines)


def _parameters_of(schema: Mapping[str, Any]) -> str:
    """The parameters a tool takes, as one clause an agent can copy names from."""
    properties = list(schema.get("properties", {}))
    required = set(schema.get("required", []))
    mandatory = [name for name in properties if name in required]
    optional = [name for name in properties if name not in required]
    parts = []
    if mandatory:
        parts.append("obbligatori " + ", ".join(f"`{name}`" for name in mandatory))
    if optional:
        parts.append("facoltativi " + ", ".join(f"`{name}`" for name in optional))
    return "parametri " + "; ".join(parts) if parts else "nessun parametro"


def unknown_argument_error(
    tool: str, names: Sequence[str], schema: Mapping[str, Any]
) -> DomainError:
    """A parameter the tool does not have (REB-808). The SDK ignores it silently, so a
    misspelt `limit` on a search answered with every row, and a misspelt `stage_id` read
    as a missing one. Names the first, offers the closest real name, and lists them all."""
    name = names[0]
    close = get_close_matches(name, list(schema.get("properties", {})), n=1)
    suggestion = f" (forse `{close[0]}`)" if close else ""
    return ValidationFailed(
        "input",
        name,
        f"parametro non riconosciuto da `{tool}`{suggestion}",
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
            f"campo obbligatorio mancante in `{tool}`",
            expected=_parameters_of(schema),
        )
    return failed
