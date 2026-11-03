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

from pigrocrm.core.contract_expenses.schemas import ContractExpenseUpdate
from pigrocrm.core.contracts.schemas import ClauseNoteUpdate, ContractUpdate, RateCardUpdate
from pigrocrm.core.customers.schemas import CustomerUpdate
from pigrocrm.core.deals.schemas import DealUpdate, DealValueLineIn
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.people.schemas import PersonUpdate
from pigrocrm.core.search.schemas import PER_CLASS_LIMIT
from pigrocrm.core.tasks.schemas import TaskUpdate
from pigrocrm.core.timetracking.schemas import YEAR_MAX, YEAR_MIN, CostUpdate, TimeEntryUpdate


def self_contained(schema: dict[str, Any]) -> dict[str, Any]:
    """`schema` with every `$ref` into its own `$defs` replaced by the definition: a schema
    embedded in a tool's listing sits under that tool's own root, where a `#/$defs/...`
    pointer no longer resolves. Titles and prose descriptions are left out."""
    definitions = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                return resolve(definitions[reference.removeprefix("#/$defs/")])
            # The listing is paid for in bytes (`test_mcp_tool_annotations`): a model's titles
            # and its class docstring say nothing a field name does not.
            return {
                key: resolve(value)
                for key, value in node.items()
                if key not in ("$defs", "title")
                and not (key == "description" and isinstance(value, str))
            }
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    resolved: dict[str, Any] = resolve(schema)
    return resolved


def _deal_changes_schema() -> dict[str, Any]:
    """`DealUpdate`'s schema with the lines left as plain objects: `create_deal` spells out a
    line once, and the listing is paid for in bytes."""
    schema = self_contained(DealUpdate.model_json_schema())
    lines = schema["properties"]["value_lines"]
    for option in lines["anyOf"]:
        if option.get("type") == "array":
            option["items"] = {"type": "object"}
    return schema


# `changes` of an `update_*`: an unvalidated dict at runtime, the real model's schema in the
# listing, so an agent sees the field names (`update_task` included, with the two
# `*_da_rimuovere` flags that clear a field). The model is built inside the call, in
# `tools/<family>.py`.
CustomerChanges = Annotated[dict[str, Any], WithJsonSchema(CustomerUpdate.model_json_schema())]
PersonChanges = Annotated[dict[str, Any], WithJsonSchema(PersonUpdate.model_json_schema())]
DealChanges = Annotated[dict[str, Any], WithJsonSchema(_deal_changes_schema())]
ContractChanges = Annotated[dict[str, Any], WithJsonSchema(ContractUpdate.model_json_schema())]
RateCardChanges = Annotated[dict[str, Any], WithJsonSchema(RateCardUpdate.model_json_schema())]
ClauseNoteChanges = Annotated[dict[str, Any], WithJsonSchema(ClauseNoteUpdate.model_json_schema())]
ContractExpenseChanges = Annotated[
    dict[str, Any], WithJsonSchema(ContractExpenseUpdate.model_json_schema())
]
TimeEntryChanges = Annotated[dict[str, Any], WithJsonSchema(TimeEntryUpdate.model_json_schema())]
CostChanges = Annotated[dict[str, Any], WithJsonSchema(CostUpdate.model_json_schema())]
TaskChanges = Annotated[dict[str, Any], WithJsonSchema(TaskUpdate.model_json_schema())]

# A date: a `str | None` at runtime, `format: date` in the listing.
IsoDateStr = Annotated[
    str | None,
    WithJsonSchema(
        {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}], "default": None}
    ),
]


def iso_date(value: str | None, field: str = "date") -> date | None:
    """An `IsoDateStr` argument as the `date` a core schema takes, or `None` when the
    agent left it out. Called *inside* the guarded tool body: a malformed string is a
    sentence naming the format, not a raw SDK rejection."""
    return required_iso_date(value, field) if value else None


def required_iso_date(value: str, field: str = "date") -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationFailed(
            "input", field, "is not a valid date", expected="an ISO date (YYYY-MM-DD)"
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
            "legal_entities", "legal_entity_id", "is not a company id", expected="a UUID"
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
            "is not one of the allowed values",
            expected=f"one of {', '.join(f'`{option}`' for option in options)}",
        )
    return value


# A closed set of values: a plain `str`, the enum in the listing.
OfferStateArg = Annotated[
    str,
    WithJsonSchema({"type": "string", "enum": ["draft", "sent", "accepted", "rejected"]}),
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
SearchLimit = Annotated[
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
            "input", "limit", f"'{value}' is not an integer", expected="an integer from 1 to 200"
        ) from exc
    if not 1 <= parsed <= 200:
        raise ValidationFailed(
            "input", "limit", f"{parsed} out of range", expected="an integer from 1 to 200"
        )
    return parsed


# A percentage, with and without the "unset" reading: `Probabilita` is for a value with no
# unset meaning (a renewal assumption without a probability is not one).
OptionalProbability = Annotated[
    int | str | None,
    WithJsonSchema(
        {
            "anyOf": [{"type": "integer", "minimum": 0, "maximum": 100}, {"type": "null"}],
            "default": None,
        }
    ),
]
Probability = Annotated[
    int | str, WithJsonSchema({"type": "integer", "minimum": 0, "maximum": 100})
]
# One line of a deal's value, as the listing shows it: short, because the whole list of tools
# is paid for in bytes. The service validates each line with `DealValueLineIn`.
_VALUE_LINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["unit", "delivery_period", "unit_price"],
    "properties": {
        "description": {"type": "string"},
        "unit": {"enum": list(get_args(DealValueLineIn.model_fields["unit"].annotation))},
        "delivery_period": {
            "enum": list(get_args(DealValueLineIn.model_fields["delivery_period"].annotation))
        },
        "unit_price": {"type": "number"},
        "quantity": {"type": "number", "default": 1},
        "start": {"type": "string", "format": "date"},
        "duration_months": {"type": "integer", "minimum": 1},
    },
}
# The lines of a deal's value, whole: a list of objects, each described in the listing from the
# schema the service validates it with (`unit`, `delivery_period`, `unit_price`, ...).
OptionalValueLines = Annotated[
    list[dict[str, Any]] | None,
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "array", "items": _VALUE_LINE_SCHEMA},
                {"type": "null"},
            ],
            "default": None,
        }
    ),
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
OptionalYear = Annotated[
    int | str | None,
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "integer", "minimum": YEAR_MIN, "maximum": YEAR_MAX},
                {"type": "null"},
            ],
            "default": None,
        }
    ),
]
Year = Annotated[
    int | str, WithJsonSchema({"type": "integer", "minimum": YEAR_MIN, "maximum": YEAR_MAX})
]
VersionNumber = Annotated[int | str, WithJsonSchema({"type": "integer", "minimum": 1})]
# A proposal's confidence and a day's quantity; `ProposalCreate` and `WorkUnitProposalFields`
# enforce the bound.
Confidence = Annotated[float | str, WithJsonSchema({"type": "number", "minimum": 0, "maximum": 1})]
QuantityArg = Annotated[float | str, WithJsonSchema({"type": "number", "exclusiveMinimum": 0})]
