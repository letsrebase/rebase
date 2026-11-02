"""The parameter types the tools declare.

Every alias is **runtime-permissive and schema-strict**, and that is the point of this
module. The MCP SDK validates a tool's arguments against the function signature *before*
the guarded body runs, so a bare `int`, `float`, `date` or a typed `changes` model would
turn a wrong argument into a raw, multi-line, English pydantic dump with an
`errors.pydantic.dev` link: the guard's `except ValueError` never sees it, because the
exception never reaches the decorator (spec §8.2 forbids that text; REB-808 translates what
still escapes). So a parameter keeps a loose runtime type (`str`, `int | str`,
`float | str`, `dict[str, Any]`) that the SDK accepts whatever a JSON client sends, and
`WithJsonSchema` overrides only what `list_tools()` *displays*: the real bounds, formats,
enums and the real field names of a `changes` object. The `*ListQuery`, `*Create` and
`*Update` schema each value then feeds enforces the bound inside the guarded call, where a
mismatch becomes rendered guidance. The alias decides what an agent is told, never what is
enforced.

A required parameter gets the alias without a `null` branch, so the schema never advertises
an unset value for an argument the core refuses to build without.
"""

from datetime import date
from typing import Annotated, Any, Literal, get_args
from uuid import UUID

from pydantic import WithJsonSchema

from pigrocrm.core.attivita.schemas import AttivitaUpdate
from pigrocrm.core.contract_expenses.schemas import ContractExpenseUpdate
from pigrocrm.core.contracts.schemas import ClauseNoteUpdate, ContractUpdate, RateCardUpdate
from pigrocrm.core.customers.schemas import CustomerUpdate
from pigrocrm.core.deals.schemas import DealUpdate
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.people.schemas import PersonUpdate
from pigrocrm.core.search.schemas import PER_CLASS_LIMIT
from pigrocrm.core.timetracking.schemas import ANNO_MAX, ANNO_MIN, CostUpdate, TimeEntryUpdate

# `changes` of an `update_*`: an unvalidated dict at runtime, the real model's schema in the
# listing, so an agent sees the field names (`update_attivita` included, with the two
# `*_da_rimuovere` flags that clear a field). The model is built inside the call, in
# `tools/<family>.py`.
CustomerChanges = Annotated[dict[str, Any], WithJsonSchema(CustomerUpdate.model_json_schema())]
PersonChanges = Annotated[dict[str, Any], WithJsonSchema(PersonUpdate.model_json_schema())]
DealChanges = Annotated[dict[str, Any], WithJsonSchema(DealUpdate.model_json_schema())]
ContractChanges = Annotated[dict[str, Any], WithJsonSchema(ContractUpdate.model_json_schema())]
RateCardChanges = Annotated[dict[str, Any], WithJsonSchema(RateCardUpdate.model_json_schema())]
ClauseNoteChanges = Annotated[dict[str, Any], WithJsonSchema(ClauseNoteUpdate.model_json_schema())]
ContractExpenseChanges = Annotated[
    dict[str, Any], WithJsonSchema(ContractExpenseUpdate.model_json_schema())
]
TimeEntryChanges = Annotated[dict[str, Any], WithJsonSchema(TimeEntryUpdate.model_json_schema())]
CostChanges = Annotated[dict[str, Any], WithJsonSchema(CostUpdate.model_json_schema())]
AttivitaChanges = Annotated[dict[str, Any], WithJsonSchema(AttivitaUpdate.model_json_schema())]

# A date: a `str | None` at runtime, `format: date` in the listing.
IsoDateStr = Annotated[
    str | None,
    WithJsonSchema(
        {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}], "default": None}
    ),
]


def iso_date(value: str | None, field: str = "data") -> date | None:
    """An `IsoDateStr` argument as the `date` a core schema takes, or `None` when the
    agent left it out. Called *inside* the guarded tool body: a malformed string is a
    sentence naming the format, not a raw SDK rejection."""
    return required_iso_date(value, field) if value else None


def required_iso_date(value: str, field: str = "data") -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationFailed(
            "input", field, "non è una data valida", expected="una data ISO (YYYY-MM-DD)"
        ) from exc


def parse_legal_entity_id(value: str | None) -> UUID | None:
    """Only an omitted id means the default azienda. A string that is given is an id
    or a refusal in words: an empty one, or one that is not a UUID, must not fall back
    to the default and write there in silence."""
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValidationFailed(
            "emitter_profile", "azienda_id", "non e' l'id di un'azienda", expected="un UUID"
        ) from exc


