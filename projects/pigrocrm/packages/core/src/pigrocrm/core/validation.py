"""Reusable Pydantic building blocks shared by every domain's Create/Update schemas.

`fields/validator.py` rejects a NUL byte in a *custom* field's text; `activities/
sanitize.py` strips one from an *audit payload*. Neither ever touched a native
`String`/`Text` column: `OrganizationCreate.legal_name`, `PersonCreate.name`,
`FieldDefinitionCreate.label`, and every other plain string field on every domain's
schema had no check at all. `" "` is valid JSON and a syntactically ordinary Python
`str`, so `"\\x00"` sails straight through Pydantic and reaches `flush()`, where
Postgres refuses a NUL byte in `text`/`varchar` storage and the driver raises a raw,
uncaught exception -- the same "unvalidated input reaches Postgres and comes back as
a raw exception" failure this project has already closed six times for other column
shapes (`max_length`, `max_digits`/`decimal_places`, `.fullmatch()` instead of
`.match()`, `escape_like`). `SafeStr` is the fix as a class instead of a seventh
special case: one annotated type, applied to every user-supplied string field on
every Create/Update schema, including list-of-string fields such as
`FieldDefinitionCreate.options` (`list[SafeStr]`).

Deliberately rejects rather than strips. `activities/sanitize.py` strips because an
audit entry must never fail the write it is riding along with; here the caller is
actively choosing a value for a field of their own record, so they can be told to fix
it. Silently altering a user's data -- even by deleting one invisible byte -- is
exactly how a character disappears with nobody noticing.
"""

from typing import Annotated, Any

from pydantic import BeforeValidator, ValidationInfo


def _reject_nul(value: Any, info: ValidationInfo) -> Any:
    """Runs as a `BeforeValidator`, so it sees the raw input ahead of any other
    constraint (`max_length`, `Literal`, ...) on the same field -- ahead of any
    other `mode="before"` validator declared directly on that field too, since a
    `@field_validator(mode="before")` on the model wraps *outside* an
    Annotated-type's own `BeforeValidator` (confirmed against the installed
    pydantic: `PersonCreate.email`'s `_normalise_email` still runs, and a NUL
    byte introduced after it is still caught here).

    Only strings are inspected -- a non-string value is left for Pydantic's own
    type coercion to accept or reject, exactly as `_clean_text` in
    `fields/validator.py` leaves non-scalar values to its own container check.
    `info.field_name` names the field in the message for a human reading it
    directly; the same information also reaches both adapters structurally,
    since pydantic-core's own `loc` already carries the exact path (including a
    list index, e.g. `("options", 1)`) regardless of what this message says.
    """
    if isinstance(value, str) and "\x00" in value:
        field = info.field_name or "valore"
        raise ValueError(f"{field}: il testo contiene un carattere nullo (\\x00), non ammesso")
    return value


SafeStr = Annotated[str, BeforeValidator(_reject_nul)]
