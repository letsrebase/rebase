from collections.abc import Mapping, Sequence
from difflib import get_close_matches
from typing import Any

import pydantic
from pydantic_core import ErrorDetails

from pigrocrm.core.errors import DomainError, ValidationFailed

# error type (pydantic-core's own `type` string, see `ValidationError.errors()`) ->
# (reason in Italian, expected value in Italian). Covers the shapes this project's
# own MCP tool arguments can actually produce: every native field on
# Customer/Person/DealCreate|Update is int, float, Decimal, bool, date, UUID, or a
# plain string -- there is no exotic pydantic type in this schema surface that
# would fall outside this table and need its own entry.
_PYDANTIC_REASON_BY_TYPE: dict[str, tuple[str, str | None]] = {
    "int_parsing": ("non è un numero intero", "un numero intero"),
    "int_type": ("non è un numero intero", "un numero intero"),
    "float_parsing": ("non è un numero", "un numero"),
    "float_type": ("non è un numero", "un numero"),
    "decimal_parsing": ("non è un importo numerico", "un importo numerico"),
    "decimal_type": ("non è un importo numerico", "un importo numerico"),
    "date_type": ("non è una data valida", "una data ISO (YYYY-MM-DD)"),
    "datetime_parsing": ("non è una data e ora valida", "una data e ora ISO 8601"),
    "datetime_type": ("non è una data e ora valida", "una data e ora ISO 8601"),
    "string_type": ("deve essere un testo", "un valore testuale"),
    "bool_parsing": ("non è un valore booleano", "true oppure false"),
    "bool_type": ("non è un valore booleano", "true oppure false"),
    "date_parsing": ("non è una data valida", "una data ISO (YYYY-MM-DD)"),
    "date_from_datetime_parsing": ("non è una data valida", "una data ISO (YYYY-MM-DD)"),
    "uuid_parsing": ("non è un identificativo valido", "un identificativo valido (UUID)"),
    "uuid_type": ("non è un identificativo valido", "un identificativo valido (UUID)"),
    "extra_forbidden": (
        "campo non riconosciuto da questo strumento",
        "uno dei campi elencati nello schema dello strumento",
    ),
    "missing": ("campo obbligatorio mancante", "un valore"),
    "list_type": ("deve essere un elenco", "un elenco"),
    "dict_type": ("deve essere un oggetto", "un oggetto JSON"),
    "model_type": ("deve essere un oggetto", "un oggetto JSON"),
}

# error type -> (the `ctx` key pydantic-core stores the bound under, the Italian
# comparison phrase). A range violation (e.g. `limit` over 200) needs the actual
# number restated, not just "out of range" -- `ctx` is where pydantic-core puts it
# (confirmed against the installed package: `ValidationError.errors()`, never
# `str(exc)`, which is the one that carries the `errors.pydantic.dev` footer this
# module exists to keep out of an agent-facing message).
_COMPARISON_BOUND_BY_TYPE: dict[str, tuple[str, str]] = {
    "less_than_equal": ("le", "minore o uguale a"),
    "less_than": ("lt", "minore di"),
    "greater_than_equal": ("ge", "maggiore o uguale a"),
    "greater_than": ("gt", "maggiore di"),
}


def _field_path(error: ErrorDetails) -> str:
    return ".".join(str(part) for part in error["loc"]) or "valore"


# `ctx` key holding the bound -> (the verb phrase, what is counted), for the two length
# violations a string or a list can commit. `min_length` is the one `SearchQuery.q`
# trips: it used to come back as pydantic's own «String should have at least 3 characters».
_LENGTH_BY_TYPE: dict[str, tuple[str, str, str]] = {
    "string_too_short": ("min_length", "almeno", "caratteri"),
    "string_too_long": ("max_length", "al massimo", "caratteri"),
    "too_short": ("min_length", "almeno", "elementi"),
    "too_long": ("max_length", "al massimo", "elementi"),
}

