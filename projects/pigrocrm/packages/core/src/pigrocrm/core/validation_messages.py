"""One Italian sentence for what pydantic-core says in English.

Shared by the MCP server (a tool's arguments) and the REST API (a request's body, query
and path), so a missing or malformed value is worded the same on both surfaces and the
English `Field required` never reaches a screen or an agent.
"""

from pydantic_core import ErrorDetails

from pigrocrm.core.errors import ValidationFailed

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
# `hours`, `amount` and the money fields.
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


def validation_failed_from_error(
    error: ErrorDetails, *, field: str | None = None
) -> ValidationFailed:
    """Translates one pydantic-core error into this project's structured, Italian
    `ValidationFailed` -- the same family `to_agent_message` renders every hand-raised
    domain error from, so a request or argument-conversion failure reads identically to
    one the core layer raised on purpose. The MCP tools and the REST request validation
    share it, so the two surfaces word a missing or malformed value the same way.

    `field` overrides the path taken from `loc`: a request error carries a leading
    `body`/`query`/`path` segment that is not a field.
    """
    field = field if field is not None else _field_path(error)
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