def literal_arg(name: str, value: str | None, allowed: Any) -> Any:
    """`value` when it is one of the strings of the `Literal` alias `allowed` (or `None`,
    an omitted filter); a refusal in words otherwise, in the wording the guard gives a
    pydantic `literal_error`. The one place a runtime `str` becomes the `Literal` a core
    schema types a field with, so the call site needs no `type: ignore`."""
    options: tuple[str, ...] = get_args(allowed)
    if value is not None and value not in options:
        raise ValidationFailed(
            "input",
            name,
            "non è uno dei valori ammessi",
            expected=f"uno fra {', '.join(f'`{option}`' for option in options)}",
        )
    return value


# A closed set of values: a plain `str`, the enum in the listing.
OfferStateArg = Annotated[
    str,
    WithJsonSchema({"type": "string", "enum": ["bozza", "inviata", "accettata", "rifiutata"]}),
]

# The three kinds of record a mail, a draft or a note is filed against.
LinkedEntityType = Literal["customer", "person", "deal"]
LinkedEntityTypeArg = Annotated[
    str,
    WithJsonSchema({"type": "string", "enum": list(get_args(LinkedEntityType))}),
]

# Numbers. `int | str` / `float | str` accept a JSON number or a numeric string and let a
# non-numeric one through to the core schema, which rejects it with guidance.
#
# The paged lists' `limit` (1..200, default 50). The global search has its own bounds, the
# palette's per-class limit (1..20, default 5), and must not advertise these.
BoundedLimit = Annotated[
    int | str,
    WithJsonSchema({"type": "integer", "minimum": 1, "maximum": 200, "default": 50}),
]
SearchLimite = Annotated[
    int | str,
    WithJsonSchema({"type": "integer", "minimum": 1, "maximum": 20, "default": PER_CLASS_LIMIT}),
]


def bounded_limit(value: int | str) -> int:
    """A `BoundedLimit` argument as the `int` of a call that has no `*ListQuery` to
    enforce the 1..200 range (the paged lists get it from their schema)."""
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(
            "input", "limit", f"'{value}' non è un numero intero", expected="un intero da 1 a 200"
        ) from exc
    if not 1 <= parsed <= 200:
        raise ValidationFailed(
            "input", "limit", f"{parsed} fuori intervallo", expected="un intero da 1 a 200"
        )
    return parsed


# A percentage, with and without the "unset" reading: `Probabilita` is for a value with no
# unset meaning (a renewal assumption without a probability is not one).
OptionalProbabilita = Annotated[
    int | str | None,
    WithJsonSchema(
        {
            "anyOf": [{"type": "integer", "minimum": 0, "maximum": 100}, {"type": "null"}],
            "default": None,
        }
    ),
]
Probabilita = Annotated[
    int | str, WithJsonSchema({"type": "integer", "minimum": 0, "maximum": 100})
]
OptionalMoney = Annotated[
    float | str | None,
    WithJsonSchema({"anyOf": [{"type": "number"}, {"type": "null"}], "default": None}),
]
# A share in [0, 1], stated afresh on every call and never stored (the concentration cap).
OptionalShare = Annotated[
    float | str | None,
    WithJsonSchema(
        {
            "anyOf": [{"type": "number", "minimum": 0, "maximum": 1}, {"type": "null"}],
            "default": None,
        }
    ),
]
# Hours and money of a time entry or a cost are required by their schemas, so no `null`.
HoursArg = Annotated[
    float | str, WithJsonSchema({"type": "number", "exclusiveMinimum": 0, "maximum": 24})
]
MoneyArg = Annotated[float | str, WithJsonSchema({"type": "number"})]
OptionalFactor = Annotated[
    float | str | None,
    WithJsonSchema(
        {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}], "default": None}
    ),
]
# A year and a version number have no `*ListQuery` behind them: the call-throughs
# (`tools/timetracking.py::_ANNO`, `tools/documents.py::_NUMERO`) each carry a `TypeAdapter`
# for the bounds advertised here. `Anno` is the required form (a ceiling is for one year).
OptionalAnno = Annotated[
    int | str | None,
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "integer", "minimum": ANNO_MIN, "maximum": ANNO_MAX},
                {"type": "null"},
            ],
            "default": None,
        }
    ),
]
Anno = Annotated[
    int | str, WithJsonSchema({"type": "integer", "minimum": ANNO_MIN, "maximum": ANNO_MAX})
]
VersionNumber = Annotated[int | str, WithJsonSchema({"type": "integer", "minimum": 1})]
# A proposal's confidence and a day's quantity; `ProposalCreate` and `GiornataProposalFields`
# enforce the bound.
Confidenza = Annotated[float | str, WithJsonSchema({"type": "number", "minimum": 0, "maximum": 1})]
QuantitaArg = Annotated[float | str, WithJsonSchema({"type": "number", "exclusiveMinimum": 0})]