# error type -> (`ctx` key, reason, expected value with the bound as `{}`): the violations
# of a `Decimal` or a pattern that `max_digits`, `decimal_places` and the like declare on
# `ore`, `importo` and the money fields.
_BOUNDED_REASON_BY_TYPE: dict[str, tuple[str, str, str]] = {
    "decimal_max_places": ("decimal_places", "ha troppi decimali", "al massimo {} decimali"),
    "decimal_max_digits": ("max_digits", "ha troppe cifre", "al massimo {} cifre in tutto"),
    "decimal_whole_digits": (
        "whole_digits",
        "ha troppe cifre intere",
        "al massimo {} cifre intere",
    ),
    "string_pattern_mismatch": ("pattern", "non ha il formato richiesto", "un testo conforme a {}"),
}


def _pydantic_error_to_validation_failed(exc: pydantic.ValidationError) -> ValidationFailed:
    """Translates pydantic-core's own error shape into this project's structured,
    Italian `ValidationFailed` -- the same family `to_agent_message` already
    renders every hand-raised domain error from, so an argument-conversion
    failure reads identically to one the core layer raised on purpose.

    Renders only the first error even when pydantic reports several: a model
    correcting one field and retrying is the same one-fix-at-a-time workflow
    `validate_custom_fields` already assumes for custom fields elsewhere in this
    project.
    """
    error = exc.errors()[0]
    field = _field_path(error)
    error_type = error["type"]

    comparison = _COMPARISON_BOUND_BY_TYPE.get(error_type)
    if comparison is not None:
        ctx_key, phrase = comparison
        bound = (error.get("ctx") or {}).get(ctx_key)
        return ValidationFailed(
            "input", field, f"deve essere {phrase} {bound}", expected=f"un valore {phrase} {bound}"
        )

    if error_type in _BOUNDED_REASON_BY_TYPE:
        ctx_key, reason, expected_template = _BOUNDED_REASON_BY_TYPE[error_type]
        bound = (error.get("ctx") or {}).get(ctx_key)
        return ValidationFailed("input", field, reason, expected=expected_template.format(bound))

    if error_type in _LENGTH_BY_TYPE:
        ctx_key, verb, unit = _LENGTH_BY_TYPE[error_type]
        bound = (error.get("ctx") or {}).get(ctx_key)
        return ValidationFailed(
            "input", field, f"deve avere {verb} {bound} {unit}", expected=f"{verb} {bound} {unit}"
        )

    if error_type in ("literal_error", "enum"):
        allowed = str((error.get("ctx") or {}).get("expected", "")).replace("'", "`")
        return ValidationFailed(
            "input",
            field,
            "non è uno dei valori ammessi",
            expected=f"uno fra {allowed.replace(' or ', ' oppure ')}" if allowed else None,
        )

    known = _PYDANTIC_REASON_BY_TYPE.get(error_type)
    if known is not None:
        return ValidationFailed("input", field, known[0], expected=known[1])
    # A validator of this project's own (`ValueError("...")` in a schema) carries its
    # sentence in `msg`, behind pydantic's «Value error, » prefix; anything else
    # pydantic words itself is English and says nothing the field name does not.
    if error_type in ("value_error", "assertion_error"):
        return ValidationFailed("input", field, error["msg"].split(", ", 1)[-1])
    return ValidationFailed("input", field, "valore non valido")


def _malformed_identifier_error() -> ValidationFailed:
    return ValidationFailed(
        "input",
        "identificativo",
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
        return _pydantic_error_to_validation_failed(exc)
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
    misspelt `limite` on a search answered with every row, and a misspelt `stage_id` read
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
    failed = _pydantic_error_to_validation_failed(exc)
    if exc.errors()[0]["type"] == "missing":
        return ValidationFailed(
            "input",
            str(failed.details["field"]),
            f"campo obbligatorio mancante in `{tool}`",
            expected=_parameters_of(schema),
        )
    return failed
