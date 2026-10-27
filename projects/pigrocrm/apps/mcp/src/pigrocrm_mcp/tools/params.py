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
from typing import Annotated, Any

from pydantic import WithJsonSchema

from pigrocrm.core.attivita.schemas import AttivitaUpdate
from pigrocrm.core.contract_expenses.schemas import ContractExpenseUpdate
from pigrocrm.core.contracts.schemas import ClauseNoteUpdate, ContractUpdate, RateCardUpdate
from pigrocrm.core.customers.schemas import CustomerUpdate
from pigrocrm.core.deals.schemas import DealUpdate
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


def iso_date(value: str | None) -> date | None:
    """An `IsoDateStr` argument as the `date` a core schema takes, or `None` when the
    agent left it out. Called *inside* the guarded tool body, so a malformed string is a
    `ValueError` the guard renders as guidance rather than a raw SDK rejection."""
    return date.fromisoformat(value) if value else None


# A closed set of values: a plain `str`, the enum in the listing.
OfferStateArg = Annotated[
    str,
    WithJsonSchema({"type": "string", "enum": ["bozza", "inviata", "accettata", "rifiutata"]}),
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
